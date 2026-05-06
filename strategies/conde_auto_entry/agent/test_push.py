"""Quick test script — push a sample CondeSignal message via redis_client_v2."""

import logging
import time

from redis_client_v2 import push_conde_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sample = {
    "timestamp":   int(time.time()),
    "symbol":      "XAUUSD",
    "direction":   "BUY",
    "entry_price": 4567.0,
    "sl":          4557.0,
    "tps":         [4573.0, 4577.0, 4581.0, 4587.0],
    "channel_name": "test_channel_alpha",
}
ok = push_conde_signal(sample)
print(f"{'OK Pushed' if ok else 'FAIL / duplicate'} — payload: {sample}")
