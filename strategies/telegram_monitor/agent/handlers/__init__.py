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
from . import commands, gvfx_signal, logs, signals, stats, status, zone_signal
from .auth import auth_required
from .keyboards import account_keyboard, lines_keyboard

log = logging.getLogger(__name__)


@auth_required
async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch inline-keyboard taps.

    callback_data shapes:
      - "{action}:{vps}:{service}"               — service_keyboard (logs/tail/signals)
      - "logs_acct:{vps}:{service}:{acct}"       — account_keyboard (logs only)
      - "logs_n:{vps}:{service}:{acct}:{n}"      — lines_keyboard (acct may be empty)
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
        if svc.mt5_logs and len(svc.mt5_logs) > 1:
            await query.message.reply_text(
                f"{vps.name}/{svc.name} runs on multiple accounts — pick one:",
                reply_markup=account_keyboard(vps.name, svc.name, svc.mt5_logs),
            )
        else:
            await query.message.reply_text(
                f"{vps.name}/{svc.name} — how many lines?",
                reply_markup=lines_keyboard(vps.name, svc.name, None),
            )
    elif action == "logs_acct":
        await query.message.reply_text(
            f"{vps.name}/{svc.name} acct {mt5_account} — how many lines?",
            reply_markup=lines_keyboard(vps.name, svc.name, mt5_account),
        )
    elif action == "logs_n":
        if len(parts) < 5:
            return
        try:
            n = max(1, min(logs.MAX_LOG_LINES, int(parts[4])))
        except ValueError:
            return
        acct = parts[3] or None
        await logs.send_logs(query.message, transport, vps, svc, n, mt5_account=acct)
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
    app.add_handler(CommandHandler("chatid", commands.cmd_chatid))
    app.add_handler(CommandHandler("status", status.cmd_status))
    app.add_handler(CommandHandler("logs", logs.cmd_logs))
    app.add_handler(CommandHandler("tail", logs.cmd_tail))
    app.add_handler(CommandHandler("tailstop", logs.cmd_tailstop))
    app.add_handler(CommandHandler("signals", signals.cmd_signals))
    app.add_handler(CommandHandler("stats", stats.cmd_stats))
    app.add_handler(gvfx_signal.conversation_handler())
    app.add_handler(zone_signal.conversation_handler())
    app.add_handler(CallbackQueryHandler(_on_callback, pattern=r"^(logs|logs_acct|logs_n|tail|signals):"))
