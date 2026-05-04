<#
.SYNOPSIS
    Check status of Python agent services on the VPS.
.DESCRIPTION
    For each strategy with an enabled agent in deploy.json, reports:
      - NSSM service status (RUNNING / STOPPED / not installed)
      - Windows service state + PID + uptime
      - Latest log file size, last write time, freshness
      - Last N lines of stdout + stderr logs
.PARAMETER Strategy
    Optional. Single strategy name (e.g. "zone_signal"). Default: all enabled agents.
.PARAMETER LogTail
    Number of log lines to show from end of each log file. Default: 15.
.EXAMPLE
    .\scripts\check-agents.ps1
    .\scripts\check-agents.ps1 -Strategy zone_signal -LogTail 30
#>
param(
    [string]$Strategy = "",
    [int]$LogTail = 15
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Config = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$agentNames = @()
if ($Strategy) {
    $agentNames = @($Strategy)
} else {
    foreach ($prop in $Config.strategies.PSObject.Properties) {
        $s = $prop.Value
        if ($s.agent -and $s.agent.enabled) { $agentNames += $prop.Name }
    }
}

if ($agentNames.Count -eq 0) {
    Write-Host "No enabled agents found in deploy.json"
    exit 0
}

$nssm = Get-Command nssm -ErrorAction SilentlyContinue

function Format-Age($lastWrite) {
    if (-not $lastWrite) { return "n/a" }
    $age = (Get-Date) - $lastWrite
    if ($age.TotalSeconds -lt 60)   { return "{0:N0}s ago" -f $age.TotalSeconds }
    if ($age.TotalMinutes -lt 60)   { return "{0:N1}m ago" -f $age.TotalMinutes }
    if ($age.TotalHours -lt 24)     { return "{0:N1}h ago" -f $age.TotalHours }
    return "{0:N1}d ago" -f $age.TotalDays
}

foreach ($name in $agentNames) {
    $strat = $Config.strategies.$name
    if (-not $strat -or -not $strat.agent) {
        Write-Host "[$name] No agent configured — skipping" -ForegroundColor Yellow
        continue
    }

    $serviceName = $strat.agent.service_name
    $agentDir    = Join-Path $RepoRoot $strat.agent.agent_dir
    $logsDir     = Join-Path $agentDir "logs"

    Write-Host ""
    Write-Host "=== $name ($serviceName) ===" -ForegroundColor Cyan

    # 1. NSSM status
    if ($nssm) {
        $nssmStatus = (nssm status $serviceName 2>&1 | Out-String).Trim()
        if ($nssmStatus -match "can't open") {
            Write-Host "  NSSM:    not installed" -ForegroundColor Red
        } else {
            $color = if ($nssmStatus -match "RUNNING") { "Green" } else { "Yellow" }
            Write-Host "  NSSM:    $nssmStatus" -ForegroundColor $color
        }
    } else {
        Write-Host "  NSSM:    binary not found in PATH" -ForegroundColor Yellow
    }

    # 2. Windows service detail
    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($svc) {
        $filter = 'Name=' + [char]39 + $serviceName + [char]39
        $wmi = Get-CimInstance Win32_Service -Filter $filter -ErrorAction SilentlyContinue
        $procPid = 0
        if ($wmi) { $procPid = $wmi.ProcessId }
        $proc = $null
        if ($procPid -gt 0) {
            $proc = Get-Process -Id $procPid -ErrorAction SilentlyContinue
        }
        $uptime = 'n/a'
        $cpuSec = 'n/a'
        $memMB  = 'n/a'
        if ($proc) {
            $uptime = Format-Age $proc.StartTime
            $cpuSec = '{0:N1}s' -f $proc.CPU
            $memMB  = '{0:N1}MB' -f ($proc.WorkingSet64 / 1MB)
        }
        Write-Host "  Service: $($svc.Status)  PID=$procPid  uptime=$uptime  CPU=$cpuSec  RAM=$memMB"
    } else {
        Write-Host "  Service: not registered" -ForegroundColor Red
    }

    # 3. Log files
    if (Test-Path $logsDir) {
        $logs = Get-ChildItem -Path $logsDir -Filter "*.log" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        if ($logs.Count -eq 0) {
            Write-Host "  Logs:    no .log files in $logsDir" -ForegroundColor Yellow
        } else {
            foreach ($log in $logs) {
                $sizeKB = "{0:N1}KB" -f ($log.Length / 1KB)
                $age    = Format-Age $log.LastWriteTime
                $stale  = if (((Get-Date) - $log.LastWriteTime).TotalMinutes -gt 5) { " [STALE]" } else { "" }
                Write-Host "  Log:     $($log.Name)  $sizeKB  ($age)$stale"

                if ($log.Length -gt 0) {
                    try {
                        $tail = Get-Content -Path $log.FullName -Tail $LogTail -ErrorAction Stop
                        foreach ($line in $tail) {
                            Write-Host "    | $line" -ForegroundColor DarkGray
                        }
                    } catch {
                        Write-Host "    | (failed to read: $($_.Exception.Message))" -ForegroundColor Red
                    }
                }
            }
        }
    } else {
        Write-Host "  Logs:    dir not found ($logsDir)" -ForegroundColor Yellow
    }

    # 4. Data dir freshness (signal files)
    $dataDir = Join-Path (Split-Path $agentDir -Parent) "data"
    if (Test-Path $dataDir) {
        $jsons = Get-ChildItem -Path $dataDir -Filter "*.json" -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 3
        if ($jsons.Count -gt 0) {
            Write-Host "  Signals (data/):"
            foreach ($j in $jsons) {
                Write-Host "    $($j.Name)  ($(Format-Age $j.LastWriteTime))"
            }
        } else {
            Write-Host "  Signals: no .json files in data/" -ForegroundColor Yellow
        }
    }
}

Write-Host ""
