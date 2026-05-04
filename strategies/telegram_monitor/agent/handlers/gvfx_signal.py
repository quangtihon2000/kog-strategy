"""/gvfx — push a GVFX signal onto the Redis stream consumed by gvfx_signal_agent.

Usage:
    /gvfx <target> [direction] [step] [tp]

Defaults: direction=BUY, step=500, tp=500. Symbol is fixed to XAUUSD
(matching gvfx_signal's single-symbol contract). Timestamp is generated
here (Unix epoch seconds) and acts as the EA's dedup identity.
"""

from __future__ import annotations

import html
import logging
import time

from redis.asyncio import Redis
from telegram import Update
from telegram.ext import ContextTypes

from .auth import auth_required

log = logging.getLogger(__name__)

DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_DIRECTION = "BUY"
DEFAULT_STEP = 500
DEFAULT_TP = 500
GVFX_STREAM = "gvfx_signals"

USAGE = (
    "usage: <code>/gvfx &lt;target&gt; [direction] [step] [tp]</code>\n"
    "defaults: direction=BUY, step=500, tp=500"
)


@auth_required
async def cmd_gvfx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    args = context.args or []

    if not args:
        await msg.reply_html(USAGE)
        return

    try:
        target = float(args[0])
    except ValueError:
        await msg.reply_html(f"target must be a number\n{USAGE}")
        return
    if target <= 0:
        await msg.reply_html("target must be positive")
        return

    direction = (args[1] if len(args) >= 2 else DEFAULT_DIRECTION).upper()
    if direction not in ("BUY", "SELL"):
        await msg.reply_html("direction must be BUY or SELL")
        return

    try:
        step = int(args[2]) if len(args) >= 3 else DEFAULT_STEP
        tp = int(args[3]) if len(args) >= 4 else DEFAULT_TP
    except ValueError:
        await msg.reply_html(f"step/tp must be integers (points)\n{USAGE}")
        return
    if step <= 0 or tp <= 0:
        await msg.reply_html("step and tp must be positive")
        return

    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await msg.reply_text("redis client not configured — cannot publish signal")
        return

    payload = {
        "timestamp": str(int(time.time())),
        "symbol": DEFAULT_SYMBOL,
        "target": str(target),
        "direction": direction,
        "step": str(step),
        "tp": str(tp),
    }

    try:
        entry_id = await redis.xadd(GVFX_STREAM, payload)
    except Exception as e:
        log.exception("xadd to %s failed", GVFX_STREAM)
        await msg.reply_text(f"failed to publish signal: {e}")
        return

    log.info("gvfx signal published: stream=%s id=%s payload=%s",
             GVFX_STREAM, entry_id, payload)

    await msg.reply_html(
        "<b>GVFX signal published</b>\n"
        f"stream: <code>{GVFX_STREAM}</code>\n"
        f"id: <code>{html.escape(entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id))}</code>\n"
        f"symbol: <code>{DEFAULT_SYMBOL}</code>\n"
        f"direction: <code>{direction}</code>\n"
        f"target: <code>{target}</code>\n"
        f"step: <code>{step}</code> pts\n"
        f"tp: <code>{tp}</code> pts\n"
        f"ts: <code>{payload['timestamp']}</code>"
    )
