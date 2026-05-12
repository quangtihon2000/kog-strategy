"""Conde signal lifecycle: new-signal notif + reply-once on first outcome.

One monitor owns both phases of the conde_auto_entry user-visible flow:

    Phase 1 (every tick): scan signal files for a changed `timestamp`. On
    change → send "🆕 new signal" to operators, capture the resulting
    (chat_id, message_id) pairs, persist them as "pending replies" keyed by
    `signal_ts`.

    Phase 2 (same tick, immediately after): drain the `conde_outcomes`
    Redis stream. For each outcome whose `signal_ts` is pending, reply-once
    to the captured messages with the close_reason + stats link, then mark
    that signal_ts as replied.

## Why both phases live in one monitor

Before this monitor existed, Phase 1 was in `signals_new.py` and Phase 2 in
a separate `conde_replies.py` job. Because those two jobs ran on independent
JobQueue tickers, an outcome could land in Redis *after* signals_new sent
the notif but *before* it finished SADD-ing the message refs — conde_replies
would then find an empty set and silently skip the reply forever.

By merging the phases into one coroutine, Phase 1 always completes (notif
sent + pending persisted) before Phase 2 reads from the stream in the same
tick. The race window is eliminated by construction, no cross-job locking
needed.

## State

In-memory (rebuilt from Redis on first tick after restart):

    _SEEN:    (vps, svc, filename) -> last-seen signal_ts string
              Per-file dedup so a re-stamp on the same file fires once.

    _PENDING: signal_ts -> list[(chat_id, message_id)]
              Notif refs awaiting their first outcome. Removed after reply.

Redis (source of truth across restarts):

    telegram_monitor:conde:signals_seen        HASH  field=`vps|svc|filename` value=ts
    telegram_monitor:conde:pending:{ts}        HASH  field=chat_id           value=message_id   TTL 7d
    telegram_monitor:conde:replied:{ts}        STRING "1"                                       TTL 30d

TTL choices: 7d on `pending` covers the EA's 24h signal validity plus
weekend slack; 30d on `replied` is the reply-once guarantee window.

## Restart hydration

First tick:
  1. HGETALL `signals_seen` → restore `_SEEN`. Files whose ts matches the
     persisted value are treated as already-notified, so a redeploy does
     not replay yesterday's signals.
  2. SCAN `pending:*` → restore `_PENDING`. An outcome that arrives during
     the bot downtime is still in the stream (consumer group cursor is
     persisted by Redis), and the pending msg refs are still here, so the
     reply still fires after restart.

If the bot crashes between sending the Phase 1 notif and persisting
`pending`, the message refs are lost — the user got the notif but won't
get the reply for that one signal. Documented and accepted: the alternative
(transactional outbox) is overkill for a chat reply.

## Consumer group

`conde_outcomes` is read via group `telegram_monitor_lifecycle`, created
with `id="$"` on first run. Replays before the first deploy are skipped on
purpose (no `pending` would exist for them). Across restarts the group
cursor + PEL guarantee at-least-once.

## Migration from the split monitors

Old key `telegram_monitor:signals_new:seen` (which held conde entries
alongside other services) is no longer read or written for conde — those
entries become orphans and stay in Redis indefinitely (HASH has no TTL).
Cheap and harmless; leave them. Old `signal_msgs:{ts}` SETs and the
previous `replied:{ts}` keys expire naturally (7d / 30d).

If the previous split design (PR #90) ever ran in production, its consumer
group `telegram_monitor_replies` will linger on `conde_outcomes` with a
stale cursor — harmless (no consumer reads it) but listed in `XINFO GROUPS`.
One-shot cleanup after this monitor is deployed:

    XGROUP DESTROY conde_outcomes telegram_monitor_replies

## Why conde only?

zone_signal / gvfx_signal have different outcome semantics and stream
schemas. This module is intentionally specific. When a second strategy
wants the same lifecycle pattern, copy this file and adapt — abstract on
the third instance, not the second.
"""

from __future__ import annotations

import hashlib
import json
import logging

import redis.asyncio as redis
from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..handlers.formatters import format_signal
from ..transports import Transport

