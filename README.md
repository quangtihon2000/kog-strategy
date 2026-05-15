# KOG Strategy — Trading Automation Monorepo

A monorepo containing multiple MetaTrader 5 Expert Advisors (EAs) and their supporting Python agents for automated trading.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  GitHub  (git push to main)                             │
│    └─ Actions workflow: compile → deploy → agent setup  │
└────────────────────────┬────────────────────────────────┘
                         │  Self-hosted runner
                         ▼
┌─────────────────────────────────────────────────────────┐
│  VPS Windows (MT5 instances)                            │
│                                                         │
│  [metaeditor64.exe] ─── compile .mq5 → .ex5            │
│                                                         │
│  MT5 Terminal 1                MT5 Terminal 2                 │
│  ├─ ZoneSignalEA.ex5           └─ GvfxSignalEA.ex5             │
│  └─ CondeAutoEntryEA.ex5                                       │
│                                                                │
│  [Python agents]                                               │
│  ├─ zone_signal_agent   (Redis → JSON → EA reads)              │
│  ├─ conde_signal_agent  (Redis → JSON → EA reads)              │
│  ├─ gvfx_signal_agent   (Redis → JSON → EA reads)              │
│  └─ telegram_monitor_bot (read-only fleet monitor)             │
└─────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
kog_strategy/
├── strategies/                      # Each strategy = EA + optional agent
│   ├── zone_signal/                 # Zone breakout M15
│   │   ├── ea/ZoneSignalEA.mq5
│   │   ├── agent/                   # Python: Redis → JSON signal writer
│   │   ├── config/accounts/         # Per-account JSON overrides (<acc>.json)
│   │   ├── data/                    # Runtime: agent writes, EA reads
│   │   └── EA_LOGIC.md
│   ├── conde_auto_entry/            # JSON signal auto entry
│   │   ├── ea/CondeAutoEntryEA.mq5
│   │   ├── agent/                   # Python: Redis → JSON signal writer
│   │   ├── config/accounts/         # Per-account JSON overrides
│   │   └── data/
│   ├── gvfx_signal/                 # Grid DCA from target-price signal
│   │   ├── ea/GvfxSignalEA.mq5
│   │   ├── agent/                   # Python: Redis → JSON signal writer
│   │   ├── config/accounts/         # Per-account JSON overrides
│   │   └── data/
│   └── telegram_monitor/            # Read-only Telegram fleet monitor
│       └── agent/
├── shared/                          # Shared Python code
│   └── agent_lib/
│       └── redis_consumer.py        # Common Redis Stream consumer
├── scripts/                         # CI/CD PowerShell scripts (VPS)
│   ├── _lib.ps1                     # Helpers (Get-LocalTerminals by VPS)
│   ├── deploy-ea.ps1                # Copy .mq5 + compile in-place (VPS-filtered)
│   ├── setup-agent.ps1              # venv + pip + service restart (VPS-filtered)
│   ├── deploy-account-configs.ps1   # Sync per-account JSON to MT5 Files
│   ├── validate-account-configs.ps1 # CI pre-check (Rule 1 uniqueness + Rule 2 eligibility)
│   └── link_ea.ps1                  # Legacy symlink helper
├── .github/workflows/deploy.yml     # CI/CD pipeline (multi-VPS matrix)
├── deploy.json                      # Terminal → VPS/accounts mapping; strategy deploy_to
└── README.md
```

---

## Strategies

| Strategy | EA | Agent | Description |
|---|---|---|---|
| `zone_signal` | ZoneSignalEA | ✅ `zone_signal_agent` | Three-tier entry on M15 zone breakout |
| `conde_auto_entry` | CondeAutoEntryEA | ✅ `conde_signal_agent` | Pre-computed entry/SL/TP auto execution |
| `gvfx_signal` | GvfxSignalEA | ✅ `gvfx_signal_agent` | Grid DCA from target-price signal with EOD cut |
| `telegram_monitor` | — | ✅ `telegram_monitor_bot` | Read-only Telegram bot for fleet monitoring |

---

## CI/CD Pipeline

### How it works

1. **Push to `main`** → GitHub Actions detects which strategies changed
2. **Deploy & compile in-place** → `.mq5` copied into each target terminal's `MQL5/Experts/<EAName>/`, then compiled by that terminal's own `metaeditor64.exe`
3. **Agent setup** → venv updated, service restarted (if agent exists)

### Configuration

Edit `deploy.json` to map strategies to MT5 terminals. Each terminal declares
the **VPS** it lives on and the **accounts** that log into it; each strategy
lists the terminals it deploys to:

```json
{
  "terminals": {
    "terminal_1": {
      "vps": "vps-sg",
      "hash": "YOUR_MT5_DATA_HASH",
      "label": "Main Account",
      "mt5_install_dir": "C:\\Program Files\\MetaTrader 5",
      "user_profile": "QuangXAU",
      "accounts": [25312392]
    }
  },
  "strategies": {
    "zone_signal": {
      "ea_source": "strategies/zone_signal/ea/ZoneSignalEA.mq5",
      "deploy_to": ["terminal_1"]
    }
  }
}
```

- `vps` — GitHub Actions runner label (e.g. `vps-sg`). The CI matrix dispatches
  per-VPS jobs and each runner only touches its own terminals.
- `accounts` — every account ID must appear in **exactly one** terminal across
  the whole file. This is enforced by `validate-account-configs.ps1` (Rule 1)
  in the `validate-configs` job (runs on `ubuntu-latest` before any VPS job).
- Per-account input overrides live at
  `strategies/<strat>/config/accounts/<account>.json`. The same validator
  (Rule 2) ensures each `<account>` is eligible for that strategy via
  `strategies.<strat>.deploy_to → terminals[*].accounts`.

### Manual deploy

```powershell
# Deploy + compile in-place at each terminal on this VPS
.\scripts\deploy-ea.ps1 -Strategies '["zone_signal","gvfx_signal"]' -Vps 'vps-sg'

# Setup agents on this VPS
.\scripts\setup-agent.ps1 -Strategies '["zone_signal"]' -Vps 'vps-sg'

# Sync per-account JSON overrides into each terminal's MQL5/Files/<EAName>/config/
.\scripts\deploy-account-configs.ps1 -Strategies '["zone_signal"]' -Vps 'vps-sg'
```

`-Vps` defaults to the `GH_RUNNER_VPS` environment variable when omitted, so on
a runner machine you can drop the flag. Terminals whose `vps` field doesn't
match are skipped silently.

### VPS Setup (one-time)

1. Install [GitHub Actions self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners)
2. Configure runner as Windows Service
3. Fill in `deploy.json` with your MT5 terminal hash(es)
4. Install Python 3.10+ and NSSM (optional, for agent services)

---

## Data Flow (Agent ↔ EA)

```
[Redis Stream]  →  [Python Agent]  →  {account}.json  →  [MT5 EA]
                    writes to data/     via symlink          reads from
                                        to MT5 Files/       MQL5/Files/
```

Each agent writes JSON signal files atomically (tmp → rename) to the strategy's `data/` folder, which is symlinked to the MT5 `Files/{EAName}/` directory.

---

## Local Development

### Running an agent locally

```bash
cd strategies/zone_signal/agent
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
# .venv\Scripts\activate       # Windows
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your config
python main.py
```

### Testing a signal

```bash
# Push a test signal via Redis
python strategies/zone_signal/agent/test_push.py
```
