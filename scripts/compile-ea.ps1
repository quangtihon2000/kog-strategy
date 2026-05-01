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
$Config = Get-Content "$RepoRoot\deploy.json" | ConvertFrom-Json
$MetaEditor = "$($Config.mt5_install_dir)\metaeditor64.exe"

if (-not (Test-Path $MetaEditor)) {
    Write-Error "MetaEditor not found: $MetaEditor"
    exit 1
}

$strategyList = $Strategies | ConvertFrom-Json
$failed = @()

foreach ($name in $strategyList) {
    $strat = $Config.strategies.$name
    if (-not $strat) {
        Write-Warning "[$name] Not found in deploy.json — skipping"
        continue
    }

    $sourceFile = Join-Path $RepoRoot $strat.ea_source
    if (-not (Test-Path $sourceFile)) {
        Write-Warning "[$name] Source not found: $sourceFile — skipping"
        continue
    }

    $logFile = [System.IO.Path]::ChangeExtension($sourceFile, ".log")
    $ex5File = [System.IO.Path]::ChangeExtension($sourceFile, ".ex5")

    # Find the MQL5 Include directory from the first terminal
    $firstTerminal = ($strat.deploy_to | Select-Object -First 1)
    $termHash = $Config.terminals.$firstTerminal.hash
    $includeDir = "$env:APPDATA\MetaQuotes\Terminal\$termHash\MQL5\Include"

    Write-Host "[$name] Compiling: $sourceFile"
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
