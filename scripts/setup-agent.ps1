<#
.SYNOPSIS
    Setup Python agents - venv, pip install, and register as Windows service.
.DESCRIPTION
    For each strategy with an agent, creates a Python venv, installs deps,
    writes .env, and prints an install hint if the NSSM service is missing.
    Service start/stop is owned by the workflow's Stop/Start steps that
    bracket this script - this script does NOT touch service state.
.PARAMETER Strategies
    JSON array of strategy names. e.g. '["zone_signal","conde_auto_entry"]'
#>
param(
    [Parameter(Mandatory)]
    [string]$Strategies,

    [string]$Vps = $env:GH_RUNNER_VPS
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_lib.ps1"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Config = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json

$strategyList = $Strategies | ConvertFrom-Json

foreach ($name in $strategyList) {
    $strat = $Config.strategies.$name
    if (-not $strat -or -not $strat.agent -or -not $strat.agent.enabled) {
        Write-Host "[$name] No agent configured - skipping"
        continue
    }

    # VPS filter: skip strategies whose deploy_to terminals all belong to other VPS.
    # Exception: if deploy_to is empty (e.g. telegram_monitor on Linux), treat as
    # "deploys everywhere" and do not filter -- preserves existing behavior.
    if ($Vps -and $strat.deploy_to -and @($strat.deploy_to).Count -gt 0) {
        $localTerminals = Get-LocalTerminals -Deploy $Config -Vps $Vps
        $hasLocal = $false
        foreach ($termName in @($strat.deploy_to)) {
            if ($localTerminals.ContainsKey($termName)) {
                $hasLocal = $true
                break
            }
        }
        if (-not $hasLocal) {
            Write-Host "[$name] No terminals on VPS=$Vps - skipping agent setup"
            continue
        }
    }

    $agentDir = Join-Path $RepoRoot $strat.agent.agent_dir
    $serviceName = $strat.agent.service_name
    $venvDir = Join-Path $agentDir ".venv"
    $requirementsFile = Join-Path $agentDir "requirements.txt"
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"

    Write-Host "[$name] Setting up agent in: $agentDir"

    # 0. Generate .env from GitHub secrets/variables
    #    Lookup order per key: ${STRATEGY_UPPER}_${KEY} (override) -> ${KEY} (shared)
    $prefix = $name.ToUpper()
    $dataDir = Join-Path (Split-Path $agentDir -Parent) "data"
    $envLines = @("MT5_SIGNAL_DIR=$dataDir")

    $required = @($strat.agent.env.required)
    $optional = @($strat.agent.env.optional)
    $missing = @()

    function Resolve-EnvValue($prefix, $key) {
        $val = [Environment]::GetEnvironmentVariable("${prefix}_${key}")
        if ([string]::IsNullOrEmpty($val)) {
            $val = [Environment]::GetEnvironmentVariable($key)
        }
        return $val
    }

    # Derive MT5_ACCOUNTS from deploy.json terminals[strategy.deploy_to[]].accounts
    # when not set via env. Single source of truth - env override still wins.
    function Resolve-AccountsFromDeploy($Config, $stratName) {
        $strat = $Config.strategies.$stratName
        if (-not $strat -or -not $strat.deploy_to) { return $null }
        $accts = New-Object System.Collections.Generic.List[string]
        foreach ($termName in $strat.deploy_to) {
            $term = $Config.terminals.$termName
            if ($term -and $term.accounts) {
                foreach ($a in $term.accounts) { [void]$accts.Add("$a") }
            }
        }
        if ($accts.Count -eq 0) { return $null }
        return ($accts -join ',')
    }

    foreach ($key in $required) {
        $val = Resolve-EnvValue $prefix $key
        if ([string]::IsNullOrEmpty($val) -and $key -eq 'MT5_ACCOUNTS') {
            $val = Resolve-AccountsFromDeploy $Config $name
            if (-not [string]::IsNullOrEmpty($val)) {
                Write-Host "[$name] MT5_ACCOUNTS resolved from deploy.json: $val"
            }
        }
        if ([string]::IsNullOrEmpty($val)) {
            $missing += $key
        } else {
            $envLines += "$key=$val"
        }
    }

    if ($missing.Count -gt 0) {
        $hint = ($missing | ForEach-Object { "${prefix}_$_ or $_" }) -join ', '
        Write-Error "[$name] Missing required env vars: $hint. Configure as GitHub Secrets/Variables."
        continue
    }

    foreach ($key in $optional) {
        $val = Resolve-EnvValue $prefix $key
        if ([string]::IsNullOrEmpty($val) -and $key -eq 'MT5_ACCOUNTS') {
            $val = Resolve-AccountsFromDeploy $Config $name
        }
        if (-not [string]::IsNullOrEmpty($val)) {
            $envLines += "$key=$val"
        }
    }

    $envFile = Join-Path $agentDir ".env"
    Set-Content -Path $envFile -Value $envLines -Encoding ASCII
    Write-Host "[$name] Wrote $envFile ($($envLines.Count) keys)"

    # 1. Create venv if missing or corrupt (pyvenv.cfg gone but python.exe present)
    $pyvenvCfg = Join-Path $venvDir "pyvenv.cfg"
    $venvBroken = (Test-Path $pythonExe) -and (-not (Test-Path $pyvenvCfg))
    if (-not (Test-Path $pythonExe) -or $venvBroken) {
        if ($venvBroken) {
            Write-Host "[$name] venv corrupt (missing pyvenv.cfg) - rebuilding"
            Remove-Item $venvDir -Recurse -Force
        }
        Write-Host "[$name] Creating Python venv..."
        python -m venv $venvDir
        if (-not (Test-Path $pythonExe)) {
            Write-Error "[$name] Failed to create venv"
            continue
        }
        Write-Host "[$name] OK venv created"
    }

    # 2. Install/update requirements
    if (Test-Path $requirementsFile) {
        Write-Host "[$name] Installing requirements..."
        & $pythonExe -m pip install --upgrade pip --quiet
        & $pythonExe -m pip install -r $requirementsFile --quiet
        Write-Host "[$name] OK Requirements installed"
    }

    # 3. Ensure data directory exists
    $dataDir = Join-Path (Split-Path $agentDir -Parent) "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        Write-Host "[$name] Created data dir: $dataDir"
    }

    # 3b. Ensure logs directory exists (NSSM AppStdout/AppStderr need it)
    $logsDir = Join-Path $agentDir "logs"
    if (-not (Test-Path $logsDir)) {
        New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
        Write-Host "[$name] Created logs dir: $logsDir"
    }

    # 4. Service install hint (first deploy only).
    # Service lifecycle (stop/start) is owned by the workflow's Stop/Start
    # steps that bracket this script. Calling `nssm restart` here raced with
    # NSSM's state machine and rolled gvfx_signal_agent into SERVICE_PAUSED
    # on the 2026-05-06 deploy, breaking the subsequent Start step.
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($nssm) {
        # Probe with Get-Service - `nssm status` writes to stderr when the
        # service does not exist, which under $ErrorActionPreference="Stop"
        # escalates to NativeCommandError and aborts the whole script.
        $serviceExists = [bool](Get-Service -Name $serviceName -ErrorAction SilentlyContinue)
        if (-not $serviceExists) {
            Write-Host "[$name] WARN Service '$serviceName' not installed."
            Write-Host "[$name] To install, run:"
            Write-Host "  nssm install $serviceName `"$pythonExe`" `"$(Join-Path $agentDir 'main.py')`""
            Write-Host "  nssm set $serviceName AppDirectory `"$agentDir`""
            Write-Host "  nssm set $serviceName AppStdout `"$(Join-Path $agentDir 'logs\stdout.log')`""
            Write-Host "  nssm set $serviceName AppStderr `"$(Join-Path $agentDir 'logs\stderr.log')`""
            Write-Host "  nssm set $serviceName AppEnvironmentExtra PYTHONUNBUFFERED=1"
            Write-Host "  nssm start $serviceName"
        } else {
            # Idempotent log-capture config: services installed before this block
            # was added are missing AppStdout/AppStderr/PYTHONUNBUFFERED, so
            # crashes leave zero trace and SERVICE_PAUSED is undebuggable.
            # Re-applying these is safe (nssm overwrites with same value).
            $stdoutLog = Join-Path $agentDir "logs\stdout.log"
            $stderrLog = Join-Path $agentDir "logs\stderr.log"
            & nssm set $serviceName AppStdout $stdoutLog 2>&1 | Out-Null
            & nssm set $serviceName AppStderr $stderrLog 2>&1 | Out-Null
            & nssm set $serviceName AppEnvironmentExtra PYTHONUNBUFFERED=1 2>&1 | Out-Null
            Write-Host "[$name] OK NSSM log capture ensured (stdout/stderr + PYTHONUNBUFFERED=1)"
        }
    } else {
        Write-Host "[$name] INFO NSSM not found. To run agent manually:"
        Write-Host "  cd $agentDir"
        Write-Host "  .venv\Scripts\python.exe main.py"
    }
}

Write-Host "OK Agent setup complete"
