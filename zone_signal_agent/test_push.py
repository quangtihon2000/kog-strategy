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
    "redbox_upper":  "4810.00",
    "redbox_lower":  "4790.00",
    "targets_above": "4825.0,4835.0",
    "targets_below": "4780.0,4770.0",
}

msg_id = r.xadd(STREAM, msg)
print(f"✅ Published to '{STREAM}' — id: {msg_id}")
print(f"   Payload: {msg}")
