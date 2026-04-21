"""Quick test script — push a sample CondeSignal message via redis_client_v2."""

import logging
import time

from redis_client_v2 import push_conde_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sample = {
    "timestamp":   int(time.time()),
    "symbol":      "XAUUSD",
    "direction":   "BUY",
    "entry_price": 4786.00,
    "sl":          4760.00,
    "tps":         [4796.0, 4800.0, 4810.0],
}

ok = push_conde_signal(sample)
print(f"{'OK Pushed' if ok else 'FAIL / duplicate'} — payload: {sample}")
