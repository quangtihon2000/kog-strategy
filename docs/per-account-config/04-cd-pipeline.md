# 04 — CD pipeline changes

## Overview

| Component | Change |
|---|---|
| `scripts/_lib.ps1` | **MỚI** — helpers chung (`Get-LocalTerminals`, `Ensure-ConfigJunction`, `Restart-Mt5Terminal`). |
| `scripts/deploy-ea.ps1` | Thêm filter `-Vps`; tạo junction config sau junction data; gọi `Restart-Mt5Terminal` từ lib. |
| `scripts/setup-agent.ps1` | Đọc `accounts` từ deploy.json (fallback env var); filter `-Vps`. |
| `scripts/deploy-account-configs.ps1` | **MỚI** — chỉ junction config + restart, không compile. |
| `scripts/validate-account-configs.ps1` | **MỚI** — JSON valid, key match EA input, account ↔ terminal. |
| `scripts/filter-by-vps.ps1` | **MỚI** — workflow helper output strategies/terminals của VPS hiện tại. |
| `.github/workflows/deploy.yml` | `detect-changes` thêm `strategies_config` + `vps_matrix`; job `validate-configs`; `compile-and-deploy` matrix theo VPS; `notify` aggregate. |

## A. Junction config — rẻ nhất, không cần copy

Thay vì copy file JSON xuống `MQL5/Files/{EA}/config/` mỗi lần CI, dùng **NTFS directory junction** trỏ từ MT5 data dir về repo:

```powershell
$repoCfgDir = Join-Path $RepoRoot "strategies\$name\config\accounts"
$eaCfgDir   = "$terminalDataRoot\MQL5\Files\$dataSubfolder\config"

if (Test-Path $repoCfgDir) {
   if (-not (Test-Path $eaCfgDir)) {
      cmd /c mklink /J "$eaCfgDir" "$repoCfgDir" | Out-Null
      Write-Host "[$name -> $termName] Config junction created: $eaCfgDir -> $repoCfgDir"
   } else {
      # Verify đúng target (nếu là junction sai → recreate)
      $existing = (Get-Item $eaCfgDir).Target
      if ($existing -ne $repoCfgDir) {
         Write-Host "[$name -> $termName] Junction target mismatch, recreating"
         cmd /c rmdir "$eaCfgDir"
         cmd /c mklink /J "$eaCfgDir" "$repoCfgDir" | Out-Null
      }
   }
}
```

Lợi ích:

- **Atomic**: `git pull` xong, EA đọc thẳng file mới ngay khi restart — không có window "file đã copy nhưng EA chưa restart".
- **Idempotent**: chạy nhiều lần ok, chỉ tạo lần đầu.
- **Không tốn space**: junction là NTFS metadata.

Logic này được **share** giữa `deploy-ea.ps1` và `deploy-account-configs.ps1` qua `_lib.ps1::Ensure-ConfigJunction`.

## B. `scripts/_lib.ps1` — module chung

```powershell
# scripts/_lib.ps1

function Get-LocalTerminals {
    param(
        [Parameter(Mandatory)][PSCustomObject]$Deploy,
        [string]$Vps = $env:GH_RUNNER_VPS
    )
    $result = [ordered]@{}
    foreach ($prop in $Deploy.terminals.PSObject.Properties) {
        $term    = $prop.Value
        $termVps = if ($term.vps) { $term.vps } else { 'vps-sg' }
        if (-not $Vps -or $termVps -eq $Vps) {
            $result[$prop.Name] = $term
        }
    }
    return $result
}

function Ensure-ConfigJunction {
    param(
        [Parameter(Mandatory)][string]$RepoCfgDir,
        [Parameter(Mandatory)][string]$EaCfgDir
    )
    if (-not (Test-Path $RepoCfgDir)) {
        Write-Host "[Junction] Skip — repo config dir not found: $RepoCfgDir"
        return $false
    }
    if (Test-Path $EaCfgDir) {
        $existing = (Get-Item $EaCfgDir).Target
        if ($existing -eq $RepoCfgDir) { return $true }
        Write-Host "[Junction] Target mismatch — recreating: $EaCfgDir"
        cmd /c rmdir "$EaCfgDir" | Out-Null
    } else {
        # Đảm bảo parent dir tồn tại
        $parent = Split-Path $EaCfgDir -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    }
    cmd /c mklink /J "$EaCfgDir" "$RepoCfgDir" | Out-Null
    Write-Host "[Junction] Created: $EaCfgDir -> $RepoCfgDir"
    return $true
}

function Restart-Mt5Terminal {
    param(
        [Parameter(Mandatory)][string]$TerminalName,
        [Parameter(Mandatory)][string]$InstallDir
    )
    # Code chuyển từ block restart cuối deploy-ea.ps1
    # Kill terminal64.exe của instance, start lại từ InstallDir
    ...
}
```

`deploy-ea.ps1`, `deploy-account-configs.ps1`, `setup-agent.ps1` đầu file:

```powershell
. "$PSScriptRoot\_lib.ps1"
```

