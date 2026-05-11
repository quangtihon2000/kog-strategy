# telegram-listener — Handoff (pause 2026-05-11)

Tạm dừng feature ở đây. File này tóm tắt trạng thái để quay lại sau.

## Trạng thái

- Branch: `claude/telegram-listener-induction`
- PR: [#59](https://github.com/quangtihon2000/kog-strategy/pull/59) — OPEN, mergeable, chưa CI, chưa review
- Scope: service mới `services/telegram-listener/` (96 files, ~12.5k LOC)
- Test: 353 pass + 37 skip (skip do thiếu `DATABASE_URL`); ruff sạch; mypy sạch (trừ `types-PyYAML` pre-existing)

## Đã xong

5-tier cascade + self-bootstrapping regex parser induction:

- **Tier 0–4**: metadata / heuristic / regex (JSONB regex_table) / LLM (Anthropic + Ollama + stub) / validator
- **Induction pipeline**: SampleCollector (SHA256 dedup + MinHash k=5/M=64 near-dup) → Synthesizer (LLM) → Evaluator (ThreadPoolExecutor timeout guard) → Alerts (low_match_rate hook)
- **Parser versioning**: proposed/shadow/active/rejected/retired, partial unique index 1 active/channel
- **CLI**: `tg-listener parser induce|list|diff|approve|reject|stats`, `channel set-auto-approve`
- **DB**: asyncpg + SQLAlchemy 2.0 async + Alembic async migrations (001 initial, 002 seed, 003 widen `parsed_by`)
- **Deploy**: docker-compose (postgres:15-alpine + redis:7-alpine)

## PR review — fixed (commit `1311c33`)

| Severity | Item | File |
|---|---|---|
| 🔴 B1 | `session.commit()` thiếu khi `auto_approve=False` → proposed parser bị rollback silently | [parser_cmd.py](src/tg_listener/cli/parser_cmd.py) |
| 🔴 B2 | `parsed_by` CHECK constraint chỉ allow `('regex','llm')` nhưng code dùng `'tier3_llm'` → CheckViolation | [003_widen_samples_parsed_by.py](src/tg_listener/db/migrations/versions/003_widen_samples_parsed_by.py) + [models.py](src/tg_listener/models.py) |
| 🟡 C4 | `anthropic_api_key: str \| None` → đổi sang `SecretStr` | [config.py](src/tg_listener/config.py) |

## TODO khi quay lại

### Concerns còn pending (không block merge, follow-up PR)
- **C1** — `maybe_emit_low_match_rate(parser_id=None, ...)` gọi TRƯỚC `propose()` ở [parser_cmd.py:140-145](src/tg_listener/cli/parser_cmd.py#L140-L145) → `parser_id` luôn None trong log. Move xuống sau `propose()`.
- **C2** — `SampleCollector.collect()` ở [sample_collector.py:79](src/tg_listener/induction/sample_collector.py#L79) recompute MinHash fingerprint cho toàn bộ `recent_n=100` samples mỗi insert. DB đã có cột `minhash` — đọc thay vì recompute.
- **C3** — `AsyncAnthropic` ở [anthropic.py:49-55](src/tg_listener/tiers/llm/anthropic.py#L49-L55) không set `httpx.Timeout(connect=, read=)`. Bổ sung để tránh TCP hang trước khi `asyncio.wait_for` kick in.
- **Nit** — `regex_engine.parse()` không cache `re.compile`; `docker-compose.yml` thiếu healthcheck postgres/redis.

### Deferred items (từ PR body — chưa làm)
1. **Cascade orchestrator wiring** — chưa có file glue Tier 3 → SampleCollector → trigger induction loop.
2. **Telethon listener entry point** — chưa có `listener.py`/`main.py` để service chạy thực sự; PR hiện tại chỉ có CLI + library.
3. **Spec §5.7 / §9 / §10** — đọc [specs/telegram-signal-listener.md](../../specs/telegram-signal-listener.md) khi quay lại.
4. **Real alert sink** — `alerts.py` hiện chỉ log structured warning, chưa wire vào Slack/Telegram/Sentry.

### Suggested next session
- **Session 7A**: wire Telethon listener entry point (Tier 0 metadata → cascade → publish). Cần `TELEGRAM_API_ID/HASH` trong `.env.example`.
- **Session 7B**: cascade orchestrator (gọi tier0→tier4, gate auto-approve, trigger SampleCollector cho tier3 results).
- **Session 7C**: gom C1+C2+C3 + nits vào 1 PR follow-up.

## Cách resume

```bash
cd services/telegram-listener
git checkout claude/telegram-listener-induction
git pull
uv sync
# DB test (optional):
docker compose up -d postgres redis
export DATABASE_URL=postgresql+asyncpg://tg:tg@localhost:5432/tg_listener
uv run alembic upgrade head
uv run pytest
```

## Deployment note

Service deploy trên **Linux VPS riêng**, KHÔNG phải Windows MT5 VPS. Docker/systemd, không NSSM/PowerShell. Xem memory `project_telegram_listener_deploy.md`.
