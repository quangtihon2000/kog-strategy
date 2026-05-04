"""Notify on new signal files (timestamp changed) for each service.

Notification — not alerting. We push the parsed signal body to the chat so
the user sees entries/SL/TPs from the phone without running /signals. Per-
file `timestamp` is the dedup identity (matches what the EA uses), so
re-stamps on the same file fire once even if mtime ticks.

State is persisted to Redis. After bot restart, the first observation that
matches the persisted timestamp is treated as already-seen — so a CI
redeploy doesn't replay yesterday's signals.
"""

from __future__ import annotations

import html
import logging

import redis.asyncio as redis
from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..handlers.formatters import format_signal
from ..transports import Transport

log = logging.getLogger(__name__)

# (vps, service, filename) -> last seen timestamp (as string for redis parity)
_SEEN: dict[tuple[str, str, str], str] = {}
_HYDRATED = False

_REDIS_KEY = "telegram_monitor:signals_new:seen"
_redis_client: redis.Redis | None = None


async def _client(url: str) -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


def _field(vps: str, svc: str, filename: str) -> str:
    return f"{vps}|{svc}|{filename}"


def _parse_field(field: str) -> tuple[str, str, str] | None:
    parts = field.split("|", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


async def _hydrate(client: redis.Redis) -> None:
    global _HYDRATED
    try:
        raw = await client.hgetall(_REDIS_KEY)
    except Exception as e:
        log.warning("signals_new: hydrate failed (%s) — starting empty", e)
        _HYDRATED = True
        return
    for field_str, ts_str in raw.items():
        parsed = _parse_field(field_str)
        if parsed is None:
            continue
        _SEEN[parsed] = ts_str
    _HYDRATED = True
    log.info("signals_new: hydrated %d previous timestamps from redis", len(_SEEN))


def _signal_ts(data: dict) -> str | None:
    """Producer-supplied timestamp is the dedup identity. Coerce to string so
    redis hash values round-trip cleanly regardless of int vs float."""
    ts = data.get("timestamp")
    if ts is None:
        return None
    return str(ts)


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]

    client: redis.Redis | None
    try:
        client = await _client(settings.redis_url)
    except Exception as e:
        log.warning("signals_new: redis client init failed: %s", e)
        client = None

    if client is not None and not _HYDRATED:
        await _hydrate(client)

    dirty: dict[str, str] = {}
    for vps, svc in settings.fleet.all_services():
        try:
            transport = transports[vps.name]
            files = await transport.list_signal_files(svc.signal_dir)
        except Exception as e:
            log.warning("signals_new: list failed (%s/%s): %s", vps.name, svc.name, e)
            continue
        if not files:
            continue
        for f in files:
            key = (vps.name, svc.name, f.name)
            try:
                data = await transport.read_signal_json(svc.signal_dir, f.name)
            except Exception as e:
                log.warning("signals_new: read failed (%s/%s/%s): %s",
                            vps.name, svc.name, f.name, e)
                continue
            if data is None:
                continue
            ts = _signal_ts(data)
            if ts is None:
                continue
            prev = _SEEN.get(key)
            if prev == ts:
                continue
            _SEEN[key] = ts
            dirty[_field(vps.name, svc.name, f.name)] = ts
            # Treat first observation as baseline: avoids replaying every
            # historical signal on startup or after CI redeploy.
            if prev is None:
                continue
            body = format_signal(svc.name, data)
            await alerts.notify(
                dedup_key=f"sig_new:{vps.name}:{svc.name}:{f.name}:{ts}",
                text=(
                    f"🆕 *{vps.name}/{svc.name}* — new signal `{f.name}`\n"
                    f"```\n{body}\n```"
                ),
            )

    if dirty and client is not None:
        try:
            await client.hset(_REDIS_KEY, mapping=dirty)
        except Exception as e:
            log.warning("signals_new: persist failed: %s", e)