## C. `scripts/deploy-ea.ps1` — sửa đổi

Vị trí thay đổi: ngay đầu vòng lặp terminal, sau khi load `$deploy`:

```powershell
param([string]$Vps = $env:GH_RUNNER_VPS, [string]$Strategies = '[]')

. "$PSScriptRoot\_lib.ps1"
$deploy = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json
$locals = Get-LocalTerminals -Deploy $deploy -Vps $Vps

foreach ($stratName in $stratsToDeploy) {
    $strat = $deploy.strategies.$stratName
    foreach ($termName in $strat.deploy_to) {
        if (-not $locals.ContainsKey($termName)) { continue }   # skip terminals khác VPS
        $terminal = $locals[$termName]

        # ... compile EA (như cũ)

        # === Junction data (như cũ) ===
        # Junction MQL5\Files\{EA} → strategies/{name}/data/  (đã có)

        # === Junction config (MỚI) ===
        $repoCfgDir = Join-Path $RepoRoot "strategies\$stratName\config\accounts"
        $eaCfgDir   = Join-Path $terminalDataRoot "MQL5\Files\$($strat.agent.data_subfolder)\config"
        Ensure-ConfigJunction -RepoCfgDir $repoCfgDir -EaCfgDir $eaCfgDir

        # === Restart terminal ===
        Restart-Mt5Terminal -TerminalName $termName -InstallDir $terminal.mt5_install_dir
    }
}
```

## D. `scripts/deploy-account-configs.ps1` — chỉ sync config + restart

Khi commit chỉ đụng file `strategies/{name}/config/accounts/*.json` (không đụng `.mq5`), không cần recompile — chỉ cần đảm bảo junction tồn tại và restart terminal để `OnInit` đọc lại.

```powershell
param(
    [Parameter(Mandatory)][string]$Strategies,
    [string]$Vps = $env:GH_RUNNER_VPS
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_lib.ps1"

$RepoRoot      = Split-Path -Parent $PSScriptRoot
$deploy        = Get-Content "$RepoRoot\deploy.json" -Raw | ConvertFrom-Json
$stratsArr     = $Strategies | ConvertFrom-Json
$locals        = Get-LocalTerminals -Deploy $deploy -Vps $Vps

foreach ($stratName in $stratsArr) {
    $strat = $deploy.strategies.$stratName
    if (-not $strat) { Write-Warning "Strategy $stratName not found"; continue }

    foreach ($termName in $strat.deploy_to) {
        if (-not $locals.ContainsKey($termName)) { continue }
        $terminal = $locals[$termName]

        $terminalDataRoot = "C:\Users\$($terminal.user_profile)\AppData\Roaming\MetaQuotes\Terminal\$($terminal.hash)"
        $repoCfgDir       = Join-Path $RepoRoot "strategies\$stratName\config\accounts"
        $eaCfgDir         = Join-Path $terminalDataRoot "MQL5\Files\$($strat.agent.data_subfolder)\config"

        Ensure-ConfigJunction -RepoCfgDir $repoCfgDir -EaCfgDir $eaCfgDir
        Restart-Mt5Terminal  -TerminalName $termName -InstallDir $terminal.mt5_install_dir
    }
}
```

## E. `scripts/validate-account-configs.ps1` — gate CI

Chạy trên `ubuntu-latest` (PowerShell Core ok, không cần Windows). Logic:

