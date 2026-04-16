"""Quick test script — push a sample ZoneSignal message to the Redis stream."""

import os

import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ["REDIS_URL"]
STREAM = "zone_signals"

r = redis.from_url(REDIS_URL, decode_responses=True)

msg = {
    "symbol":        "XAUUSD",
    "redbox_upper":  "2350.00",
    "redbox_lower":  "2340.00",
    "targets_above": "2360.0,2370.0,2380.0",
    "targets_below": "2330.0,2320.0,2310.0",
}

msg_id = r.xadd(STREAM, msg)
print(f"✅ Published to '{STREAM}' — id: {msg_id}")
print(f"   Payload: {msg}")
