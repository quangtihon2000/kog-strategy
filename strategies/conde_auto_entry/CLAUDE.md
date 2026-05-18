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
- Each position gets comment `CAE_T{n}_{timestamp}_{mode}` (e.g., `CAE_T1_1713259200_ATR`)
- `mode` ∈ `ORG` (signal TP1 or ATR fallback) / `ATR` (ATR-based) / `FIX` (fixed pts) — lets you distinguish TP source from broker history
- Dedup uses **prefix match** (`CAE_T{n}_{ts}_`) so mode swap between runs doesn't re-fire
- `ParseTsFromComment()` ignores the trailing `_{mode}` (`StringToInteger` stops at `_`)
- On restart, `ScanMaxSeenTimestamp()` scans open positions + history to find last executed signal
- Never re-fires a signal that was already executed

### Trade Management
1. **Break-even**: Profit >= `InpBeTriggerPts` → SL to entry + `InpBeOffsetPts`
2. **Trailing**: Profit >= `InpTrailStartPts` → SL trails `InpTrailDistPts` behind price
3. **TP1 invalidation**: If market reaches TP1 before pendings fill → cancel all pending orders for that signal

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
