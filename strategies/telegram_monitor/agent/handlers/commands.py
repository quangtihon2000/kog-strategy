"""Generic commands: /start, /help, /whoami, /chatid.

/whoami and /chatid are unauthed on purpose — operators need the numeric
ids *before* they can be added to TELEGRAM_ALLOWED_USER_IDS or
TELEGRAM_ALERT_CHAT_IDS. They only echo back what Telegram already
tells the bot.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .auth import auth_required

HELP_TEXT = (
    "*KOG fleet monitor* (read-only)\n"
    "\n"
    "/status — service state for every agent\n"
    "/status `<svc>` — verbose state for one service\n"
    "/logs `<svc>` `[N]` — last N lines per log type (default 10)\n"
    "/tail `<svc>` — live tail, batched every 5s\n"
    "/tailstop — stop the active tail in this chat\n"
    "/signals `<svc>` — newest signal files + age\n"
    "/gvfx — publish GVFX signal; no args = wizard with buttons,\n"
    "         or `<target>` `[dir]` `[step]` `[tp]` `[low]` `[high]` (defaults: BUY/500/500/0/0)\n"
    "         low/high are price-zone gates: BUY only above low, SELL only below high (0 = disabled)\n"
    "/cancel — abort the GVFX wizard mid-flow\n"
    "/whoami — your Telegram user id\n"
    "/chatid — this chat's id (for TELEGRAM_ALERT_CHAT_IDS)\n"
    "/help — this message"
)


@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_markdown(HELP_TEXT)


@auth_required
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_markdown(HELP_TEXT)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.effective_message.reply_text(
        f"user_id: {user.id}\nusername: @{user.username or '-'}"
    )


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    title = chat.title or chat.username or "(private)"
    await update.effective_message.reply_text(
        f"chat_id: {chat.id}\ntype: {chat.type}\ntitle: {title}"
    )
