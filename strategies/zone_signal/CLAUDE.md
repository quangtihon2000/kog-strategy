# CLAUDE.md — Zone Signal Strategy

## Overview

Three-tier entry system on XAUUSD M15 zone breakouts. A Python agent writes zone signals from Redis to JSON files; the EA reads them and executes trades.

## Components

- **EA**: `ea/ZoneSignalEA.mq5` — M15 breakout detection + scalp/normal/mid-zone entries
- **Agent**: `agent/` — Consumes Redis Stream `zone_signals`, writes `{account}.json`
- **Data**: `data/` — Runtime directory where agent writes, EA reads via symlink

## EA Logic (Three-Tier Entry)

| Tier | Trigger | Description |
|---|---|---|
| **Scalp** | Tick-based, after breakout | Re-enterable up to `InpMaxScalpPerDir` times per direction. TP frees slot, SL consumes permanently |
| **Normal** | Tick-based, retrace to zone | Opens one position per target when price retraces to within `InpRetracePts` of redbox edge |
| **Mid-zone** | M15 bar close inside zone | Opens one extra position at last target's TP |

### Signal Lifecycle
```
New timestamp → breakout detected (M15 close) → scalp/normal/mid entries
→ T1 reached → move to break-even → direction DONE
→ Both directions done → signal deactivated
```

### Key Rules
- **R1**: T1 TP hit marks direction as DONE and moves all same-direction positions to break-even
- **R4/R6**: SL does NOT end the direction. If all positions drain without T1 TP, re-entry flags re-arm
- **R12**: Scalp positions excluded from trailing stop management
- **R13**: Signal deactivated only when BOTH directions are done

## Agent Signal Format

```json
{
  "timestamp": 1713259200,
  "symbol": "XAUUSD",
  "redbox_upper": 2350.0,
  "redbox_lower": 2340.0,
  "targets_above": [2360.0, 2370.0],
  "targets_below": [2330.0, 2320.0]
}
```

- `timestamp` is **NOT re-stamped** — producer-supplied (unix epoch seconds), preserved end-to-end
- This is critical because `timestamp` is embedded in each position's comment
  (`ZB_T{n}_{ts}`, `ZS_T{n}_{ts}`, scalp/mid variants) and strategy-stats joins
  `ZoneOutcome.signal_ts ↔ ZoneSignal.signal_ts` on that same value
- File path: `data/{account}.json` (e.g., `data/5100000.json`)
- EA reads from `MQL5/Files/ZoneSignalEA/{account}.json` via symlink

## Agent Config (`.env`)

```
MT5_SIGNAL_DIR=../data
MT5_ACCOUNTS=5100000,5100001
REDIS_URL=redis://localhost:6379
REDIS_STREAM=zone_signals
REDIS_GROUP=ea_writer
```

## Important Notes

- EA polls the JSON file every 1 second (throttled by `TimeCurrent()`)
- Breakout detection uses M15 bar close, but entries are tick-based for speed
- Scalp spacing: minimum `InpScalpSpacingPts` between consecutive scalp entries
- Trailing stop: configurable start, distance, and step thresholds
