"""/stats — per-channel signal effectiveness for conde_auto_entry.

Joins `conde_signals` and `conde_outcomes` Redis Streams in a window and
renders a monospace report. Reuses the pure aggregator from the
conde_auto_entry agent so the math lives in one place.
"""

from __future__ import annotations

import html
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from telegram import Update
from telegram.ext import ContextTypes

from .auth import auth_required

# The aggregator + dataclass live in the conde_auto_entry agent (single source
# of truth for stats math). Inject its parent on sys.path once, then import.
_CONDE_AGENT_DIR = Path(__file__).resolve().parents[3] / "conde_auto_entry" / "agent"
if str(_CONDE_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_CONDE_AGENT_DIR))

import stats as conde_stats   # noqa: E402  (after sys.path mutation)

log = logging.getLogger(__name__)


def _decode(v: Any) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _ms_to_stream_id(ms: int) -> str:
    return f"{int(ms)}-0"


async def _fetch_signals(r: Redis, since_ms: int) -> list[dict]:
    raw = await r.xrange(conde_stats.SIGNALS_STREAM, min=_ms_to_stream_id(since_ms), max="+")
    out: list[dict] = []
    for _msg_id, fields in raw:
        f = {_decode(k): _decode(v) for k, v in fields.items()}
        # Skip legacy entries published before channel_name became required —
        # they'd just clutter the report as a permanent "unknown" row with exec=0.
        channel = (f.get("channel_name") or "").strip()
        if not channel:
            continue
        try:
            tps = [float(x) for x in f.get("tps", "").split(",") if x.strip()]
            out.append({
                "signal_ts":    int(f.get("timestamp", 0)),
                "channel_name": channel,
                "symbol":       f.get("symbol", ""),
                "direction":    f.get("direction", ""),
                "entry_price":  float(f.get("entry_price") or 0),
                "sl":           float(f.get("sl") or 0),
                "tps":          tps,
            })
        except (ValueError, TypeError) as exc:
            log.warning("Skipping malformed signal: %s (%s)", f, exc)
    return out


async def _fetch_outcomes(r: Redis, since_ms: int) -> list[dict]:
    raw = await r.xrange(conde_stats.OUTCOMES_STREAM, min=_ms_to_stream_id(since_ms), max="+")
    out: list[dict] = []
    for _msg_id, fields in raw:
        f = {_decode(k): _decode(v) for k, v in fields.items()}
        try:
            out.append(conde_stats._parse_outcome(f))
        except (ValueError, TypeError) as exc:
            log.warning("Skipping malformed outcome: %s (%s)", f, exc)
    return out


@auth_required
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await update.effective_message.reply_text("redis client not configured — /stats unavailable")
        return

    args = context.args or []
    user_specified = bool(args)
    since_arg = args[0] if args else "30d"
    try:
        window_s = conde_stats.parse_duration(since_arg)
    except (ValueError, KeyError):
        await update.effective_message.reply_text(
            f"bad duration: {since_arg!r} — use forms like 7d, 24h, 90m"
        )
        return

    if not user_specified:
        await update.effective_message.reply_text(
            f"using default window {since_arg} — pass an arg like /stats 7d, /stats 24h, /stats 90m to change it"
        )

    since_ms = int((time.time() - window_s) * 1000)

    try:
        signals = await _fetch_signals(redis, since_ms)
        outcomes = await _fetch_outcomes(redis, since_ms)
    except RedisError as exc:
        log.warning("redis error in /stats: %s", exc)
        await update.effective_message.reply_text(
            f"redis error: {exc} — try again shortly"
        )
        return

    stats_map = conde_stats.aggregate(signals, outcomes)
    body = conde_stats.format_report(stats_map, since_label=f"last {since_arg}")
    await update.effective_message.reply_html(f"<pre>{html.escape(body)}</pre>")
