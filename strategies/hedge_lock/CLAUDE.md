# CLAUDE.md — Hedge Lock Strategy

## Overview

A hedging strategy that opens a Buy+Sell pair on XAUUSD, with the Sell placed above the Buy via a limit order. When the combined floating profit exceeds a threshold, both positions are closed and a new pair is opened. Requires a **hedging-enabled** MT5 account.

## Components

- **EA**: `ea/HedgeLockEA.mq5` — Standalone EA, no Python agent needed
- No agent, no data directory — all logic is self-contained in the EA

## EA Logic

### Pair Lifecycle
```
Open BUY (market) + SELL LIMIT (above buy fill) → wait for both to fill
→ monitor combined floating P&L
→ floating > InpMinProfit → close both + open new pair
→ repeat
```

### State Machine
| State | Positions | Pendings | Action |
|---|---|---|---|
| Nothing open | 0 | 0 | Open new pair |
| Waiting for sell fill | 1 | 1 | Normal — wait |
| Both filled | 2 | 0 | Monitor floating, recycle on profit |
| Buy filled, sell missing | 1 | 0 | Restore sell limit |
| Orphan pending | 0 | 1+ | Delete and retry |

### Distance Calculation
```
distance (pts) = max(spread × InpSpreadMult + InpBufferPts,
                     broker_stop_level + InpBufferPts)
```
Sell limit is placed at `buy_fill_price + distance` to ensure the spread is covered.

## Key Parameters

| Parameter | Default | Description |
|---|---|---|
| `InpLots` | 0.01 | Lot size (same for both legs) |
| `InpSpreadMult` | 1.5 | Distance = spread × mult + buffer |
| `InpBufferPts` | 10 | Extra points safety cushion |
| `InpMinProfit` | 0.0 | Min combined floating to close pair |

## Important Notes

- **Requires hedging account** (`ACCOUNT_MARGIN_MODE_RETAIL_HEDGING`) — EA aborts on netting accounts
- Floating P&L includes swap
- No SL/TP on individual positions — profit is managed by the pair as a whole
- Throttled to 1Hz (once per second)
- If the sell limit gets cancelled (broker rejection, etc.), the EA automatically restores it
