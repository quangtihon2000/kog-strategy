"""Quick test script — push a sample CondeSignal message via redis_client_v2."""

import logging
import time

from redis_client_v2 import push_conde_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sample = {
    "timestamp":   int(time.time()),
    "symbol":      "XAUUSD",
    "direction":   "BUY",
    "entry_price": 4747.0,
    "sl":          4742.0,
    "tps":         [4754.0, 4763.0, 4779.0],
}
ok = push_conde_signal(sample)
print(f"{'OK Pushed' if ok else 'FAIL / duplicate'} — payload: {sample}")
