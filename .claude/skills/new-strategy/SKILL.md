---
name: new-strategy
description: Scaffold a new MT5 EA + Python signal agent strategy in this monorepo. Use when the user asks to "tạo strategy mới", "add new EA", "scaffold {name} strategy", or to add a Redis-fed signal agent for a new EA.
---

# Adding a new EA + agent strategy

End-to-end playbook for adding `strategies/{name}/` (EA + Python signal agent) to this monorepo. Follow in order — many of the steps have hidden gotchas that don't surface until deploy time.

## 0. Decide naming

- `{name}` — snake_case strategy id (e.g. `gvfx_signal`, `conde_auto_entry`). Used as folder name + Redis stream prefix.
- `{Name}EA` — PascalCase EA class (e.g. `GvfxSignalEA`). Drives `.mq5` filename and `MQL5/Files/{Name}EA/` data subfolder.
- `{name}_agent` — NSSM service name on the VPS (telegram_monitor is the one exception → `telegram_monitor_bot`).
- `{PREFIX}` — uppercase of `{name}` (e.g. `GVFX_SIGNAL`). Drives env var names per `${PREFIX}_${KEY}` convention.

Pick a unique `InpMagic` ulong (no other EA in the repo may share it) — grep existing `.mq5` files for `InpMagic` to confirm.

## 1. Pick a reference strategy to copy from

| Pattern | Reference |
|---|---|
| Per-symbol signal file `{account}_{symbol}.json`, producer-supplied timestamp preserved | `strategies/conde_auto_entry/` |
| Per-account signal file `{account}.json`, agent re-stamps timestamp | `strategies/zone_signal/` |
| Grid DCA EA with daily P&L cut | `strategies/gvfx_signal/` |

Read the reference's `CLAUDE.md` and `agent/` files first.

## 2. File layout to create

```
strategies/{name}/
├── CLAUDE.md                  # strategy overview (overview + EA logic + signal contract + .env)
├── ea/
│   └── {Name}EA.mq5
├── agent/
│   ├── main.py                # event loop + RedisConsumer wiring
│   ├── config.py              # Pydantic Settings or dataclass from .env
│   ├── models.py              # signal dataclass with from_dict/validate/to_json
│   ├── signal_writer.py       # atomic tmp → os.replace, retry on PermissionError
│   ├── requirements.txt       # redis>=5.0, python-dotenv>=1.0
│   └── .env.example
└── data/                      # runtime junction → MT5 Files/{Name}EA/ (created by deploy)
```

The `agent/` files mirror the reference 1:1 — only the model fields, redis stream defaults, and signal_writer file-path template change.

## 3. Edit `deploy.json`

Add the strategy entry to the root `strategies` object:

```json
"{name}": {
  "ea_source": "strategies/{name}/ea/{Name}EA.mq5",
  "deploy_to": ["mt5_main"],          // confirm with user — which terminal?
  "agent": {
    "enabled": true,
    "agent_dir": "strategies/{name}/agent",
    "service_name": "{name}_agent",
    "data_subfolder": "{Name}EA",
    "env": {
      "required": ["MT5_ACCOUNTS", "MT5_SYMBOLS"],   // adjust per signal contract
      "optional": ["REDIS_URL", "REDIS_STREAM", "REDIS_GROUP", "REDIS_CONSUMER", "LOG_LEVEL"]
    }
  }
}
```

`data_subfolder = null` if the agent doesn't write per-EA files (e.g. telegram_monitor).

## 4. Update `.github/workflows/deploy.yml` — THREE spots + fleet.yaml

This is the most-missed step. The workflow has hardcoded service lists in three places. Grep `deploy.yml` for an existing service name (e.g. `gvfx_signal_agent`) — every match is a site you must extend. Skipping any → silent breakage on the VPS.

**4a. Stop services foreach** (~line 117):
```yaml
foreach ($svc in @('zone_signal_agent','conde_signal_agent','gvfx_signal_agent','{name}_agent','telegram_monitor_bot')) {
```

**4b. Env mapping block** (~line 150–172): add a per-strategy block under the existing ones:
```yaml
# {name}
{PREFIX}_MT5_ACCOUNTS: ${{ secrets.{PREFIX}_MT5_ACCOUNTS }}
{PREFIX}_MT5_SYMBOLS: ${{ vars.{PREFIX}_MT5_SYMBOLS }}
{PREFIX}_REDIS_STREAM: ${{ vars.{PREFIX}_REDIS_STREAM }}
{PREFIX}_REDIS_GROUP: ${{ vars.{PREFIX}_REDIS_GROUP }}
{PREFIX}_REDIS_CONSUMER: ${{ vars.{PREFIX}_REDIS_CONSUMER }}
```
Use `secrets.*` for sensitive (account ids), `vars.*` for non-sensitive. The keys must match `agent.env.required` + `agent.env.optional` from `deploy.json`.

**4c. Start services foreach** (~line 187): add `{name}_agent` to the same array as in 4a.

