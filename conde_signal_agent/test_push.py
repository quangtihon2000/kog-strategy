"""Quick test script — push a sample CondeSignal message via redis_client_v2."""

import logging
import time

from redis_client_v2 import push_conde_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sample = {
    "timestamp":   int(time.time()),
    "symbol":      "XAUUSD",
    "direction":   "BUY",
    "entry_price": 2350.00,
    "sl":          2340.00,
    "tps":         [2355.0, 2360.0, 2365.0],
}

ok = push_conde_signal(sample)
print(f"{'OK Pushed' if ok else 'FAIL / duplicate'} — payload: {sample}")
