"""Reply-once to a new-signal notification when its first outcome lands.

Flow:
    signals_new.py sends "🆕 conde_auto_entry — new signal" → stores the
    resulting (chat_id, message_id) pairs in a Redis SET keyed by signal_ts.

    This monitor tails the `conde_outcomes` stream. On the FIRST outcome for
    a given signal_ts it replies to each stored message with the close_reason
    + stats link, then sets a "replied" sentinel so subsequent outcomes for
    the same signal_ts are silent.

Dedup is via `SET ... NX EX`: race-safe even if two outcomes land in the
same tick. We only ever reply once per signal_ts, by design.

Consumer group `telegram_monitor_replies` is created with `id="$"` so a
first deploy doesn't replay historical outcomes (we wouldn't have stored
message_ids for them anyway). Subsequent restarts resume from the group's
persisted cursor.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis
from telegram.ext import ContextTypes

from ..config import Settings

log = logging.getLogger(__name__)

OUTCOMES_STREAM = "conde_outcomes"
CONSUMER_GROUP = "telegram_monitor_replies"
CONSUMER_NAME = "tg_monitor"
BATCH = 50

# Tracks "we already replied to this signal_ts" — keeps the reply-once
# guarantee across bot restarts. 30d covers any reasonable signal lifetime
# (EA itself rejects signals older than 24h).
_REPLIED_KEY_FMT = "telegram_monitor:conde:replied:{ts}"
_REPLIED_TTL_S = 30 * 24 * 3600

# Mirrors signals_new._CONDE_MSGS_KEY_FMT (kept independent to avoid cross-
# imports between sibling monitors).
_MSGS_KEY_FMT = "telegram_monitor:conde:signal_msgs:{ts}"

_redis_client: redis.Redis | None = None
_group_ready = False


async def _client(url: str) -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


async def _ensure_group(client: redis.Redis) -> bool:
    global _group_ready
    if _group_ready:
        return True
    try:
        await client.xgroup_create(
            OUTCOMES_STREAM, CONSUMER_GROUP, id="$", mkstream=True,
        )
        log.info("conde_replies: created consumer group %s on %s",
                 CONSUMER_GROUP, OUTCOMES_STREAM)
    except Exception as e:
        # BUSYGROUP = group already exists — expected on every restart after
        # the first deploy. Anything else is a real failure (redis down,
        # permission denied, etc.) — skip this tick and retry next interval.
        if "BUSYGROUP" not in str(e):
            log.warning("conde_replies: xgroup_create failed: %s", e)
            return False
    _group_ready = True
    return True


def _parse_msg_ref(member: str) -> tuple[int, int] | None:
    try:
        cid_str, mid_str = member.split(":", 1)
        return int(cid_str), int(mid_str)
    except ValueError:
        return None


def _reply_text(close_reason: str | None, stats_url: str | None, ts: str) -> str:
    reason = (close_reason or "OTHER").upper()
    head = f"🏁 First position closed: *{reason}*"
    if stats_url:
        base = stats_url.rstrip("/")
        head += f" — [📈 stats]({base}/conde/signal/{ts})"
    return head


async def _handle_outcome(
    context: ContextTypes.DEFAULT_TYPE,
    client: redis.Redis,
    settings: Settings,
    fields: dict,
) -> None:
    ts = fields.get("signal_ts")
    if not ts:
        return
    # Atomic reply-once gate. SET NX returns True only the first time.
    replied_key = _REPLIED_KEY_FMT.format(ts=ts)
    try:
        first = await client.set(replied_key, "1", nx=True, ex=_REPLIED_TTL_S)
    except Exception as e:
        log.warning("conde_replies: replied-gate SET failed (ts=%s): %s", ts, e)
        return
    if not first:
        return

    msgs_key = _MSGS_KEY_FMT.format(ts=ts)
    try:
        members = await client.smembers(msgs_key)
    except Exception as e:
        log.warning("conde_replies: smembers failed (ts=%s): %s", ts, e)
        return
    if not members:
        # Signal predates this feature (no message_ids stored). Sentinel set
        # above already prevents future retries for this ts.
        return

    text = _reply_text(fields.get("close_reason"), settings.signal_stats_url, ts)
    for member in members:
        ref = _parse_msg_ref(member)
        if ref is None:
            continue
        chat_id, message_id = ref
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
            log.warning("conde_replies: reply send failed (chat=%s msg=%s ts=%s): %s",
                        chat_id, message_id, ts, e)

    log.info("conde_replies: replied ts=%s reason=%s recipients=%d",
             ts, fields.get("close_reason"), len(members))


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]

    try:
        client = await _client(settings.redis_url)
    except Exception as e:
        log.warning("conde_replies: redis client init failed: %s", e)
        return
    if not await _ensure_group(client):
        return

    try:
        # block=0 → non-blocking poll; JobQueue already paces us.
        results = await client.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=CONSUMER_NAME,
            streams={OUTCOMES_STREAM: ">"},
            count=BATCH,
            block=0,
        )
    except Exception as e:
        log.warning("conde_replies: xreadgroup failed: %s", e)
        return
    if not results:
        return

    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                await _handle_outcome(context, client, settings, fields)
            except Exception as e:
                log.error("conde_replies: handler crashed (msg=%s): %s",
                          msg_id, e, exc_info=True)
            try:
                await client.xack(OUTCOMES_STREAM, CONSUMER_GROUP, msg_id)
            except Exception as e:
                log.warning("conde_replies: xack failed (msg=%s): %s", msg_id, e)
