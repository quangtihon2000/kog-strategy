"""Producer-side helper: push a CondeSignal dict to the Redis Stream."""

import hashlib
import json
import logging
import time

import redis

from config import load_settings

logger = logging.getLogger(__name__)

try:
    _settings = load_settings()
    redis_client = redis.from_url(
        _settings.redis_url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    _stream_name = _settings.redis_stream
    logger.info(f"Redis client initialized connecting to {_settings.redis_url}")
except Exception as e:
    logger.error(f"Failed to initialize Redis client: {e}")
    redis_client = None
    _stream_name = "conde_signals"


def push_conde_signal(data: dict) -> bool:
    """
    Format and push a CondeAutoEntry signal to the Redis Stream.

    Expected `data` keys:
        symbol       : str           e.g. "XAUUSD"
        direction    : str           "BUY" or "SELL"
        entry_price  : float | str
        sl           : float | str
        tps          : list[float]   e.g. [2355.0, 2360.0, 2365.0]
        timestamp    : int (optional — defaults to now)
    """
    if not redis_client:
        logger.error("Redis client not available. Skipping push.")
        return False

    try:
        symbol = str(data.get("symbol", "")).upper()
        if symbol == "GOLD":
            symbol = "XAUUSD"
        if not symbol:
            logger.error("symbol is required")
            return False

        direction = str(data.get("direction", "")).upper()
        if direction not in ("BUY", "SELL"):
            logger.error(f"direction must be BUY or SELL, got {direction!r}")
            return False

        channel_name = str(data.get("channel_name", "")).strip()
        if not channel_name:
            logger.error("channel_name is required and must be non-empty")
            return False

        try:
            entry_price_f = float(data["entry_price"])
            sl_f = float(data["sl"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.error(f"entry_price/sl parse failed: {exc} (channel_name={channel_name!r})")
            return False
        if entry_price_f <= 0:
            logger.error(f"entry_price must be > 0, got {entry_price_f} (channel_name={channel_name!r})")
            return False
        if sl_f <= 0:
            logger.error(f"sl must be > 0, got {sl_f} (channel_name={channel_name!r})")
            return False
        entry_price = str(entry_price_f)
        sl = str(sl_f)

        tps_list = data.get("tps", [])
        if not tps_list:
            logger.error("tps is empty")
            return False
        try:
            tps_floats = [float(x) for x in tps_list]
        except (TypeError, ValueError) as exc:
            logger.error(f"tps parse failed: {exc} (channel_name={channel_name!r})")
            return False
        for i, tp in enumerate(tps_floats):
            if tp <= 0:
                logger.error(f"tps[{i}] must be > 0, got {tp} (channel_name={channel_name!r})")
                return False
        tps = ",".join(str(x) for x in tps_floats)

        timestamp = int(data.get("timestamp", int(time.time())))
        if timestamp <= 0:
            logger.error(f"timestamp must be > 0, got {timestamp} (channel_name={channel_name!r})")
            return False

        conde_signal = {
            "timestamp":    str(timestamp),   # Redis Stream fields must be strings
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  entry_price,
            "sl":           sl,
            "tps":          tps,
            "channel_name": channel_name,
        }

        # Dedup — hash content excluding timestamp
        dedup_fields = {k: v for k, v in conde_signal.items() if k != "timestamp"}
        content_hash = hashlib.md5(
            json.dumps(dedup_fields, sort_keys=True).encode()
        ).hexdigest()
        dedup_key = f"dedup:conde_signals:{content_hash}"

        if not redis_client.set(dedup_key, 1, ex=60, nx=True):
            logger.warning(f"Duplicate signal skipped: {content_hash}")
            return False

        stream_id = redis_client.xadd(
            _stream_name,
            conde_signal,
            maxlen=1_000_000,
            approximate=True,
        )

        logger.info(f"Pushed to stream — id: {stream_id}, payload: {conde_signal}")
        return True

    except Exception as e:
        logger.error(f"Error pushing signal to Redis: {e}")
        return False
