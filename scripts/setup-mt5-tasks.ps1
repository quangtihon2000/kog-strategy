<#
.SYNOPSIS
    One-time setup: register a Scheduled Task per MT5 terminal so the deploy
    workflow can launch MT5 inside the target user's interactive session.
.DESCRIPTION
    Run this on the Windows VPS, logged in as the user that owns the MT5
    installs (e.g. QuangXAU). It reads deploy.json, and for each terminal
    creates a task named "KOG_MT5_<termName>" that:
      - Runs as the current user ($env:USERNAME)
      - "Run only when user is logged on" (no password required, will use
        the existing interactive session - this is what we want so MT5
        appears on QuangXAU's desktop, not Session 0)
      - Action = the terminal's terminal64.exe with the install dir as cwd
      - No trigger - the task only runs when invoked via `schtasks /run`
        (deploy-ea.ps1 triggers it after compiling new .ex5)

    The deploy workflow runs as Administrator/LocalSystem in Session 0;
    `schtasks /run /tn KOG_MT5_<termName>` from there causes the Windows
    Task Scheduler service to start the process in the principal's session.
.NOTES
    - Must be run as the user listed in deploy.json terminals.user_profile
      (currently all = QuangXAU). Refuses to run otherwise.
    - Idempotent: re-running deletes and recreates each task.
#>
param(
    [string]$DeployJson
)

# NB: ErrorActionPreference=Continue (not Stop). schtasks.exe writes to stderr
# on the "task not found" probe path, and under Stop that escalates to
# NativeCommandError and aborts the loop on the first terminal.
$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $DeployJson) { $DeployJson = Join-Path $RepoRoot "deploy.json" }

$Config = Get-Content $DeployJson -Raw | ConvertFrom-Json
$me = $env:USERNAME

Write-Host "Running as: $me"
Write-Host "Reading: $DeployJson"
Write-Host ""

$terminals = $Config.terminals.PSObject.Properties
foreach ($prop in $terminals) {
    $termName = $prop.Name
    $term = $prop.Value
    $taskName = "KOG_MT5_$termName"
    $exePath = Join-Path $term.mt5_install_dir "terminal64.exe"

    if (-not (Test-Path $exePath)) {
        Write-Warning "[$termName] terminal64.exe not found: $exePath - skipping"
        continue
    }

    if ($term.user_profile -and $term.user_profile -ne $me) {
        Write-Warning "[$termName] user_profile=$($term.user_profile) but you are $me - skipping. Re-run this script logged in as $($term.user_profile)."
        continue
    }

    # Drop existing task (idempotent rerun)
    & schtasks.exe /query /tn $taskName *> $null
    if ($LASTEXITCODE -eq 0) {
        & schtasks.exe /delete /tn $taskName /f *> $null
        Write-Host "[$termName] Removed existing task"
    }

    # Build XML so we can set "Run only when user is logged on" + working dir.
    # /RL LIMITED + omitting /RP works because the task runs in the existing
    # interactive session (no password prompt, no stored credential).
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Launch $($term.label) under $me's interactive session. Triggered by KOG deploy workflow.</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$me</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$exePath</Command>
      <WorkingDirectory>$($term.mt5_install_dir)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

    $xmlFile = Join-Path $env:TEMP "$taskName.xml"
    # schtasks /create /xml requires UTF-16 LE on Windows
    [System.IO.File]::WriteAllText($xmlFile, $xml, [System.Text.Encoding]::Unicode)

    & schtasks.exe /create /tn $taskName /xml $xmlFile /f *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[$termName] schtasks /create failed (exit $LASTEXITCODE). XML kept at $xmlFile for debugging."
        continue
    }
    Remove-Item $xmlFile -Force

    Write-Host "[$termName] OK Task '$taskName' created (runs $exePath as $me when triggered)"
}

Write-Host ""
Write-Host "Done. Verify with: schtasks /query /tn KOG_MT5_mt5_main /v /fo list"
Write-Host "Test trigger:    schtasks /run /tn KOG_MT5_mt5_main"
