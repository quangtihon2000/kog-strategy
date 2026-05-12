"""Outcome publisher — file → Redis Stream `conde_outcomes`.

EA writes one JSON file per closed position to `data/outcomes/{position_id}.json`.
This worker tails that directory and publishes each new file to a Redis Stream so
`/stats` (and any future reader) can XRANGE a durable, queryable history.

Design choices:
- Files older than `PURGE_AFTER_S` are deleted hourly to bound disk usage.
  Redis Stream (maxlen=1M, ~3y) is the long-term source of truth; files
  are a short-window backup.
- Dedup uses Redis SET `conde_outcomes:ingested` (SISMEMBER + SADD). The SET
  TTL (90d) outlives file retention (15d) so a stale file can't be re-ingested.
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

STREAM_NAME      = "conde_outcomes"
INGESTED_SET_KEY = "conde_outcomes:ingested"
INGESTED_TTL_SEC = 90 * 24 * 3600   # 90 days — keeps SET bounded
STREAM_MAXLEN    = 1_000_000        # ~3y at 1k/day, ~200MB ceiling
POLL_INTERVAL    = 5.0
PURGE_AFTER_S    = 15 * 24 * 3600   # delete outcome files older than this
PURGE_INTERVAL_S = 3600             # how often the daemon runs the sweep


def _flatten(payload: dict) -> dict:
    """Redis Stream fields must be strings — coerce everything."""
    return {k: str(v) for k, v in payload.items()}


def _publish_one(r: redis_lib.Redis, path: Path) -> bool:
    """Read one outcome file and XADD if not already ingested. Returns True if pushed."""
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
    # Refresh TTL on every write so the set never expires while activity continues.
    r.expire(INGESTED_SET_KEY, INGESTED_TTL_SEC)
    log.info("[outcome→stream] published pos=%s reason=%s",
             member, payload.get("close_reason"))
    return True


def _scan_once(r: redis_lib.Redis, outcomes_dir: Path) -> int:
    """Walk all *.json in the outcomes dir, publish new ones. Returns count pushed."""
    if not outcomes_dir.exists():
        return 0
    pushed = 0
    for path in sorted(outcomes_dir.glob("*.json")):
        try:
            if _publish_one(r, path):
                pushed += 1
        except redis_lib.RedisError:
            raise   # bubble up so caller can back off
        except Exception as exc:
            log.error("Unexpected error publishing %s: %s", path.name, exc, exc_info=True)
    return pushed


def _purge_old(outcomes_dir: Path, now_wall: float) -> int:
    """Delete *.json older than PURGE_AFTER_S by mtime. Returns count removed.

    Uses mtime, not filename: position_id is an MT5 ticket — it encodes no date.
    """
    if not outcomes_dir.exists():
        return 0
    cutoff = now_wall - PURGE_AFTER_S
    removed = 0
    for path in outcomes_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue   # raced with another deleter — fine
        except OSError as exc:
            log.warning("purge: failed to remove %s: %s", path.name, exc)
    if removed:
        log.info("purge: removed %d outcome file(s) older than %d days",
                 removed, PURGE_AFTER_S // 86400)
    return removed


def run(redis_client: redis_lib.Redis, outcomes_dir: Path) -> None:
    """Daemon loop — call once, never returns. Safe to wrap in threading.Thread."""
    log.info("Outcome publisher started — dir=%s stream=%s", outcomes_dir, STREAM_NAME)
    # monotonic for cadence (immune to wall-clock jumps), wall time for mtime compare.
    last_purge = 0.0
    while True:
        try:
            _scan_once(redis_client, outcomes_dir)
            now_mono = time.monotonic()
            if now_mono - last_purge >= PURGE_INTERVAL_S:
                _purge_old(outcomes_dir, time.time())
                last_purge = now_mono
        except redis_lib.RedisError as exc:
            log.warning("Redis error during outcome scan — will retry: %s", exc)
        except Exception as exc:
            log.error("Outcome publisher loop error: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# CLI: one-shot backfill
# ---------------------------------------------------------------------------

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Publish outcome files to Redis Stream")
    parser.add_argument("--backfill", action="store_true",
                        help="Scan once and exit (instead of running the daemon loop)")
    parser.add_argument("--outcomes-dir", default=None,
                        help="Override outcomes directory (default: settings.mt5_signal_dir/outcomes)")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))
    from config import load_settings   # local import — avoids forcing settings on importers

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
