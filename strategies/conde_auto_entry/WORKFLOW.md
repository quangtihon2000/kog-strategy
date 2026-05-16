# Conde Auto Entry — End-to-End Workflow

Operational pipeline cho strategy `conde_auto_entry`: từ Telegram signal đến MT5 execution. Tài liệu này focus vào deploy/runtime; logic EA + signal format xem [CLAUDE.md](./CLAUDE.md).

## Signal flow (live trading)

```
Telegram channel
  -> telegram_monitor agent (NSSM service)
  -> Redis Stream "conde_signals"
  -> conde_auto_entry agent (NSSM service)
  -> data/{account}_{symbol}.json   (atomic write: tmp + os.replace)
  -> CondeAutoEntryEA.mq5 (in MT5)   (poll 1Hz, dedup via CAE_T{n}_{ts} comment)
  -> MetaTrader broker
```

`timestamp` field là producer-supplied và **không được re-stamp** ở agent — nó là dedup identity của EA.

## Stats / outcome dashboard

Live dashboard do service [`strategy-stats`](../../services/strategy-stats/) phục vụ (FastAPI + Postgres). Xem README service đó để biết deploy + URL.

Trước đây có một publisher tĩnh (`ConderStatsPublish` scheduled task) render HTML rồi push lên repo public `conde-stats` (GitHub Pages). Pipeline đó đã retire — `strategy-stats` thay thế hoàn toàn.

## VPS layout (Windows, host `WIN-PU7A75LHO5T`, user `QuangXAU`)

| Path | Purpose |
|---|---|
| `C:\actions-runner\_work\kog-strategy\kog-strategy\` | kog-strategy clone (GitHub Actions runner checkout). Venv lives here at `strategies/conde_auto_entry/agent/.venv`. |
| Redis | `redis://:Strong2026!@localhost:6379` (URL-encode password as `Strong2026%21` in env files) |

Note: `C:\actions-runner\_work\...` is owned by runner service — `QuangXAU` cần admin elevation để write trực tiếp.

## Services

- **NSSM service `conde_auto_entry`** — chạy agent. Logs ở `strategies/conde_auto_entry/agent/logs/`. Cần `PYTHONUNBUFFERED=1` (xem feedback memory).

## Key scripts

| Script | When to run | What it does |
|---|---|---|
| [`scripts/reset-conde-stats.sh`](../../scripts/reset-conde-stats.sh) | Manual | Wipe Redis stats streams for fresh `/stats` baseline (Telegram bot + strategy-stats) |
