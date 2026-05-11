"""Outcome publisher — file → Redis Stream `zone_outcomes`.

EA writes one JSON file per closed position to `data/outcomes/{position_id}.json`.
This worker tails that directory and publishes each new file to a Redis Stream so
strategy-stats (and any future reader) can XRANGE a durable, queryable history.

Design choices:
- Files are NEVER deleted — they are the local working copy / replay source.
- Dedup uses Redis SET `zone_outcomes:ingested` (SISMEMBER + SADD).
- Failures (Redis down, malformed JSON) log + skip; next poll retries.
- Runs as a daemon thread alongside the main XREADGROUP loop.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import redis as redis_lib

log = logging.getLogger(__name__)

STREAM_NAME      = "zone_outcomes"
INGESTED_SET_KEY = "zone_outcomes:ingested"
INGESTED_TTL_SEC = 90 * 24 * 3600
STREAM_MAXLEN    = 1_000_000
POLL_INTERVAL    = 5.0


def _flatten(payload: dict) -> dict:
    return {k: str(v) for k, v in payload.items()}


def _publish_one(r: redis_lib.Redis, path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Skipping unreadable outcome file %s: %s", path.name, exc)
        return False

    pid = payload.get("position_id")
    if pid is None:
        log.warning("Outcome %s missing position_id — skipping", path.name)
        return False

    member = str(pid)
    if r.sismember(INGESTED_SET_KEY, member):
        return False

    r.xadd(STREAM_NAME, _flatten(payload), maxlen=STREAM_MAXLEN, approximate=True)
    r.sadd(INGESTED_SET_KEY, member)
    r.expire(INGESTED_SET_KEY, INGESTED_TTL_SEC)
    log.info("[outcome→stream] published pos=%s reason=%s",
             member, payload.get("close_reason"))
    return True


def _scan_once(r: redis_lib.Redis, outcomes_dir: Path) -> int:
    if not outcomes_dir.exists():
        return 0
    pushed = 0
    for path in sorted(outcomes_dir.glob("*.json")):
        try:
            if _publish_one(r, path):
                pushed += 1
        except redis_lib.RedisError:
            raise
        except Exception as exc:
            log.error("Unexpected error publishing %s: %s", path.name, exc, exc_info=True)
    return pushed


def run(redis_client: redis_lib.Redis, outcomes_dir: Path) -> None:
    log.info("Outcome publisher started — dir=%s stream=%s", outcomes_dir, STREAM_NAME)
    while True:
        try:
            _scan_once(redis_client, outcomes_dir)
        except redis_lib.RedisError as exc:
            log.warning("Redis error during outcome scan — will retry: %s", exc)
        except Exception as exc:
            log.error("Outcome publisher loop error: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Publish Zone outcome files to Redis Stream")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--outcomes-dir", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))
    from config import load_settings

    settings = load_settings()
    out_dir = Path(args.outcomes_dir) if args.outcomes_dir else (settings.mt5_signal_dir.resolve() / "outcomes")
    r = redis_lib.from_url(settings.redis_url, decode_responses=True)

    if args.backfill:
        pushed = _scan_once(r, out_dir)
        log.info("Backfill complete — pushed %d new outcomes", pushed)
        return 0

    run(r, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
