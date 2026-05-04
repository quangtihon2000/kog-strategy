"""Tiny heartbeat publisher for Python agents.

Drop into an agent's main loop to opt into the telegram_monitor's heartbeat
alert. Sets a Redis key with TTL; if the agent dies or stalls, the key
expires and the monitor pages the operator.

    from agent_lib.heartbeat import Heartbeat
    hb = Heartbeat(redis_client, vps="vps-main", service="zone_signal")
    hb.beat()  # call once per loop iteration

Design:
    * Sync — every existing agent uses sync redis. The monitor side is async
      and reads the same keys via redis.asyncio.
    * Skipped silently on failure — heartbeat must never crash the agent.
    * TTL = 3 × interval by default, so a single missed beat doesn't alert.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import redis

log = logging.getLogger(__name__)


@dataclass
class Heartbeat:
    client: redis.Redis
    vps: str
    service: str
    ttl_s: int = 180  # ~3× a 60s loop; tune per agent

    @property
    def key(self) -> str:
        return f"agent:heartbeat:{self.vps}:{self.service}"

    def beat(self) -> None:
        """Stamp the heartbeat. Swallow errors so agent stays up."""
        try:
            self.client.set(self.key, str(time.time()), ex=self.ttl_s)
        except Exception as e:
            log.debug("heartbeat publish failed (%s): %s", self.key, e)
