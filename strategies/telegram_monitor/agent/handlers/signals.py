"""/signals — list latest signal JSON files for a service, with age."""

from __future__ import annotations

import html
import time

from telegram import Message, Update
from telegram.ext import ContextTypes

from ..config import Service, Settings, Vps
from ..transports import Transport
from .auth import auth_required
from .formatters import format_signal
from .keyboards import service_keyboard


def _humanize(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


async def send_signals(message: Message, transport: Transport, vps: Vps, svc: Service) -> None:
    if svc.signal_dir is None:
        await message.reply_text(f"{svc.name} has no signal directory configured")
        return
    files = await transport.list_signal_files(svc.signal_dir)
    if not files:
        await message.reply_text(f"no signal files in {svc.signal_dir}")
        return
    now = time.time()
    threshold_s = (svc.signal_freshness_min or 0) * 60
    newest = files[0]
    age = now - newest.mtime_epoch
    glyph = "🟢" if age <= threshold_s else "🔴"
    # HTML parse mode: paths/names contain `_` which break legacy Markdown.
    parts = [
        f"<b>{html.escape(svc.name)}</b> — "
        f"<code>{html.escape(svc.signal_dir)}</code>",
        f"{glyph} <b>{html.escape(newest.name)}</b> — "
        f"{_humanize(age)} ago, {newest.size_bytes}B",
    ]
    data = await transport.read_signal_json(svc.signal_dir, newest.name)
    if data is None:
        parts.append("<i>(could not read newest signal)</i>")
    else:
        body = format_signal(svc.name, data)
        parts.append(f"<pre>{html.escape(body)}</pre>")
    if len(files) > 1:
        older = ["<b>older:</b>"]
        for f in files[1:6]:
            f_age = now - f.mtime_epoch
            older.append(
                f"• <code>{html.escape(f.name)}</code> — {_humanize(f_age)} ago"
            )
        parts.append("\n".join(older))
    await message.reply_html("\n".join(parts))


@auth_required
async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Pick a service for /signals:",
            reply_markup=service_keyboard(settings, "signals"),
        )
        return
    found = settings.fleet.find_service(args[0])
    if not found:
        await update.effective_message.reply_text(f"unknown service: {args[0]}")
        return
    vps, svc = found
    await send_signals(update.effective_message, transports[vps.name], vps, svc)
