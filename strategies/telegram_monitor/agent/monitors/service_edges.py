"""Alert when a service transitions away from RUNNING (and when it recovers).

Edge-only on purpose — a steady STOPPED state generates one alert, not
one per tick. State is in-process; on bot restart we treat the first
observation as baseline (no alert) so we don't spam after a redeploy.
"""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import ServiceState, Transport

log = logging.getLogger(__name__)

# (vps, service) -> last observed state
_PREV: dict[tuple[str, str], ServiceState] = {}


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]

    for vps, svc in settings.fleet.all_services():
        try:
            st = await transports[vps.name].get_service_status(svc.nssm_service)
        except Exception as e:
            log.warning("service probe failed (%s/%s): %s", vps.name, svc.name, e)
            continue
        key = (vps.name, svc.name)
        prev = _PREV.get(key)
        _PREV[key] = st.state
        if prev is None or prev == st.state:
            continue
        # Transition detected.
        if prev == ServiceState.RUNNING and st.state != ServiceState.RUNNING:
            await alerts.notify(
                dedup_key=f"svc_down:{vps.name}:{svc.name}",
                text=f"🔴 *{vps.name}/{svc.name}* — `{prev.value}` → `{st.state.value}`",
            )
        elif st.state == ServiceState.RUNNING and prev != ServiceState.RUNNING:
            await alerts.notify(
                dedup_key=f"svc_up:{vps.name}:{svc.name}",
                text=f"🟢 *{vps.name}/{svc.name}* — recovered (`{st.state.value}`)",
            )
