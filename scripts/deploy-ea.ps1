# Deploy MQL5 EAs to MT5 terminals by copying source and compiling in-place.
# For each strategy, for each target terminal:
#   1. Copy .mq5 source into MQL5/Experts/<EAName>/
#   2. Run that terminal own metaeditor64.exe to compile in-place
#   3. Verify .ex5 was produced
# Param Strategies: JSON array of strategy names. Example: ["zone_signal","hedge_lock"]
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

    $sourceFile = Join-Path $RepoRoot $strat.ea_source
    if (-not (Test-Path $sourceFile)) {
        Write-Warning "[$name] Source not found: $sourceFile — skipping"
        continue
    }

    $eaBaseName = [System.IO.Path]::GetFileNameWithoutExtension($sourceFile)

    foreach ($termName in $strat.deploy_to) {
        $terminal = $Config.terminals.$termName
        if (-not $terminal) {
            Write-Warning "[$name] Terminal '$termName' not found in config — skipping"
            continue
        }

        $expertsRoot = "$env:APPDATA\MetaQuotes\Terminal\$($terminal.hash)\MQL5\Experts"
        $expertsDir = Join-Path $expertsRoot $eaBaseName

        if (-not (Test-Path $expertsDir)) {
            New-Item -ItemType Directory -Path $expertsDir -Force | Out-Null
            Write-Host "[$name → $termName] Created subfolder: $expertsDir"
        }

        # Remove legacy .ex5 / .mq5 at Experts root (pre-subfolder layout)
        foreach ($ext in @(".ex5", ".mq5")) {
            $legacyFile = Join-Path $expertsRoot "$eaBaseName$ext"
            if (Test-Path $legacyFile) {
                Remove-Item $legacyFile -Force
                Write-Host "[$name → $termName] Removed legacy file at root: $legacyFile"
            }
        }

        $destMq5 = Join-Path $expertsDir "$eaBaseName.mq5"
        $destEx5 = Join-Path $expertsDir "$eaBaseName.ex5"
        $logFile = Join-Path $expertsDir "$eaBaseName.log"

        # Backup existing .ex5
        if (Test-Path $destEx5) {
            $backupFile = "$destEx5.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item $destEx5 $backupFile
            Write-Host "[$name → $termName] Backup: $backupFile"
        }

        # Copy source .mq5 into the terminal's Experts subfolder
        Copy-Item $sourceFile $destMq5 -Force
        Write-Host "[$name → $termName] 📄 Source copied to $destMq5"

        # Compile in-place using THIS terminal's MetaEditor + include dir
        $MetaEditor = "$($terminal.mt5_install_dir)\metaeditor64.exe"
        $includeDir = "$env:APPDATA\MetaQuotes\Terminal\$($terminal.hash)\MQL5"

        if (-not (Test-Path $MetaEditor)) {
            Write-Error "[$name → $termName] MetaEditor not found: $MetaEditor"
            $failed += "$name@$termName"
            continue
        }

        Write-Host "[$name → $termName] Compiling in-place: $destMq5"
        Write-Host "[$name → $termName] MetaEditor: $MetaEditor"

        $compileArgs = "/compile:`"$destMq5`" /include:`"$includeDir`" /log:`"$logFile`""
        Start-Process -FilePath $MetaEditor -ArgumentList $compileArgs `
            -Wait -PassThru -NoNewWindow | Out-Null

        if (Test-Path $logFile) {
            $logContent = Get-Content $logFile -Raw -Encoding Unicode
            Write-Host $logContent

            if ($logContent -match "(\d+) error\(s\)") {
                $errorCount = [int]$Matches[1]
                if ($errorCount -gt 0) {
                    Write-Error "[$name → $termName] Compilation FAILED with $errorCount error(s)"
                    $failed += "$name@$termName"
                    continue
                }
            }
        }

        if (Test-Path $destEx5) {
            $size = (Get-Item $destEx5).Length
            Write-Host "[$name → $termName] ✅ Compiled & deployed → $destEx5 ($size bytes)"
        } else {
            Write-Error "[$name → $termName] .ex5 not found after compilation"
            $failed += "$name@$termName"
            continue
        }

        # Setup data symlink if agent is configured
        if ($strat.agent -and $strat.agent.data_subfolder) {
            $dataSubfolder = $strat.agent.data_subfolder
            $filesDir = "$env:APPDATA\MetaQuotes\Terminal\$($terminal.hash)\MQL5\Files\$dataSubfolder"
            $agentDataDir = Join-Path $RepoRoot "strategies\$name\data"

            if (-not (Test-Path $agentDataDir)) {
                New-Item -ItemType Directory -Path $agentDataDir -Force | Out-Null
            }

            # Create symlink: MT5 Files/{EAName} → strategies/{name}/data
            if (-not (Test-Path $filesDir)) {
                cmd /c mklink /D "$filesDir" "$agentDataDir"
                Write-Host "[$name → $termName] 🔗 Symlink: $filesDir → $agentDataDir"
            } else {
                Write-Host "[$name → $termName] ℹ️  Data dir already exists: $filesDir"
            }
        }
    }
}

if ($failed.Count -gt 0) {
    Write-Error "❌ Failed: $($failed -join ', ')"
    exit 1
}

Write-Host "✅ All deployments complete"