```powershell
param([string]$RepoRoot = (Get-Location).Path)
$ErrorActionPreference = 'Stop'

$deploy = Get-Content "$RepoRoot/deploy.json" -Raw | ConvertFrom-Json
$errors = @()

# Rule 1: uniqueness — mỗi account chỉ trong 1 terminal
$accountToTerminal = @{}
foreach ($prop in $deploy.terminals.PSObject.Properties) {
    $term = $prop.Value
    if ($null -eq $term.accounts) { continue }
    foreach ($acc in $term.accounts) {
        if ($accountToTerminal.ContainsKey($acc)) {
            $errors += "Account $acc declared in both $($accountToTerminal[$acc]) and $($prop.Name)"
        } else {
            $accountToTerminal[$acc] = $prop.Name
        }
    }
}

# Rule 2-5: per-file validation
$configFiles = Get-ChildItem -Path "$RepoRoot/strategies" -Recurse -Filter "*.json" |
               Where-Object { $_.FullName -match "config[\\/]accounts[\\/]" }

foreach ($f in $configFiles) {
    $stratName = ($f.FullName -split '[\\/]')[-4]   # strategies / {strat} / config / accounts / {acc}.json
    $fileName  = $f.BaseName

    # Filename là số?
    if ($fileName -notmatch '^\d+$') {
        $errors += "$($f.FullName): filename must be numeric account ID"
        continue
    }
    $acc = [long]$fileName

    # JSON parse?
    try {
        $cfg = Get-Content $f.FullName -Raw | ConvertFrom-Json
    } catch {
        $errors += "$($f.FullName): invalid JSON — $($_.Exception.Message)"
        continue
    }

    # schema_version
    if ($cfg.meta.schema_version -ne 1) {
        $errors += "$($f.FullName): meta.schema_version must be 1, got '$($cfg.meta.schema_version)'"
    }

    # meta vs inputs key disjointness
    $metaKeys   = if ($cfg.meta)   { $cfg.meta.PSObject.Properties.Name }   else { @() }
    $inputKeys  = if ($cfg.inputs) { $cfg.inputs.PSObject.Properties.Name } else { @() }
    $overlap    = $metaKeys | Where-Object { $inputKeys -contains $_ }
    if ($overlap) {
        $errors += "$($f.FullName): keys appear in both meta and inputs: $($overlap -join ', ')"
    }

    # Inputs match EA
    $eaFile = "$RepoRoot/strategies/$stratName/ea/" + (Get-ChildItem "$RepoRoot/strategies/$stratName/ea/" -Filter "*.mq5" | Select-Object -First 1 -ExpandProperty Name)
    if (Test-Path $eaFile) {
        $eaContent = Get-Content $eaFile -Raw
        $inputNames = [regex]::Matches($eaContent, '^input\s+\S+\s+(\w+)\s*=', 'Multiline') | ForEach-Object { $_.Groups[1].Value }
        foreach ($k in $inputKeys) {
            if ($inputNames -notcontains $k) {
                $errors += "$($f.FullName): inputs.$k does not match any 'input' declaration in $eaFile"
            }
        }
    } else {
        $errors += "$($f.FullName): EA source not found at $eaFile"
    }

    # Account in eligible terminal
    $strat = $deploy.strategies.$stratName
    if (-not $strat) { $errors += "$($f.FullName): strategy $stratName not in deploy.json"; continue }
    $eligibleAccs = @()
    foreach ($t in $strat.deploy_to) {
        $term = $deploy.terminals.$t
        if ($term.accounts) { $eligibleAccs += $term.accounts }
    }
    if ($eligibleAccs -notcontains $acc) {
        $errors += "$($f.FullName): account $acc not in any terminal of strategy '$stratName' (eligible: $($eligibleAccs -join ', '))"
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Host "ERROR: $_" }
    exit 1
}
Write-Host "All $($configFiles.Count) account config files valid."
```

## F. Change detection trong `.github/workflows/deploy.yml`

`detect-changes` job tính 3 outputs:

```pseudo
files_changed = paths-filter or git diff HEAD^ HEAD
strategies_ea     = []     # strategies có .mq5 / agent / ea/ thay đổi
strategies_config = []     # strategies có config/accounts/ thay đổi
vps_matrix        = set()  # VPS labels của terminals thuộc strategies thay đổi

for file in files_changed:
    if file matches "strategies/(.+)/ea/.*\.mq5":           strategies_ea.add($1)
    if file matches "strategies/(.+)/agent/":               strategies_ea.add($1)    # restart agent cũng cần
    if file matches "strategies/(.+)/config/accounts/":     strategies_config.add($1)
    if file == "deploy.json" or file matches "scripts/":    # toàn bộ — full redeploy
        strategies_ea = all_strategies
        strategies_config = all_strategies
        vps_matrix = all_vps_in_deploy_json
        break

# Tính vps_matrix
for strat in (strategies_ea ∪ strategies_config):
    for term in deploy.strategies[strat].deploy_to:
        vps_matrix.add(deploy.terminals[term].vps or "vps-sg")
```

Push 3 outputs này ra `$GITHUB_OUTPUT`.

## G. `notify` job

```yaml
notify:
  needs: compile-and-deploy
  if: always()
  runs-on: ubuntu-latest
  steps:
    - name: Build summary
      id: summary
      run: |
        # GitHub injects matrix job outcomes via needs.compile-and-deploy.result (overall)
        # Để có per-matrix detail, dùng workflow API hoặc gh CLI:
        #   gh run view ${{ github.run_id }} --json jobs -q '.jobs[] | select(.name | startswith("compile-and-deploy")) | {name, conclusion}'
        # Sau đó format thành: "✅ vps-sg / ❌ vps-tk (compile-and-deploy failed)"
    - name: Send Telegram
      env:
        BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
        CHAT_ID:   ${{ secrets.TELEGRAM_OPS_CHAT_ID }}
      run: |
        curl -s "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
          -d chat_id=$CHAT_ID \
          -d text="${{ steps.summary.outputs.text }}"
```

## H. Backward-compat trong rollout

Trong PR đầu (chỉ scaffolding + 1 VPS hiện có):

- `deploy.json` thêm `"vps": "vps-sg"` + `"accounts": [...]` cho 3 terminal hiện có.
- Cài runner cũ thêm label `vps-sg` (nếu chưa).
- Workflow vẫn chạy bình thường — matrix sẽ chỉ có 1 entry `vps-sg`.

Khi thêm VPS thứ 2, không cần đụng workflow YAML — chỉ thêm terminal entry với `vps` mới và cài runner có label đó.
