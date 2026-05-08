<#
.SYNOPSIS
    Render conde stats dashboard and publish to the public Pages repo.
.DESCRIPTION
    Runs on the Windows VPS. Activates the conde_auto_entry agent venv,
    runs dump_html.py against Redis, then commits/pushes the output to
    the public conde-stats repo (served via GitHub Pages).

    Designed to be invoked by a Windows Scheduled Task every 15 minutes.

.PARAMETER RepoRoot
    Path to the kog-strategy clone on this VPS.
    Default: parent of the scripts dir (resolved from $PSScriptRoot).

.PARAMETER PublicRepo
    Path to the conde-stats public repo clone.
    Default: C:\bots\conde-stats

.PARAMETER Window
    Stats window passed to dump_html.py (e.g. 30d, 7d, 24h).
    Default: 30d
#>
param(
    [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PublicRepo = "C:\bots\conde-stats",
    [string]$Window = "30d"
)

$ErrorActionPreference = "Stop"

$AgentDir = Join-Path $RepoRoot "strategies\conde_auto_entry\agent"
$PythonExe = Join-Path $AgentDir ".venv\Scripts\python.exe"
$DumpScript = Join-Path $AgentDir "dump_html.py"

if (-not (Test-Path $PythonExe)) {
    throw "venv python not found: $PythonExe — run setup-agent.ps1 first"
}
if (-not (Test-Path $DumpScript)) {
    throw "dump_html.py not found: $DumpScript"
}
if (-not (Test-Path (Join-Path $PublicRepo ".git"))) {
    throw "PublicRepo is not a git clone: $PublicRepo — clone https://github.com/quangtihon2000/conde-stats.git there first"
}

Write-Host "[publish-conde-stats] window=$Window out=$PublicRepo"

# Pull latest first to avoid push conflicts (in case of manual edits / README update)
Push-Location $PublicRepo
try {
    git pull --ff-only --quiet
} finally {
    Pop-Location
}

# Render dashboard directly into the public repo working tree
& $PythonExe $DumpScript --window $Window --out $PublicRepo
if ($LASTEXITCODE -ne 0) {
    throw "dump_html.py failed with exit code $LASTEXITCODE"
}

# Commit + push if anything changed
Push-Location $PublicRepo
try {
    git add -A
    $changed = git status --porcelain
    if (-not $changed) {
        Write-Host "[publish-conde-stats] no changes — skipping commit"
        return
    }
    $stamp = (Get-Date -Format "yyyy-MM-dd HH:mm")
    git -c user.email="bot@conde-stats" -c user.name="conde-stats-bot" commit -m "stats $stamp ($Window)" --quiet
    git push --quiet
    Write-Host "[publish-conde-stats] published @ $stamp"
} finally {
    Pop-Location
}
