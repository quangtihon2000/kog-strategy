# Move-StrandedCondeOutcomes.ps1
#
# One-shot recovery: move CondeAutoEntryEA outcome JSON files from MT5's
# Common\Files\ (where EA mistakenly wrote them when InpUseCommonDir=true)
# into the terminal-specific MQL5\Files\ folder that outcome_publisher.py polls.
#
# Run AS THE MT5 USER (QuangXAU / luanxau), NOT as Administrator — paths under
# $env:APPDATA are user-scoped.
#
# Usage:
#   .\Move-StrandedCondeOutcomes.ps1                  # auto-detect target
#   .\Move-StrandedCondeOutcomes.ps1 -TargetHash abc  # pick a specific terminal
#   .\Move-StrandedCondeOutcomes.ps1 -DryRun          # preview only

[CmdletBinding()]
param(
    [string]$TargetHash = $null,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ea         = 'CondeAutoEntryEA'
$commonRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files\$ea\outcomes"
$termRoot   = Join-Path $env:APPDATA 'MetaQuotes\Terminal'

if (-not (Test-Path -LiteralPath $commonRoot)) {
    Write-Host "No stranded outcomes — '$commonRoot' does not exist."
    exit 0
}

$strandedFiles = @(Get-ChildItem -LiteralPath $commonRoot -Filter '*.json' -File -ErrorAction SilentlyContinue)
if ($strandedFiles.Count -eq 0) {
    Write-Host "No stranded outcomes — '$commonRoot' is empty."
    exit 0
}
Write-Host "Found $($strandedFiles.Count) stranded outcome file(s) under:"
Write-Host "  $commonRoot"
Write-Host ""

# Enumerate terminal hash dirs that have an EA outcomes folder
$candidates = Get-ChildItem -LiteralPath $termRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne 'Common' } |
    ForEach-Object {
        $eaDir = Join-Path $_.FullName "MQL5\Files\$ea\outcomes"
        if (Test-Path -LiteralPath $eaDir) {
            [PSCustomObject]@{ Hash = $_.Name; OutcomesDir = $eaDir }
        }
    }

if (-not $candidates -or $candidates.Count -eq 0) {
    Write-Error "No terminal under '$termRoot' has 'MQL5\Files\$ea\outcomes' — is the EA deployed under this user?"
    exit 1
}

if ($TargetHash) {
    $target = $candidates | Where-Object { $_.Hash -eq $TargetHash }
    if (-not $target) {
        Write-Error "TargetHash '$TargetHash' not found. Available: $(($candidates.Hash) -join ', ')"
        exit 1
    }
} elseif ($candidates.Count -eq 1) {
    $target = $candidates[0]
} else {
    Write-Error "Multiple terminals found — re-run with -TargetHash <hash>. Candidates:`n$(($candidates | ForEach-Object { "  $($_.Hash) -> $($_.OutcomesDir)" }) -join "`n")"
    exit 1
}

Write-Host "Target outcomes dir:"
Write-Host "  $($target.OutcomesDir)"
Write-Host ""

# Safety: confirm target is NOT itself the Common dir (paranoid check against weird junctions)
$targetItem = Get-Item -LiteralPath $target.OutcomesDir -Force
if ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    $resolved = (Resolve-Path -LiteralPath $target.OutcomesDir).ProviderPath
    Write-Host "NOTE: target is a reparse point/junction -> $resolved"
}

$moved   = 0
$skipped = 0
$failed  = 0

foreach ($f in $strandedFiles) {
    $dest = Join-Path $target.OutcomesDir $f.Name
    if (Test-Path -LiteralPath $dest) {
        Write-Host "  SKIP (exists): $($f.Name)"
        $skipped++
        continue
    }
    if ($DryRun) {
        Write-Host "  DRY-RUN move : $($f.Name)"
        $moved++
        continue
    }
    try {
        Move-Item -LiteralPath $f.FullName -Destination $dest -ErrorAction Stop
        Write-Host "  MOVED        : $($f.Name)"
        $moved++
    } catch {
        Write-Host "  FAILED       : $($f.Name) -- $($_.Exception.Message)"
        $failed++
    }
}

Write-Host ""
Write-Host "Done. moved=$moved skipped=$skipped failed=$failed dry_run=$($DryRun.IsPresent)"
if ($failed -gt 0) { exit 2 }
