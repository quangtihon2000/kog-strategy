"""Telegram command handlers.

Each handler is small and resolves the (Vps, Service) pair through
Settings.fleet, then calls the matching Transport. Adding a new command
means adding a function here and registering it in `register_handlers`.
"""

from __future__ import annotations

from telegram.ext import Application, CommandHandler

from . import commands, logs, signals, status


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
