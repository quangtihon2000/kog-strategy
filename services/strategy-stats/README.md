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
| `POSTGRES_HOST` / `POSTGRES_PORT` | Default `strategy-stats-postgres` / `5432`. We use an explicit alias (not the bare service name `postgres`) because the web container also joins `portfolio-engine_portfolio-network`, where `postgres` resolves to a different, unrelated DB. |
| `UPSTREAM_REDIS_URL` | Redis on the **MT5 VPS**. Prod: `rediss://default:<password>@<mt5-vps-host>:6380` (TLS + requirepass). Local dev: `redis://host.docker.internal:6379`. Must NOT point to a local Redis on the Linux VPS — there's nothing there to consume. |
| `REDIS_STREAM_PREFIX` | Stream namespace prefix. Empty = prod names (`conde_signals`); set `dev_` / `test_` for staging |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | Single shared credential for `/` |
| `WEB_PORT` | Loopback host port for the web container (default 8080); external `portfolio-caddy` fronts public traffic via the `strategy-stats-web` network alias |
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

End-to-end runbook for first-time deploy. Two VPS in play:

- **MT5 VPS** (Windows): runs the EAs + Redis. Needs to expose Redis TLS+auth on a public port.
- **Linux VPS**: runs this stack (`docker compose`). Connects to MT5 VPS Redis as a consumer.

Reverse-proxy / TLS is provided by an **existing `portfolio-caddy`** container on the Linux VPS (part of the `portfolio-engine` stack on ports 80/443). This service does **not** bundle its own Caddy — instead the `web` container joins `portfolio-engine_portfolio-network` with the alias `strategy-stats-web`, and a site block is appended to the portfolio-caddy Caddyfile. Prerequisite: `portfolio-caddy` is already running on the host.

### Step 1 — Expose Redis on the MT5 VPS (TLS + requirepass)

The default Memurai / Redis-on-Windows install listens on `127.0.0.1:6379` plaintext. We wrap it with stunnel for TLS and add `requirepass`.

1. **Set `requirepass` in Redis config** (`redis.windows.conf` or Memurai equivalent):
   ```
   requirepass <strong-random-password>
   ```
   Restart the Redis/Memurai service. Sanity check: `redis-cli -a <password> XLEN conde_signals` works.

2. **Install stunnel for Windows** and create `stunnel.conf`:
   ```ini
   [redis-tls]
   accept = 0.0.0.0:6380
   connect = 127.0.0.1:6379
   cert = C:\stunnel\redis.pem
   ```
   Generate `redis.pem` with openssl (self-signed is fine — `rediss://` client will skip CN check if we set `ssl_cert_reqs=none`, but prefer a real cert from Let's Encrypt via `certbot` if the MT5 VPS has a public DNS name).

3. **Firewall**: Windows Defender Firewall → inbound rule → allow TCP 6380 **only** from the Linux VPS public IP. Block 6379 from the public interface (keep it loopback-only).

4. **Verify from the Linux VPS**:
   ```bash
   redis-cli --tls -h <mt5-vps-host> -p 6380 -a <password> XLEN conde_signals
   ```

### Step 2 — DNS for the dashboard

Point an A record (`stats.auto-trade.life`) at the Linux VPS public IP. portfolio-caddy needs the domain to be publicly resolvable to auto-issue a Let's Encrypt cert; ports 80 + 443 must be reachable from the internet (already true if portfolio-caddy is serving other sites).

### Step 3 — Bring up the app containers

```bash
ssh linux-vps
cd ~/kog_strategy && git pull
cd services/strategy-stats

cp .env.example .env
# Edit .env — at minimum:
#   POSTGRES_PASSWORD=<strong-random>
#   BASIC_AUTH_PASSWORD=<strong-random>
#   UPSTREAM_REDIS_URL=rediss://default:<redis-password>@<mt5-vps-host>:6380

docker compose up --build -d postgres
docker compose run --rm ingest alembic upgrade head
docker compose up -d ingest web
```

At this point `curl -fsS http://127.0.0.1:8080/healthz` on the VPS returns 200 — public HTTPS still needs the next step.

### Step 4 — Wire portfolio-caddy reverse proxy

`web` joined `portfolio-engine_portfolio-network` automatically (see `docker-compose.yml`), so it's reachable from inside the portfolio-caddy container as `strategy-stats-web:8080`. Now append a site block to the **portfolio-caddy Caddyfile** (the file mounted into the container — confirm via `docker inspect portfolio-caddy --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'`; typical path is `~/portfolio-engine/Caddyfile`):

```caddy
stats.auto-trade.life {
        encode gzip
        reverse_proxy strategy-stats-web:8080
        header {
                Strict-Transport-Security "max-age=31536000; includeSubDomains"
                X-Content-Type-Options "nosniff"
                Referrer-Policy "strict-origin-when-cross-origin"
        }
}
```

Then reload Caddy. `caddy reload` runs inside the container, so use the container-side config path (`/etc/caddy/Caddyfile` for the standard image):

```bash
docker exec portfolio-caddy caddy reload --config /etc/caddy/Caddyfile
# If reload doesn't pick up the new block (rare; symptom: log shows no
# "obtaining certificate" line for the new host), force a restart:
docker restart portfolio-caddy
```

A copy of this snippet lives at `services/strategy-stats/Caddyfile` for reference.

### Step 5 — Verify

```bash
# Ingest connected to all 6 streams
docker compose logs ingest | grep "StreamConsumer.*ready"

# Backfill landed
docker compose exec postgres psql -U stats -d strategy_stats \
  -c "SELECT count(*) FROM conde_signals;"
# Compare against MT5 VPS Redis: redis-cli --tls ... XLEN conde_signals

# Cert issued + public dashboard reachable
docker exec portfolio-caddy ls /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/ \
  | grep stats   # expect <your-stats-domain>
docker logs portfolio-caddy --since 2m 2>&1 \
  | grep -iE "stats|obtain|certificate" | tail -10
curl -sS https://stats.auto-trade.life/healthz                # {"status":"ok"}
curl -u admin:<basic-auth-pw> -sI https://stats.auto-trade.life/  # 200 OK
```

Pitfalls (learned the hard way on the first prod deploy):

- `caddy reload` reports `config is unchanged` if you edit a Caddyfile *outside* the bind mount. Always confirm the mount path before editing — the file at `/etc/caddy/Caddyfile` on the host is **not** what portfolio-caddy reads.
- HTTP→HTTPS 308 on `:80` does **not** prove the site block loaded — Caddy's global auto-HTTP-redirect fires for every host once any TLS site exists. Trust the cert directory and the `tls.obtain` log lines, not the redirect.
- Use `curl -vI` (not `-sI`) when debugging TLS — `-sI` swallows handshake errors.

### Updating

```bash
cd ~/kog_strategy && git pull
cd services/strategy-stats
docker compose up --build -d
docker compose run --rm ingest alembic upgrade head  # only if migrations changed
```

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
