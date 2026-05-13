"""/zone — push a ZoneSignal onto the Redis stream consumed by zone_signal_agent.

Two modes:
  - Fast path:  /zone <upper> <lower> <targets_above> <targets_below> [symbol]
                e.g. /zone 4520 4500 4540,4560 4480,4460
  - Wizard:     /zone           (no args → guided flow)

Symbol defaults to XAUUSD (zone_signal's primary contract). Targets are
comma-separated lists of price levels above/below the redbox.

Re-stamping: the zone_signal agent re-stamps timestamps at write time, so
the timestamp we send here is informational only — fresh xadd id is what
the EA effectively sees as identity.
"""

from __future__ import annotations

import html
import logging
import os
import sys

from redis.asyncio import Redis
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .auth import auth_required

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "shared"))
from agent_lib.timefmt import now_unix  # noqa: E402

log = logging.getLogger(__name__)

DEFAULT_SYMBOL = "XAUUSD"
ZONE_STREAM = "zone_signals"

USAGE = (
    "usage: <code>/zone &lt;upper&gt; &lt;lower&gt; &lt;targets_above&gt; &lt;targets_below&gt; [symbol]</code>\n"
    "or send <code>/zone</code> alone for the wizard\n"
    "example: <code>/zone 4520 4500 4540,4560 4480,4460</code>\n"
    "targets_above/below are comma-separated price lists; symbol defaults to XAUUSD"
)

W_UPPER, W_LOWER, W_ABOVE, W_BELOW, W_REVIEW = range(5)
WIZARD_KEY = "zone_wizard"


def _parse_targets(s: str) -> list[float]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty target list")
    return [float(p) for p in parts]


def _review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data="zonew:publish")],
        [InlineKeyboardButton("✖ Cancel", callback_data="zonew:cancel")],
    ])


def _review_text(state: dict) -> str:
    upper = state.get("upper", 0.0)
    lower = state.get("lower", 0.0)
    above = state.get("above", [])
    below = state.get("below", [])
    symbol = state.get("symbol", DEFAULT_SYMBOL)
    return (
        "<b>review:</b>\n"
        f"symbol: <code>{symbol}</code>\n"
        f"redbox_upper: <code>{upper}</code>\n"
        f"redbox_lower: <code>{lower}</code>\n"
        f"targets_above: <code>{','.join(str(x) for x in above)}</code>\n"
        f"targets_below: <code>{','.join(str(x) for x in below)}</code>\n"
    )


async def _publish(
    msg: Message,
    redis: Redis,
    *,
    upper: float,
    lower: float,
    above: list[float],
    below: list[float],
    symbol: str,
) -> None:
    payload = {
        "timestamp": str(now_unix()),
        "symbol": symbol,
        "redbox_upper": str(upper),
        "redbox_lower": str(lower),
        "targets_above": ",".join(str(x) for x in above),
        "targets_below": ",".join(str(x) for x in below),
    }
    try:
        entry_id = await redis.xadd(ZONE_STREAM, payload)
    except Exception as e:
        log.exception("xadd to %s failed", ZONE_STREAM)
        await msg.reply_text(f"failed to publish signal: {e}")
        return

    log.info("zone signal published: stream=%s id=%s payload=%s",
             ZONE_STREAM, entry_id, payload)

    await msg.reply_html(
        "<b>Zone signal published</b>\n"
        f"stream: <code>{ZONE_STREAM}</code>\n"
        f"id: <code>{html.escape(entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id))}</code>\n"
        f"symbol: <code>{symbol}</code>\n"
        f"redbox_upper: <code>{upper}</code>\n"
        f"redbox_lower: <code>{lower}</code>\n"
        f"targets_above: <code>{','.join(str(x) for x in above)}</code>\n"
        f"targets_below: <code>{','.join(str(x) for x in below)}</code>\n"
        f"ts: <code>{payload['timestamp']}</code>"
    )


def _validate(upper: float, lower: float, above: list[float], below: list[float]) -> str | None:
    if upper <= lower:
        return "redbox_upper must be &gt; redbox_lower"
    if not above:
        return "targets_above must contain at least one price"
    if not below:
        return "targets_below must contain at least one price"
    if any(t <= upper for t in above):
        return "every target_above must be &gt; redbox_upper"
    if any(t >= lower for t in below):
        return "every target_below must be &lt; redbox_lower"
    return None


