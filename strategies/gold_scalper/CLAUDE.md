# CLAUDE.md — Gold Scalper Strategy

## Overview

Scalp XAUUSD M5 with two complementary setups: London/NY killzone breakout (trend-following) and VWAP rejection (mean-reversion). Targets 1-2% equity/day with hard 2% daily loss cap and a 23:30 force-close. Self-contained EA, no Python agent.

## Philosophy

EA này nhắm trung bình **1-1.5%/ngày tính trên 20 ngày giao dịch, KHÔNG phải mỗi ngày**. Có ngày flat, có ngày âm trong giới hạn daily loss. Việc cố ép profit mỗi ngày sẽ phá vỡ expectancy của hệ thống. Daily profit target + green day lock chỉ là cơ chế bảo toàn khi đã đạt mục tiêu — không phải mệnh lệnh phải đạt mỗi ngày.

## Components

- **EA**: `ea/GoldScalperEA.mq5` — single file, all classes inline
- No agent, no data directory

## EA Logic

### Daily flow
| Time (broker) | Action |
|---|---|
| 00:00 | Reset day: clear Asian range, trade counters, consec losses, snapshot balance |
| 06:00–13:00 | Asian session — accumulate VWAP, no entries |
| ≥ 13:00 | Asian range frozen (H/L of M5 bars 06:00–13:00) |
| 13:00–16:00 | London killzone — Setup A armed |
| 16:00–19:00 | NY killzone — Setup A armed |
| Outside KZ | Setup B (VWAP rejection) only |
| ≥ 22:00 | No new entries (cutoff) |
| ≥ 23:30 | Force close all open positions |

### Setup A — Killzone breakout retest
1. Phase 1 (detect): a closed M5 bar closes beyond Asian range AND aligns with M15 EMA50 AND VWAP. Records swing extreme (5-bar low/high) for SL anchoring.
2. Phase 2 (entry): within 12 M5 bars (1 hour), price retests the broken edge with a bullish/bearish confirmation candle (engulfing or pin bar).
3. SL = swing extreme ± `InpSL_BufferPoints` (default 200pt = 20pip). TP1 at 1R (close 50%, move SL to BE), TP2 at 2.5R.
4. One Setup A trade per day max.

### Setup B — VWAP rejection (mean reversion)
- Only when Setup A is not currently in a position.
- Triggered when a closed M5 bar wicks ≥ `InpVWAP_DeviationPips` (default 60pip) from VWAP and prints a confirmation candle pulling back toward VWAP.
- SL = 20 pip beyond the wick extreme. TP at fixed RR `InpVWAP_RR` (default 1.5).

### Risk gates (checked on each new M5 bar before entry logic)
- Daily PnL ≤ -`InpDailyLossLimitPct` → stop until next day
- Trades today ≥ `InpMaxTradesPerDay` → stop
- Consecutive losses ≥ `InpMaxConsecLosses` → stop
- Daily profit target hit (closed P/L ≥ `InpDailyProfitTargetPct`) → block new entries until 00:00
- Spread > `InpMaxSpreadPoints` → skip
- Inside news window (`InpNewsTimes` ± `InpNewsBufferMinutes`) → skip
- After cutoff time → skip
- Already 1 position open → skip (max 1 concurrent)

### Daily profit target & green day lock (per-tick, not per-bar)
- **Arm**: when closed P/L (balance-based) for the day reaches `InpDailyProfitTargetPct` × start balance, set `g_dailyProfitTargetHit = true`. From this point, `ShouldStopTrading()` returns true → no new entries until next 00:00 reset. Open positions continue to be managed normally (TP1 partial, BE move, TP2/SL).
- **Green day lock** (`InpEnableGreenDayLock = true`): once target is armed AND a position is still open, if equity-based P/L (closed + floating) retraces to `InpGreenDayLockRetracePct`% of target money, immediately `ForceCloseAll()` to preserve the green day. Fires at most once per day.

### VWAP
Custom cumulative VWAP, resets at 00:00 broker time. Per closed M5 bar: `cumPV += typical * tickVolume`, `cumV += tickVolume`, `vwap = cumPV / cumV`.

### Lot sizing
`riskMoney / lossPerLot` using `SYMBOL_TRADE_TICK_VALUE` and `SYMBOL_TRADE_TICK_SIZE`, rounded to `SYMBOL_VOLUME_STEP`, clamped to min/max.

### Order send
Up to 3 attempts on requote / price changed / off-quotes; abort on any other error. 200ms gap between retries.

## Key Parameters

| Parameter | Default | Description |
|---|---|---|
| `InpRiskPercent` | 0.75 | % balance risked per trade |
| `InpDailyLossLimitPct` | 2.0 | Stop trading if daily PnL ≤ -X% |
| `InpMaxTradesPerDay` | 3 | Max executed trades per broker day |
| `InpMaxConsecLosses` | 2 | Stop after N consecutive losses |
| `InpDailyProfitTargetPct` | 1.5 | Block new entries when closed P/L ≥ X% of start balance |
| `InpEnableGreenDayLock` | true | Close open trade if profit retraces below lock threshold |
| `InpGreenDayLockRetracePct` | 50.0 | % of target — close to preserve green day |
| `InpEnableLong` / `InpEnableShort` | true / true | Direction toggles for A/B testing |
| `InpEnableSetupA` / `InpEnableSetupB` | true / true | Per-setup toggles for A/B testing |
| `InpAsianStart` / `InpAsianEnd` | 06:00 / 13:00 | Asian session window |
| `InpLondonKZStart` / `InpLondonKZEnd` | 13:00 / 16:00 | London killzone |
| `InpNYKZStart` / `InpNYKZEnd` | 16:00 / 19:00 | NY killzone |
| `InpDailyCutoff` / `InpForceCloseTime` | 22:00 / 23:30 | No-new-entry / force-close |
| `InpRetestMaxBars` | 12 | M5 bars to wait for Setup A retest |
| `InpSL_BufferPoints` | 200 | SL buffer beyond swing (200pt = 20pip) |
| `InpEMA_M15_Period` | 50 | EMA filter on M15 |
| `InpTP1_RR` / `InpTP2_RR` | 1.0 / 2.5 | Setup A targets (RR) |
| `InpTP1_PartialPct` | 50 | % volume closed at TP1 |
| `InpEnableTP1Partial` | true | false → skip partial close + BE move; full position runs to TP2 |
| `InpVWAP_DeviationPips` | 60 | Min wick deviation from VWAP for Setup B |
| `InpVWAP_RR` | 1.5 | Fixed RR for Setup B |
| `InpMaxSpreadPoints` | 30 | Skip entries when spread > X points |
| `InpNewsTimes` | "" | CSV `HH:MM` list (broker tz), e.g. `"14:30,20:00"` |
| `InpNewsBufferMinutes` | 15 | Window ± minutes around each news time |
| `InpMagic` | 20260503 | Unique magic number |

## Important Notes

- All times are broker time. Tune session inputs for your broker's GMT offset.
- `1 pip = 10 points` for XAUUSD (digits 2 or 3). Lot calc and SL buffers respect this.
- Real account triggers a one-shot `Alert()` at OnInit when `InpAlertOnRealAccount=true` (does not block trading).
- Magic number is unique to this EA — do not run another EA with magic `20260503` on the same instance.
- Force close is unconditional past `InpForceCloseTime`; positions are not held overnight.
- Consecutive-loss counter resets at the same 00:00 daily reset as PnL tracking.
