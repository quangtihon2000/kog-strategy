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

    # Base interpreter for NSSM Application — bypass venv shim PID-handle race.
    # Venv shim `.venv\Scripts\python.exe` execs base interpreter on launch → PID
    # changes → NSSM "Failed to open process handle" → fabricates exit 255 →
    # AppExit Default Exit → SERVICE_STOPPED. Validated 2026-05-18: gvfx +
    # telegram fail deterministically under shim, succeed under base+PYTHONPATH.
    # Venv packages still load via PYTHONPATH=<venv>\Lib\site-packages.
    $basePython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $basePython) { $basePython = "C:\Program Files\Python310\python.exe" }
    $venvSitePackages = Join-Path $venvDir "Lib\site-packages"
    $mainPy = Join-Path $agentDir "main.py"
    # NSSM `AppEnvironmentExtra` requires each KEY=VAL as a SEPARATE argument
    # — passing a single space-joined string makes NSSM store the whole blob
    # under the first key, so PYTHONPATH/VIRTUAL_ENV silently never apply.
    $envExtraArgs = @(
        "PYTHONUNBUFFERED=1",
        "PYTHONPATH=$venvSitePackages",
        "VIRTUAL_ENV=$venvDir"
    )

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
            Write-Host "  nssm install $serviceName `"$basePython`" `"-u`" `"$mainPy`""
            Write-Host "  nssm set $serviceName AppDirectory `"$agentDir`""
            Write-Host "  nssm set $serviceName AppStdout `"$(Join-Path $agentDir 'logs\stdout.log')`""
            Write-Host "  nssm set $serviceName AppStderr `"$(Join-Path $agentDir 'logs\stderr.log')`""
            Write-Host "  nssm set $serviceName AppEnvironmentExtra $($envExtraArgs -join ' ')"
            Write-Host "  nssm set $serviceName AppThrottle 0"
            Write-Host "  nssm set $serviceName AppExit Default Exit"
            Write-Host "  nssm start $serviceName"
        } else {
            # Idempotent log-capture config: services installed before this block
            # was added are missing AppStdout/AppStderr/PYTHONUNBUFFERED, so
            # crashes leave zero trace and SERVICE_PAUSED is undebuggable.
            # Re-applying these is safe (nssm overwrites with same value).
            $stdoutLog = Join-Path $agentDir "logs\stdout.log"
            $stderrLog = Join-Path $agentDir "logs\stderr.log"
            # Repoint Application from venv shim → base interpreter (see comment
            # near $basePython above for the PID-handle race). Venv deps still
            # load via PYTHONPATH in AppEnvironmentExtra below.
            & nssm set $serviceName Application $basePython 2>&1 | Out-Null
            & nssm set $serviceName AppParameters "-u `"$mainPy`"" 2>&1 | Out-Null
            & nssm set $serviceName AppDirectory $agentDir 2>&1 | Out-Null
            & nssm set $serviceName AppStdout $stdoutLog 2>&1 | Out-Null
            & nssm set $serviceName AppStderr $stderrLog 2>&1 | Out-Null
            & nssm set $serviceName AppEnvironmentExtra @envExtraArgs 2>&1 | Out-Null
            # AppThrottle default (1500ms) was racing slow Python cold-start
            # (redis connect + apscheduler init) on gvfx + telegram_monitor,
            # leaving services in SERVICE_PAUSED after every deploy even
            # though the process was healthy. Disable throttle entirely.
            & nssm set $serviceName AppThrottle 0 2>&1 | Out-Null
            # If the agent really crashes, stop the service instead of
            # NSSM auto-restarting in a loop -- deploy fails fast and
            # stderr.log has a single clean traceback to read.
            & nssm set $serviceName AppExit Default Exit 2>&1 | Out-Null
            Write-Host "[$name] OK NSSM config ensured (base-python + venv PYTHONPATH + logs + throttle=0 + exit=stop)"

            # Reap orphan python.exe from prior incarnation BEFORE the workflow's
            # Start step fires. NSSM's Stop step alone isn't sufficient: the venv
            # shim execs the base interpreter (PID changes) and NSSM occasionally
            # loses the process handle, leaving the real worker alive after Stop.
            # The orphan then holds singleton resources (Redis consumer name /
            # Telegram getUpdates slot) -> the next instance crashes with exit
            # 255 in <100ms, before NSSM attaches stderr -> undebuggable empty
            # log. Filter by ExecutablePath (reliable on shimmed venvs) AND
            # CommandLine as fallback.
            $venvPython = Join-Path $agentDir ".venv\Scripts\python.exe"
            $orphans = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.ExecutablePath -eq $venvPython -or
                    $_.CommandLine -like "*$agentDir*"
                }
            foreach ($p in $orphans) {
                Write-Host "[$name] Reaping orphan python.exe PID=$($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
            if ($orphans) { Start-Sleep -Seconds 2 }
        }
    } else {
        Write-Host "[$name] INFO NSSM not found. To run agent manually:"
        Write-Host "  cd $agentDir"
        Write-Host "  .venv\Scripts\python.exe main.py"
    }
}

Write-Host "OK Agent setup complete"
