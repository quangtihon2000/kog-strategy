# Validate per-account config files against deploy.json.
# Runs on ubuntu-latest via pwsh (cross-platform compatible).
#
# Rule 1: each account ID must appear in at most ONE terminal across all
#         deploy.terminals.*.accounts arrays.
# Rule 2: for every strategies/<strat>/config/accounts/<acc>.json file,
#         <acc> must exist in the union of terminal accounts for the
#         terminals listed in deploy.strategies[strat].deploy_to.
#
# Exit 0 on success, exit 1 on any violation.

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deployPath = Join-Path $repoRoot "deploy.json"
$deploy = Get-Content $deployPath -Raw | ConvertFrom-Json

$failed = $false

# ---------------------------------------------------------------
# Rule 1: uniqueness - account must appear in at most one terminal
# ---------------------------------------------------------------
$accountToTerminals = @{}

foreach ($prop in $deploy.terminals.PSObject.Properties) {
    $termName = $prop.Name
    $term = $prop.Value
    # Wrap with @() to handle single-element arrays that PS unwraps to scalar
    foreach ($acc in @($term.accounts)) {
        if ($null -eq $acc) { continue }
        $key = "$acc"
        if (-not $accountToTerminals.ContainsKey($key)) {
            $accountToTerminals[$key] = @()
        }
        $accountToTerminals[$key] += $termName
    }
}

foreach ($entry in $accountToTerminals.GetEnumerator()) {
    if ($entry.Value.Count -gt 1) {
        Write-Error "Rule1 FAIL: Account $($entry.Key) declared in multiple terminals: $($entry.Value -join ', ')"
        $failed = $true
    }
}

# ---------------------------------------------------------------
# Rule 2: per-account config files must match an eligible terminal
# ---------------------------------------------------------------
$configFiles = Get-ChildItem -Path (Join-Path $repoRoot "strategies") `
    -Filter "*.json" -Recurse |
    Where-Object { $_.FullName -replace '\\', '/' -match 'config/accounts/' }

if ($null -eq $configFiles -or ($configFiles | Measure-Object).Count -eq 0) {
    Write-Host "No per-account config files found -- skipping Rule 2"
} else {
    foreach ($file in $configFiles) {
        # Extract strategy name from path parts (cross-platform)
        # Expected layout: .../<repoRoot>/strategies/<strat>/config/accounts/<acc>.json
        $normalizedPath = $file.FullName -replace '\\', '/'
        $repoRootNorm = $repoRoot -replace '\\', '/'

        $relativePath = $normalizedPath.Substring($repoRootNorm.Length).TrimStart('/')
        # relativePath = strategies/<strat>/config/accounts/<acc>.json
        $parts = $relativePath -split '/'

        if ($parts.Count -lt 5) {
            Write-Host "Skipping unrecognised config path: $relativePath"
            continue
        }

        # parts[0]=strategies, parts[1]=<strat>, parts[2]=config, parts[3]=accounts, parts[4]=<acc>.json
        $stratName = $parts[1]
        $accStr = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)

        $strat = $deploy.strategies.$stratName
        if (-not $strat) {
            Write-Error "Rule2 FAIL: $relativePath - strategy '$stratName' not found in deploy.json"
            $failed = $true
            continue
        }

        # Build eligible account set from deploy_to terminals
        $eligibleAccounts = @()
        foreach ($termName in @($strat.deploy_to)) {
            if ([string]::IsNullOrEmpty($termName)) { continue }
            $term = $deploy.terminals.$termName
            if ($term -and $term.accounts) {
                foreach ($a in @($term.accounts)) {
                    if ($null -ne $a) { $eligibleAccounts += "$a" }
                }
            }
        }

        if ($eligibleAccounts -notcontains $accStr) {
            $eligibleStr = if ($eligibleAccounts.Count -gt 0) { $eligibleAccounts -join ', ' } else { '(none)' }
            Write-Error "Rule2 FAIL: $relativePath - account '$accStr' not in any terminal of strategy '$stratName'. Eligible: $eligibleStr"
            $failed = $true
        } else {
            Write-Host "OK $relativePath (account $accStr eligible for $stratName)"
        }
    }
}

if ($failed) {
    Write-Error "validate-account-configs: one or more validation failures"
    exit 1
}

Write-Host "OK validate-account-configs"
exit 0
