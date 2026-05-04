"""/status — fleet-wide service health snapshot.

Bare /status walks the whole fleet in parallel and shows, per service:
service state + age of the newest signal file (with a freshness glyph
based on `signal_freshness_min`). The signal column replaces the old
auto-paging freshness monitor — same info, on demand.

/status <svc> gives the raw nssm output for one service, useful when
state==UNKNOWN.
"""

from __future__ import annotations

import asyncio
import time

from telegram import Update
from telegram.ext import ContextTypes

from ..config import Service, Settings, Vps
from ..transports import ServiceState, Transport
from .auth import auth_required
from .signals import _humanize

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


async def _signal_age_s(transport: Transport, svc: Service, now: float) -> float | None:
    """Age (seconds) of newest signal file, or None if dir is empty/unreadable."""
    try:
        files = await transport.list_signal_files(svc.signal_dir)
    except Exception:
        return None
    if not files:
        return None
    return now - files[0].mtime_epoch


def _signal_segment(age_s: float | None, svc: Service) -> str:
    if age_s is None:
        return ""
    threshold_s = svc.signal_freshness_min * 60
    glyph = "🟢" if age_s <= threshold_s else "🔴"
    suffix = "" if age_s <= threshold_s else f" >{svc.signal_freshness_min}m"
    return f" · signal {_humanize(age_s)} {glyph}{suffix}"


async def _status_all(update, settings: Settings, transports: dict[str, Transport]) -> None:
    pairs = settings.fleet.all_services()
    now = time.time()

    async def probe(vps: Vps, svc: Service):
        st_task = transports[vps.name].get_service_status(svc.nssm_service)
        sig_task = _signal_age_s(transports[vps.name], svc, now)
        st, age = await asyncio.gather(st_task, sig_task)
        return vps, svc, st, age

    results = await asyncio.gather(*(probe(v, s) for v, s in pairs))

    lines = ["*Fleet status*"]
    by_vps: dict[str, list[str]] = {}
    for vps, svc, st, age in results:
        glyph = _STATE_GLYPH.get(st.state, "❓")
        line = f"{glyph} `{svc.name}` — {st.state.value}{_signal_segment(age, svc)}"
        by_vps.setdefault(vps.name, []).append(line)
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
