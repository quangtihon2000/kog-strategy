"""/gvfx — push a GVFX signal onto the Redis stream consumed by gvfx_signal_agent.

Two modes:
  - Fast path:  /gvfx <target> [direction] [step] [tp] [low] [high] [atr]
  - Wizard:     /gvfx           (no args → guided flow with inline buttons)

Defaults: direction=BUY, step=500, tp=500, low=0, high=0, atr=true. Symbol
is fixed to XAUUSD (matching gvfx_signal's single-symbol contract). Timestamp
is generated here (Unix epoch seconds) and acts as the EA's dedup identity.

low/high are optional price-zone gates (0 = disabled). When set, the EA
only opens BUY when price > low, and only opens SELL when price < high.

atr (bool): when true the EA derives effective step/tp from a cached iATR
handle; signal-supplied step/tp become fallback values used only when the
ATR buffer is unavailable.
"""

from __future__ import annotations

import html
import logging
import time

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

log = logging.getLogger(__name__)

DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_DIRECTION = "BUY"
DEFAULT_STEP = 500
DEFAULT_TP = 500
DEFAULT_USE_ATR = True
GVFX_STREAM = "gvfx_signals"

USAGE = (
    "usage: <code>/gvfx &lt;target&gt; [direction] [step] [tp] [low] [high] [atr]</code>\n"
    "or send <code>/gvfx</code> alone for the wizard\n"
    "defaults: direction=BUY, step=500, tp=500, low=0, high=0, atr=true\n"
    "low/high (price): 0 disables. BUY enters only above low; SELL only below high.\n"
    "atr (true/false): EA uses ATR-derived step/tp; signal step/tp become fallback."
)

_ATR_TRUE = frozenset({"1", "true", "yes", "on", "y", "t"})
_ATR_FALSE = frozenset({"0", "false", "no", "off", "n", "f"})


def _parse_atr_arg(s: str) -> bool | None:
    v = s.strip().lower()
    if v in _ATR_TRUE:
        return True
    if v in _ATR_FALSE:
        return False
    return None

W_DIRECTION, W_TARGET, W_REVIEW, W_STEPTP, W_LOWHIGH = range(5)
WIZARD_KEY = "gvfx_wizard"


def _direction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 BUY", callback_data="gvfxw:dir:BUY"),
            InlineKeyboardButton("🔴 SELL", callback_data="gvfxw:dir:SELL"),
        ],
        [InlineKeyboardButton("✖ Cancel", callback_data="gvfxw:cancel")],
    ])


def _review_keyboard(use_atr: bool = DEFAULT_USE_ATR) -> InlineKeyboardMarkup:
    atr_label = f"ATR: {'ON' if use_atr else 'OFF'}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data="gvfxw:publish")],
        [
            InlineKeyboardButton("✏ Edit step/tp", callback_data="gvfxw:edit_steptp"),
            InlineKeyboardButton("✏ Edit low/high", callback_data="gvfxw:edit_lowhigh"),
        ],
        [InlineKeyboardButton(atr_label, callback_data="gvfxw:toggle_atr")],
        [InlineKeyboardButton("✖ Cancel", callback_data="gvfxw:cancel")],
    ])


def _review_text(state: dict) -> str:
    direction = state.get("direction", DEFAULT_DIRECTION)
    target = state.get("target", 0.0)
    step = state.get("step", DEFAULT_STEP)
    tp = state.get("tp", DEFAULT_TP)
    low = state.get("low", 0.0)
    high = state.get("high", 0.0)
    use_atr = state.get("use_atr", DEFAULT_USE_ATR)
    gate = ""
    if low > 0 or high > 0:
        gate = (
            f"low: <code>{low}</code>"
            + (" (BUY gate)" if direction == "BUY" and low > 0 else "")
            + "\n"
            f"high: <code>{high}</code>"
            + (" (SELL gate)" if direction == "SELL" and high > 0 else "")
            + "\n"
        )
    else:
        gate = "low/high: <code>disabled</code>\n"
    atr_label = "ON (signal step/tp = fallback)" if use_atr else "OFF"
    step_tp_suffix = " (fallback)" if use_atr else ""
    return (
        "<b>review:</b>\n"
        f"direction: <b>{direction}</b>\n"
        f"target: <code>{target}</code>\n"
        f"step: <code>{step}</code> pts{step_tp_suffix}\n"
        f"tp: <code>{tp}</code> pts{step_tp_suffix}\n"
        f"{gate}"
        f"atr: <b>{atr_label}</b>\n"
    )


