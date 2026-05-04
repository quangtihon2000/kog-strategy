"""/signals — list latest signal JSON files for a service, with age."""

from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..transports import Transport
from .auth import auth_required


def _humanize(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


@auth_required
async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("usage: /signals <service>")
        return
    found = settings.fleet.find_service(args[0])
    if not found:
        await update.effective_message.reply_text(f"unknown service: {args[0]}")
        return
    vps, svc = found
    files = await transports[vps.name].list_signal_files(svc.signal_dir)
    if not files:
        await update.effective_message.reply_text(
            f"no signal files in {svc.signal_dir}"
        )
        return

    now = time.time()
    threshold_s = svc.signal_freshness_min * 60
    lines = [f"*{vps.name}/{svc.name}* — {svc.signal_dir}"]
    for f in files[:10]:
        age = now - f.mtime_epoch
        glyph = "🟢" if age <= threshold_s else "🔴"
        lines.append(f"{glyph} `{f.name}` — {_humanize(age)} ago, {f.size_bytes}B")
    await update.effective_message.reply_markdown("\n".join(lines))
