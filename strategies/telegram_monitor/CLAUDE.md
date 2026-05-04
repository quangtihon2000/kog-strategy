# telegram_monitor — Read-only Telegram bot for the agent fleet

Operational view of the kog_strategy agents from your phone. Read-only by
design: the bot can inspect services/logs/signals and push alerts, but
cannot start, stop, or modify anything.

## Architecture

```
Telegram <──polling──> bot.py (Application)
                         ├── handlers/   /status, /logs, /tail, /signals, /help, /whoami
                         ├── monitors/   service_edges, log_errors, heartbeat
                         ├── transports/ local (Phase 0) | ssh (Phase 1)
                         └── alerts.py   chat fan-out + cooldown dedup
```

- **Phase 0**: bot runs on the same Windows VPS as the agents. `transport: local`
  shells out via `nssm`/file reads.
- **Phase 1**: add `transport: ssh` in `transports/ssh.py`, add the new VPS to
  `agent/fleet.yaml` with `host`/`user`/`key_path`. Handlers and monitors do not
  change — they only see the abstract `Transport` interface.

## One-time setup

### 1. Create the bot with @BotFather

1. Open Telegram → message `@BotFather` → `/newbot`
2. Pick a display name (e.g. `KOG Fleet Monitor`) and a username ending in `bot`
   (e.g. `kog_fleet_monitor_bot`).
3. BotFather returns a token like `1234567890:AA...`. Save it — this is
   `TELEGRAM_BOT_TOKEN`.

The command list (the `/` autocomplete + menu button) is registered
automatically by `bot.py` on every startup via `set_my_commands`, so you do
not need to `/setcommands` in BotFather — edit `_BOT_COMMANDS` in `bot.py`
and redeploy.

### 2. Find your numeric user id

Start a chat with the bot and send `/whoami` (this command is unauthed on
purpose so you can bootstrap the whitelist). Save the number.

### 3. Configure GitHub Actions

In the repo settings:

- **Secret** `TELEGRAM_MONITOR_BOT_TOKEN` — the BotFather token.
- **Variable** `TELEGRAM_MONITOR_ALLOWED_USER_IDS` — comma-separated user ids,
  e.g. `123456789,987654321`. Empty = bot rejects everyone.

Naming follows `setup-agent.ps1`'s `${PREFIX}_${KEY}` convention
(`PREFIX = TELEGRAM_MONITOR`). The workflow at
[.github/workflows/deploy.yml](../../.github/workflows/deploy.yml) maps these
to the agent's `.env` automatically.

### 4. First deploy

Push to `main`. The workflow:

1. Stops `telegram_monitor_bot` (NSSM) along with the other agents.
2. Runs `setup-agent.ps1 -Strategies '["telegram_monitor"]'` which creates the
   venv, generates `.env`, and prints the NSSM install command if the service
   doesn't exist yet.
3. **One-time manual step on the VPS**: copy/paste the printed `nssm install`
   commands, then add:
   ```powershell
   nssm set telegram_monitor_bot AppEnvironmentExtra PYTHONUNBUFFERED=1
   nssm start telegram_monitor_bot
   ```
   `PYTHONUNBUFFERED=1` is required or NSSM's redirected log file stays empty
   even when the bot is healthy.

## Local development

```bash
cd strategies/telegram_monitor/agent
python -m venv .venv
. .venv/Scripts/activate     # Windows
pip install -r requirements.txt
cp .env.example .env         # fill TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
python main.py
```

`fleet.yaml` ships pointing at the runner's working tree
(`C:\actions-runner\_work\kog_strategy\...`); for local dev override `FLEET_CONFIG`
to a local copy with adjusted paths, or just point to a throwaway VPS.

## Heartbeat — opt-in for existing agents

The heartbeat monitor only alerts on agents that *have ever* published a beat,
so legacy agents stay silent until they opt in. To wire one up:

```python
from agent_lib.heartbeat import Heartbeat

hb = Heartbeat(redis_client, vps="vps-main", service="zone_signal")

while True:
    hb.beat()        # call once per loop iteration
    do_work()
```

Default `ttl_s=180` tolerates one missed 60s beat. The monitor pages when the
key disappears after having been seen at least once.

## Extension points

| When you want to… | Touch this |
|---|---|
| Add a second VPS | Add an entry under `vpses:` in `agent/fleet.yaml`. Implement `transports/ssh.py` (subclass `Transport`) and register it in `transports/__init__.py`. |
| Add a new monitor | Drop a module in `agent/monitors/`, register it in `register_monitors()` in `bot.py`. Use the JobQueue, push via `AlertDispatcher`. |
| Add a new command | Drop a handler in `agent/handlers/`, decorate with `@auth_required`, register in `register_handlers()`. |
| Change alert cooldown | `AlertDispatcher(cooldown_s=...)` in `bot.py`. |
| Change freshness threshold | Per-service `signal_freshness_min` in `fleet.yaml` (drives the red/green glyph in `/status`). |

## Why bare-script entrypoint

NSSM launches the bot as `python.exe main.py`, not `python -m agent.main`.
That breaks relative imports inside the package. `main.py` detects the
bare-script case, grafts the parent directory onto `sys.path` so `agent`
resolves as a package, then re-imports itself as `agent.main` so the rest of
the package can use `from .bot import ...` cleanly.
