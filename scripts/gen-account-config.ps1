# Generate a per-account config JSON from the strategy template.
# Seeds strategies/<strategy>/config/accounts/<account>.json with EA input defaults.
#
# Usage:
#   .\scripts\gen-account-config.ps1 -Strategy zone_signal -Account 9999999 [-Owner quang] [-Label "ZoneSignal — mt5_X"] [-Force]
param(
    [Parameter(Mandatory)]
    [string]$Strategy,

    [Parameter(Mandatory)]
    [string]$Account,

    [string]$Owner = "unset",

    [string]$Label = "",

    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path

$supportedStrategies = @("zone_signal", "conde_auto_entry", "gvfx_signal")
if ($supportedStrategies -notcontains $Strategy) {
    Write-Error "Unsupported strategy '$Strategy'. Must be one of: $($supportedStrategies -join ', ')"
    exit 1
}

$templatePath = Join-Path $RepoRoot "strategies\$Strategy\config\_template.json"
if (-not (Test-Path $templatePath)) {
    Write-Error "Template not found: $templatePath"
    exit 1
}

$targetDir = Join-Path $RepoRoot "strategies\$Strategy\config\accounts"
$targetPath = Join-Path $targetDir "$Account.json"

if ((Test-Path $targetPath) -and (-not $Force)) {
    Write-Error "Target already exists: strategies/$Strategy/config/accounts/$Account.json -- use -Force to overwrite"
    exit 1
}

$content = Get-Content $templatePath -Raw -Encoding UTF8

# Replace placeholder tokens
$content = $content -replace '<STRATEGY>', $Strategy
$content = $content -replace '<ACCOUNT>', $Account
$content = $content -replace '<OWNER>', $Owner

# If -Label provided, override the label field
if ($Label -ne "") {
    # Replace the label value (the substituted placeholder or any existing string)
    $content = $content -replace '"label"\s*:\s*"[^"]*"', """label"": ""$Label"""
}

if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($targetPath, $content, $utf8NoBom)

Write-Host "Created strategies/$Strategy/config/accounts/$Account.json -- review + edit before committing."
