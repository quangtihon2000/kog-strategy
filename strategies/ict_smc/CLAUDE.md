# CLAUDE.md — ICT/SMC Strategy

## Overview

ICT (Inner Circle Trader) / Smart-Money-Concepts market-structure EA for XAUUSD.

**Phase 1 (current): DETECTION + VISUALIZATION ONLY — the EA places no orders.**
It computes market structure on two timeframes and draws it on the chart (plus
`[IctSmc]` Print logs) so the logic can be eye-verified before any auto-trading
is added in Phase 2.

## Components

- **EA**: `ea/IctSmcEA.mq5` — self-contained; computes everything from price data
- No Python agent, no Redis, no signal files, no `data/` directory

## What it detects & draws

| Concept | Definition | Drawn as |
|---|---|---|
| **Swing high/low (đỉnh/đáy)** | Fractal pivot: bar's high/low strictly beyond `InpSwingLookback` (N) bars each side | ▼ / ▲ markers (HTF + LTF) |
| **HTF bias** | HTF (`InpHTF`) swing sequence: HH+HL = BULL, LH+LL = BEAR, else hold (sticky) | Corner label `ICT HTF H4 bias: …` |
| **BOS** (Break of Structure) | Closed bar breaks a swing **with** the prevailing trend (continuation) | Solid line + `BOS` text |
| **MSS** (Market Structure Shift / CHoCH) | Closed bar breaks a swing **against** the prevailing trend (reversal) | Dash-dot line + `MSS` text |
| **Fibonacci OTE** | After an LTF MSS: fib on the impulse leg — 0.5 (premium/discount) + OTE 0.62/0.705/0.79 | Goldenrod band + level lines |

## Multi-timeframe

- **HTF** (`InpHTF`, default H4) → directional bias.
- **LTF** (`InpLTF`, default M15) → MSS detection + OTE entry zone.
- Both timeframes are read via period-parameterized series calls
  (`iHigh/iLow/iClose/iTime/iBars(_Symbol, tf, shift)`), so the EA works on a
  chart of **any** period. Objects are keyed by absolute `time` so HTF structure
  renders correctly on an LTF chart.
- Work is throttled to once per **new bar** on each timeframe independently
  (compares `iTime(_Symbol, tf, 0)`). Classification uses the last **closed**
  bar (`iClose(tf, 1)`) to avoid repainting.

## Key Inputs

| Input | Default | Purpose |
|---|---|---|
| `InpHTF` / `InpLTF` | H4 / M15 | Bias / entry timeframes |
| `InpSwingLookback` | 3 | Fractal half-width N |
| `InpMaxHistoryBars` | 500 | Bars scanned per TF |
| `InpMaxSwingsTracked` | 60 | Swing ring-buffer cap per TF |
| `InpBiasSwingsForTrend` | 2 | HH/HL (or LH/LL) pairs to confirm bias |
| `InpDrawFib` / `InpDrawEquilibrium` | true | Toggle OTE fib / 0.5 line |
| `InpFibOTE1/2/3` | 0.62 / 0.705 / 0.79 | OTE band ratios |
| `InpShowHTFObjects` | true | Draw HTF swings/BOS/MSS too |
| `InpCol*` | — | Colors for swings/BOS/MSS/fib/bias |
| `InpMagic` | 20260627 | Reserved for Phase 2 orders |
| `InpVerboseLog` | true | `[IctSmc]` verbosity |

## Per-account config overlay

Same pattern as the other EAs: `Inp*` → `g_cfg_*` shadow globals → sparse-merge
JSON overlay via `LoadAccountConfig()` from
`MQL5/Files/IctSmcEA/config/<ACCOUNT_LOGIN>.json` (synced by
`deploy-account-configs.ps1`). Meta keys (`enabled`, `label`, `owner`) coexist
with `Inp*` keys. Enum/color inputs are overridden by their integer value
(e.g. `"InpHTF": 16388` = H4, `"InpLTF": 15` = M15). See `config/_template.json`.

## Drawing internals

- All chart objects use the prefix `IctSmc_`; full cleanup is one
  `ObjectsDeleteAll(0, "IctSmc_")` in `OnInit` and `OnDeinit`.
- Object names embed the bar `time` (absolute) as the trailing token.
- Swing markers are delete-and-redrawn each recompute; BOS/MSS lines persist per
  event and are time-pruned (`PruneOldObjects`); the fib set is cleared and
  redrawn on each new MSS (`ClearFibObjects`).

## Deployment

- `deploy.json` → `strategies.ict_smc` with `agent.enabled: false` (EA-only, no
  service). `deploy_to` is currently `[]` (placeholder) — set it to a terminal
  name (e.g. `mt5_2`) when you pick where to visualize, then push to `main`.
- Attach the compiled `IctSmcEA` to a separate XAUUSD chart in that terminal
  (any period). Watch the **Experts** tab for `[IctSmc] BOS/MSS …` logs.

## Phase 2 — Entry / SL / TP (laddered OTE entries)

When an LTF **MSS aligned with HTF bias** forms, the EA builds a `TradeSetup` and
**always draws** the entry/SL/TP levels on the chart. Whether it actually places
orders is gated by **`InpEnableTrading`** (default **false** = draw-only).

| Component | Rule |
|---|---|
| **Entry** | 3 laddered limit orders at OTE fibs `InpEntryFib1/2/3` (default 0.62 / 0.705 / 0.785) of the impulse leg |
| **SL** | Beyond the protected swing (leg origin that produced the MSS) + `InpSlBufferPts` |
| **TP** | Per-tier liquidity ladder (see Phase 4); fallback `InpFallbackRR` (×SL) when no liquidity |
| **Direction filter** | `InpRequireBiasAlign` — only trade MSS in the HTF-bias direction |