log = logging.getLogger(__name__)

CONDE_SVC_NAME = "conde_auto_entry"

OUTCOMES_STREAM = "conde_outcomes"
CONSUMER_GROUP = "telegram_monitor_lifecycle"
CONSUMER_NAME = "tg_monitor"
OUTCOMES_BATCH = 50

_SEEN_KEY = "telegram_monitor:conde:signals_seen"
_PENDING_KEY_FMT = "telegram_monitor:conde:pending:{ts}"
_PENDING_TTL_S = 7 * 24 * 3600
_REPLIED_KEY_FMT = "telegram_monitor:conde:replied:{ts}"
_REPLIED_TTL_S = 30 * 24 * 3600

# Truncate raw payload in bad-signal alert body to stay under Telegram's
# 4096-char message limit.
_ALERT_SNIPPET_MAX = 1500

# In-memory state — see module docstring.
_SEEN: dict[tuple[str, str, str], str] = {}
_PENDING: dict[str, list[tuple[int, int]]] = {}
_HYDRATED = False
_GROUP_READY = False
_redis_client: redis.Redis | None = None


async def _client(url: str) -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


def _seen_field(vps: str, svc: str, filename: str) -> str:
    return f"{vps}|{svc}|{filename}"


def _parse_seen_field(field: str) -> tuple[str, str, str] | None:
    parts = field.split("|", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def _snippet(text: str) -> str:
    if len(text) <= _ALERT_SNIPPET_MAX:
        return text
    return text[:_ALERT_SNIPPET_MAX] + f"\n... [truncated {len(text) - _ALERT_SNIPPET_MAX} chars]"


def _signal_ts(data: dict) -> str | None:
    ts = data.get("timestamp")
    if ts is None:
        return None
    return str(ts)


async def _hydrate(client: redis.Redis) -> None:
    """First-tick replay of persisted state. Idempotent; safe if Redis is empty."""
    global _HYDRATED
    try:
        seen_raw = await client.hgetall(_SEEN_KEY)
    except Exception as e:
        log.warning("conde_lifecycle: hydrate seen failed (%s) — starting empty", e)
        _HYDRATED = True
        return
    for field_str, ts_str in seen_raw.items():
        parsed = _parse_seen_field(field_str)
        if parsed is None:
            continue
        _SEEN[parsed] = ts_str

    pending_loaded = 0
    try:
        async for key in client.scan_iter(match="telegram_monitor:conde:pending:*"):
            ts = key.rsplit(":", 1)[-1]
            try:
                hash_raw = await client.hgetall(key)
            except Exception as e:
                log.warning("conde_lifecycle: hydrate pending %s failed: %s", key, e)
                continue
            refs: list[tuple[int, int]] = []
            for cid_str, mid_str in hash_raw.items():
                try:
                    refs.append((int(cid_str), int(mid_str)))
                except ValueError:
                    continue
            if refs:
                _PENDING[ts] = refs
                pending_loaded += 1
    except Exception as e:
        log.warning("conde_lifecycle: scan pending failed: %s", e)

    _HYDRATED = True
    log.info("conde_lifecycle: hydrated seen=%d pending=%d", len(_SEEN), pending_loaded)


async def _ensure_group(client: redis.Redis) -> bool:
    global _GROUP_READY
    if _GROUP_READY:
        return True
    try:
        await client.xgroup_create(
            OUTCOMES_STREAM, CONSUMER_GROUP, id="$", mkstream=True,
        )
        log.info("conde_lifecycle: created consumer group %s on %s",
                 CONSUMER_GROUP, OUTCOMES_STREAM)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            log.warning("conde_lifecycle: xgroup_create failed: %s", e)
            return False
    _GROUP_READY = True
    return True


async def _alert_bad_signal(
    alerts: AlertDispatcher, vps_name: str, svc_name: str, fname: str,
    reason: str, raw: str | None,
) -> None:
    body = _snippet(raw) if raw else "(file unreadable)"
    digest = hashlib.sha1((raw or reason).encode("utf-8", errors="replace")).hexdigest()[:10]
    await alerts.notify(
        dedup_key=f"sig_bad:{vps_name}:{svc_name}:{fname}:{digest}",
        text=(
            f"⚠️ *{vps_name}/{svc_name}* — bad signal `{fname}`\n"
            f"reason: {reason}\n"
            f"```\n{body}\n```"
        ),
    )


def _new_signal_text(vps_name: str, svc_name: str, fname: str,
                     data: dict, ts: str, stats_url: str) -> str:
    body = format_signal(svc_name, data)
    text = (
        f"🆕 *{vps_name}/{svc_name}* — new signal `{fname}`\n"
        f"```\n{body}\n```"
    )
    if stats_url:
        base = stats_url.rstrip("/")
        text += f"\n[📈 stats]({base}/conde/signal/{ts})"
    return text


async def _scan_signals(
    settings: Settings,
    transports: dict[str, Transport],
    alerts: AlertDispatcher,
    client: redis.Redis,
) -> None:
    """Phase 1: detect new conde signal files, notify, persist pending refs."""
    dirty_seen: dict[str, str] = {}
    for vps, svc in settings.fleet.all_services():
        if svc.name != CONDE_SVC_NAME or svc.signal_dir is None:
            continue
        try:
            transport = transports[vps.name]
            files = await transport.list_signal_files(svc.signal_dir)
        except Exception as e:
            log.warning("conde_lifecycle: list failed (%s/%s): %s",
                        vps.name, svc.name, e)
            continue
        if not files:
            continue
        for f in files:
            key = (vps.name, svc.name, f.name)
            try:
                raw = await transport.read_signal_text(svc.signal_dir, f.name)
            except Exception as e:
                log.warning("conde_lifecycle: read failed (%s/%s/%s): %s",
                            vps.name, svc.name, f.name, e)
                continue
            if raw is None:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                log.warning("conde_lifecycle: parse failed (%s/%s/%s): %s",
                            vps.name, svc.name, f.name, e)
                await _alert_bad_signal(alerts, vps.name, svc.name, f.name,
                                        f"json decode: {e}", raw)
                continue
            if not isinstance(parsed, dict):
                await _alert_bad_signal(alerts, vps.name, svc.name, f.name,
                                        f"top-level is {type(parsed).__name__}, expected object", raw)
                continue
            ts = _signal_ts(parsed)
            if ts is None:
                await _alert_bad_signal(alerts, vps.name, svc.name, f.name,
                                        "missing `timestamp` field", raw)
                continue
            prev = _SEEN.get(key)
            if prev == ts:
                continue
            _SEEN[key] = ts
            dirty_seen[_seen_field(vps.name, svc.name, f.name)] = ts
            # First observation per (vps, svc, filename) is baseline — don't
            # replay historical signals on initial deploy.
            if prev is None:
                continue
            text = _new_signal_text(vps.name, svc.name, f.name, parsed, ts,
                                    settings.signal_stats_url)
            refs = await alerts.send_capture(text=text)
            if not refs:
                continue
            _PENDING[ts] = refs
            try:
                pending_key = _PENDING_KEY_FMT.format(ts=ts)
                mapping = {str(cid): str(mid) for cid, mid in refs}
                await client.hset(pending_key, mapping=mapping)
                await client.expire(pending_key, _PENDING_TTL_S)
            except Exception as e:
                log.warning("conde_lifecycle: persist pending failed (ts=%s): %s",
                            ts, e)

    if dirty_seen:
        try:
            await client.hset(_SEEN_KEY, mapping=dirty_seen)
        except Exception as e:
            log.warning("conde_lifecycle: persist seen failed: %s", e)


def _reply_text(close_reason: str | None, stats_url: str, ts: str) -> str:
    # Markdown-safe: close_reason comes from EA-written JSON; escape just in
    # case a future close_reason ever contains formatting characters.
    raw_reason = (close_reason or "OTHER").upper()
    safe_reason = raw_reason.replace("*", "").replace("_", "").replace("`", "")
    head = f"🏁 First position closed: *{safe_reason}*"
    if stats_url:
        base = stats_url.rstrip("/")
        head += f" — [📈 stats]({base}/conde/signal/{ts})"
    return head


async def _reply_for_outcome(
    context: ContextTypes.DEFAULT_TYPE,
    client: redis.Redis,
    settings: Settings,
    fields: dict,
) -> None:
    ts = fields.get("signal_ts")
    if not ts:
        return
    # Reply-once gate — protects against (a) duplicate XADDs in the stream
    # and (b) the case where the bot replied in a previous run but PEL still
    # delivers the message on restart.
    try:
        first = await client.set(
            _REPLIED_KEY_FMT.format(ts=ts), "1", nx=True, ex=_REPLIED_TTL_S,
        )
    except Exception as e:
        log.warning("conde_lifecycle: replied-gate SET failed (ts=%s): %s", ts, e)
        return
    if not first:
        return

    refs = _PENDING.pop(ts, None)
    if refs is None:
        # No in-mem pending. Fall back to Redis (hydration may have missed a
        # ts created mid-tick; or the signal predates this feature).
        try:
            hash_raw = await client.hgetall(_PENDING_KEY_FMT.format(ts=ts))
        except Exception as e:
            log.warning("conde_lifecycle: pending lookup failed (ts=%s): %s", ts, e)
            return
        refs = []
        for cid_str, mid_str in hash_raw.items():
            try:
                refs.append((int(cid_str), int(mid_str)))
            except ValueError:
                continue
    if not refs:
        return

    text = _reply_text(fields.get("close_reason"), settings.signal_stats_url, ts)
    for chat_id, message_id in refs:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_to_message_id=message_id,
                disable_notification=True,
                allow_sending_without_reply=True,
            )
        except Exception as e:
            log.warning("conde_lifecycle: reply send failed (chat=%s msg=%s ts=%s): %s",
                        chat_id, message_id, ts, e)

    # Pending HASH no longer needed; let it expire naturally (or delete now
    # to free memory faster — cheap).
    try:
        await client.delete(_PENDING_KEY_FMT.format(ts=ts))
    except Exception as e:
        log.warning("conde_lifecycle: pending delete failed (ts=%s): %s", ts, e)

    log.info("conde_lifecycle: replied ts=%s reason=%s recipients=%d",
             ts, fields.get("close_reason"), len(refs))


