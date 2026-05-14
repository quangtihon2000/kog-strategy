# 06 — Rollout plan & verification

## Order of implementation

Triển khai theo blast-radius tăng dần. Mỗi step có thể merge độc lập (PR riêng) hoặc gộp 2-3 step nếu small.

### Step 1 — Stats service (rủi ro thấp nhất, deploy độc lập)

**Tại sao trước**: không đụng EA, không đụng trading. Deploy fail cũng không ảnh hưởng money.

Tasks:

1. Alembic migration `services/strategy-stats/alembic/versions/0002_account_dimension.py` — composite index trên `(account, signal_ts)` cho `conde_outcomes` + `gvfx_outcomes`.
2. `services/strategy-stats/app/stats/conde.py` — `aggregate_by_account` (+ dataclasses).
3. `services/strategy-stats/app/stats/gvfx.py` — `aggregate_by_account` (+ dataclasses).
4. Routes `/conde/account/{account}` + `/gvfx/account/{account}` trong `app/web/routers/conde.py` + `gvfx.py`.
5. Templates `conde_account.html` + `gvfx_account.html` clone từ `zone_account.html`.
6. Macro chung trong `_macros.html` để 3 template share UI.
7. Card "Top accounts" trên overview pages.

Deploy: tự động qua `strategy-stats-deploy.yml` khi merge main.

