# Copy per-account config JSON files to each MT5 terminal's Files/<EAName>/config/ directory.
# Only copies configs for accounts that belong to the given terminal (per deploy.json).
# This script does NOT manage NSSM service lifecycle -- that is owned by the workflow.
#
# Usage:
#   .\scripts\deploy-account-configs.ps1 -Vps 'vps-sg' -Strategies '["zone_signal","gvfx_signal"]'
param(
    [Parameter(Mandatory)]
    [string]$Vps,

    [Parameter(Mandatory)]
    [string]$Strategies   # JSON array string, e.g. '["zone_signal"]'
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path

. "$PSScriptRoot\_lib.ps1"

$deploy = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$strategyList = $Strategies | ConvertFrom-Json

foreach ($name in $strategyList) {
    $strat = $deploy.strategies.$name

    # Skip if strategy unknown
    if (-not $strat) {
        Write-Host "[$name] Not found in deploy.json - skipping"
        continue
    }

    # telegram_monitor has no ea_source (agent-only, Linux VPS) -- no EA config dir
    if ([string]::IsNullOrEmpty($strat.ea_source)) {
        Write-Host "[$name] No ea_source (agent-only strategy) - skipping config deploy"
        continue
    }

    # Source directory for per-account configs
    $sourceDir = Join-Path $RepoRoot "strategies\$name\config\accounts"
    if (-not (Test-Path $sourceDir)) {
        Write-Host "[$name] No config/accounts dir ($sourceDir) - skipping"
        continue
    }

    # Determine EA name from ea_source filename
    $eaName = [System.IO.Path]::GetFileNameWithoutExtension($strat.ea_source)

    foreach ($termName in @($strat.deploy_to)) {
        if ([string]::IsNullOrEmpty($termName)) { continue }

        $terminal = $deploy.terminals.$termName
        if (-not $terminal) {
            Write-Host "[$name/$termName] Terminal not found in deploy.json - skipping"
            continue
        }

        # VPS filter: skip terminals not belonging to this VPS
        if ($terminal.vps) {
            $termVps = $terminal.vps
        } else {
            $termVps = 'vps-sg'
        }
        if ($termVps -ne $Vps) {
            Write-Host "[$name/$termName] Skipped - belongs to $termVps, current VPS=$Vps"
            continue
        }

        # Resolve AppData for the user that runs this MT5 instance
        if ($terminal.user_profile) {
            $userAppData = "C:\Users\$($terminal.user_profile)\AppData\Roaming"
        } else {
            $userAppData = $env:APPDATA
            Write-Warning "[$name/$termName] No user_profile in deploy.json, using runner APPDATA: $userAppData"
        }

        $targetDir = "$userAppData\MetaQuotes\Terminal\$($terminal.hash)\MQL5\Files\$eaName\config"

        if (-not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            Write-Host "[$name/$termName] Created target dir: $targetDir"
        }

        # Build set of accounts for this terminal (wrap with @() for single-element safety)
        $termAccounts = @()
        foreach ($a in @($terminal.accounts)) {
            if ($null -ne $a) { $termAccounts += "$a" }
        }

        # Copy only configs whose filename (account ID) is in this terminal's account list
        $configFiles = Get-ChildItem -Path $sourceDir -Filter "*.json" -ErrorAction SilentlyContinue
        if (-not $configFiles) {
            Write-Host "[$name/$termName] No .json files in $sourceDir - nothing to copy"
            continue
        }

        foreach ($file in $configFiles) {
            $accStr = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
            if ($termAccounts -notcontains $accStr) {
                Write-Host "[$name/$termName] Skipping $($file.Name) - account $accStr not in this terminal's account list"
                continue
            }

            $destFile = Join-Path $targetDir $file.Name
            Copy-Item -Path $file.FullName -Destination $destFile -Force
            Write-Host "[$name/$termName] Copied $($file.Name) -> $destFile"
        }
    }
}

Write-Host "OK deploy-account-configs complete"
