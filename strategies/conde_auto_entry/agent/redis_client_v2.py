"""Producer-side helper: push a CondeSignal dict to the Redis Stream."""

import hashlib
import json
import logging
import re
import time
import unicodedata

import redis

from config import load_settings

logger = logging.getLogger(__name__)


def _clean_channel_name(s: str) -> str:
    """NFKC-normalize then strip non-printable-ASCII (emojis, etc.)."""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[^\x20-\x7E]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

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
        channel_id   : int           Telegram channel id (BIGINT)
        channel_name : str           Telegram channel display name
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

        channel_name = _clean_channel_name(str(data.get("channel_name", "")))
        if not channel_name:
            logger.error("channel_name is required and must be non-empty")
            return False

        channel_id_raw = data.get("channel_id")
        if channel_id_raw is None or str(channel_id_raw).strip() == "":
            logger.error("channel_id is required (Telegram channel BIGINT)")
            return False
        try:
            channel_id = int(channel_id_raw)
        except (TypeError, ValueError):
            logger.error(f"channel_id must be int-castable, got {channel_id_raw!r}")
            return False

        entry_price = str(data["entry_price"])
        sl = str(data["sl"])

        # tps có thể rỗng — consumer EA fallback ATR/Fixed-TP nếu được phép
        tps_list = data.get("tps", []) or []
        tps = ",".join(str(x) for x in tps_list)

        timestamp = int(data.get("timestamp", int(time.time())))

        conde_signal = {
            "timestamp":    str(timestamp),   # Redis Stream fields must be strings
            "symbol":       symbol,
            "direction":    direction,
            "entry_price":  entry_price,
            "sl":           sl,
            "tps":          tps,
            "channel_id":   str(channel_id),
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
