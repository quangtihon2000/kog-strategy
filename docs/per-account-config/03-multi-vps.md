# 03 — Multi-VPS deployment

## Bối cảnh

Hiện tại pipeline `.github/workflows/deploy.yml` dùng `runs-on: self-hosted` **generic** — implicit assumption "chỉ có 1 VPS". `deploy.json` cũng không có field nào báo terminal đó nằm trên VPS nào.

Khi đội mở rộng sang nhiều VPS (vd Singapore, Tokyo, Frankfurt), pipeline phải:

1. Biết terminal nào ở VPS nào.
2. Gửi job deploy đến **đúng** VPS đó (không phải mọi VPS).
3. Cho phép các VPS deploy **song song**.
4. Khi 1 VPS lỗi/offline, các VPS khác vẫn deploy được (không block toàn bộ pipeline).

Per-account config (feature chính của PR này) phải đến đúng VPS chứa terminal/account đó — không spam mọi VPS với config không thuộc về nó.

## Thiết kế

### A. Runner setup

Mỗi VPS chạy 1 GitHub Actions self-hosted runner với **label** riêng (ngoài label mặc định `self-hosted`):

| VPS | Labels |
|---|---|
| Singapore | `self-hosted, vps-sg` |
| Tokyo | `self-hosted, vps-tk` |
| Frankfurt | `self-hosted, vps-de` |

Cài label khi register runner:

```powershell
.\config.cmd --url https://github.com/<owner>/<repo> --token <REG_TOKEN> --labels vps-sg --unattended
```

Mỗi VPS export machine-level environment variable `GH_RUNNER_VPS` = label của nó (dùng cho PowerShell script biết mình đang ở đâu, fallback khi không nhận tham số):

```powershell
[Environment]::SetEnvironmentVariable("GH_RUNNER_VPS", "vps-sg", "Machine")
```

### B. Mở rộng `deploy.json`

Thêm field `vps` per terminal, và **move `accounts` từ env var sang đây** (single source of truth — đồng thời enable cross-check validator).

```json
{
  "terminals": {
    "mt5_8": {
      "vps": "vps-sg",
      "hash": "7D70CC401B91FAC031C1DD6731E80E7A",
      "label": "MetaTrader5_8 — ZoneSignal",
      "mt5_install_dir": "C:\\Program Files\\MetaTrader5_8",
      "user_profile": "QuangXAU",
      "accounts": [5100000]
    },
    "mt5_main": {
      "vps": "vps-sg",
      "hash": "D0E8209F77C8CF37AD8BF550E51FF075",
      "label": "MetaTrader 5 — CondeAutoEntry",
      "mt5_install_dir": "C:\\Program Files\\MetaTrader 5",
      "user_profile": "QuangXAU",
      "accounts": [5100123]
    },
    "mt5_tk_1": {
      "vps": "vps-tk",
      "hash": "AB12CD34EF56...",
      "label": "MetaTrader5 Tokyo — Gvfx",
      "mt5_install_dir": "C:\\Program Files\\MetaTrader5",
      "user_profile": "QuangTK",
      "accounts": [5200001, 5200002]
    }
  },
  "strategies": {
    "zone_signal": {
      "ea_source": "strategies/zone_signal/ea/ZoneSignalEA.mq5",
      "deploy_to": ["mt5_8"],
      "agent": { ... }
    }
  }
}
```

Backward-compat: nếu `vps` thiếu → default `vps-sg` (giả định cluster đầu tiên). `accounts` thiếu → fallback đọc env var `MT5_ACCOUNTS` như cũ.

### C. Workflow matrix theo VPS

Trong `.github/workflows/deploy.yml`:

```yaml
detect-changes:
  runs-on: ubuntu-latest
  outputs:
    strategies_ea:     ${{ steps.detect.outputs.strategies_ea }}
    strategies_config: ${{ steps.detect.outputs.strategies_config }}
    vps_matrix:        ${{ steps.detect.outputs.vps_matrix }}
  steps:
    - uses: actions/checkout@v4
    - id: detect
      run: |
        # Tính strategies_ea, strategies_config (như đề xuất ở 04-cd-pipeline.md)
        # vps_matrix = unique VPS labels của tất cả terminals thuộc deploy_to[] của strategies thay đổi
        # Nếu deploy.json đổi → emit toàn bộ VPS trong terminals.* (full redeploy)

validate-configs:
  needs: detect-changes
  if: needs.detect-changes.outputs.strategies_config != '[]'
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - run: pwsh ./scripts/validate-account-configs.ps1

compile-and-deploy:
  needs: [detect-changes, validate-configs]
  if: needs.detect-changes.outputs.vps_matrix != '[]'
  strategy:
    fail-fast: false
    matrix:
      vps: ${{ fromJSON(needs.detect-changes.outputs.vps_matrix) }}
  runs-on: [self-hosted, "${{ matrix.vps }}"]
  steps:
    - uses: actions/checkout@v4

    - name: Compile EA changes
      if: needs.detect-changes.outputs.strategies_ea != '[]'
      shell: powershell
      run: .\scripts\deploy-ea.ps1 -Vps '${{ matrix.vps }}' -Strategies '${{ needs.detect-changes.outputs.strategies_ea }}'

    - name: Sync per-account configs
      if: needs.detect-changes.outputs.strategies_config != '[]'
      shell: powershell
      run: .\scripts\deploy-account-configs.ps1 -Vps '${{ matrix.vps }}' -Strategies '${{ needs.detect-changes.outputs.strategies_config }}'

    - name: Setup / restart agents
      shell: powershell
      run: .\scripts\setup-agent.ps1 -Vps '${{ matrix.vps }}'

notify:
  needs: compile-and-deploy
  if: always()
  runs-on: ubuntu-latest
  steps:
    - name: Aggregate per-VPS results and post to Telegram
      run: |
        # Đọc needs.compile-and-deploy.result và phân tích per-matrix-job
        # Telegram: "✅ vps-sg / ❌ vps-tk (deploy-ea step failed)"
```

