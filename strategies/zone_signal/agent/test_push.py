"""Quick test script — push a sample ZoneSignal message via redis_client_v2."""

import logging

from redis_client_v2 import push_zone_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ocr_data = {
    "symbol":        "XAUUSD",
    "redbox_upper":  4630.0,
    "redbox_lower":  4604.0,
    "targets_above": [4637.0, 4655.0],
    "targets_below": [4595.0, 4570.0, 4560.0],
}

ok = push_zone_signal(ocr_data)
print(f"{'✅ Pushed' if ok else '❌ Failed / duplicate'} — payload: {ocr_data}")
