"""Quick test script — push a sample ZoneSignal message via redis_client_v2."""

import logging

from redis_client_v2 import push_zone_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ocr_data = {
    "symbol":        "XAUUSD",
    "redbox_upper":  4810.00,
    "redbox_lower":  4790.00,
    "targets_above": [4835.0, 4850.0],
    "targets_below": [4770.0, 4755.0],
    "support":       [4740.0, 4720.0],
    "resistance":    [4870.0, 4890.0],
}

ok = push_zone_signal(ocr_data)
print(f"{'✅ Pushed' if ok else '❌ Failed / duplicate'} — payload: {ocr_data}")
