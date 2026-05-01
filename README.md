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
│  MT5 Terminal 1                MT5 Terminal 2            │
│  ├─ ZoneSignalEA.ex5          ├─ AsiaRangeBreakoutEA    │
│  ├─ CondeAutoEntryEA.ex5      └─ WyckoffSpringEA        │
│  └─ HedgeLockEA.ex5                                    │
│                                                         │
│  [Python agents]                                        │
│  ├─ zone_signal_agent   (Redis → JSON → EA reads)       │
│  └─ conde_signal_agent  (Redis → JSON → EA reads)       │
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
│   │   ├── data/                    # Runtime: agent writes, EA reads
│   │   └── EA_LOGIC.md
│   ├── conde_auto_entry/            # JSON signal auto entry
│   │   ├── ea/CondeAutoEntryEA.mq5
│   │   ├── agent/                   # Python: Redis → JSON signal writer
│   │   └── data/
│   ├── asia_range_breakout/         # Asia session breakout (standalone)
│   │   └── ea/AsiaRangeBreakoutEA.mq5
│   ├── wyckoff_spring/              # Wyckoff spring/upthrust (standalone)
│   │   └── ea/WyckoffSpringEA.mq5
│   └── hedge_lock/                  # Hedge lock pair (standalone)
│       └── ea/HedgeLockEA.mq5
├── shared/                          # Shared Python code
│   └── agent_lib/
│       └── redis_consumer.py        # Common Redis Stream consumer
├── scripts/                         # CI/CD PowerShell scripts (VPS)
│   ├── compile-ea.ps1               # Compile .mq5 → .ex5
│   ├── deploy-ea.ps1                # Copy .ex5 to MT5 folders
│   ├── setup-agent.ps1              # venv + pip + service restart
│   └── link_ea.ps1                  # Legacy symlink helper
├── .github/workflows/deploy.yml     # CI/CD pipeline
├── deploy.json                      # EA → MT5 instance mapping
└── README.md
```

---

## Strategies

| Strategy | EA | Agent | Description |
|---|---|---|---|
| `zone_signal` | ZoneSignalEA | ✅ `zone_signal_agent` | Three-tier entry on M15 zone breakout |
| `conde_auto_entry` | CondeAutoEntryEA | ✅ `conde_signal_agent` | Pre-computed entry/SL/TP auto execution |
| `asia_range_breakout` | AsiaRangeBreakoutEA | ❌ | Scalp breakout of Asia session range |
| `wyckoff_spring` | WyckoffSpringEA | ❌ | Wyckoff spring/upthrust reversal |
| `hedge_lock` | HedgeLockEA | ❌ | Buy+Sell pair recycling on profit |

---

## CI/CD Pipeline

### How it works

1. **Push to `main`** → GitHub Actions detects which strategies changed
2. **Compile** → `metaeditor64.exe` compiles `.mq5` → `.ex5` on VPS
3. **Deploy** → `.ex5` copied to configured MT5 terminal(s)
4. **Agent setup** → venv updated, service restarted (if agent exists)

### Configuration

Edit `deploy.json` to map strategies to MT5 terminals:

```json
{
  "terminals": {
    "terminal_1": { "hash": "YOUR_MT5_DATA_HASH", "label": "Main Account" }
  },
  "strategies": {
    "zone_signal": {
      "ea_source": "strategies/zone_signal/ea/ZoneSignalEA.mq5",
      "deploy_to": ["terminal_1"]
    }
  }
}
```

### Manual deploy

```powershell
# Compile all
.\scripts\compile-ea.ps1 -Strategies '["zone_signal","hedge_lock"]'

# Deploy
.\scripts\deploy-ea.ps1 -Strategies '["zone_signal","hedge_lock"]'

# Setup agents
.\scripts\setup-agent.ps1 -Strategies '["zone_signal"]'
```

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
