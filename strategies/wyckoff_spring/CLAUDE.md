# CLAUDE.md — Wyckoff Spring Strategy

## Overview

Wyckoff Spring/Upthrust reversal strategy on XAUUSD H1. Detects sideways consolidation ranges, then trades the failed breakout (spring below support or upthrust above resistance) back into the range.

## Components

- **EA**: `ea/WyckoffSpringEA.mq5` — Standalone EA, no Python agent needed
- No agent, no data directory — all logic is self-contained in the EA

## EA Logic

### Range Detection
- Scan bars 2..N+1 on H1 (skip bar 1 = candidate, bar 0 = forming)
- Range = High/Low of those bars
- **Respect check**: at least `InpRangeRespectPct` (70%) of bar closes must stay inside an inner band (range ± 10% buffer)
- Filtered by `InpMinRangeUSD` / `InpMaxRangeUSD`

### Spring (Bullish)
A Spring occurs when:
1. Bar 1 low breaks below range low (wick penetration)
2. Bar 1 closes back above range low (rejection)
3. Bar 1 is bullish (close > open)
4. Wick depth: `InpMinWickUSD` ≤ depth ≤ `InpMaxWickUSD`
5. Body ratio ≥ `InpMinBodyRatio` (rejects dojis)

→ **BUY** at market, SL = wick low − buffer, TP = range high − buffer

### Upthrust (Bearish)
Mirror of Spring:
1. Bar 1 high breaks above range high
2. Bar 1 closes back below range high
3. Bar 1 is bearish (close < open)

→ **SELL** at market, SL = wick high + buffer, TP = range low + buffer

### Filters
- RR ≥ 1.0 (TP distance must be ≥ SL distance)
- Cooldown: `InpCooldownBars` H1 bars between trades
- Max `InpMaxTradesPerDay` trades per day
- No entry if already holding a position

## Key Parameters

| Parameter | Default | Description |
|---|---|---|
| `InpRangeBars` | 18 | H1 bars to scan for range |
| `InpMinRangeUSD` | 4.0 | Min range size ($) |
| `InpMaxRangeUSD` | 12.0 | Max range size ($) |
| `InpRangeRespectPct` | 0.70 | Min fraction of closes inside range |
| `InpMinWickUSD` | 0.50 | Min wick penetration ($) |
| `InpMaxWickUSD` | 4.0 | Max wick depth ($) |
| `InpRiskPercent` | 0.5 | % balance risked per trade |
| `InpCooldownBars` | 6 | Min H1 bars between trades |

## Important Notes

- Only processes on new H1 bar close (not every tick)
- One position at a time — waits for current trade to close before taking another
- Body ratio filter rejects doji/spinning top candles that look like springs but lack conviction
