"""Whitelist guard.

Empty whitelist = bot rejects everyone. Non-whitelisted users are silently
ignored (no reply) so the bot doesn't leak its existence to scrapers; the
attempt is still logged for the operator.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def auth_required(fn: Handler) -> Handler:
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        settings: Settings = context.application.bot_data["settings"]
        user = update.effective_user
        uid = user.id if user else None
        if uid is None or uid not in settings.allowed_user_ids:
            log.warning("rejected message from uid=%s username=%s",
                        uid, getattr(user, "username", None))
            return None
        return await fn(update, context)
    return wrapper
