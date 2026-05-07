"""Entry point — fan-out loop that writes one signal to all matching (account, symbol) pairs."""

import logging
import os
import sys
import threading
import time

import redis as redis_lib

# Add shared/ to import path so agent_lib is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared"))

from config import load_settings
from models import CondeSignal, _clean_channel_name
from agent_lib.redis_consumer import RedisConsumer
from signal_writer import SignalWriter
import outcome_publisher

log = logging.getLogger(__name__)


def run_once(consumer: RedisConsumer, writers_by_symbol: dict) -> None:
    result = consumer.consume_one(block_ms=5000)
    if result is None:
        return   # timeout — nothing in the stream

    msg_id, data = result

    # Parse message
    try:
        sig = CondeSignal.from_dict(data)
        sig.validate()
    except (KeyError, ValueError) as exc:
        log.error(
            "Bad message %s -- discarding: %s (channel_name=%s) | raw=%r",
            msg_id, exc, _clean_channel_name(str(data.get("channel_name") or "")), data,
        )
        consumer.ack(msg_id)   # avoid infinite requeue of a malformed message
        return

    # Fan-out: write to every writer matching the signal's symbol
    writers = writers_by_symbol.get(sig.symbol, [])
    if not writers:
        log.warning(
            "No writer configured for symbol=%s -- discarding msg %s",
            sig.symbol, msg_id,
        )
        consumer.ack(msg_id)
        return

    for writer in writers:
        try:
            writer.write(sig)
            log.info(
                "[%s/%s] Written  dir=%s  entry=%.5f  sl=%.5f  tps=%d  ts=%d",
                writer.account_id,
                writer.symbol,
                sig.direction,
                sig.entry_price,
                sig.sl,
                len(sig.tps),
                sig.timestamp,
            )
        except Exception as exc:
            log.error("[%s/%s] Write failed: %s", writer.account_id, writer.symbol, exc)

    consumer.ack(msg_id)


def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    out_dir = settings.mt5_signal_dir.resolve()   # follow symlinks
    out_dir.mkdir(parents=True, exist_ok=True)

    consumer = RedisConsumer(
        redis_url=settings.redis_url,
        stream=settings.redis_stream,
        group=settings.redis_group,
        consumer=settings.redis_consumer,
    )
    consumer.create_group_if_missing()

    # Outcome publisher — separate Redis client (decoupled from consumer's internals)
    # tails data/outcomes/*.json and XADDs to `conde_outcomes` for /stats to consume.
    outcomes_dir = out_dir / "outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    publisher_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
    threading.Thread(
        target=outcome_publisher.run,
        args=(publisher_redis, outcomes_dir),
        name="outcome-publisher",
        daemon=True,
    ).start()

    # Cross-product fan-out: one writer per (account, symbol) pair.
    # Indexed by symbol so run_once() can route a signal to the right subset.
    writers_by_symbol: dict = {}
    for sym in settings.mt5_symbols:
        writers_by_symbol[sym] = [
            SignalWriter(acc, sym, out_dir) for acc in settings.mt5_accounts
        ]

    log.info(
        "Started. Accounts=%s  Symbols=%s  Stream=%s  Dir=%s",
        settings.mt5_accounts,
        settings.mt5_symbols,
        settings.redis_stream,
        out_dir,
    )

    while True:
        try:
            run_once(consumer, writers_by_symbol)
        except Exception as exc:
            log.error("Loop error: %s", exc, exc_info=True)
            time.sleep(5)   # back off before retrying


if __name__ == "__main__":
    main()