Verify: xem [05-stats-service.md §G](./05-stats-service.md#g-verification).

### Step 2 — EA changes per strategy (theo blast-radius tăng dần)

**Order**: `zone_signal` → `conde_auto_entry` → `gvfx_signal`.

Zone có 1 account, blast radius nhỏ nhất → áp dụng pattern, validate, rồi áp cho Conde (channel-based, nhiều signal hơn) và Gvfx (grid DCA, complex hơn).

Per-strategy task:

1. Khai báo `g_cfg_*` shadow globals (1 per `input`).
2. Implement `InitShadowsFromInputs()`, `LoadAccountConfig()`, JSON helpers (`JsonGetBool/Double/Long`).
3. Wire vào `OnInit`: gọi 2 hàm trên trước `g_trade.SetExpertMagicNumber(g_cfg_Magic)`.
4. Gate entry trong `OnTick`: `if (!g_cfg_Enabled) { ManagePositions(); return; }`.
5. Rename `Inp*` → `g_cfg_*` trong mọi function ngoài `InitShadowsFromInputs` (dùng Edit `replace_all=true` per variable).
6. Compile sandbox local (MetaEditor) → no errors.
7. Tạo `strategies/{name}/config/accounts/.gitkeep` (thư mục).

CI grep gate đảm bảo không sót ref `Inp*`.

### Step 3 — deploy.json schema + setup-agent backward-compat

1. Thêm `"vps": "vps-sg"` + `"accounts": [<acc>]` cho 3 terminal hiện có trong `deploy.json`.
2. Cập nhật `scripts/setup-agent.ps1` để đọc `accounts` từ deploy.json (fallback env var `MT5_ACCOUNTS` khi field thiếu — backward-compat).
3. Cài label `vps-sg` cho self-hosted runner hiện tại (nếu chưa).
4. Set machine env var `GH_RUNNER_VPS=vps-sg` trên VPS hiện có.

Verify: chạy workflow `redeploy-agent.yml` manual → agent .env có `MT5_ACCOUNTS=<acc>` đúng như cũ.

### Step 4 — CD pipeline

1. Tạo `scripts/_lib.ps1` với `Get-LocalTerminals`, `Ensure-ConfigJunction`, `Restart-Mt5Terminal`.
2. Refactor `scripts/deploy-ea.ps1`:
   - Nhận tham số `-Vps`.
   - Dùng `Get-LocalTerminals` để filter.
   - Gọi `Ensure-ConfigJunction` sau block junction data.
   - Gọi `Restart-Mt5Terminal` từ lib (extract block restart cuối file).
3. Tạo `scripts/validate-account-configs.ps1` (chi tiết ở [04-cd-pipeline.md §E](./04-cd-pipeline.md#e-scriptsvalidate-account-configsps1--gate-ci)).
4. Tạo `scripts/deploy-account-configs.ps1` ([§D](./04-cd-pipeline.md#d-scriptsdeploy-account-configsps1--chỉ-sync-config--restart)).
5. Update `.github/workflows/deploy.yml`:
   - `detect-changes` emit `strategies_ea`, `strategies_config`, `vps_matrix`.
   - Job `validate-configs` (ubuntu-latest).
   - `compile-and-deploy` chuyển matrix theo VPS, `runs-on: [self-hosted, "${{ matrix.vps }}"]`.
   - `notify` aggregate per-VPS result qua `gh run view --json jobs`.

Test workflow trên feature branch với `workflow_dispatch force_all=true`. Chạy 1 runner trước (vps-sg), sau đó cài runner thứ 2 (vps-tk) để verify matrix fan-out.

### Step 5 — Tạo config file đầu tiên (smoke test end-to-end)

1. Tạo `strategies/zone_signal/config/accounts/<acc>.json` với 1-2 override không quan trọng (vd `InpMaxSpreadPts`).
2. Push → validate-configs pass → deploy-account-configs sync junction + restart terminal.
3. Trên MT5 Experts tab verify log `[Config] Loaded acc=<acc> ...`.
4. Trade live (hoặc demo) thử 1 lệnh → confirm magic / lot khớp config.

### Step 6 — Docs cập nhật

1. Root `README.md` — thêm section "Per-account configuration" + link tới `docs/per-account-config/`.
2. Per-strategy `CLAUDE.md` (nếu có) — note rằng inputs có thể override per-account.
3. `services/strategy-stats/README.md` — note routes per-account mới.
4. Root README section "Adding a new VPS" — cài runner + label + env var.

## Critical files (đầy đủ)

### MQL5

- `strategies/zone_signal/ea/ZoneSignalEA.mq5` — input lines 13–31, helper line 971/997, OnInit 73–91.
- `strategies/conde_auto_entry/ea/CondeAutoEntryEA.mq5` — input lines 12–38.
- `strategies/gvfx_signal/ea/GvfxSignalEA.mq5` — input lines 12–26.

### Config

- `strategies/{zone_signal,conde_auto_entry,gvfx_signal}/config/accounts/.gitkeep` (mới — placeholder).

### Deploy

- `deploy.json` — thêm `vps` + `accounts` per terminal.
- `scripts/_lib.ps1` (mới).
- `scripts/deploy-ea.ps1` — refactor.
- `scripts/setup-agent.ps1` — đọc accounts từ deploy.json + filter VPS.
- `scripts/deploy-account-configs.ps1` (mới).
- `scripts/validate-account-configs.ps1` (mới).
- `.github/workflows/deploy.yml` — 3 outputs + 1 job mới + matrix.

### Stats

- `services/strategy-stats/app/models.py` — không sửa schema.
- `services/strategy-stats/app/stats/conde.py` — `aggregate_by_account`.
- `services/strategy-stats/app/stats/gvfx.py` — `aggregate_by_account`.
- `services/strategy-stats/app/web/routers/conde.py` + `gvfx.py` — route `/account/{account}`.
- `services/strategy-stats/app/web/templates/conde_account.html` + `gvfx_account.html` (mới).
- `services/strategy-stats/app/web/templates/_macros.html` — macro chung.
- `services/strategy-stats/alembic/versions/0002_account_dimension.py` (mới).

## End-to-end verification

### 1. Local compile

```bash
cd strategies/zone_signal/ea
# Mở MetaEditor sandbox → Compile → no errors
# Verify trong code không còn ref Inp* ngoài InitShadowsFromInputs:
grep -nE '\bInp(Lot|Max|Magic|Min|Trail|Be|Scalp|Retrace)' ZoneSignalEA.mq5 | grep -v InitShadowsFromInputs
# (phải trả 0 dòng)
```

### 2. CI gate

```bash
# Push feature branch
git push origin claude/account-specific-config-EkVym

# Quan sát Actions UI:
# - validate-configs: pass (mọi file JSON valid, key match EA inputs, account ↔ terminal correct)
# - compile-and-deploy: ma trận chạy đúng 1 job vps-sg, EA compile success
# - notify: gửi Telegram "✅ vps-sg"
```

### 3. Junction trên VPS

Trên VPS `vps-sg`, PowerShell:

```powershell
Get-Item "C:\Users\QuangXAU\AppData\Roaming\MetaQuotes\Terminal\7D70CC401B91FAC031C1DD6731E80E7A\MQL5\Files\ZoneSignalEA\config"
# Mode: ld---  Target should be: C:\<repo>\strategies\zone_signal\config\accounts
```

### 4. EA runtime log

Mở MT5 → tab Experts:

```
[Config] Loaded acc=5100000 label=QuangXAU prop demo magic=20240417 lot=0.02 maxpos=8 enabled=true
```

Nếu `meta.enabled=false`:

```
[Config] Loaded acc=5100000 label=... enabled=false
[Config] DISABLED — manage existing positions only, no new entries
```

### 5. Stats per-account

```
GET https://stats.example.com/conde/account/5100123?since=7d   → render bucket per-channel
GET https://stats.example.com/gvfx/account/5200001?since=24h   → render bucket per-(symbol, mode)
GET https://stats.example.com/conde/                            → card "Top accounts"
```

### 6. Negative tests

| Scenario | Expected |
|---|---|
| Corrupt JSON (missing brace) | `validate-configs` fail CI — không deploy. |
| Tạo file `strategies/zone_signal/config/accounts/9999999.json` (account không tồn tại) | `validate-configs` fail: "account 9999999 not in any terminal of strategy zone_signal". |
| Đổi `InpMagic` khi account đang có lệnh mở | EA restart với magic mới → lệnh cũ orphan (không quản lý). Validator warn (không block) khi diff `InpMagic`. **Operator phải đóng lệnh trước.** |
| Set `meta.enabled=false` | EA log `[Config] DISABLED`; signal mới đến không vào lệnh; lệnh cũ vẫn trail/BE/close TP/SL bình thường. |
| Xoá file config khi đã deploy | `git pull` → file biến mất → terminal restart → EA `OnInit` không tìm thấy → dùng defaults compile. Log: "No override at ... — defaults". |
| VPS `vps-tk` offline khi push | Matrix job `vps-tk` pending; `vps-sg` vẫn deploy success; `notify` báo per-VPS status. Manual re-run job khi VPS online. |

## Risks & mitigations (toàn cảnh)

| Risk | Mitigation |
|---|---|
| JSON corrupt giữa lúc EA đọc | EA `OnInit` chạy 1 lần, junction chỉ đổi khi git push + terminal restart sequence. Không có window race trong steady state. |
| Sót ref `Inp*` sau rename | CI grep gate fail PR. |
| Đổi magic khi có lệnh open | Doc operator: đóng hết lệnh trước; validator warn khi diff `InpMagic`. |
| Key trùng giữa `meta` và `inputs` (parser flat) | Validator enforce disjointness. |
| Validator reject forward-compat human change | Validator chỉ error key lạ trong `inputs.*`; `meta.*` free-form (trừ `schema_version`). |
| 1 VPS offline khi push | `fail-fast: false`; manual re-run khi VPS online; Telegram per-VPS alert. |
| Account đăng nhập sai (vd login dev account thay vì prod) | EA đọc `ACCOUNT_LOGIN` thực tế, file `<wrong-acc>.json` không tồn tại → defaults. Mất ngày nhưng không catastrophic. Có thể thêm log warn-level. |
| Junction NTFS không hỗ trợ (vd file system khác) | Pre-flight check `Test-Path` + try-catch; fallback copy nếu cần (chưa làm v1). |

## Rollback plan

Nếu sau deploy phát hiện EA có bug:

1. Revert commit EA trong git, push.
2. CD pipeline tự compile + redeploy EA cũ.
3. Terminal restart → EA cũ load (không đọc config file mới — vì code cũ không có `LoadAccountConfig`).
4. Config files trong `strategies/*/config/accounts/` vẫn còn (không xoá), chờ deploy fix.

Hoặc emergency disable:

1. Set `meta.enabled=false` cho mọi account.
2. Push → sync junction + restart.
3. EA chuyển sang manage-only mode, không vào lệnh mới.
4. Investigate + fix → re-enable.

Cả 2 path đều an toàn vì EA backward-compat (thiếu file → defaults compile).
