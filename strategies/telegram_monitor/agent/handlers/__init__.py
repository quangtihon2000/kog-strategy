"""Telegram command handlers.

Each handler is small and resolves the (Vps, Service) pair through
Settings.fleet, then calls the matching Transport. Adding a new command
means adding a function here and registering it in `register_handlers`.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from ..config import Settings
from ..transports import Transport
from . import commands, logs, signals, status
from .auth import auth_required

log = logging.getLogger(__name__)


@auth_required
async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline-keyboard taps.

    callback_data shapes:
      - "{action}:{vps}:{service}"           — service_keyboard (logs/tail/signals)
      - "logs_acct:{vps}:{service}:{acct}"   — account_keyboard (logs only)
    """
    query = update.callback_query
    # Always ack so the Telegram client clears the loading spinner.
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    action, vps_name, svc_name = parts[0], parts[1], parts[2]
    mt5_account = parts[3] if action == "logs_acct" and len(parts) >= 4 else None

    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    found = settings.fleet.find_service(svc_name)
    if not found or found[0].name != vps_name:
        await query.message.reply_text(f"unknown service: {vps_name}/{svc_name}")
        return
    vps, svc = found
    transport = transports[vps.name]

    if action == "logs":
        await logs.send_logs(query.message, transport, vps, svc, logs.DEFAULT_LOG_LINES)
    elif action == "logs_acct":
        await logs.send_logs(
            query.message, transport, vps, svc, logs.DEFAULT_LOG_LINES,
            mt5_account=mt5_account,
        )
    elif action == "tail":
        await logs.start_tail(query.message, context.application, transport, vps, svc)
    elif action == "signals":
        await signals.send_signals(query.message, transport, vps, svc)
    else:
        log.warning("unknown callback action: %s", action)


def register_handlers(app: Application) -> None:
    """Wire every command. Settings + transports are pulled from
    `app.bot_data` inside each handler so we don't carry refs around."""
    app.add_handler(CommandHandler("start", commands.cmd_start))
    app.add_handler(CommandHandler("help", commands.cmd_help))
    app.add_handler(CommandHandler("whoami", commands.cmd_whoami))
    app.add_handler(CommandHandler("status", status.cmd_status))
    app.add_handler(CommandHandler("logs", logs.cmd_logs))
    app.add_handler(CommandHandler("tail", logs.cmd_tail))
    app.add_handler(CommandHandler("tailstop", logs.cmd_tailstop))
    app.add_handler(CommandHandler("signals", signals.cmd_signals))
    app.add_handler(CallbackQueryHandler(_on_callback, pattern=r"^(logs|logs_acct|tail|signals):"))