**Execution guards** (only when `InpEnableTrading=true && enabled`):
`IsSpreadOK` (`InpMaxSpreadPts`), `CountOpenPositions`/`SumOpenLots` caps
(`InpMaxSetupPositions`, `InpMaxTotalLots`), restart-safe dedup
`TradeExistsByCommentPrefix`, broker min-stop clamp, `NormalizeLot`. A tier whose
price has already been passed by the market is skipped (limit must rest the right side).

**Lifecycle**: a new MSS supersedes the old setup (`CancelAllPendings`). Unfilled
limits are cancelled after `InpPendingExpiryBars` LTF bars (`ManagePendings`).
`OnTradeTransaction` writes a closed-trade outcome JSON to
`Files/IctSmcEA/outcomes/<position_id>.json` (magic-filtered; fields mirror the other
EAs plus `entry_tier`; `signal_ts` = the MSS bar time, embedded in the comment
`ICT_E{1..3}_{mssTime}_{B|S}`).

### Trading inputs

| Input | Default | Purpose |
|---|---|---|
| `InpEnableTrading` | false | Master switch: false=draw only, true=place orders |
| `InpEntryFib1/2/3` | 0.62 / 0.705 / 0.785 | 3 OTE entry ratios |
| `InpLotPerEntry` | 0.01 | Lot per entry (×3) |
| `InpMaxSetupPositions` | 3 | Max positions+pendings per direction |
| `InpMaxTotalLots` | 0.30 | Max total lots per direction |
| `InpSlBufferPts` | 200 | SL buffer beyond the MSS swing (points) |
| `InpPendingExpiryBars` | 12 | Cancel unfilled limit after N LTF bars |
| `InpMinStopPts` | 150 | Min SL distance to accept a setup |
| `InpFallbackRR` | 2.0 | TP fallback R:R when no opposing liquidity |
| `InpRequireBiasAlign` | true | Only trade MSS aligned with HTF bias |
| `InpMaxSpreadPts` | 50 | Max spread to place entries (0=off) |
| `InpColEntry/SL/TP` | aqua/red/lime | Setup level colors |

## Phase 3 — Trade management (filled positions)

`ManageOpenPositions()` runs on a 1 Hz tick throttle for every position of this
magic+symbol. It is **stateless** (derived from each position's open price, SL, TP,
volume) so it survives EA restarts, and it runs **regardless of `InpEnableTrading`**
so positions opened earlier keep being managed even after trading is switched off.

| Feature | Toggle | Rule |
|---|---|---|
| **Break-even** | `InpEnableBreakEven` (true) | At `InpBeTriggerPts` profit, move SL to entry ± `InpBeOffsetPts` |
| **Points trailing** | `InpEnableTrailing` (true) | At `InpTrailStartPts` profit, trail SL `InpTrailDistPts` behind price |
| **Structural trailing** | `InpStructuralTrail` (true) | At `InpTrailStartPts` profit, trail SL behind the newest protective LTF swing + `InpStructBufferPts` — see Phase 5 |
| **Partial close** | `InpEnablePartialClose` (false) | At `InpPartialClosePts` profit, close `InpPartialClosePct` of volume **once** (skipped if the split would breach broker min-lot — a no-op at 0.01 lot; size up to use it) |

All SL candidates (BE, points-trail, structural-trail) are evaluated together and
the **most protective** one wins (`SLImproves`); SL only ever moves in the safer
direction (≥ `InpTrailStepPts` step) and never crosses the TP. Partial close fires
only while a position is still full size (`vol ≥ InpLotPerEntry`), which is how "do
it once" stays restart-safe without extra state.

## Phase 4 — Per-tier TP ladder

Each of the 3 entry tiers gets its own TP at successive opposing-liquidity levels
(structural scale-out), controlled by **`InpPerTierTP`** (default true):

- `CollectLiquidity()` gathers opposing swings beyond the leg end (bull → swing-highs
  above; bear → swing-lows below), sorts them by distance (nearest first), and dedups
  levels closer than `InpMinStopPts`.
- **tier 1** (entry 0.62, shallowest) → **nearest** liquidity; **tier 3** (entry 0.785,
  deepest) → **farthest**. Missing tiers reuse the farthest level found.
- No liquidity at all → an RR ladder `InpFallbackRR + tier` (≈ 2R / 3R / 4R) so the 3
  TPs stay distinct. Each tier's TP is validated to sit the correct side of its entry.
- `InpPerTierTP=false` → all tiers share the nearest liquidity (Phase-2 behavior).

`TradeSetup` carries `tp[3]` + `rr[3]`; `DrawSetup` renders 3 TP lines (`TP1/TP2/TP3`
with per-tier RR). Each limit order is placed with its own tier TP. Outcome JSON is
unchanged (it reads each position's actual TP via the close deal).

## Phase 5 — Structural SL trailing

`GetStructuralSL()` derives an SL from live LTF market structure rather than a fixed
point distance: for a long it returns the **newest swing-low below price** minus
`InpStructBufferPts` (for a short, the newest swing-high above price plus buffer). As
price prints higher lows in an uptrend, the SL "climbs the staircase" of higher-lows —
the ICT way to ride a trend. It is folded into the same most-protective-wins selection
as break-even and points-trailing (`InpStructuralTrail`, default true), so the three
coexist and whichever protects most is used. Stateless → restart-safe.

## Phase 6 (future)
- Move SL to the previous tier's entry as each deeper tier fills (entry ratchet).
