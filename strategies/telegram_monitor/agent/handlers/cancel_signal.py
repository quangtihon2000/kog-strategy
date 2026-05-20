"""/cancel_gvfx and /cancel_zone — deactivate the current signal.

The command shows two inline buttons so the operator picks the scope:

  - "Block new entries"  → active=false, close_all=false (default, safe)
        EA stops opening new positions; open positions keep their TP/SL.
  - "Close all + cancel" → active=false, close_all=true
        EA additionally closes all open positions at market and cancels
        pending orders. Realises P&L now — there is no resume.

Either way the agent rewrites every signal file with active=false (timestamp
preserved); the EA detects the same-timestamp rewrite. There is no resume —
publish a fresh signal via /gvfx or /zone to trade again.

No arguments: each strategy normally has at most one active signal at a time,
so the command always targets "the current signal".
"""

from __future__ import annotations

import html
import logging
import time

from redis.asyncio import Redis
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from .auth import auth_required

log = logging.getLogger(__name__)

GVFX_STREAM = "gvfx_signals"
ZONE_STREAM = "zone_signals"

# callback_data: "cancelsig:{strat}:{choice}"  — strat ∈ gvfx|zone, choice ∈ block|closeall|abort
_CB_PREFIX = "cancelsig"

_STREAMS = {"gvfx": GVFX_STREAM, "zone": ZONE_STREAM}
_LABELS = {"gvfx": "GVFX", "zone": "ZONE"}


def _confirm_keyboard(strat: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 Block new entries",
                              callback_data=f"{_CB_PREFIX}:{strat}:block")],
        [InlineKeyboardButton("🛑 Close all + cancel",
                              callback_data=f"{_CB_PREFIX}:{strat}:closeall")],
        [InlineKeyboardButton("✖ Abort", callback_data=f"{_CB_PREFIX}:{strat}:abort")],
    ])


async def _prompt(update: Update, strat: str) -> None:
    label = _LABELS[strat]
    await update.effective_message.reply_html(
        f"<b>Cancel current {label} signal?</b>\n"
        "• <b>Block new entries</b> — stop opening new positions, "
        "open positions keep their TP/SL\n"
        "• <b>Close all + cancel</b> — also close every open position at "
        "market and cancel pendings (realises P&amp;L now)",
        reply_markup=_confirm_keyboard(strat),
    )


@auth_required
async def cmd_cancel_gvfx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt the operator for the GVFX cancel scope."""
    await _prompt(update, "gvfx")


@auth_required
async def cmd_cancel_zone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt the operator for the zone_signal cancel scope."""
    await _prompt(update, "zone")


@auth_required
async def on_cancel_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline-button tap — publish the deactivate control message."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return
    _, strat, choice = parts
    if strat not in _STREAMS:
        return

    label = _LABELS[strat]
    if choice == "abort":
        await query.edit_message_text(f"{label} cancel aborted")
        return

    close_all = (choice == "closeall")

    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await query.edit_message_text("redis client not configured — cannot cancel signal")
        return

    payload = {
        "action": "deactivate",
        "close_all": "true" if close_all else "false",
        "timestamp": str(int(time.time())),
    }
    stream = _STREAMS[strat]
    try:
        entry_id = await redis.xadd(stream, payload)
    except Exception as e:
        log.exception("xadd deactivate to %s failed", stream)
        await query.edit_message_text(f"failed to publish cancel: {e}")
        return

    eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
    log.info("%s deactivate published: stream=%s id=%s close_all=%s",
             label, stream, eid, close_all)

    if close_all:
        effect = ("EA will <b>close all open positions</b> + cancel pendings "
                  "and block new entries. Positions close at market.")
    else:
        effect = ("EA will block <b>new</b> entries — open positions keep "
                  "their TP/SL.")
    await query.edit_message_text(
        f"<b>{label} signal cancel sent</b>\n"
        f"stream: <code>{stream}</code>\n"
        f"id: <code>{html.escape(eid)}</code>\n"
        f"close_all: <code>{close_all}</code>\n"
        f"{effect}",
        parse_mode="HTML",
    )


def register(app) -> None:
    """Wire the two commands + the shared callback handler."""
    app.add_handler(CommandHandler("cancel_gvfx", cmd_cancel_gvfx))
    app.add_handler(CommandHandler("cancel_zone", cmd_cancel_zone))
    app.add_handler(CallbackQueryHandler(on_cancel_choice, pattern=rf"^{_CB_PREFIX}:"))
