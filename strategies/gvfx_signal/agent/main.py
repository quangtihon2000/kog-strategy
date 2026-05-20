"""Entry point — fan-out loop that writes one signal to all matching (account, symbol) pairs."""

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
from models import GvfxSignal
from agent_lib.redis_consumer import RedisConsumer
from signal_writer import SignalWriter

log = logging.getLogger(__name__)


_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "y", "t"})


def handle_deactivate(
    consumer: RedisConsumer, writers_by_symbol: dict, msg_id, data: dict,
) -> None:
    """Rewrite signal files with active=false for the operator's cancel command.

    `symbol` in the message is optional — if present, only that symbol's files
    are deactivated; otherwise every configured symbol is cancelled.
    `close_all` (optional, default false) is forwarded to the signal file so
    the EA knows whether to also close open positions. Always ACKs: a
    deactivate that finds nothing to cancel is not an error.
    """
    sym = str(data.get("symbol", "")).strip()
    close_all = str(data.get("close_all", "")).strip().lower() in _TRUE_TOKENS
    if sym:
        targets = {sym: writers_by_symbol.get(sym, [])}
    else:
        targets = writers_by_symbol

    total = 0
    for symbol, writers in targets.items():
        for writer in writers:
            try:
                if writer.deactivate(close_all=close_all):
                    total += 1
                    log.info(
                        "[%s/%s] Signal DEACTIVATED by operator (msg %s, close_all=%s)",
                        writer.account_id, symbol, msg_id, close_all,
                    )
                else:
                    log.info(
                        "[%s/%s] Deactivate no-op — no active signal on disk",
                        writer.account_id, symbol,
                    )
            except Exception as exc:
                log.error("[%s/%s] Deactivate failed: %s", writer.account_id, symbol, exc)

    log.info(
        "Deactivate msg %s done — %d signal file(s) cancelled (close_all=%s)",
        msg_id, total, close_all,
    )
    consumer.ack(msg_id)


def run_once(consumer: RedisConsumer, writers_by_symbol: dict) -> None:
    result = consumer.consume_one(block_ms=5000)
    if result is None:
        return   # timeout — nothing in the stream

    msg_id, data = result

    #--- Control message: deactivate the current signal (operator /cancel_gvfx).
    #    Rewrites every (account, symbol) signal file with active=false so the
    #    EA stops opening new positions; open positions keep their TP/SL.
    if str(data.get("action", "")).lower() == "deactivate":
        handle_deactivate(consumer, writers_by_symbol, msg_id, data)
        return

    try:
        sig = GvfxSignal.from_dict(data)
        sig.validate()
    except (KeyError, ValueError) as exc:
        log.error("Bad message %s — discarding: %s | raw=%r", msg_id, exc, data)
        consumer.ack(msg_id)
        return

    writers = writers_by_symbol.get(sig.symbol, [])
    if not writers:
        log.warning(
            "No writer configured for symbol=%s — discarding msg %s",
            sig.symbol, msg_id,
        )
        consumer.ack(msg_id)
        return

    for writer in writers:
        try:
            writer.write(sig)
            log.info(
                "[%s/%s] Written  dir=%s  target=%.5f  step=%d  tp=%d  low=%.5f  high=%.5f  ts=%d",
                writer.account_id,
                writer.symbol,
                sig.direction,
                sig.target,
                sig.step,
                sig.tp,
                sig.low,
                sig.high,
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

    out_dir = settings.mt5_signal_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    consumer = RedisConsumer(
        redis_url=settings.redis_url,
        stream=settings.redis_stream,
        group=settings.redis_group,
        consumer=settings.redis_consumer,
    )
    consumer.create_group_if_missing()

    outcomes_dir = out_dir / "outcomes"
    outcomes_dir.mkdir(parents=True, exist_ok=True)
    publisher_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)
    threading.Thread(
        target=outcome_publisher.run,
        args=(publisher_redis, outcomes_dir),
        name="outcome-publisher",
        daemon=True,
    ).start()

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
        except KeyboardInterrupt:
            log.info("Shutdown requested — exiting")
            return
        except Exception as exc:
            log.error("Loop error: %s", exc, exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
