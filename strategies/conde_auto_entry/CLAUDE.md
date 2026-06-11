# CLAUDE.md — Conde Auto Entry Strategy

## Overview

Reads pre-computed trade signals (entry, SL, TPs) from a JSON file and executes them automatically — either as market orders or pending orders depending on distance from entry price.

## Components

- **EA**: `ea/CondeAutoEntryEA.mq5` — Market/pending order execution with break-even + trailing stop
- **Agent**: `agent/` — Consumes Redis Stream `conde_signals`, writes `{account}_{symbol}.json`
- **Data**: `data/` — Runtime directory where agent writes, EA reads via symlink

## EA Logic

### TP policy
All positions opened from one signal target **TP1** (`sig.tps[0]`), regardless of slot index. The number of positions still equals `len(tps[])` — that controls position count + lot sizing — but every position exits together when price first touches TP1. TP2/TP3 in the JSON are effectively ignored as TP destinations.

### Order Mode Selection
```
distance = |market_price - entry_price|

distance <= InpMaxSlippagePts    → MARKET order
distance <= InpMaxPendingDistPts → PENDING order (limit/stop)
distance >  InpMaxPendingDistPts → SKIP (too far)
```

### Pending Order Types
| Direction | Entry vs Market | Order Type |
|---|---|---|
| BUY | entry < market | BUY_LIMIT (buy on pullback) |
| BUY | entry > market | BUY_STOP (buy on breakout) |
| SELL | entry > market | SELL_LIMIT (sell on rally) |
| SELL | entry < market | SELL_STOP (sell on breakdown) |

### Dedup (restart-safe)
- Each position gets comment `CAE_T{n}_{timestamp}_{mode}` (e.g., `CAE_T1_1713259200_ATRm3x1.0`)
- `mode` ∈ `ORG` (signal TP1 won the min-distance compare, or ATR fallback) / `ATR{tf}x{mult}` (ATR TP won the compare — e.g. `ATRm1x0.9`, `ATRm3x1.0`, encodes the ATR timeframe + multiplier so you can read the TP source straight from broker history) / `FIX{pips}` (fixed pts override — e.g. `FIX10pips`; pips = `InpFixedTpPts / 10`, since 1 pip = 10 points on XAUUSD)
- ATR tag built by `AtrModeTag()`: `EnumToString(tf)` minus `PERIOD_` prefix, lowercased, + `x%.1f` mult — kept short to stay under MT5's ~31-char comment limit
- When `InpUseAtrTp=true`: TP = min-distance(ATR_TP, signal TP1) from entry — picks whichever exits sooner
- Dedup uses **prefix match** (`CAE_T{n}_{ts}_`) so mode swap between runs doesn't re-fire
- `ParseTsFromComment()` ignores the trailing `_{mode}` (`StringToInteger` stops at `_`)
- On restart, `ScanMaxSeenTimestamp()` scans open positions + history to find last executed signal
- Never re-fires a signal that was already executed

### Trade Management
1. **Break-even**: Profit >= `InpBeTriggerPts` → SL to entry + `InpBeOffsetPts`
2. **Trailing**: Profit >= `InpTrailStartPts` → SL trails `InpTrailDistPts` behind price
3. **TP1 invalidation**: If market reaches TP1 before pendings fill → cancel all pending orders for that signal

### Rest time windows
- Two windows (HH:MM, **Vietnam time / GMT+7**) gate NEW entries: defaults `13:00-14:15` and `15:00-15:15`
- Computed as `TimeGMT() + 7h` so the window is independent of broker server TZ
- Inside a window: `OpenTrades` is skipped and `g_lastSigTs` is **not** advanced, so the same signal can still fire after the window closes (subject to the 24h freshness cap)
- Trailing/BE management, pending invalidation, and outcome capture still run during rest windows — only entries are blocked
- Inputs: `InpEnableRestTime`, `InpRestTime{1,2}Start`, `InpRestTime{1,2}End`. End is exclusive; empty string disables that window; wrap over midnight is supported.

### Account-config hot reload
- `OnTimer` polls the mtime of `MQL5/Files/CondeAutoEntryEA/config/<login>.json` every `InpConfigReloadSec` seconds (default 5; set 0 to disable)
- On change: re-runs `LoadAccountConfig()`, reapplies `magic`/slippage to `CTrade`, and rebuilds the ATR handle if `InpUseAtrTp`/`InpAtrTf`/`InpAtrPeriod` changed
- Removes the need to F7-recompile or remove+readd the EA after a config-only push; CI's `deploy-account-configs.ps1` drops the new JSON and the EA picks it up within `InpConfigReloadSec`
- Magic-number changes still orphan existing positions tagged with the old magic — the watcher logs a WARN but does not adopt orphans

## Agent Signal Format

```json
{
  "timestamp": 1745219000,
  "symbol": "XAUUSD",
  "direction": "BUY",
  "entry_price": 2350.00,
  "sl": 2340.00,
  "tps": [2355.0, 2360.0, 2365.0],
  "channel_id": -1001234567890,
  "channel_name": "Conder VIP"
}
```

- `timestamp` is **NOT re-stamped** — producer-supplied, preserved end-to-end
- This is critical because `timestamp` is embedded in each position's comment for dedup
- `channel_id` (Telegram BIGINT) + `channel_name` are **required** by `push_conde_signal` — strategy-stats ingest skips and ACKs any signal without `channel_id` to keep per-channel stats clean
- File path: `data/{account}_{symbol}.json` (e.g., `data/5100000_XAUUSD.json`)
- EA reads from `MQL5/Files/CondeAutoEntryEA/{account}_{symbol}.json`

## Agent Config (`.env`)

```
MT5_SIGNAL_DIR=../data
MT5_ACCOUNTS=5100000,5100001
MT5_SYMBOLS=XAUUSD,EURUSD
REDIS_URL=redis://localhost:6379
REDIS_STREAM=conde_signals
REDIS_GROUP=conde_writer
```

## Important Notes

- Signal expires after 24 hours (`now - timestamp > 86400` → rejected)
- Future timestamps are rejected
- Lot sizes capped per-position (`InpMaxLotsPerPosition`) and per-direction total (`InpMaxTotalLotsPerDir`)
- `ClampStop()` enforces broker's minimum stop distance (`SYMBOL_TRADE_STOPS_LEVEL`)
