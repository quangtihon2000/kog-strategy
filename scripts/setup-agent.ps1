<#
.SYNOPSIS
    Setup Python agents — venv, pip install, and register as Windows service.
.DESCRIPTION
    For each strategy with an agent, creates a Python venv, installs deps,
    and optionally restarts the agent service using NSSM.
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
        Write-Host "[$name] No agent configured — skipping"
        continue
    }

    $agentDir = Join-Path $RepoRoot $strat.agent.agent_dir
    $serviceName = $strat.agent.service_name
    $venvDir = Join-Path $agentDir ".venv"
    $requirementsFile = Join-Path $agentDir "requirements.txt"
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"

    Write-Host "[$name] Setting up agent in: $agentDir"

    # 0. Generate .env from GitHub secrets/variables
    #    Lookup order per key: ${STRATEGY_UPPER}_${KEY} (override) → ${KEY} (shared)
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

    # 1. Create venv if it doesn't exist
    if (-not (Test-Path $pythonExe)) {
        Write-Host "[$name] Creating Python venv..."
        python -m venv $venvDir
        if (-not (Test-Path $pythonExe)) {
            Write-Error "[$name] Failed to create venv"
            continue
        }
        Write-Host "[$name] ✅ venv created"
    }

    # 2. Install/update requirements
    if (Test-Path $requirementsFile) {
        Write-Host "[$name] Installing requirements..."
        & $pythonExe -m pip install --upgrade pip --quiet
        & $pythonExe -m pip install -r $requirementsFile --quiet
        Write-Host "[$name] ✅ Requirements installed"
    }

    # 3. Ensure data directory exists
    $dataDir = Join-Path (Split-Path $agentDir -Parent) "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        Write-Host "[$name] Created data dir: $dataDir"
    }

    # 4. Restart service if NSSM is available
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if ($nssm) {
        $serviceExists = (nssm status $serviceName 2>&1) -notmatch "can't open"

        if ($serviceExists) {
            Write-Host "[$name] Restarting service: $serviceName"
            nssm restart $serviceName
            Write-Host "[$name] ✅ Service restarted"
        } else {
            Write-Host "[$name] ⚠️  Service '$serviceName' not installed."
            Write-Host "[$name] To install, run:"
            Write-Host "  nssm install $serviceName `"$pythonExe`" `"$(Join-Path $agentDir 'main.py')`""
            Write-Host "  nssm set $serviceName AppDirectory `"$agentDir`""
            Write-Host "  nssm start $serviceName"
        }
    } else {
        Write-Host "[$name] ℹ️  NSSM not found. To run agent manually:"
        Write-Host "  cd $agentDir"
        Write-Host "  .venv\Scripts\python.exe main.py"
    }
}

Write-Host "✅ Agent setup complete"