async def _publish(
    msg: Message,
    redis: Redis,
    *,
    target: float,
    direction: str,
    step: int,
    tp: int,
    low: float,
    high: float,
    use_atr: bool,
) -> None:
    payload = {
        "timestamp": str(int(time.time())),
        "symbol": DEFAULT_SYMBOL,
        "target": str(target),
        "direction": direction,
        "step": str(step),
        "tp": str(tp),
        "low": str(low),
        "high": str(high),
        "use_atr": "true" if use_atr else "false",
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
        f"low: <code>{low}</code>\n"
        f"high: <code>{high}</code>\n"
        f"atr: <code>{'ON' if use_atr else 'OFF'}</code>\n"
        f"ts: <code>{payload['timestamp']}</code>"
    )


@auth_required
async def cmd_gvfx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point. With args → publish immediately; without args → wizard."""
    msg = update.effective_message
    args = context.args or []

    if not args:
        context.user_data[WIZARD_KEY] = {}
        await msg.reply_html(
            "<b>GVFX signal wizard</b> — step 1/3\nchoose direction:",
            reply_markup=_direction_keyboard(),
        )
        return W_DIRECTION

    try:
        target = float(args[0])
    except ValueError:
        await msg.reply_html(f"target must be a number\n{USAGE}")
        return ConversationHandler.END
    if target <= 0:
        await msg.reply_html("target must be positive")
        return ConversationHandler.END

    direction = (args[1] if len(args) >= 2 else DEFAULT_DIRECTION).upper()
    if direction not in ("BUY", "SELL"):
        await msg.reply_html("direction must be BUY or SELL")
        return ConversationHandler.END

    try:
        step = int(args[2]) if len(args) >= 3 else DEFAULT_STEP
        tp = int(args[3]) if len(args) >= 4 else DEFAULT_TP
    except ValueError:
        await msg.reply_html(f"step/tp must be integers (points)\n{USAGE}")
        return ConversationHandler.END
    if step <= 0 or tp <= 0:
        await msg.reply_html("step and tp must be positive")
        return ConversationHandler.END

    try:
        low = float(args[4]) if len(args) >= 5 else 0.0
        high = float(args[5]) if len(args) >= 6 else 0.0
    except ValueError:
        await msg.reply_html(f"low/high must be numbers\n{USAGE}")
        return ConversationHandler.END
    if low < 0 or high < 0:
        await msg.reply_html("low and high must be >= 0 (use 0 to disable)")
        return ConversationHandler.END
    if low > 0 and high > 0 and low >= high:
        await msg.reply_html("low must be &lt; high when both are set")
        return ConversationHandler.END

    use_atr = DEFAULT_USE_ATR
    if len(args) >= 7:
        parsed = _parse_atr_arg(args[6])
        if parsed is None:
            await msg.reply_html(f"atr must be true/false (or 1/0, yes/no)\n{USAGE}")
            return ConversationHandler.END
        use_atr = parsed

    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await msg.reply_text("redis client not configured — cannot publish signal")
        return ConversationHandler.END

    await _publish(
        msg, redis,
        target=target, direction=direction,
        step=step, tp=tp, low=low, high=high,
        use_atr=use_atr,
    )
    return ConversationHandler.END


@auth_required
async def _w_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) >= 2 and parts[1] == "cancel":
        await query.edit_message_text("wizard cancelled")
        context.user_data.pop(WIZARD_KEY, None)
        return ConversationHandler.END

    direction = parts[2] if len(parts) >= 3 and parts[1] == "dir" else DEFAULT_DIRECTION
    if direction not in ("BUY", "SELL"):
        return W_DIRECTION

    context.user_data.setdefault(WIZARD_KEY, {})["direction"] = direction
    await query.edit_message_text(
        f"direction = <b>{direction}</b>\n"
        "step 2/3 — send the <b>target price</b> (e.g. <code>4600</code>)",
        parse_mode="HTML",
    )
    return W_TARGET


@auth_required
async def _w_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    try:
        target = float(text)
    except ValueError:
        await msg.reply_text("target must be a number — try again or /cancel")
        return W_TARGET
    if target <= 0:
        await msg.reply_text("target must be positive — try again or /cancel")
        return W_TARGET

    state = context.user_data.setdefault(WIZARD_KEY, {})
    state["target"] = target
    state.setdefault("step", DEFAULT_STEP)
    state.setdefault("tp", DEFAULT_TP)
    state.setdefault("low", 0.0)
    state.setdefault("high", 0.0)
    state.setdefault("use_atr", DEFAULT_USE_ATR)

    await msg.reply_html(
        "step 3/3 — " + _review_text(state),
        reply_markup=_review_keyboard(state["use_atr"]),
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

    if action == "edit_steptp":
        await query.edit_message_text(
            "send <b>step tp</b> as two integers (points), e.g. <code>500 500</code>",
            parse_mode="HTML",
        )
        return W_STEPTP

    if action == "edit_lowhigh":
        await query.edit_message_text(
            "send <b>low high</b> as two prices (0 = disabled), e.g. "
            "<code>4500 4700</code> or <code>4500 0</code>",
            parse_mode="HTML",
        )
        return W_LOWHIGH

    if action == "toggle_atr":
        state = context.user_data.setdefault(WIZARD_KEY, {})
        state["use_atr"] = not state.get("use_atr", DEFAULT_USE_ATR)
        await query.edit_message_text(
            _review_text(state),
            parse_mode="HTML",
            reply_markup=_review_keyboard(state["use_atr"]),
        )
        return W_REVIEW

    if action == "publish":
        state = context.user_data.get(WIZARD_KEY, {})
        if "target" not in state:
            await query.edit_message_text("missing target — wizard aborted")
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
            target=state["target"],
            direction=state.get("direction", DEFAULT_DIRECTION),
            step=state.get("step", DEFAULT_STEP),
            tp=state.get("tp", DEFAULT_TP),
            low=state.get("low", 0.0),
            high=state.get("high", 0.0),
            use_atr=state.get("use_atr", DEFAULT_USE_ATR),
        )
        context.user_data.pop(WIZARD_KEY, None)
        return ConversationHandler.END

    return W_REVIEW


@auth_required
async def _w_steptp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) != 2:
        await msg.reply_text("send exactly two integers: <step> <tp>, or /cancel")
        return W_STEPTP
    try:
        step = int(parts[0])
        tp = int(parts[1])
    except ValueError:
        await msg.reply_text("step/tp must be integers (points) — try again or /cancel")
        return W_STEPTP
    if step <= 0 or tp <= 0:
        await msg.reply_text("step and tp must be positive — try again or /cancel")
        return W_STEPTP

    state = context.user_data.setdefault(WIZARD_KEY, {})
    state["step"] = step
    state["tp"] = tp
    await msg.reply_html(
        _review_text(state),
        reply_markup=_review_keyboard(state.get("use_atr", DEFAULT_USE_ATR)),
    )
    return W_REVIEW


@auth_required
async def _w_lowhigh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    text = (msg.text or "").strip()
    parts = text.split()
    if len(parts) != 2:
        await msg.reply_text("send exactly two numbers: <low> <high>, or /cancel")
        return W_LOWHIGH
    try:
        low = float(parts[0])
        high = float(parts[1])
    except ValueError:
        await msg.reply_text("low/high must be numbers — try again or /cancel")
        return W_LOWHIGH
    if low < 0 or high < 0:
        await msg.reply_text("low and high must be >= 0 (use 0 to disable)")
        return W_LOWHIGH
    if low > 0 and high > 0 and low >= high:
        await msg.reply_text("low must be < high when both are set — try again or /cancel")
        return W_LOWHIGH

    state = context.user_data.setdefault(WIZARD_KEY, {})
    state["low"] = low
    state["high"] = high
    await msg.reply_html(
        _review_text(state),
        reply_markup=_review_keyboard(state.get("use_atr", DEFAULT_USE_ATR)),
    )
    return W_REVIEW


async def _w_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(WIZARD_KEY, None)
    await update.effective_message.reply_text("wizard cancelled")
    return ConversationHandler.END


def conversation_handler() -> ConversationHandler:
    """Wraps the entry command + wizard states. Register this in place of the
    bare CommandHandler — the args path still works because cmd_gvfx returns
    ConversationHandler.END when args are present."""
    return ConversationHandler(
        entry_points=[CommandHandler("gvfx", cmd_gvfx)],
        states={
            W_DIRECTION: [CallbackQueryHandler(_w_direction, pattern=r"^gvfxw:")],
            W_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_target)],
            W_REVIEW: [CallbackQueryHandler(_w_review, pattern=r"^gvfxw:")],
            W_STEPTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_steptp)],
            W_LOWHIGH: [MessageHandler(filters.TEXT & ~filters.COMMAND, _w_lowhigh)],
        },
        fallbacks=[CommandHandler("cancel", _w_cancel)],
        allow_reentry=True,
        per_chat=True,
    )
