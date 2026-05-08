"""Edit and republish workflow for agent "Bad message" alerts.

Triggered by an inline `[✏ Edit]` button attached by `monitors/log_errors.py`
when an agent emits a parseable `Bad message ... raw=<dict>` line. The bot
caches the original payload in Redis (key `badmsg:{token}`, 24h TTL); the
button's callback_data carries that token.

Flow:
  alert  ── tap Edit ──▶  S_AWAIT_JSON  (bot shows pretty JSON, asks for fix)
                            │
                            └── JSON reply ──▶  S_REVIEW  (Publish / Edit again / Cancel)
                                                  │
                                                  └── Publish ──▶  XADD to spec.stream

Cache miss (TTL'd or bot restart cleared Redis) is reported clearly and the
conversation ends.
"""

from __future__ import annotations

import html
import json
import logging
import re

from redis.asyncio import Redis
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .auth import auth_required
from .badmsg_registry import get_spec, known_streams_help

log = logging.getLogger(__name__)

S_AWAIT_JSON, S_REVIEW = range(2)
STATE_KEY = "badmsg_state"

# Code-fence wrappers users often paste back with the corrected JSON.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _review_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data=f"badmsg:publish:{token}")],
        [InlineKeyboardButton("✏ Edit again", callback_data=f"badmsg:edit_again:{token}")],
        [InlineKeyboardButton("✖ Cancel", callback_data=f"badmsg:cancel:{token}")],
    ])


def _format_payload_block(payload: dict) -> str:
    return "<pre><code class=\"language-json\">" + html.escape(
        json.dumps(payload, indent=2, ensure_ascii=False)
    ) + "</code></pre>"


