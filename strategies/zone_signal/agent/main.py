"""Entry point — fan-out loop that writes one signal to all configured accounts."""

import logging
import os
import sys
import threading
import time

import redis as redis_lib

# Add shared/ to import path so agent_lib is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

import outcome_publisher
from config import load_settings
from models import ZoneSignal
from agent_lib.redis_consumer import RedisConsumer
from signal_writer import SignalWriter

log = logging.getLogger(__name__)


_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "y", "t"})


def handle_deactivate(consumer: RedisConsumer, writers: list, msg_id, data: dict) -> None:
    """Rewrite every account's signal file with active=false for /cancel_zone.

    `close_all` (optional, default false) is forwarded to the signal file so
    the EA knows whether to also close open positions. Always ACKs — a
    deactivate that finds nothing to cancel is not an error.
    """
    close_all = str(data.get("close_all", "")).strip().lower() in _TRUE_TOKENS
    total = 0
    for writer in writers:
        try:
            if writer.deactivate(close_all=close_all):
                total += 1
                log.info("[%s] Signal DEACTIVATED by operator (msg %s, close_all=%s)",
                         writer.account_id, msg_id, close_all)
            else:
                log.info("[%s] Deactivate no-op — no active signal on disk",
                         writer.account_id)
        except Exception as exc:
            log.error("[%s] Deactivate failed: %s", writer.account_id, exc)

    log.info("Deactivate msg %s done — %d signal file(s) cancelled (close_all=%s)",
             msg_id, total, close_all)
    consumer.ack(msg_id)


def run_once(consumer: RedisConsumer, writers: list) -> None:
    result = consumer.consume_one(block_ms=5000)
    if result is None:
        return   # timeout — nothing in the stream

    msg_id, data = result

    # Control message: deactivate the current signal (operator /cancel_zone).
    # Rewrites every account's signal file with active=false so the EA stops
    # opening new positions; open positions keep their TP/SL and trailing.
    if str(data.get("action", "")).lower() == "deactivate":
        handle_deactivate(consumer, writers, msg_id, data)
        return

    # Parse message
    try:
        sig = ZoneSignal.from_dict(data)
        sig.validate()
    except (KeyError, ValueError) as exc:
        log.error("Bad message %s — discarding: %s | raw=%r", msg_id, exc, data)
        consumer.ack(msg_id)   # avoid infinite requeue of a malformed message
        return

    # Fan-out: write to every configured account
    for writer in writers:
        try:
            writer.write(sig)
            log.info(
                "[%s] Written  symbol=%s  zone=[%.5f, %.5f]  ts=%d",
                writer.account_id,
                sig.symbol,
                sig.redbox_lower,
                sig.redbox_upper,
                sig.timestamp,
            )
        except Exception as exc:
            log.error("[%s] Write failed: %s", writer.account_id, exc)

    consumer.ack(msg_id)


def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Ensure output directory exists (handles Windows symlinks correctly)
    sig_dir = settings.mt5_signal_dir.resolve()   # follow symlinks to real path
    if not sig_dir.exists():
        sig_dir.mkdir(parents=True, exist_ok=True)
    settings.mt5_signal_dir = sig_dir

    consumer = RedisConsumer(
        redis_url=settings.redis_url,
        stream=settings.redis_stream,
        group=settings.redis_group,
        consumer=settings.redis_consumer,
    )
    consumer.create_group_if_missing()

    outcomes_dir = settings.mt5_signal_dir / "outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    publisher_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
    threading.Thread(
        target=outcome_publisher.run,
        args=(publisher_redis, outcomes_dir),
        name="outcome-publisher",
        daemon=True,
    ).start()

    writers = [SignalWriter(acc, settings.mt5_signal_dir) for acc in settings.mt5_accounts]
    log.info(
        "Started. Accounts: %s  |  Stream: %s  |  Dir: %s",
        settings.mt5_accounts,
        settings.redis_stream,
        settings.mt5_signal_dir,
    )

    while True:
        try:
            run_once(consumer, writers)
        except Exception as exc:
            log.error("Loop error: %s", exc, exc_info=True)
            time.sleep(5)   # back off before retrying


if __name__ == "__main__":
    main()
