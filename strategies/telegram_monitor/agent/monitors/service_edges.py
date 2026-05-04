"""Alert when a service transitions away from RUNNING (and when it recovers).

Edge-only on purpose — a steady STOPPED state generates one alert, not
one per tick. State is persisted to Redis so transitions across the bot's
own restart (CI redeploys it as an NSSM service alongside the agents) still
fire the recovery alert. Without this, a deploy that happens to coincide
with a restarted agent silently swallows the 🟢 — which is exactly what we
saw in production.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis
from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import ServiceState, Transport

log = logging.getLogger(__name__)

# (vps, service) -> last observed state. Hydrated from Redis on first tick.
_PREV: dict[tuple[str, str], ServiceState] = {}
_HYDRATED = False

_REDIS_KEY = "telegram_monitor:service_edges:prev"
_redis_client: redis.Redis | None = None


async def _client(url: str) -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


def _field(vps: str, svc: str) -> str:
    return f"{vps}:{svc}"


async def _hydrate(client: redis.Redis) -> None:
    """One-shot load of `_PREV` from Redis. Called on first tick after startup
    so a CI redeploy doesn't lose the previous state and miss recovery alerts."""
    global _HYDRATED
    try:
        raw = await client.hgetall(_REDIS_KEY)
    except Exception as e:
        log.warning("service_edges: hydrate failed (%s) — starting from empty", e)
        _HYDRATED = True
        return
    for field_str, state_str in raw.items():
        try:
            vps, svc = field_str.split(":", 1)
            _PREV[(vps, svc)] = ServiceState(state_str)
        except (ValueError, KeyError):
            continue
    _HYDRATED = True
    log.info("service_edges: hydrated %d previous states from redis", len(_PREV))


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]

    client: redis.Redis | None
    try:
        client = await _client(settings.redis_url)
    except Exception as e:
        log.warning("service_edges: redis client init failed: %s", e)
        client = None

    if client is not None and not _HYDRATED:
        await _hydrate(client)

    dirty: dict[str, str] = {}
    for vps, svc in settings.fleet.all_services():
        try:
            st = await transports[vps.name].get_service_status(svc.nssm_service)
        except Exception as e:
            log.warning("service probe failed (%s/%s): %s", vps.name, svc.name, e)
            continue
        key = (vps.name, svc.name)
        prev = _PREV.get(key)
        if prev != st.state:
            _PREV[key] = st.state
            dirty[_field(vps.name, svc.name)] = st.state.value
        if prev is None or prev == st.state:
            continue
        # Transition detected.
        if prev == ServiceState.RUNNING and st.state != ServiceState.RUNNING:
            await alerts.notify(
                dedup_key=f"svc_down:{vps.name}:{svc.name}",
                text=f"🔴 *{vps.name}/{svc.name}* — `{prev.value}` → `{st.state.value}`",
            )
        elif st.state == ServiceState.RUNNING and prev != ServiceState.RUNNING:
            await alerts.notify(
                dedup_key=f"svc_up:{vps.name}:{svc.name}",
                text=f"🟢 *{vps.name}/{svc.name}* — recovered (`{st.state.value}`)",
            )

    if dirty and client is not None:
        try:
            await client.hset(_REDIS_KEY, mapping=dirty)
        except Exception as e:
            log.warning("service_edges: persist failed: %s", e)