Đặc điểm:

- `fail-fast: false` → 1 VPS lỗi không kéo các VPS khác.
- `validate-configs` chạy 1 lần trên ubuntu-latest (lint thuần, không cần MT5).
- Compile EA chỉ chạy nếu strategies_ea có thay đổi; sync config chỉ chạy nếu strategies_config có thay đổi → tiết kiệm thời gian khi chỉ tweak JSON.

### D. Script-side changes — filter theo VPS

Tất cả script PowerShell deploy nhận thêm `-Vps <label>` (string) và filter terminal trong vòng lặp:

```powershell
# scripts/deploy-ea.ps1 (đoạn loop terminals)
foreach ($termName in $terminalNames) {
   $terminal = $deploy.terminals.$termName
   if ($terminal.vps -and $Vps -and $terminal.vps -ne $Vps) {
      Write-Host "[Skip] $termName belongs to $($terminal.vps), current=$Vps"
      continue
   }
   # ... deploy block như cũ
}
```

Helper trong `scripts/_lib.ps1` (mới):

```powershell
function Get-LocalTerminals {
   param([Parameter(Mandatory)][PSCustomObject]$Deploy,
         [string]$Vps = $env:GH_RUNNER_VPS)
   $result = @{}
   foreach ($prop in $Deploy.terminals.PSObject.Properties) {
      $term = $prop.Value
      $termVps = if ($term.vps) { $term.vps } else { 'vps-sg' }   # default backward-compat
      if (-not $Vps -or $termVps -eq $Vps) {
         $result[$prop.Name] = $term
      }
   }
   return $result
}
```

Các script `deploy-ea.ps1`, `setup-agent.ps1`, `deploy-account-configs.ps1` import lib này:

```powershell
. "$PSScriptRoot\_lib.ps1"
$locals = Get-LocalTerminals -Deploy $deploy -Vps $Vps
```

### E. Validator cross-check (chạy trên ubuntu-latest)

`scripts/validate-account-configs.ps1` thực hiện rule **account ↔ terminal ↔ VPS**:

```pseudo
deploy = load deploy.json
all_account_to_terminal = {}   # acc_id -> [terminal_name]
for each terminal in deploy.terminals:
   for each acc in terminal.accounts:
      all_account_to_terminal[acc].append(terminal_name)

# Rule: mỗi account chỉ thuộc đúng 1 terminal
for acc, terms in all_account_to_terminal.items():
   if len(terms) > 1: FAIL "Account $acc declared in multiple terminals: $terms"

# Rule: mỗi file config phải có terminal tương ứng cho strategy đó
for file in glob "strategies/*/config/accounts/*.json":
   strat = parent dir name
   acc = filename without .json
   eligible_terminals = deploy.strategies[strat].deploy_to
   eligible_accounts = union(deploy.terminals[t].accounts for t in eligible_terminals)
   if acc not in eligible_accounts:
      FAIL "$file: account $acc not in any terminal of strategy $strat (eligible: $eligible_accounts)"
```

→ Bắt trường hợp tạo `5999999.json` cho `zone_signal` nhưng account 5999999 không tồn tại ở terminal nào của Zone.

### F. Stats service không đổi

`services/strategy-stats/` deploy trên 1 server **riêng** (không phải VPS MT5). Outcomes từ mọi VPS đều push lên cùng Redis stream → ingest consumer aggregate cross-VPS tự nhiên. Per-account stats hoạt động đúng vì account ID là duy nhất toàn cầu (MT5 login number).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 1 VPS offline khi push → terminal đó miss update | `fail-fast: false`; manual re-run job khi VPS online lại; alert qua Telegram (`notify` job hiển thị status per-VPS). |
| `git pull` chậm/timeout trên VPS xa | `actions/checkout@v4` default shallow `fetch-depth: 1` đã ok; có thể tăng timeout per-step nếu cần. |
| Account vô tình tồn tại trên 2 terminal (vd copy paste) | Validator rule "uniqueness across terminals" — fail CI ngay. |
| Thêm VPS mới = phải đụng workflow YAML? | Không — chỉ cần thêm terminal có `"vps": "vps-new"` trong `deploy.json`. Matrix tự pick lên VPS label đó. (Cần ai đó cài runner với label tương ứng trước.) |
| Self-runner offline → matrix job pending mãi | GitHub Actions auto-fail sau ~24h. `notify` job dùng `if: always()` để vẫn alert. |
| `GH_RUNNER_VPS` env var khác với label thực tế | Workflow truyền `-Vps '${{ matrix.vps }}'` qua tham số (single source of truth); env var chỉ là fallback khi chạy manual. |

## Quy trình thêm VPS mới

1. Provision Windows VPS, cài MT5 (các terminal cần thiết).
2. Cài GitHub Actions self-hosted runner với label `vps-<region>`:
   ```powershell
   .\config.cmd --url https://github.com/<owner>/<repo> --token <REG_TOKEN> --labels vps-<region> --unattended
   .\svc install
   .\svc start
   ```
3. Set machine env var `GH_RUNNER_VPS=vps-<region>`.
4. Thêm terminal entry trong `deploy.json` với `"vps": "vps-<region>"` và `"accounts": [...]`.
5. Push → matrix sẽ schedule job vào runner mới.
6. (Optional) Tạo file `strategies/{strat}/config/accounts/{acc}.json` cho account mới.

Toàn bộ thay đổi commit qua git, không có thao tác cấu hình bằng tay sau lần cài runner đầu tiên.