async def _drain_outcomes(
    context: ContextTypes.DEFAULT_TYPE,
    client: redis.Redis,
    settings: Settings,
) -> None:
    """Phase 2: pull new outcomes, fire reply-once for each unseen signal_ts."""
    try:
        results = await client.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams={OUTCOMES_STREAM: ">"},
            count=OUTCOMES_BATCH,
            block=0,
        )
    except Exception as e:
        log.warning("conde_lifecycle: xreadgroup failed: %s", e)
        return
    if not results:
        return

    for _stream_name, messages in results:
        for msg_id, fields in messages:
            handled = False
            try:
                await _reply_for_outcome(context, client, settings, fields)
                handled = True
            except Exception as e:
                log.error("conde_lifecycle: handler crashed (msg=%s): %s",
                          msg_id, e, exc_info=True)
            # Only XACK on success — on crash, leave in PEL so the next tick
            # retries. The reply-once gate (replied:{ts} SET-NX) prevents
            # double-replies on retry.
            if handled:
                try:
                    await client.xack(OUTCOMES_STREAM, CONSUMER_GROUP, msg_id)
                except Exception as e:
                    log.warning("conde_lifecycle: xack failed (msg=%s): %s",
                                msg_id, e)


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]

    try:
        client = await _client(settings.redis_url)
    except Exception as e:
        log.warning("conde_lifecycle: redis client init failed: %s", e)
        return

    if not _HYDRATED:
        await _hydrate(client)
    if not await _ensure_group(client):
        return

    # Phase order matters: scan first so any signal_ts observed in this tick
    # is registered in _PENDING before we read outcomes that might reference
    # it. See the "Why both phases live in one monitor" section above.
    await _scan_signals(settings, transports, alerts, client)
    await _drain_outcomes(context, client, settings)
