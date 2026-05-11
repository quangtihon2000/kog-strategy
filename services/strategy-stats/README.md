# strategy-stats

Postgres-backed multi-strategy dashboard. Ingests Redis Streams produced by the MT5 EAs (conde / gvfx / zone), persists signals + outcomes, exposes a server-rendered web UI for stats.

## Stack

- FastAPI + Jinja2 + HTMX + Tailwind CDN (single web container)
- SQLAlchemy 2.0 async + asyncpg, Alembic migrations
- Postgres 15
- Async Redis consumer (`asyncio.gather` over 6 streams)
- HTTP Basic Auth (single shared credential)

## Layout

```
services/strategy-stats/
├── docker-compose.yml          # postgres + ingest + web
├── Dockerfile.ingest / Dockerfile.web
├── alembic.ini + migrations/
└── app/
    ├── settings.py / db.py / models.py / deps.py
    ├── ingest/                 # 6 stream consumers
    ├── stats/                  # per-strategy aggregations
    └── web/                    # FastAPI routers + Jinja2 templates
```

## Env vars (`.env`)

Copy `.env.example` → `.env` and fill in. All required:

| Var | Purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres creds (used by compose + app) |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Default `postgres` / `5432` (compose service name) |
| `UPSTREAM_REDIS_URL` | Redis on the **MT5 VPS** (e.g. `redis://10.0.0.5:6379` via SSH tunnel or TLS-exposed). Must NOT point to a local Redis on the Linux VPS — there's nothing there to consume. |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | Single shared credential for `/` |
| `WEB_PORT` | Host port for the web container (default 8080) |
| `INGEST_BATCH_COUNT` / `INGEST_BLOCK_MS` / `INGEST_CONSUMER_NAME` | XREADGROUP tuning |

## Stream → consumer-group mapping

Ingest uses `stats_*` group names to avoid colliding with the EA-writer groups:

| Stream | Consumer group | Idempotency key |
|---|---|---|
| `conde_signals` | `stats_conde_sig` | `(signal_ts, symbol)` |
| `conde_outcomes` | `stats_conde_out` | `position_id` |
| `gvfx_signals` | `stats_gvfx_sig` | `(signal_ts, symbol)` |
| `gvfx_outcomes` | `stats_gvfx_out` | `position_id` |
| `zone_signals` | `stats_zone_sig` | `(signal_ts, symbol)` |
| `zone_outcomes` | `stats_zone_out` | `position_id` |

First start with `id=0` → backfills whatever's still in Redis stream retention. `XACK` only on handler success, so failures stay in the PEL for inspection. Postgres uses `INSERT ... ON CONFLICT DO NOTHING`.

**Conde backfill policy:** `conde_signals` messages without a `channel_id` are skipped (ack'd, not stored). Legacy backfill from before the producer added `channel_id` would otherwise create channel-less rows that pollute per-channel stats — we'd rather lose them than misattribute.

## Local smoke test

```bash
cd services/strategy-stats
cp .env.example .env   # edit passwords + UPSTREAM_REDIS_URL=redis://host.docker.internal:6379

docker compose up --build -d postgres
docker compose run --rm ingest alembic upgrade head
docker compose up -d ingest web

docker compose logs -f ingest    # expect StreamConsumer ready × 6
```

Seed a fake signal:
```bash
redis-cli XADD conde_signals '*' \
  timestamp 1700000000 symbol XAUUSD direction BUY \
  entry_price 2000 sl 1990 tps 2010,2020,2030 channel_name TEST
```
Open <http://localhost:8080/> → Basic Auth → home page renders 3 KPI cards.

## Production deploy (Linux VPS)

```bash
ssh linux-vps
cd ~/kog_strategy && git pull
cd services/strategy-stats
docker compose up --build -d
docker compose run --rm ingest alembic upgrade head
```

Reverse-proxy (Caddy / nginx) terminates TLS in front of the `web` container. Verify backfill: `SELECT count(*) FROM conde_signals` should approach `XLEN conde_signals` on the MT5 VPS Redis.

### Cross-VPS Redis access

Ingest reads Redis on the MT5 (Windows) VPS, not local. Pick one:

- **SSH tunnel** (preferred): systemd unit on Linux VPS keeps `ssh -L 6379:127.0.0.1:6379 mt5-vps` open; set `UPSTREAM_REDIS_URL=redis://host.docker.internal:6379`.
- **Direct expose**: MT5 VPS Redis with `requirepass` + TLS, firewall whitelist Linux VPS IP.

## Routes

| Path | Auth | Purpose |
|---|---|---|
| `/` | Basic | Home — 3 KPI cards (conde / gvfx / zone) |
| `/conde` | Basic | Per-channel table + win-rate |
| `/conde/channel/{channel_id}` | Basic | Per-signal breakdown |
| `/gvfx` | Basic | Per-symbol cards + mode_tag (A/F/S) breakdown |
| `/gvfx/symbol/{symbol}` | Basic | Per-signal grid |
| `/zone` | Basic | Per-account + per-tier (SCALP/NORMAL/MID) |
| `/zone/account/{account}` | Basic | Per-signal tier breakdown |
| `/healthz` | **none** | Docker healthcheck JSON |

HTMX powers `?since=7d|30d|all` selectors and column sorts (`hx-get` + `hx-target="#data"` + `hx-push-url=true`).

## Schema highlights

- `channels(channel_id BIGINT PK, name TEXT, name_history JSONB)` — rename history appended on ingest when `name` diverges from stored row. Only conde signals carry channel_id; gvfx/zone NULL.
- Per-strategy `*_signals` / `*_outcomes` tables. `raw JSONB` column on all of them for forensics.
- No FK between signals ↔ outcomes (outcome can arrive before signal during cold replay).
- GVFX outcomes carry `mode_tag` (A/F/S/?) parsed from comment `GVFX_T{ts}_{mode}`; `close_reason` adds `EOD` value for cut-window closes.
- Zone outcomes carry `tier` (SCALP/NORMAL/MID/UNKNOWN) + `slot_index` parsed from comment `ZB|ZS_(SCALP{n}|T{n}|MID)_{ts}`.

## Verification

Cross-check against existing static `conde-stats` GitHub Pages: same source streams + same algorithm → win-rate per channel must match. Once stable, retire the `ConderStatsPublish` scheduled task on the MT5 VPS.
