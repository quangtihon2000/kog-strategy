"""Generic commands: /start, /help, /whoami.

/whoami is unauthed on purpose — operators need their numeric user id
*before* they can be added to the whitelist. It only echoes back what
Telegram already tells the bot.
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
    "/whoami — your Telegram user id\n"
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
