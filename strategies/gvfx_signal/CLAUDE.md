# CLAUDE.md — GVFX Signal Strategy

## Overview

Grid DCA strategy: từ một "target price" với hướng (BUY/SELL), EA mở lệnh market đầu tiên rồi DCA về phía bất lợi mỗi khi giá chạy thêm `step` points ngược chiều mong đợi. Tối đa 20 vị thế đồng thời, mỗi lệnh có TP cố định và hard SL 10000 points. Khi giá chạm target → signal inactive (positions đang mở vẫn chạy theo TP/SL, không vào thêm). Có daily P&L cut: nếu `realized_today − floating > InpDailyCutUsd` thì đóng all + cancel pendings, không reset realized counter, EA tiếp tục đặt grid cho signal hiện tại (re-arm immediately).

## Components

- **EA**: `ea/GvfxSignalEA.mq5` — Market grid DCA với daily P&L cut và spread guard
- **Agent**: `agent/` — Consume Redis Stream `gvfx_signals`, ghi `{account}_{symbol}.json`
- **Data**: `data/` — Runtime symlink → `MQL5/Files/GvfxSignalEA/`

## EA Logic

### Entry rules
- **Lệnh đầu** (`open_count == 0`): vào market khi `|target − market| ≥ step` đúng hướng (price guard).
  - BUY: `ask ≤ target − step*_Point`
  - SELL: `bid ≥ target + step*_Point`
- **Lệnh tiếp** (`open_count ≥ 1`): vào market khi giá chạy thêm `step` ngược hướng so với last entry.
  - BUY: `ask ≤ last_entry − step*_Point`
  - SELL: `bid ≥ last_entry + step*_Point`
- **Cap**: 20 OPEN positions max (`InpMaxPositions`). Khi 1 position TP/SL → slot trống, nhưng grid level chỉ extend xuống thêm khi giá chạy thêm `step` so với last entry — không re-place tại level cũ.
- **Spread guard**: chỉ vào lệnh khi `current_spread_pts ≤ InpMaxSpreadPts` (default 30 pts). Áp dụng cho cả lệnh đầu lẫn các lệnh kế tiếp — nếu spread vượt ngưỡng thì skip tick đó, đợi tick sau.

### Per-order risk
- Lot = `InpLotPerOrder` (default 0.01), normalize theo `SYMBOL_VOLUME_STEP`.
- TP = entry ± `signal.tp * _Point` (clamped via broker stops level).
- Hard SL = entry ∓ `InpMaxLossPtsPerOrder * _Point` (default 10000 pts, clamped).

### Signal lifecycle
- New `timestamp` → reset grid anchor (`g_lastEntryPrice = 0`), `g_signalActive = true`.
- Target reached → `g_signalActive = false`. Positions đang mở vẫn chạy theo TP/SL, không vào thêm.
  - BUY: `bid ≥ target` → inactive
  - SELL: `ask ≤ target` → inactive

### Daily cut
- Daily anchor = midnight server time (`TimeTradeServer()`).
- Mỗi tick recompute `g_dailyRealized` từ `HistorySelect(g_dailyAnchor, now)` filter magic + symbol (self-healing sau crash).
- Nếu `g_dailyRealized − floating > InpDailyCutUsd` → `CloseAllAndCancel()`:
  - Đóng all positions + cancel pendings của magic + symbol
  - **KHÔNG reset** `g_dailyRealized` → daily counter có thể trigger lại trong cùng ngày
  - **KHÔNG disable** signal → EA re-arm grid ngay khi điều kiện entry tiếp theo thỏa
  - Reset `g_lastEntryPrice = 0`

### Dedup (restart-safe)
- Position comment: `GVFX_T{timestamp}` (e.g., `GVFX_T1777896356`).
- `ScanMaxSeenTimestamp()` scan POSITIONS + ORDERS + DEAL HISTORY (lookback `InpHistoryLookbackDays` ngày), parse `GVFX_T{ts}` từ comment, lấy max → `g_lastSigTs` recovered on restart.
- Reconstruct `g_lastEntryPrice` từ vị thế bất lợi nhất (max BUY entry hoặc min SELL entry) cùng magic + symbol.

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
- Daily anchor = server time midnight (không dùng broker timezone hay local time).
- Daily cut **không reset** realized counter — có thể trigger nhiều lần trong cùng ngày.
- Mỗi lệnh đặt cố định lot size từ `InpLotPerOrder`; không có lot scaling theo level grid.
- Slot trống do TP/SL **không** trigger re-entry tại level cũ — grid chỉ extend khi giá chạy thêm `step` so với last entry hiện tại.