async def _load_cache(redis: Redis, token: str) -> dict | None:
    """Fetch cached badmsg record. Returns None on miss or decode error."""
    try:
        raw = await redis.get(f"badmsg:{token}")
    except Exception as e:
        log.warning("badmsg cache read failed (token=%s): %s", token, e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        log.warning("badmsg cache decode failed (token=%s): %s", token, e)
        return None


def _missing_required(payload: dict, required: tuple[str, ...]) -> list[str]:
    return [f for f in required if f not in payload]


@auth_required
async def _on_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — fired when the user taps `[✏ Edit]` on an alert."""
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3:
        return ConversationHandler.END
    token = parts[2]

    redis: Redis | None = context.application.bot_data.get("redis")
    if redis is None:
        await query.message.reply_text("redis client not configured — cannot load bad message")
        return ConversationHandler.END

    record = await _load_cache(redis, token)
    if record is None:
        await query.message.reply_text(
            "session expired (>24h or bot restart cleared cache) — "
            "use /zone, /conde, or /gvfx to publish manually"
        )
        return ConversationHandler.END

    service = record.get("service", "")
    spec = get_spec(service)
    payload = record.get("payload") or {}
    exc = record.get("exc", "")

    if spec is None:
        # Service not in the registry — no automatic republish path. Show the
        # parsed payload so the operator can hand-publish via an existing wizard.
        await query.message.reply_html(
            f"<b>Bad message — {html.escape(service or 'unknown')}</b>\n"
            f"exception: <code>{html.escape(exc)}</code>\n"
            "no automatic publish path for this service. parsed payload:\n"
            f"{_format_payload_block(payload)}\n"
            "publish manually via one of:\n"
            f"<pre>{html.escape(known_streams_help())}</pre>"
        )
        return ConversationHandler.END

    context.user_data[STATE_KEY] = {
        "token": token,
        "service": spec.service_name,
        "stream": spec.stream,
        "required": list(spec.required_fields),
        "optional": list(spec.optional_fields),
        "payload": payload,
    }

    await query.message.reply_html(
        f"<b>Edit bad message — {html.escape(spec.service_name)}</b>\n"
        f"exception: <code>{html.escape(exc)}</code>\n"
        f"target stream: <code>{html.escape(spec.stream)}</code>\n"
        "original payload:\n"
        f"{_format_payload_block(payload)}\n"
        f"required: <code>{html.escape(', '.join(spec.required_fields))}</code>\n"
        "reply with corrected JSON, or /cancel"
    )
    return S_AWAIT_JSON


@auth_required
async def _on_json_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse the operator's JSON reply, validate shape, show review screen."""
    msg = update.effective_message
    state = context.user_data.get(STATE_KEY)
    if state is None:
        await msg.reply_text("no active edit session — tap Edit on an alert to start")
        return ConversationHandler.END

    text = _strip_fence((msg.text or "").strip())
    try:
        fixed = json.loads(text)
    except json.JSONDecodeError as e:
        await msg.reply_text(f"invalid JSON: {e} — try again or /cancel")
        return S_AWAIT_JSON
    if not isinstance(fixed, dict):
        await msg.reply_text("payload must be a JSON object — try again or /cancel")
        return S_AWAIT_JSON

    missing = _missing_required(fixed, tuple(state["required"]))
    if missing:
        await msg.reply_text(
            f"missing required fields: {', '.join(missing)} — try again or /cancel"
        )
        return S_AWAIT_JSON

    state["payload"] = fixed
    await msg.reply_html(
        f"<b>Review — {html.escape(state['service'])}</b>\n"
        f"will publish to stream: <code>{html.escape(state['stream'])}</code>\n"
        f"{_format_payload_block(fixed)}",
        reply_markup=_review_keyboard(state["token"]),
    )
    return S_REVIEW


@auth_required
async def _on_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Publish / Edit-again / Cancel from the review screen."""
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3:
        return S_REVIEW
    action, token = parts[1], parts[2]

    state = context.user_data.get(STATE_KEY)
    if state is None or state.get("token") != token:
        await query.edit_message_text("session lost — tap Edit on the alert again")
        return ConversationHandler.END

    if action == "cancel":
        await query.edit_message_text("cancelled")
        context.user_data.pop(STATE_KEY, None)
        return ConversationHandler.END

    if action == "edit_again":
        await query.edit_message_text(
            f"<b>Edit again — {html.escape(state['service'])}</b>\n"
            f"current payload:\n{_format_payload_block(state['payload'])}\n"
            f"required: <code>{html.escape(', '.join(state['required']))}</code>\n"
            "reply with corrected JSON, or /cancel",
            parse_mode="HTML",
        )
        return S_AWAIT_JSON

    if action == "publish":
        spec = get_spec(state["service"])
        if spec is None:
            await query.edit_message_text(f"unknown service: {state['service']}")
            context.user_data.pop(STATE_KEY, None)
            return ConversationHandler.END
        redis: Redis | None = context.application.bot_data.get("redis")
        if redis is None:
            await query.edit_message_text("redis client not configured — cannot publish")
            context.user_data.pop(STATE_KEY, None)
            return ConversationHandler.END

        try:
            flat = spec.flatten(state["payload"])
        except (KeyError, ValueError, TypeError) as e:
            await query.edit_message_text(f"flatten failed: {e}")
            return S_REVIEW

        try:
            entry_id = await redis.xadd(spec.stream, flat)
        except Exception as e:
            log.exception("xadd to %s failed", spec.stream)
            await query.edit_message_text(f"failed to publish: {e}")
            return S_REVIEW

        # Best-effort: clear the cache so the same token can't republish twice.
        try:
            await redis.delete(f"badmsg:{token}")
        except Exception as e:
            log.warning("badmsg cache delete failed (token=%s): %s", token, e)

        log.info(
            "badmsg republished: service=%s stream=%s id=%s payload=%s",
            spec.service_name, spec.stream, entry_id, flat,
        )
        entry_str = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        await query.edit_message_text(
            f"<b>republished</b>\n"
            f"stream: <code>{html.escape(spec.stream)}</code>\n"
            f"id: <code>{html.escape(entry_str)}</code>",
            parse_mode="HTML",
        )
        context.user_data.pop(STATE_KEY, None)
        return ConversationHandler.END

    return S_REVIEW


@auth_required
async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(STATE_KEY, None)
    await update.effective_message.reply_text("cancelled")
    return ConversationHandler.END


def conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(_on_edit, pattern=r"^badmsg:edit:")],
        states={
            S_AWAIT_JSON: [MessageHandler(filters.TEXT & ~filters.COMMAND, _on_json_reply)],
            S_REVIEW: [
                CallbackQueryHandler(
                    _on_review,
                    pattern=r"^badmsg:(publish|edit_again|cancel):",
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        allow_reentry=True,
        per_chat=True,
    )
