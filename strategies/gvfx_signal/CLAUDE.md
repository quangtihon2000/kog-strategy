# CLAUDE.md — GVFX Signal Strategy

## Overview

Grid DCA strategy: từ một "target price" với hướng (BUY/SELL), EA liên tục mở lệnh market miễn là (1) chưa chạm target và (2) trong phạm vi ±`step` points quanh giá hiện tại chưa có vị thế nào của EA. Tối đa 20 vị thế đồng thời, mỗi lệnh có TP cố định và hard SL 10000 points. Khi giá chạm target → signal inactive (positions đang mở vẫn chạy theo TP/SL, không vào thêm). Daily P&L cut hiện đang **disabled** — sẽ define lại sau (helpers `RefreshDailyAnchor` / `ComputeRealizedSince` / `CloseAllAndCancel` vẫn giữ trong code làm scaffolding).

## Components

- **EA**: `ea/GvfxSignalEA.mq5` — Market grid DCA với spread guard (daily cut tạm disabled)
- **Agent**: `agent/` — Consume Redis Stream `gvfx_signals`, ghi `{account}_{symbol}.json`
- **Data**: `data/` — Runtime symlink → `MQL5/Files/GvfxSignalEA/`

## EA Logic

### Entry rules
- **Gating**: vào market mỗi tick khi tất cả các điều kiện sau thỏa:
  1. `g_signalActive` (chưa chạm target).
  2. `open_count < InpMaxPositions` (cap 20).
  3. `current_spread_pts ≤ InpMaxSpreadPts`.
  4. `HasOpenWithinStep(entry_price, step*_Point) == false` — không có vị thế EA nào đang mở với `|open_price − entry_price| < step*_Point`.
- **Entry price**: BUY → `SymbolInfoDouble(_Symbol, SYMBOL_ASK)`; SELL → `SymbolInfoDouble(_Symbol, SYMBOL_BID)`.
- **No price guard against target**: lệnh được phép mở ngay sát target — chỉ cần signal vẫn active.
- **Re-entry sau TP/SL**: khi 1 position TP/SL → slot trống. Nếu giá hiện tại không nằm trong ±`step` của bất kỳ vị thế nào còn lại → re-entry ngay tick kế tiếp (kể cả tại level cũ).
- **Spread guard**: chỉ vào lệnh khi `current_spread_pts ≤ InpMaxSpreadPts` (default 30 pts) — nếu vượt thì skip tick đó.

### Per-order risk
- Lot = `InpLotPerOrder` (default 0.01), normalize theo `SYMBOL_VOLUME_STEP`.
- TP = entry ± `signal.tp * _Point` (clamped via broker stops level).
- Hard SL = entry ∓ `InpMaxLossPtsPerOrder * _Point` (default 10000 pts, clamped).

### Signal lifecycle
- New `timestamp` → `g_signalActive = true`, cache `g_currentSig`.
- Target reached → `g_signalActive = false`. Positions đang mở vẫn chạy theo TP/SL, không vào thêm.
  - BUY: `bid ≥ target` → inactive
  - SELL: `ask ≤ target` → inactive

### Daily cut — **DISABLED**
Logic cắt all hiện đang bỏ trong `OnTick`. `RefreshDailyAnchor` + `ComputeRealizedSince` vẫn chạy mỗi tick (g_dailyRealized luôn fresh), `CloseAllAndCancel` còn nguyên — chỉ thiếu trigger condition. Sẽ define lại theo rule mới khi cần.

### Dedup (restart-safe)
- Position comment: `GVFX_T{timestamp}` (e.g., `GVFX_T1777896356`).
- `ScanMaxSeenTimestamp()` scan POSITIONS + ORDERS + DEAL HISTORY (lookback `InpHistoryLookbackDays` ngày), parse `GVFX_T{ts}` từ comment, lấy max → `g_lastSigTs` recovered on restart.
- Không cần reconstruct grid anchor: `HasOpenWithinStep` đọc trực tiếp từ vị thế đang mở mỗi tick → state-less, restart-safe by construction.

## Agent Signal Format

```json
{
  "timestamp": 1777896356,
  "symbol": "XAUUSD",
  "target": 4860.0,
  "direction": "BUY",
  "step": 500,
  "tp": 500
}
```

- `step`, `tp`: integer, đơn vị **MT5 points** (1pt = `_Point`; với XAUUSD 2-digit → 500 pts = 5.00 price).
- `timestamp` **NOT re-stamped** — producer-supplied, preserved end-to-end. Đây là dedup identity nhúng vào position comment.
- File path: `data/{account}_{symbol}.json` (e.g., `data/5100000_XAUUSD.json`).
- EA reads from `MQL5/Files/GvfxSignalEA/{account}_{symbol}.json`.

## Agent Config (`.env`)

```
MT5_SIGNAL_DIR=../data
MT5_ACCOUNTS=5100000
MT5_SYMBOLS=XAUUSD
REDIS_URL=redis://localhost:6379
REDIS_STREAM=gvfx_signals
REDIS_GROUP=gvfx_writer
REDIS_CONSUMER=gvfx-agent-1
LOG_LEVEL=INFO
```

## Important Notes

- Tất cả unit `step` / `tp` / `InpMaxLossPtsPerOrder` theo **MT5 points**, không phải pip / price.
- `ClampStop()` enforce broker's minimum stop distance (`SYMBOL_TRADE_STOPS_LEVEL`).
- Daily anchor = server time midnight (không dùng broker timezone hay local time) — vẫn được track dù cut logic đang disabled.
- Mỗi lệnh đặt cố định lot size từ `InpLotPerOrder`; không có lot scaling theo level grid.
- Re-entry rule là **state-less**: chỉ phụ thuộc vào set vị thế đang mở của magic+symbol — không cache "last entry price". Slot trống do TP/SL **được phép re-entry tại level cũ** ngay tick kế tiếp nếu ±step radius hiện tại rỗng.
