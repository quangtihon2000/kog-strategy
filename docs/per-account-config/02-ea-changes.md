# 02 — EA code changes (MQL5)

## Vấn đề kỹ thuật

Biến `input` trong MQL5 **immutable sau `OnInit`** — không gán lại được. Mọi reference `InpLotPerTarget`, `InpMagic`... trong các function `OpenPosition`, `ManagePositions`, `Trail`... đang dùng trực tiếp biến `input`.

→ Không thể "ghi đè" `input` từ JSON.

## Giải pháp: shadow globals

Tạo **shadow global** cho mỗi `input`, cùng kiểu, prefix `g_cfg_`. Trong `OnInit`:

1. `InitShadowsFromInputs()` — copy giá trị từ `Inp*` sang `g_cfg_*`.
2. `LoadAccountConfig()` — đọc JSON nếu có, overlay lên `g_cfg_*`.
3. Mọi code path dùng `g_cfg_*`, **không bao giờ** dùng `Inp*` ngoài `InitShadowsFromInputs`.

## File touched

| File | Input lines | Helper có sẵn |
|---|---|---|
| `strategies/zone_signal/ea/ZoneSignalEA.mq5` | 13–31 | `ReadFileToString` (line 971), `JsonGetString` (line 997) |
| `strategies/conde_auto_entry/ea/CondeAutoEntryEA.mq5` | 12–38 | tương tự |
| `strategies/gvfx_signal/ea/GvfxSignalEA.mq5` | 12–26 | tương tự |

## Skeleton (áp dụng cho mỗi EA, ví dụ Zone)

### 1. Shadow globals — khai báo ngay sau block `input`

```mq5
// === Shadow globals: init từ inputs trong OnInit, overlay bởi JSON config ===
double g_cfg_LotPerTarget;
double g_cfg_MaxLots;
int    g_cfg_MaxPositions;
ulong  g_cfg_Magic;
int    g_cfg_MaxSpreadPts;
int    g_cfg_TrailStartPts;
int    g_cfg_TrailStepPts;
int    g_cfg_BreakevenAtPts;
bool   g_cfg_Enabled       = true;
string g_cfg_AccountLabel  = "";
// ... một biến cho mỗi input
```

### 2. JSON helpers bổ sung (cùng style với `JsonGetString`)

Trong `JsonGetString` đã có sẵn. Thêm:

```mq5
bool JsonGetBool(const string json, const string key, bool defv) {
   string raw = JsonGetString(json, key);
   if (raw == "") return defv;
   StringToLower(raw);
   if (raw == "true") return true;
   if (raw == "false") return false;
   return defv;
}

double JsonGetDouble(const string json, const string key, double defv) {
   string raw = JsonGetString(json, key);
   if (raw == "") return defv;
   return StringToDouble(raw);
}

long JsonGetLong(const string json, const string key, long defv) {
   string raw = JsonGetString(json, key);
   if (raw == "") return defv;
   return StringToInteger(raw);
}
```

Note: `JsonGetString` đã handle quoted string. `JsonGetDouble`/`Long` đọc numeric (unquoted) — cần verify behavior với raw value không có quotes; nếu chưa, mở rộng `JsonGetString` để trả về numeric token cũng được.

### 3. `InitShadowsFromInputs()`

```mq5
void InitShadowsFromInputs() {
   g_cfg_LotPerTarget   = InpLotPerTarget;
   g_cfg_MaxLots        = InpMaxLots;
   g_cfg_MaxPositions   = InpMaxPositions;
   g_cfg_Magic          = InpMagic;
   g_cfg_MaxSpreadPts   = InpMaxSpreadPts;
   g_cfg_TrailStartPts  = InpTrailStartPts;
   g_cfg_TrailStepPts   = InpTrailStepPts;
   g_cfg_BreakevenAtPts = InpBreakevenAtPts;
   // ... full list — mỗi input đúng 1 dòng
}
```

### 4. `LoadAccountConfig()`

