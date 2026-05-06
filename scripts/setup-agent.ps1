<#
.SYNOPSIS
    Setup Python agents - venv, pip install, and register as Windows service.
.DESCRIPTION
    For each strategy with an agent, creates a Python venv, installs deps,
    writes .env, and prints an install hint if the NSSM service is missing.
    Service start/stop is owned by the workflow's Stop/Start steps that
    bracket this script — this script does NOT touch service state.
.PARAMETER Strategies
    JSON array of strategy names. e.g. '["zone_signal","conde_auto_entry"]'
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
    if (-not $strat -or -not $strat.agent -or -not $strat.agent.enabled) {
        Write-Host "[$name] No agent configured - skipping"
        continue
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

    foreach ($key in $required) {
        $val = Resolve-EnvValue $prefix $key
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
        }
    } else {
        Write-Host "[$name] INFO NSSM not found. To run agent manually:"
        Write-Host "  cd $agentDir"
        Write-Host "  .venv\Scripts\python.exe main.py"
    }
}

Write-Host "OK Agent setup complete"