@auth_required
async def cmd_zone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point. With args → publish immediately; without args → wizard."""
    msg = update.effective_message
    args = context.args or []

    if not args:
        context.user_data[WIZARD_KEY] = {"symbol": DEFAULT_SYMBOL}
        await msg.reply_html(
            "<b>Zone signal wizard</b> — step 1/4\n"
            "send the <b>redbox upper</b> price (e.g. <code>4520</code>)",
        )
        return W_UPPER

    if len(args) < 4:
        await msg.reply_html(USAGE)
        return ConversationHandler.END

    try:
        upper = float(args[0])
        lower = float(args[1])
    except ValueError:
        await msg.reply_html(f"upper/lower must be numbers\n{USAGE}")
        return ConversationHandler.END

    try:
        above = _parse_targets(args[2])
        below = _parse_targets(args[3])
    except ValueError as e:
        await msg.reply_html(f"targets parse error: {e}\n{USAGE}")
        return ConversationHandler.END

    symbol = args[4] if len(args) >= 5 else DEFAULT_SYMBOL

    err = _validate(upper, lower, above, below)
    if err:
        await msg.reply_html(err)
        return ConversationHandler.END

    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await msg.reply_text("redis client not configured — cannot publish signal")
        return ConversationHandler.END

    await _publish(
        msg, redis,
        upper=upper, lower=lower, above=above, below=below, symbol=symbol,
    )
    return ConversationHandler.END


@auth_required
async def _w_upper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    try:
        upper = float(text)
    except ValueError:
        await msg.reply_text("upper must be a number — try again or /cancel")
        return W_UPPER
    if upper <= 0:
        await msg.reply_text("upper must be positive — try again or /cancel")
        return W_UPPER
    context.user_data.setdefault(WIZARD_KEY, {})["upper"] = upper
    await msg.reply_html(
        f"upper = <code>{upper}</code>\n"
        "step 2/4 — send the <b>redbox lower</b> price (e.g. <code>4500</code>)",
    )
    return W_LOWER


@auth_required
async def _w_lower(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    try:
        lower = float(text)
    except ValueError:
        await msg.reply_text("lower must be a number — try again or /cancel")
        return W_LOWER
    state = context.user_data.setdefault(WIZARD_KEY, {})
    upper = state.get("upper", 0.0)
    if lower <= 0:
        await msg.reply_text("lower must be positive — try again or /cancel")
        return W_LOWER
    if lower >= upper:
        await msg.reply_text(f"lower must be < upper ({upper}) — try again or /cancel")
        return W_LOWER
    state["lower"] = lower
    await msg.reply_html(
        f"lower = <code>{lower}</code>\n"
        "step 3/4 — send <b>targets_above</b> as comma-separated prices "
        "(e.g. <code>4540,4560</code>)",
    )
    return W_ABOVE


@auth_required
async def _w_above(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    try:
        above = _parse_targets(text)
    except ValueError:
        await msg.reply_text(
            "targets_above must be comma-separated numbers — try again or /cancel"
        )
        return W_ABOVE
    state = context.user_data.setdefault(WIZARD_KEY, {})
    upper = state.get("upper", 0.0)
    if any(t <= upper for t in above):
        await msg.reply_text(
            f"every target_above must be > upper ({upper}) — try again or /cancel"
        )
        return W_ABOVE
    state["above"] = above
    await msg.reply_html(
        f"targets_above = <code>{','.join(str(x) for x in above)}</code>\n"
        "step 4/4 — send <b>targets_below</b> as comma-separated prices "
        "(e.g. <code>4480,4460</code>)",
    )
    return W_BELOW


@auth_required
async def _w_below(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    try:
        below = _parse_targets(text)
    except ValueError:
        await msg.reply_text(
            "targets_below must be comma-separated numbers — try again or /cancel"
        )
        return W_BELOW
    state = context.user_data.setdefault(WIZARD_KEY, {})
    lower = state.get("lower", 0.0)
    if any(t >= lower for t in below):
        await msg.reply_text(
            f"every target_below must be < lower ({lower}) — try again or /cancel"
        )
        return W_BELOW
    state["below"] = below
    await msg.reply_html(
        _review_text(state),
        reply_markup=_review_keyboard(),
    )
    return W_REVIEW


@auth_required
async def _w_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) >= 2 else ""

    if action == "cancel":
        await query.edit_message_text("wizard cancelled")
        context.user_data.pop(WIZARD_KEY, None)
        return ConversationHandler.END

    if action == "publish":
        state = context.user_data.get(WIZARD_KEY, {})
        required = ("upper", "lower", "above", "below")
        if any(k not in state for k in required):
            await query.edit_message_text("missing fields — wizard aborted")
            context.user_data.pop(WIZARD_KEY, None)
            return ConversationHandler.END
        redis: Redis | None = context.application.bot_data.get("redis")
        if redis is None:
            await query.edit_message_text("redis client not configured — cannot publish signal")
            context.user_data.pop(WIZARD_KEY, None)
            return ConversationHandler.END
        await query.edit_message_text("publishing…")
        await _publish(
            query.message, redis,
            upper=state["upper"],
            lower=state["lower"],
            above=state["above"],
            below=state["below"],
            symbol=state.get("symbol", DEFAULT_SYMBOL),
        )
        context.user_data.pop(WIZARD_KEY, None)
        return ConversationHandler.END

    return W_REVIEW


async def _w_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(WIZARD_KEY, None)
    await update.effective_message.reply_text("wizard cancelled")
    return ConversationHandler.END


def conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("zone", cmd_zone)],
        states={
            W_UPPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_upper)],
            W_LOWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_lower)],
            W_ABOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_above)],
            W_BELOW: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_below)],
            W_REVIEW: [CallbackQueryHandler(_w_review, pattern=r"^zonew:")],
        },
        fallbacks=[CommandHandler("cancel", _w_cancel)],
        allow_reentry=True,
        per_chat=True,
    )