```mq5
bool LoadAccountConfig() {
   long   account = AccountInfoInteger(ACCOUNT_LOGIN);
   string path    = "ZoneSignalEA\\config\\" + IntegerToString(account) + ".json";
   string json    = ReadFileToString(path);
   if (json == "") {
      PrintFormat("[Config] No override at %s — defaults", path);
      return false;
   }

   // Overlay từng key — defv là giá trị shadow hiện tại (đã init từ Inp*)
   g_cfg_LotPerTarget   = JsonGetDouble(json, "InpLotPerTarget",   g_cfg_LotPerTarget);
   g_cfg_MaxLots        = JsonGetDouble(json, "InpMaxLots",        g_cfg_MaxLots);
   g_cfg_MaxPositions   = (int)JsonGetLong(json, "InpMaxPositions", g_cfg_MaxPositions);
   g_cfg_Magic          = (ulong)JsonGetLong(json, "InpMagic",     (long)g_cfg_Magic);
   g_cfg_MaxSpreadPts   = (int)JsonGetLong(json, "InpMaxSpreadPts", g_cfg_MaxSpreadPts);
   g_cfg_TrailStartPts  = (int)JsonGetLong(json, "InpTrailStartPts", g_cfg_TrailStartPts);
   g_cfg_TrailStepPts   = (int)JsonGetLong(json, "InpTrailStepPts", g_cfg_TrailStepPts);
   g_cfg_BreakevenAtPts = (int)JsonGetLong(json, "InpBreakevenAtPts", g_cfg_BreakevenAtPts);

   g_cfg_Enabled        = JsonGetBool(json,   "enabled", true);
   g_cfg_AccountLabel   = JsonGetString(json, "label");

   PrintFormat("[Config] Loaded acc=%I64d label=%s magic=%I64u lot=%.2f maxpos=%d enabled=%s",
               account, g_cfg_AccountLabel, g_cfg_Magic, g_cfg_LotPerTarget,
               g_cfg_MaxPositions, g_cfg_Enabled ? "true" : "false");
   return true;
}
```

### 5. `OnInit` — wire it up

Thay block khởi tạo hiện tại (Zone EA: line 73–91):

```mq5
int OnInit() {
   InitShadowsFromInputs();
   LoadAccountConfig();

   g_trade.SetExpertMagicNumber(g_cfg_Magic);

   if (!g_cfg_Enabled) {
      Print("[Config] DISABLED — manage existing positions only, no new entries");
   }

   // ... phần còn lại của OnInit như cũ (init objects, indicators...)
   return INIT_SUCCEEDED;
}
```

### 6. `OnTick` — gate entry khi disabled

Ngay sau throttle 1Hz:

```mq5
void OnTick() {
   if (TimeCurrent() == g_last_tick_sec) return;
   g_last_tick_sec = TimeCurrent();

   if (!g_cfg_Enabled) {
      ManagePositions();   // vẫn trail, BE, close TP/SL
      return;              // không vào lệnh mới
   }
   // ... phần còn lại như cũ
}
```

### 7. Rename `Inp*` → `g_cfg_*` ở mọi code path

Bằng `Edit replace_all=true` cho từng input. **Trừ** trong `InitShadowsFromInputs` (đó là điểm duy nhất cần đọc `Inp*`).

CI grep gate (chạy trong job `validate-configs`):

```bash
# Tìm tham chiếu Inp* ngoài InitShadowsFromInputs → fail
awk '
/void InitShadowsFromInputs/{skip=1; next}
/^}/{if(skip){skip=0; next}}
!skip && /\bInp(Lot|Max|Magic|Min|Trail|Be|Scalp|Retrace)/ {
  print FILENAME ":" NR ": " $0; found=1
}
END{exit found}
' strategies/*/ea/*.mq5
```

## Edge cases

| Tình huống | Hành vi |
|---|---|
| File không tồn tại | Log info, dùng defaults. EA chạy bình thường. |
| File rỗng | `ReadFileToString` → "" → coi như không có file. |
| File corrupt (sai JSON) | `JsonGet*` không tìm thấy key → từng key fallback default. EA vẫn start. Log warning per-key fail nếu detect được. |
| Account ID không khớp | EA đọc `ACCOUNT_LOGIN` thực tế (vd `5100000`), tìm file `5100000.json`. Nếu account đăng nhập khác account ID trong filename → đơn giản là không tìm thấy file → defaults. |
| Magic đổi khi đang có lệnh | **Operator phải đóng hết lệnh trước.** EA load magic mới → lệnh cũ bị orphan (không thuộc magic mới). Validator sẽ warn (không block) khi diff `InpMagic` so với deploy gần nhất. |

## Tại sao không hot-reload v1

- MT5 không có file-watcher; phải poll `ReadFileToString` mỗi tick → tốn IO.
- Đổi magic giữa chừng dễ orphan position.
- Đổi `MaxPositions` xuống giữa chừng có thể tạo trạng thái mâu thuẫn (số position hiện tại > giới hạn mới).
- Restart terminal sau deploy là idiom đã có sẵn trong `deploy-ea.ps1` — reuse được.

Khi cần hot-reload thật sự (vd disable nhanh), bổ sung sau v1 với scope hẹp (chỉ reload `enabled` flag, không đụng magic/lot).

## Compile sanity

Trước khi push, compile từng EA local (hoặc qua MetaEditor sandbox trên VPS) — đảm bảo:

- Tất cả `g_cfg_*` được declare.
- Không còn ref `Inp*` ngoài `InitShadowsFromInputs`.
- `LoadAccountConfig` types khớp (`int`, `ulong`, `double`, `bool`).

CI sẽ catch nếu sót, nhưng compile local nhanh hơn nhiều so với round-trip qua self-hosted runner.
