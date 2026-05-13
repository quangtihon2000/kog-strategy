import hashlib
import json
import logging
import time

import redis

from config import load_settings

logger = logging.getLogger(__name__)

# Initialize Redis client
try:
    _settings = load_settings()
    redis_client = redis.from_url(
        _settings.redis_url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    logger.info(f"Redis client initialized connecting to {_settings.redis_url}")
except Exception as e:
    logger.error(f"Failed to initialize Redis client: {e}")
    redis_client = None


def push_zone_signal(ocr_data: dict) -> bool:
    """
    Formats the OCR data and pushes to Redis Stream (XADD).

    Sample target format:
    {
        "symbol":        "XAUUSD",
        "redbox_upper":  "2350.00",
        "redbox_lower":  "2340.00",
        "targets_above": "2360.0,2370.0",
        "targets_below": "2330.0,2320.0",
        "support":       "2300.0,2290.0",
        "resistance":    "2400.0,2410.0",
    }
    """
    if not redis_client:
        logger.error("Redis client not available. Skipping push.")
        return False

    try:
        # 1. Format Symbol
        symbol = ocr_data.get("symbol", "GOLD")
        if symbol.upper() == "GOLD":
            symbol = "XAUUSD"

        # 2. Extract and format numerical fields as strings
        redbox_upper = str(ocr_data.get("redbox_upper", ""))
        redbox_lower = str(ocr_data.get("redbox_lower", ""))

        # 3. Join lists into comma-separated strings
        targets_above = ",".join(map(str, ocr_data.get("targets_above", [])))
        targets_below = ",".join(map(str, ocr_data.get("targets_below", [])))
        support      = ",".join(map(str, ocr_data.get("support", [])))
        resistance   = ",".join(map(str, ocr_data.get("resistance", [])))

        # 4. Build the final payload (all values must be strings for Redis Stream)
        # `timestamp` is unix epoch seconds — preserved end-to-end so the EA's
        # position comment, the agent JSON file, and strategy-stats all use the
        # same value as the join key.
        zone_signal = {
            "symbol":        symbol,
            "redbox_upper":  redbox_upper,
            "redbox_lower":  redbox_lower,
            "targets_above": targets_above,
            "targets_below": targets_below,
            "support":       support,
            "resistance":    resistance,
            "timestamp":     str(int(time.time())),
        }

        # 5. Dedup — hash nội dung, exclude timestamp để tránh false miss
        dedup_fields = {k: v for k, v in zone_signal.items() if k != "timestamp"}
        content_hash = hashlib.md5(
            json.dumps(dedup_fields, sort_keys=True).encode()
        ).hexdigest()
        dedup_key = f"dedup:zone_signals:{content_hash}"

        if not redis_client.set(dedup_key, 1, ex=60, nx=True):
            logger.warning(f"Duplicate signal skipped: {content_hash}")
            return False

        # 6. Push to Redis Stream
        stream_id = redis_client.xadd(
            "zone_signals",
            zone_signal,
            maxlen=1000,
            approximate=True,
        )

        logger.info(f"Pushed to stream — id: {stream_id}, payload: {zone_signal}")
        return True

    except Exception as e:
        logger.error(f"Error pushing signal to Redis: {e}")
        return False