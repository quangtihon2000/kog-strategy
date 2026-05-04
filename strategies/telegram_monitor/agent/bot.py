"""Bot factory: builds the Application, wires transports/handlers/monitors.

Kept separate from `main.py` so tests (or a future REPL) can construct the
app without taking over the event loop with `run_polling()`.
"""

from __future__ import annotations

import logging

from telegram.ext import Application, ApplicationBuilder

from .alerts import AlertDispatcher
from .config import Settings
from .handlers import register_handlers
from .monitors import register_monitors
from .transports import Transport, get_transport

log = logging.getLogger(__name__)


def _build_transports(settings: Settings) -> dict[str, Transport]:
    transports: dict[str, Transport] = {}
    for vps in settings.fleet.vpses:
        transports[vps.name] = get_transport(vps.transport)
        log.info("transport ready: vps=%s kind=%s", vps.name, vps.transport)
    return transports


def build_app(settings: Settings) -> Application:
    app = ApplicationBuilder().token(settings.bot_token).build()
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
