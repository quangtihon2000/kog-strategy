<#
.SYNOPSIS
    Register Windows Scheduled Task that runs publish-conde-stats.ps1 every 15 minutes.
.DESCRIPTION
    One-time installer (run as Administrator on the VPS).
    Creates a task that fires every 15 min, runs as the current user,
    and is allowed to start whether the user is logged in or not.

    Re-running this script overwrites the existing task.

.PARAMETER TaskName
    Default: ConderStatsPublish

.PARAMETER RepoRoot
    Path to kog-strategy clone (defaults to parent of this script).

.PARAMETER PublicRepo
    Path to conde-stats clone. Default: C:\bots\conde-stats

.PARAMETER Window
    Stats window. Default: 30d

.PARAMETER IntervalMinutes
    Default: 15
#>
param(
    [string]$TaskName = "ConderStatsPublish",
    [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PublicRepo = "C:\bots\conde-stats",
    [string]$Window = "30d",
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"

$Script = Join-Path $PSScriptRoot "publish-conde-stats.ps1"
if (-not (Test-Path $Script)) {
    throw "publish-conde-stats.ps1 not found: $Script"
}

$argList = @(
    "-NoProfile"
    "-ExecutionPolicy", "Bypass"
    "-File", "`"$Script`""
    "-RepoRoot", "`"$RepoRoot`""
    "-PublicRepo", "`"$PublicRepo`""
    "-Window", $Window
) -join " "

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $argList `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

# Run as the user installing the task, with stored credentials so it
# fires whether the user is logged in or not.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Password `
    -RunLevel Highest

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[install-conde-stats-task] removing existing task '$TaskName'"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "[install-conde-stats-task] registering '$TaskName' (every $IntervalMinutes min)"
Write-Host "  RepoRoot:   $RepoRoot"
Write-Host "  PublicRepo: $PublicRepo"
Write-Host "  Window:     $Window"
Write-Host ""
Write-Host "You will be prompted for $env:USERDOMAIN\$env:USERNAME password (stored by Task Scheduler)."

$cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" `
    -Message "Password for $env:USERDOMAIN\$env:USERNAME (so task can run when logged out)"

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -User $cred.UserName `
    -Password $cred.GetNetworkCredential().Password | Out-Null

Write-Host ""
Write-Host "Done. Verify with:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "Run once now with:  Start-ScheduledTask -TaskName $TaskName"
