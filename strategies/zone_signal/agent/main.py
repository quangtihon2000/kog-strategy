"""Entry point — fan-out loop that writes one signal to all configured accounts."""

import logging
import os
import sys
import time

# Add shared/ to import path so agent_lib is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from config import load_settings
from models import ZoneSignal
from agent_lib.redis_consumer import RedisConsumer
from signal_writer import SignalWriter

log = logging.getLogger(__name__)


def run_once(consumer: RedisConsumer, writers: list) -> None:
    result = consumer.consume_one(block_ms=5000)
    if result is None:
        return   # timeout — nothing in the stream

    msg_id, data = result

    # Parse message
    try:
        sig = ZoneSignal.from_dict(data)
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
