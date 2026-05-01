# CLAUDE.md — Project Context for AI Assistants

## Project Overview

**KOG Strategy** is a monorepo containing multiple MetaTrader 5 Expert Advisors (EAs) for automated XAUUSD trading, plus Python signal agents that bridge external signals to the EAs via JSON files.

## Architecture

```
[Redis Stream] → [Python Agent] → {account}.json → [MT5 EA]
                  (writes data/)   (symlink to       (reads from
                                    MT5 Files/)       MQL5/Files/)
```

- **EAs** are `.mq5` files compiled by `metaeditor64.exe` into `.ex5` binaries on a Windows VPS
- **Agents** are Python processes that consume Redis Streams and write JSON signal files atomically (tmp → `os.replace`)
- **CI/CD** uses GitHub Actions with a self-hosted runner on the Windows VPS

## Project Structure

```
kog_strategy/
├── strategies/              # Each strategy = EA + optional Python agent
│   ├── zone_signal/         # Zone breakout M15 (EA + agent)
│   ├── conde_auto_entry/    # JSON signal auto entry (EA + agent)
│   ├── asia_range_breakout/ # Asia session breakout (EA only)
│   ├── wyckoff_spring/      # Wyckoff spring/upthrust (EA only)
│   └── hedge_lock/          # Hedge lock pair (EA only)
├── shared/agent_lib/        # Shared Python code (RedisConsumer)
├── scripts/                 # PowerShell CI/CD scripts (run on VPS)
├── .github/workflows/       # GitHub Actions pipeline
├── deploy.json              # EA → MT5 instance mapping config
└── README.md
```

## Key Conventions

### MQL5 (EA files)
- Language: MQL5 (C++-like, compiled by MetaEditor)
- All EAs use `#include <Trade\Trade.mqh>` for order management
- Each EA has a unique `InpMagic` number to identify its orders
- EAs read JSON signal files from `MQL5/Files/{EAName}/` directory
- File polling is throttled to 1Hz (once per second via `TimeCurrent()`)
- JSON parsing is hand-rolled (no external libraries in MQL5)

### Python Agents
- Each agent lives in `strategies/{name}/agent/`
- Uses venv (`.venv/` per agent, gitignored)
- Dependencies in `requirements.txt` (typically just `redis` and `python-dotenv`)
- Config loaded from `.env` file via `python-dotenv`
- Imports shared code via `sys.path` insertion for `shared/agent_lib/`
- Atomic file writing: write to `.tmp`, then `os.replace()` to target

### Signal File Formats
- **ZoneSignal**: `{account}.json` with `timestamp`, `symbol`, `redbox_upper`, `redbox_lower`, `targets_above[]`, `targets_below[]`
- **CondeSignal**: `{account}_{symbol}.json` with `timestamp`, `symbol`, `direction`, `entry_price`, `sl`, `tps[]`
- Timestamp is the dedup key — EA detects new signal when timestamp changes

### CI/CD
- Pipeline defined in `.github/workflows/deploy.yml`
- Triggered on push to `main` branch (path-filtered per strategy)
- `deploy.json` maps strategies to MT5 terminal instances (by hash)
- PowerShell scripts in `scripts/` handle compile, deploy, agent setup
- Agents can run as Windows Services via NSSM

## Important Patterns

1. **Never re-stamp CondeSignal timestamps** — the producer-supplied timestamp is the EA's dedup identity
2. **ZoneSignal timestamps ARE re-stamped** at write time by the agent
3. **EA signal lifecycle**: new timestamp → enter trades → SL/TP hit → signal deactivated
4. **Atomic file writes** prevent partial reads by the EA — always use tmp+rename pattern
5. **Each MT5 instance has a unique data folder hash** under `%AppData%\MetaQuotes\Terminal\{hash}\`

## Common Tasks

- **Add a new strategy**: Create `strategies/{name}/ea/` with `.mq5` file, add entry to `deploy.json`
- **Add an agent to a strategy**: Create `strategies/{name}/agent/` with `main.py`, `config.py`, `models.py`, `signal_writer.py`, `requirements.txt`
- **Modify an EA**: Edit the `.mq5` file, push to main → CI/CD compiles and deploys
- **Test agent locally**: `cd strategies/{name}/agent && python -m venv .venv && pip install -r requirements.txt && python main.py`
