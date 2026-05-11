# telegram-listener

Telegram signal listener service for `kog-strategy`. Reads messages from
whitelisted Telegram channels via a Telethon **user** account, runs them
through a 4-tier cascade (metadata → heuristic → regex → LLM → validator),
and pushes validated signals onto Redis Streams for downstream workers.

> Spec: [`specs/telegram-signal-listener.md`](../../specs/telegram-signal-listener.md) — read this before
> changing the pipeline. Section numbers in code comments refer to that doc.

## Status

Phase 1 — scaffold only. Tier logic, Telethon client, Redis writer, tests
and Docker are deliberately not yet implemented; each will land in its own
focused PR.

## Layout

```
services/telegram-listener/
├── pyproject.toml          # uv-managed, Python >=3.11
├── channels.yaml.example   # channel + parser + LLM config (spec §8)
├── .env.example            # process env vars (spec §5.1)
└── src/tg_listener/
    ├── __init__.py
    ├── models.py           # Pydantic v2: Signal, ParsedSignalFields, ValidationResult
    ├── config.py           # Settings + channels.yaml loader
    ├── exceptions.py       # domain exceptions
    ├── logging_setup.py    # structlog JSON
    ├── tiers/              # tier0..tier4 (spec §5.2-5.6)
    ├── parsers/            # per-channel regex + dispatcher (spec §5.4)
    └── storage/            # Redis Streams + Postgres adapters (spec §5.7, §7)
```

## Dev setup

```bash
cd services/telegram-listener
cp .env.example .env          # fill in TELEGRAM_API_ID, ANTHROPIC_API_KEY, ...
cp channels.yaml.example channels.yaml

uv sync                       # install runtime + dev deps
uv run python -c "import tg_listener; print(tg_listener.__version__)"
```

To run lint / type / tests once those land:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

## Operational notes

- `*.session` files contain full Telegram account credentials. They are
  gitignored and must live on a persistent volume — see spec §11.
- The service is single-replica only (Telethon sessions cannot be shared).
- Configuration is split: env vars for secrets / paths, `channels.yaml` for
  channel + parser + LLM behavior. Reload story is documented in spec §10.

## Next sessions

Each tier ships in its own PR:

1. Tier 0 metadata filter + Telethon client skeleton.
2. Tier 1 heuristic + audit sampling.
3. Tier 2 regex parsers (first 2 channels).
4. Tier 4 validator + symbol/price providers.
5. Redis Streams writer + Prometheus metrics.
6. Tier 3 LLM extractor (Phase 2).
