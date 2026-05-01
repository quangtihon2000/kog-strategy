"""Quick test script — push a sample ZoneSignal message via redis_client_v2."""

import logging

from redis_client_v2 import push_zone_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ocr_data = {
    "symbol":        "XAUUSD",
    "redbox_upper":  4600.00,
    "redbox_lower":  4560.00,
    "targets_above": [4650.0, 4700.0],
    "targets_below": [4540.0, 4500.0],
    "support":       [4740.0, 4720.0],
    "resistance":    [4870.0, 4890.0],
}

ok = push_zone_signal(ocr_data)
print(f"{'✅ Pushed' if ok else '❌ Failed / duplicate'} — payload: {ocr_data}")
