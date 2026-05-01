# CLAUDE.md — Asia Range Breakout Strategy

## Overview

Scalp XAUUSD at London open by trading the breakout of the Asia session range. One trade per day maximum, with optional retest entry after the initial breakout fills.

## Components

- **EA**: `ea/AsiaRangeBreakoutEA.mq5` — Standalone EA, no Python agent needed
- No agent, no data directory — all logic is self-contained in the EA

## EA Logic

### Daily Phases
| Phase | Trigger | Action |
|---|---|---|
| 0 (idle) | New day | Wait for session start |
| 0 → 1 | `InpSessionStartHour` | Compute Asia range, place BuyStop/SellStop |
| 1 → 2 | Breakout fills | Cancel opposite pending, place retest limit |
| 2 → 3 | Retest fills | Done for the day |
| any → 3 | `InpSessionEndHour` | Cancel unfilled pendings, done |

### Asia Range Calculation
- Range = High/Low of last `InpRangeHours` closed H1 bars before session start
- Filtered by `InpMinRangeUSD` and `InpMaxRangeUSD` (skip day if range too tight or too wide)
- SL = mid-point of range (half-range risk)
- TP = entry ± (range × `InpTPratio`)

### Risk Management
- Lot size = `InpRiskPercent` of account balance / stop distance
- Direction toggles: `InpEnableLong` and `InpEnableShort` (short is off by default — gold has long bias)

### Retest Entry
- After breakout fills, places a limit order at the range edge (pullback entry)
- Same SL (mid-range) and TP calculation
- Offset configurable via `InpRetestOffsetUSD`

## Key Parameters

| Parameter | Default | Description |
|---|---|---|
| `InpSessionStartHour` | 10 | Broker hour to place pendings (≈ London open) |
| `InpSessionEndHour` | 16 | Broker hour to cancel unfilled pendings |
| `InpRangeHours` | 14 | H1 bars lookback = Asia session |
| `InpRiskPercent` | 0.5 | % balance risked per trade |
| `InpTPratio` | 0.75 | TP distance = range × this |
| `InpEnableRetest` | true | Place limit at range edge after breakout |

## Important Notes

- Uses broker time (default assumes GMT+3 broker)
- All pending orders use `ORDER_TIME_DAY` expiry (auto-cancel end of day)
- One trade per direction per day maximum
