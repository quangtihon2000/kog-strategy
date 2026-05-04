"""/status — fleet-wide service health snapshot.

Bare /status walks the whole fleet in parallel. /status <svc> gives the
raw nssm output for one service, useful when state==UNKNOWN.
"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Settings
from ..transports import ServiceState, Transport
from .auth import auth_required

_STATE_GLYPH = {
    ServiceState.RUNNING: "🟢",
    ServiceState.STOPPED: "🔴",
    ServiceState.PAUSED: "🟡",
    ServiceState.START_PENDING: "🟡",
    ServiceState.STOP_PENDING: "🟡",
    ServiceState.NOT_INSTALLED: "⚪",
    ServiceState.UNKNOWN: "❓",
}


@auth_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    transports: dict[str, Transport] = context.application.bot_data["transports"]
    args = context.args or []

    if args:
        await _status_one(update, settings, transports, args[0])
    else:
        await _status_all(update, settings, transports)


async def _status_all(update, settings: Settings, transports: dict[str, Transport]) -> None:
    pairs = settings.fleet.all_services()

    async def probe(vps, svc):
        st = await transports[vps.name].get_service_status(svc.nssm_service)
        return vps, svc, st

    results = await asyncio.gather(*(probe(v, s) for v, s in pairs))

    lines = ["*Fleet status*"]
    by_vps: dict[str, list[str]] = {}
    for vps, svc, st in results:
        glyph = _STATE_GLYPH.get(st.state, "❓")
        by_vps.setdefault(vps.name, []).append(f"{glyph} `{svc.name}` — {st.state.value}")
    for vps_name, items in by_vps.items():
        lines.append(f"\n_{vps_name}_")
        lines.extend(items)
    await update.effective_message.reply_markdown("\n".join(lines))


async def _status_one(update, settings: Settings, transports: dict[str, Transport], name: str) -> None:
    found = settings.fleet.find_service(name)
    if not found:
        await update.effective_message.reply_text(f"unknown service: {name}")
        return
    vps, svc = found
    st = await transports[vps.name].get_service_status(svc.nssm_service)
    glyph = _STATE_GLYPH.get(st.state, "❓")
    body = (
        f"{glyph} *{svc.name}* on _{vps.name}_\n"
        f"state: `{st.state.value}`\n"
        f"nssm: `{svc.nssm_service}`\n\n"
        f"```\n{st.raw[:1500]}\n```"
    )
    await update.effective_message.reply_markdown(body)
