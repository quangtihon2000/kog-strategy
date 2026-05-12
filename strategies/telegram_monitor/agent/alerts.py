"""Alert dispatcher.

Centralizes outbound alerts so monitors don't talk to Telegram directly.
Two guards prevent floods:

1. **Cooldown** — same `dedup_key` won't re-fire within `min_interval_s`.
   Useful for "service still down" or repeated regex matches.
2. **Edge-only** — for boolean state (e.g. RUNNING ↔ STOPPED) the monitor
   should call `notify` only on transitions; the dispatcher itself stays
   stateless about meaning.

Sends to every chat id in `Settings.alert_chat_ids` — user ids (DM) or
negative group/channel ids. Defaults to `allowed_user_ids` when the
`TELEGRAM_ALERT_CHAT_IDS` env var is unset.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from telegram import Bot, InlineKeyboardMarkup

log = logging.getLogger(__name__)

DEFAULT_COOLDOWN_S = 300  # 5 min — re-fire window for the same dedup key


@dataclass
class AlertDispatcher:
    bot: Bot
    chat_ids: frozenset[int]
    cooldown_s: int = DEFAULT_COOLDOWN_S
    _last_sent: dict[str, float] = field(default_factory=dict)

    async def notify(
        self,
        dedup_key: str,
        text: str,
        *,
        force: bool = False,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = "Markdown",
    ) -> bool:
        """Send `text` to every operator. Returns True if actually sent.

        `dedup_key` collapses repeated alerts (e.g. "log_err:zone_signal:Traceback").
        Pass `force=True` for daily summaries or operator-initiated pings.
        `reply_markup` attaches inline buttons (e.g. Edit on bad-message alerts).
        """
        now = time.time()
        last = self._last_sent.get(dedup_key, 0.0)
        if not force and (now - last) < self.cooldown_s:
            return False
        self._last_sent[dedup_key] = now
        for chat_id in self.chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            except Exception as e:
                log.warning("alert send failed (chat=%s key=%s): %s", chat_id, dedup_key, e)
        return True

    async def send_capture(
        self,
        text: str,
        *,
        parse_mode: str | None = "Markdown",
    ) -> list[tuple[int, int]]:
        """Send `text` to every operator and return [(chat_id, message_id), ...].

        Skips cooldown dedup — caller is responsible for not flooding (e.g.
        signals_new uses a per-(file, ts) `_SEEN` table to send once per
        signal). Used when we need the resulting message_ids to later reply
        to those messages (e.g. position-closed reply on new-signal notif).
        """
        refs: list[tuple[int, int]] = []
        for chat_id in self.chat_ids:
            try:
                msg = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                )
                refs.append((chat_id, msg.message_id))
            except Exception as e:
                log.warning("send_capture failed (chat=%s): %s", chat_id, e)
        return refs
