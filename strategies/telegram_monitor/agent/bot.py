"""Bot factory: builds the Application, wires transports/handlers/monitors.

Kept separate from `main.py` so tests (or a future REPL) can construct the
app without taking over the event loop with `run_polling()`.
"""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from .alerts import AlertDispatcher
from .config import Settings
from .handlers import register_handlers
from .monitors import register_monitors
from .transports import Transport, get_transport

log = logging.getLogger(__name__)

# Shown in Telegram's "/" autocomplete and the menu button. Order = display
# order. set_my_commands runs every startup so this list is the source of
# truth — no need to /setcommands in BotFather manually.
_BOT_COMMANDS: list[BotCommand] = [
    BotCommand("status", "Service state + signal freshness"),
    BotCommand("logs", "Last N log lines (agent + MT5)"),
    BotCommand("tail", "Live tail (5s batched)"),
    BotCommand("tailstop", "Stop active tail"),
    BotCommand("signals", "Newest signal files + age"),
    BotCommand("whoami", "Your Telegram user id"),
    BotCommand("help", "Show commands"),
]


def _build_transports(settings: Settings) -> dict[str, Transport]:
    transports: dict[str, Transport] = {}
    for vps in settings.fleet.vpses:
        transports[vps.name] = get_transport(vps.transport)
        log.info("transport ready: vps=%s kind=%s", vps.name, vps.transport)
    return transports


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(_BOT_COMMANDS)
        log.info("registered %d bot commands with Telegram", len(_BOT_COMMANDS))
    except Exception as e:
        # Non-fatal — bot still works, just no autocomplete menu.
        log.warning("set_my_commands failed: %s", e)


def build_app(settings: Settings) -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .build()
    )
    transports = _build_transports(settings)
    alerts = AlertDispatcher(
        bot=app.bot,
        chat_ids=settings.allowed_user_ids,
    )

    # Stash for handlers (auth decorator + commands pull from here).
    app.bot_data["settings"] = settings
    app.bot_data["transports"] = transports
    app.bot_data["alerts"] = alerts

    register_handlers(app)
    register_monitors(app, settings, transports, alerts)
    return app
