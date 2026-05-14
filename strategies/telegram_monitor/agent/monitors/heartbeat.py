"""Opt-in heartbeat monitor via Redis keys.

Each agent is expected to periodically `SET agent:heartbeat:{vps}:{svc} <ts> EX <ttl>`.
This monitor only alerts for services we've *ever seen publish* — that way a
service that doesn't use the heartbeat helper stays silent and the monitor
costs only one MGET per tick.

Phase 0: redis is local on the same VPS, so a single connection is fine.
Phase 1 (multi-VPS): each VPS will likely have its own redis; revisit by
keying the redis client per-vps.
"""

from __future__ import annotations

import logging
import time

import redis.asyncio as redis
from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import Transport  # noqa: F401  — kept for symmetry with other monitors

log = logging.getLogger(__name__)

# Services that have *ever* published a heartbeat. Until then we stay silent
# (opt-in pattern), so legacy agents don't get spammed.
_SEEN: set[tuple[str, str]] = set()
# Last successful heartbeat timestamp, for the alert message.
_LAST_TS: dict[tuple[str, str], float] = {}

_redis_client: redis.Redis | None = None


def _key(vps: str, svc: str) -> str:
    return f"agent:heartbeat:{vps}:{svc}"


async def _client(url: str) -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    alerts: AlertDispatcher = ctx["alerts"]

    try:
        client = await _client(settings.redis_url)
    except Exception as e:
        log.warning("heartbeat: redis client init failed: %s", e)
        return

    pairs = settings.fleet.all_services()
    keys = [_key(v.name, s.name) for v, s in pairs]
    if not keys:
        return
    try:
        values = await client.mget(keys)
    except Exception as e:
        log.warning("heartbeat: mget failed: %s", e)
        return

    now = time.time()
    for (vps, svc), val in zip(pairs, values):
        ident = (vps.name, svc.name)
        if val is not None:
            _SEEN.add(ident)
            try:
                _LAST_TS[ident] = float(val)
            except (TypeError, ValueError):
                _LAST_TS[ident] = now
            continue
        # Missing key.
        if ident not in _SEEN:
            # Service has never published — opt-in, stay silent.
            continue
        last = _LAST_TS.get(ident)
        age_s = (now - last) if last else None
        age_txt = f"{age_s/60:.1f} min" if age_s else "unknown"
        await alerts.notify(
            dedup_key=f"heartbeat:{vps.name}:{svc.name}",
            text=(
                f"💔 *{svc.name}* — heartbeat missing "
                f"(last seen: {age_txt} ago)"
            ),
        )