**4d. Register with telegram_monitor fleet** — edit [strategies/telegram_monitor/agent/fleet.yaml](../../../strategies/telegram_monitor/agent/fleet.yaml). Without this, `/status` and the new-signal notifier ignore the strategy:
```yaml
- name: {name}
  nssm_service: {name}_agent
  agent_dir: C:\actions-runner\_work\kog-strategy\kog-strategy\strategies\{name}\agent
  log_dir:   C:\actions-runner\_work\kog-strategy\kog-strategy\strategies\{name}\agent\logs
  signal_dir: C:\actions-runner\_work\kog-strategy\kog-strategy\strategies\{name}\data
  signal_freshness_min: 30                # alert threshold for /status glyph
  mt5_logs:
    - account: "{login}"
      log_dir: C:\Users\QuangXAU\AppData\Roaming\MetaQuotes\Terminal\{hash}\MQL5\Logs
```
The `signals_new` monitor (`monitors/signals_new.py`) loops over `settings.fleet.all_services()` and pings the chat whenever a new file appears under `signal_dir`. Skipping this entry → bot is silent on new signals AND `/status` doesn't list the service.

## 5. Configure GitHub repo Secrets/Variables BEFORE pushing

Repo Settings → Secrets and variables → Actions:

- **Secrets**: `{PREFIX}_MT5_ACCOUNTS` (and any other sensitive)
- **Variables**: `{PREFIX}_MT5_SYMBOLS`, `{PREFIX}_REDIS_STREAM`, `{PREFIX}_REDIS_GROUP`, `{PREFIX}_REDIS_CONSUMER`

If you push without these, `setup-agent.ps1` aborts with `Missing required env vars: {PREFIX}_MT5_ACCOUNTS or MT5_ACCOUNTS, ...`.

## 6. Push to main

`git push origin main` — the workflow auto-triggers. Watch:
```bash
gh run watch
```
Path filter requires the diff to touch `strategies/**`, `shared/**`, `deploy.json`, `scripts/**`, or `.github/workflows/**`. Editing `deploy.json` or `.github/workflows/` triggers a redeploy of ALL strategies (`configChanged` branch).

## 7. One-time manual NSSM install on the VPS

First deploy of a new agent: `setup-agent.ps1` only PRINTS the install hint, it doesn't install. RDP into the VPS and run the **full 6-line sequence** (the script's printed hint is incomplete — only shows 3 lines, see memory `feedback_setup_agent_install_hint_incomplete`):

```powershell
$svc = "{name}_agent"
$dir = "C:\actions-runner\_work\kog-strategy\kog-strategy\strategies\{name}\agent"
$py  = "$dir\.venv\Scripts\python.exe"

nssm install $svc "$py" "$dir\main.py"
nssm set $svc AppDirectory "$dir"
nssm set $svc AppStdout "$dir\logs\stdout.log"
nssm set $svc AppStderr "$dir\logs\stderr.log"
nssm set $svc AppEnvironmentExtra PYTHONUNBUFFERED=1
nssm start $svc
```

Without `AppStdout`/`AppStderr` → `stdout.log` doesn't exist on disk even when service is running. Without `PYTHONUNBUFFERED=1` → log file exists but stays 0 bytes.

## 8. Verify

```powershell
nssm status {name}_agent                               # → SERVICE_RUNNING
Get-Content "$dir\logs\stdout.log" -Tail 20            # → consumer connected, waiting for messages
```

Push a test signal via Redis:
```bash
redis-cli XADD {stream} '*' timestamp $(date +%s) symbol XAUUSD ...
```
→ check `strategies/{name}/data/{account}_{symbol}.json` appears with correct shape.

## 9. EA-side verification

`deploy-ea.ps1` compiles the EA via `metaeditor64.exe` per terminal hash and creates a junction `MQL5/Files/{Name}EA` → `strategies/{name}/data`. After CI green:

1. RDP, open MT5 instance, attach `{Name}EA` to chart.
2. Tail `Experts` log for the EA's prefix messages.
3. Push a signal, confirm trade enters per signal contract.

## Common gotchas (in order of how often they bite)

1. **Forgot 4c (Start services foreach)** → CI green, agent installed, but service stays SERVICE_STOPPED forever. Symptom: no log activity.
1b. **Forgot 4d (telegram_monitor fleet.yaml)** → agent runs and writes signal files, but Telegram chat never gets the new-signal ping; `/status` doesn't list the service.
2. **Forgot to configure GitHub Secrets/Variables before pushing** → CI fails on `setup-agent.ps1` with `Missing required env vars`.
3. **Pasted incomplete NSSM install hint** → service runs, no log file, no obvious symptom until you try to debug.
4. **Re-using an existing `InpMagic`** → EA opens trades but `ScanMaxSeenTimestamp` cross-contaminates with the other strategy's history.
5. **Wrong `deploy_to` terminal hash** → EA compiled but installed to the wrong MT5 instance (which may not even have the symbol).

## Reference: stable plumbing that does NOT need changes

- `scripts/setup-agent.ps1` — generic, walks `deploy.json` strategies. Don't fork per-strategy.
- `shared/agent_lib/redis_consumer.py` — reuse as-is, never copy into `strategies/{name}/`.
- `scripts/deploy-ea.ps1` — generic, walks `deploy.json` `terminals` × `strategies`. Don't touch.
