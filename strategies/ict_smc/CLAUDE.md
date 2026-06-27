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

## Phase 2 (future) — trading

The scaffolding is already trade-ready so Phase 2 is additive:
- `CTrade g_trade` + `InpMagic` are set in `OnInit`.
- `g_cfg_Enabled` already gates (Phase 1 only logs it).
- Entry trigger is already computed: an LTF MSS aligned with HTF bias + price
  tapping the OTE band → market/limit order, SL beyond the MSS swing, TP at the
  opposing liquidity / fib extension.
- Add `OnTradeTransaction` + outcome JSON under `Files/IctSmcEA/outcomes/`
  (mirror `ZoneSignalEA`) for strategy-stats joins.
