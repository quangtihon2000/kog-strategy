# ZoneSignalEA — Logic Summary

## 1. Signal Validation (`LoadSignal`)

A JSON signal file must pass **all** of these checks before the EA accepts it:

| # | Check | Reject if |
|---|-------|-----------|
| 1 | File readable | Cannot open signal file |
| 2 | `timestamp` present | Missing or empty |
| 3 | `redbox_upper` present | Missing or empty |
| 4 | `redbox_lower` present | Missing or empty |
| 5 | `targets_above[]` | Missing or empty array |
| 6 | `targets_below[]` | Missing or empty array |
| 7 | Timestamp not in future | `ts > now` |
| 8 | Signal not expired | Age > 86400 seconds (24h) |
| 9 | New timestamp | Same `timestamp` as last applied signal → skip (not an error) |

> [!NOTE]
> Checks 7–8 only apply to **new** signals (different timestamp than the last one).

---

## 2. Conditions to Open a Position

### 2.1 Breakout Entry (one per direction per signal)

Triggered on each new M15 bar. **All** conditions must be true:

| # | Condition | Description |
|---|-----------|-------------|
| 1 | `g_sig.valid == true` | Signal must be active |
| 2 | `close[1] > redbox_upper` (BUY) or `close[1] < redbox_lower` (SELL) | M15 bar closed outside the zone |
| 3 | `!g_breakoutBuy` / `!g_breakoutSell` | Breakout not already taken for this direction |
| 4 | `!g_buyDone` / `!g_sellDone` | Direction not locked (no TP/SL hit yet) |
| 5 | Price not already at/past T1 | `ASK < targets_above[0]` (BUY) or `BID > targets_below[0]` (SELL) |

### 2.2 Per-Target Filters (inside `OpenTrades` loop)

Each individual target must also pass:

| # | Filter | Skips if |
|---|--------|----------|
| 1 | Max positions cap | `opened >= InpMaxPositions` (per direction) → **break** |
| 2 | TP above/below entry | BUY: `tp <= entry` / SELL: `tp >= entry` → **skip** |
| 3 | Min TP distance | `\|tp - entry\| < InpMinTpPts` points → **skip** |
| 4 | Max lot size | Lot clamped to `min(InpLotPerTarget, InpMaxLots)` |

### 2.3 Mid-Zone Reentry (one extra position per direction)

Triggered when price pulls back into the redbox after a breakout:

| # | Condition | Description |
|---|-----------|-------------|
| 1 | `close[1]` inside redbox | `redbox_lower <= close[1] <= redbox_upper` |
| 2 | Breakout already taken | `g_breakoutBuy == true` or `g_breakoutSell == true` |
| 3 | Mid-entry not done | `!g_midEntryBuyDone` / `!g_midEntrySellDone` |
| 4 | Direction not locked | `!g_buyDone` / `!g_sellDone` |
| 5 | Max positions cap | `CountOpenPositions(dir) < InpMaxPositions` |
| 6 | Min TP distance | Same 300-point filter as breakout targets |

> [!IMPORTANT]
> Mid-zone entry uses the **last target's TP** (furthest target) and the **same SL** as breakout trades.

---

## 3. Direction Lock Rules

Once **any** position in a direction hits TP or SL:

```
g_buyDone = true   →  No more BUY breakout, no BUY mid-zone entry
g_sellDone = true  →  No more SELL breakout, no SELL mid-zone entry
```

Existing positions (e.g. T2/T3 at break-even) continue running until their SL/TP.

All flags reset only when a **new signal timestamp** arrives.

---

## 4. Break-Even Rule

When **Target 1** hits **TP** → all remaining positions in that direction have their SL moved to their **entry price** (break even).

---

## Input Parameters Summary

| Parameter | Default | Description |
|---|---|---|
| `InpLotPerTarget` | 0.01 | Lot size per position |
| `InpMaxLots` | 0.10 | Max lot size cap |
| `InpMaxPositions` | 10 | Max open positions per direction |
| `InpMinTpPts` | 300 | Min TP distance in points |
| `InpSlBufferPts` | 50 | Extra SL buffer beyond zone edge |
| `InpMagic` | 20240416 | Magic number |
| `InpUseCommonDir` | true | Use MT5 common Files folder |
