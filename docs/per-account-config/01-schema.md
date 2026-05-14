# 01 — Schema file per-account config

## Đường dẫn

```
strategies/{strategy_name}/config/accounts/{account}.json
```

- `{strategy_name}`: `zone_signal`, `conde_auto_entry`, `gvfx_signal`.
- `{account}`: MT5 account login number, dạng integer (vd `5100000`).
- Một file = một account override của một chiến lược.
- Thiếu file → EA dùng default đã compile vào (zero-risk default).

## Ví dụ

```json
{
  "meta": {
    "label": "QuangXAU prop demo",
    "owner": "quang",
    "enabled": true,
    "schema_version": 1
  },
  "inputs": {
    "InpLotPerTarget": 0.02,
    "InpMaxLots": 0.20,
    "InpMaxPositions": 8,
    "InpMagic": 20240417,
    "InpMaxSpreadPts": 50
  }
}
```

## Các block

### `meta`

| Key | Type | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `label` | string | không | Human-readable label, log ra Experts tab khi `OnInit`. |
| `owner` | string | không | Người chịu trách nhiệm chính (audit). |
| `enabled` | bool | không (default `true`) | `false` → EA chỉ quản lệnh đang mở, không vào lệnh mới. |
| `schema_version` | int | có | Hiện tại `1`. Validator check exact match. Khi đổi schema lớn, bump version + migration. |

### `inputs`

Key trong `inputs.*` khớp **1:1** với tên biến `input` trong file `.mq5` tương ứng. Ví dụ:

- `inputs.InpLotPerTarget` ↔ `input double InpLotPerTarget = 0.01;` trong `ZoneSignalEA.mq5`.
- `inputs.InpMagic` ↔ `input ulong InpMagic = 20240417;`.

Quy tắc parse:

- Key **thiếu** trong JSON → EA giữ giá trị default đã compile (additive override, không reset).
- Key **lạ** (không khớp biến `input` nào) → EA log warning, ignore (forward-compat khi rename hoặc xóa input).
- Sai type (vd `"InpMagic": "abc"`) → EA log warning, giữ default.

### Top-level

Không cho phép key khác `meta` và `inputs` (validator reject). Lý do: tránh nhầm lẫn về nơi đặt một giá trị nào đó.

## Parser MQL5 — quy ước nesting

MQL5 không có thư viện JSON; helper `JsonGetString` / `JsonGetDouble` / `JsonGetBool` / `JsonGetLong` đều dùng `StringFind` tìm `"key"` ở bất kỳ đâu trong file (flat). Nesting `meta` / `inputs` là **quy ước cho người đọc và validator**, không phải MQL5 parser.

**Hệ quả**: không được đặt cùng tên key ở 2 block khác nhau, vd:

```json
{ "meta": { "label": "A" }, "inputs": { "label": "B" } }   // SAI
```

Validator CI enforce: keyset `meta.*` và `inputs.*` phải disjoint.

## Validation rules (CI)

`scripts/validate-account-configs.ps1` (hoặc `.py`) check:

1. **JSON valid** — parseable, không trailing comma.
2. **Filename** — `{account}.json` với `{account}` là số nguyên dương.
3. **schema_version** — phải bằng `1`.
4. **Keys disjoint** — không có key xuất hiện ở cả `meta` và `inputs`.
5. **Inputs match EA** — mọi key trong `inputs.*` phải tồn tại làm biến `input` trong file `.mq5` của strategy đó (regex `^input\s+\S+\s+(\w+)\s*=`).
6. **Account ∈ terminal** — `{account}` phải thuộc một terminal có `vps` chứa strategy này (cross-check với `deploy.json`).
7. **Uniqueness across terminals** — một account không được khai báo ở nhiều terminal (mỗi account chỉ chạy ở 1 chỗ).

Vi phạm bất kỳ rule nào → **fail CI** trước khi compile/deploy.

## Forward-compat policy

- Thêm input mới vào EA: backward-compat — config cũ thiếu key → EA dùng default compile. Không cần migration.
- Xóa input khỏi EA: key cũ trong JSON sẽ trigger warning (key lạ), không fail. Khuyến nghị: xóa key khỏi config trong cùng PR xóa input.
- Đổi tên input: bump `schema_version` + migration script chuyển key cũ → mới trong mọi `accounts/*.json`.

## Quan hệ với chart template

Chart `.tpl` hiện tại set inputs cho EA. **Sau khi feature này live**:

- Chart template chỉ dùng để set timeframe + chỉ báo visual (không liên quan EA inputs).
- Inputs trong template **bị override hoàn toàn** bởi JSON nếu file tồn tại.
- Nếu không có file (vd account mới chưa khai báo) → EA dùng default compile (không phải value từ template).

## Lifecycle

```
[Edit JSON] → [git commit + push]
   → [CI validate]
   → [Sync junction xuống VPS đúng]
   → [Restart MT5 terminal]
   → [EA OnInit đọc JSON, log "Loaded acc=... label=..."]
```

Không hot-reload runtime — restart terminal là cách duy nhất pick up thay đổi (v1).
