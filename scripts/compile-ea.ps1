<#
.SYNOPSIS
    Compile MQL5 EA files using MetaEditor command line.
.DESCRIPTION
    Reads deploy.json, finds .mq5 source for each changed strategy,
    compiles with metaeditor64.exe, and checks the log for errors.
.PARAMETER Strategies
    JSON array of strategy names to compile. e.g. '["zone_signal","hedge_lock"]'
#>
param(
    [Parameter(Mandatory)]
    [string]$Strategies
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Config = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$strategyList = $Strategies | ConvertFrom-Json
$failed = @()

foreach ($name in $strategyList) {
    $strat = $Config.strategies.$name
    if (-not $strat) {
        Write-Warning "[$name] Not found in deploy.json — skipping"
        continue
    }

    # Skip strategies with no terminals assigned
    if (-not $strat.deploy_to -or $strat.deploy_to.Count -eq 0) {
        Write-Host "[$name] No terminals assigned — skipping compile"
        continue
    }

    $sourceFile = Join-Path $RepoRoot $strat.ea_source
    if (-not (Test-Path $sourceFile)) {
        Write-Warning "[$name] Source not found: $sourceFile — skipping"
        continue
    }

    $logFile = [System.IO.Path]::ChangeExtension($sourceFile, ".log")
    $ex5File = [System.IO.Path]::ChangeExtension($sourceFile, ".ex5")

    # Use MetaEditor from the first target terminal's install dir
    $firstTerminal = ($strat.deploy_to | Select-Object -First 1)
    $terminal = $Config.terminals.$firstTerminal
    $termHash = $terminal.hash
    $MetaEditor = "$($terminal.mt5_install_dir)\metaeditor64.exe"
    $includeDir = "$env:APPDATA\MetaQuotes\Terminal\$termHash\MQL5"

    if (-not (Test-Path $MetaEditor)) {
        Write-Error "[$name] MetaEditor not found: $MetaEditor"
        $failed += $name
        continue
    }

    Write-Host "[$name] Compiling: $sourceFile"
    Write-Host "[$name] MetaEditor: $MetaEditor"
    Write-Host "[$name] Include:  $includeDir"

    # Run MetaEditor compiler
    $compileArgs = "/compile:`"$sourceFile`" /include:`"$includeDir`" /log:`"$logFile`""
    $process = Start-Process -FilePath $MetaEditor -ArgumentList $compileArgs `
        -Wait -PassThru -NoNewWindow

    # Parse log for errors
    if (Test-Path $logFile) {
        $logContent = Get-Content $logFile -Raw -Encoding Unicode
        Write-Host $logContent

        if ($logContent -match "(\d+) error\(s\)") {
            $errorCount = [int]$Matches[1]
            if ($errorCount -gt 0) {
                Write-Error "[$name] Compilation FAILED with $errorCount error(s)"
                $failed += $name
                continue
            }
        }
    }

    # Verify .ex5 was created
    if (Test-Path $ex5File) {
        $size = (Get-Item $ex5File).Length
        Write-Host "[$name] ✅ Compiled OK → $ex5File ($size bytes)"
    } else {
        Write-Error "[$name] .ex5 file not found after compilation"
        $failed += $name
    }
}

if ($failed.Count -gt 0) {
    Write-Error "❌ Failed strategies: $($failed -join ', ')"
    exit 1
}

Write-Host "✅ All compilations successful"
