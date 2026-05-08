# Conde Auto Entry — End-to-End Workflow

Operational pipeline cho strategy `conde_auto_entry`: từ Telegram signal đến dashboard public. Tài liệu này focus vào deploy/runtime; logic EA + signal format xem [CLAUDE.md](./CLAUDE.md).

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

## Outcome / stats flow (every 15 min)

```
[MT5 EA + agent]                                    [GitHub]                [Browser]
     |                                                  |                       |
     |  signals  -> Redis Stream conde_signals          |                       |
     |  outcomes -> Redis Stream conde_outcomes         |                       |
     v                                                  |                       |
[VPS: Redis tren VPS] <--+                              |                       |
                         |                              |                       |
                 +-------+------------+                 |                       |
                 |  Scheduled Task    |                 |                       |
                 |  ConderStatsPublish|                 |                       |
                 |  (chay moi 15 phut)|                 |                       |
                 +-------+------------+                 |                       |
                         |                              |                       |
                         v                              |                       |
              publish-conde-stats.ps1                   |                       |
                  1. git pull C:\bots\conde-stats       |                       |
                  2. python dump_html.py --out ...      |                       |
                     -> doc Redis -> render index.html  |                       |
                  3. git add/commit/push --------------->                       |
                                                        v                       |
                                              repo: conde-stats (public)        |
                                                        |                       |
                                              GitHub Pages auto-deploy          |
                                                        v                       |
                                  https://quangtihon2000.github.io/conde-stats -+
```

## Two GitHub repos

| Repo | Purpose | Visibility |
|---|---|---|
| `quangtihon2000/kog-strategy` | code, EA, agents, CI | private |
| `quangtihon2000/conde-stats` | rendered dashboard (HTML + JSON) | public (Pages source) |

Lý do tách: stats cần public viewable nhưng code phải private. Push-only-rendered-output → public repo nhỏ, không leak source.

## VPS layout (Windows, host `WIN-PU7A75LHO5T`, user `QuangXAU`)

| Path | Purpose |
|---|---|
| `C:\actions-runner\_work\kog-strategy\kog-strategy\` | kog-strategy clone (GitHub Actions runner checkout). Venv lives here at `strategies/conde_auto_entry/agent/.venv`. |
| `C:\bots\conde-stats\` | Public conde-stats clone (cloned via SSH). |
| Redis | `redis://:Strong2026!@localhost:6379` (URL-encode password as `Strong2026%21` in env files) |

Note: `C:\actions-runner\_work\...` is owned by runner service — `QuangXAU` cần admin elevation để write trực tiếp.

## Services & tasks

- **NSSM service `conde_auto_entry`** — chạy agent. Logs ở `strategies/conde_auto_entry/agent/logs/`. Cần `PYTHONUNBUFFERED=1` (xem feedback memory).
- **Scheduled Task `ConderStatsPublish`** — repeat every 15 min, `LogonType=Password` (run whether logged in or not), `RunLevel=Highest`.

## Key scripts

| Script | When to run | What it does |
|---|---|---|
| [`scripts/install-conde-stats-task.ps1`](../../scripts/install-conde-stats-task.ps1) | One-time, PS Admin | Register Windows scheduled task, prompts for QuangXAU password |
| [`scripts/publish-conde-stats.ps1`](../../scripts/publish-conde-stats.ps1) | Auto every 15 min via task | Render dashboard + push to public repo |
| [`scripts/reset-conde-stats.sh`](../../scripts/reset-conde-stats.sh) | Manual | Wipe Redis stats streams for fresh `/stats` baseline |

## Operational notes

- Force-refresh dashboard: `Start-ScheduledTask -TaskName ConderStatsPublish` (PS Admin).
- Verify task: `Get-ScheduledTask -TaskName ConderStatsPublish | Get-ScheduledTaskInfo` — check `LastTaskResult=0` and `NextRunTime`.
- CI changes to `scripts/*.ps1` deploy to actions-runner checkout automatically; scheduled task picks up new versions on next fire.
- 15-min cadence is push-based (task → Pages), not poll-based (Pages → anything). Tradeoff freshness vs Pages rebuild quota.
