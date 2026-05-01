<#
.SYNOPSIS
    Deploy compiled .ex5 files to MT5 terminal data folders.
.DESCRIPTION
    Reads deploy.json, copies each strategy's .ex5 to the configured
    MT5 terminal(s) Experts directory. Creates backup of existing files.
.PARAMETER Strategies
    JSON array of strategy names to deploy. e.g. '["zone_signal","hedge_lock"]'
#>
param(
    [Parameter(Mandatory)]
    [string]$Strategies
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Config = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$strategyList = $Strategies | ConvertFrom-Json

foreach ($name in $strategyList) {
    $strat = $Config.strategies.$name
    if (-not $strat) {
        Write-Warning "[$name] Not found in deploy.json — skipping"
        continue
    }

    $sourceFile = Join-Path $RepoRoot $strat.ea_source
    $ex5File = [System.IO.Path]::ChangeExtension($sourceFile, ".ex5")

    if (-not (Test-Path $ex5File)) {
        Write-Warning "[$name] .ex5 not found: $ex5File — skipping deploy"
        continue
    }

    foreach ($termName in $strat.deploy_to) {
        $terminal = $Config.terminals.$termName
        if (-not $terminal) {
            Write-Warning "[$name] Terminal '$termName' not found in config — skipping"
            continue
        }

        $eaBaseName = [System.IO.Path]::GetFileNameWithoutExtension($ex5File)
        $expertsRoot = "$env:APPDATA\MetaQuotes\Terminal\$($terminal.hash)\MQL5\Experts"
        $expertsDir = Join-Path $expertsRoot $eaBaseName

        if (-not (Test-Path $expertsDir)) {
            New-Item -ItemType Directory -Path $expertsDir -Force | Out-Null
            Write-Host "[$name → $termName] Created subfolder: $expertsDir"
        }

        # Remove legacy .ex5 at Experts root (pre-subfolder layout)
        $legacyFile = Join-Path $expertsRoot ([System.IO.Path]::GetFileName($ex5File))
        if (Test-Path $legacyFile) {
            Remove-Item $legacyFile -Force
            Write-Host "[$name → $termName] Removed legacy file at root: $legacyFile"
        }

        $destFile = Join-Path $expertsDir ([System.IO.Path]::GetFileName($ex5File))

        # Backup existing file
        if (Test-Path $destFile) {
            $backupFile = "$destFile.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Copy-Item $destFile $backupFile
            Write-Host "[$name → $termName] Backup: $backupFile"
        }

        # Copy new .ex5
        Copy-Item $ex5File $destFile -Force
        Write-Host "[$name → $termName] ✅ Deployed to $destFile"

        # Copy source .mq5 alongside (so it shows in MetaEditor / Navigator)
        if (Test-Path $sourceFile) {
            $destMq5 = Join-Path $expertsDir ([System.IO.Path]::GetFileName($sourceFile))
            Copy-Item $sourceFile $destMq5 -Force
            Write-Host "[$name → $termName] 📄 Source copied to $destMq5"
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

Write-Host "✅ All deployments complete"
