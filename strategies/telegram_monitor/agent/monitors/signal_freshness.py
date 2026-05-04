"""Alert when newest signal file is older than the per-service threshold.

Cooldown lives in the dispatcher (5 min default), so a service stuck
stale for an hour pages once at first detection then every 5 min — quiet
enough to ignore on weekends, loud enough to notice on a workday.
"""

from __future__ import annotations

import logging
import time

from telegram.ext import ContextTypes

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import Transport

log = logging.getLogger(__name__)


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = context.job.data
    settings: Settings = ctx["settings"]
    transports: dict[str, Transport] = ctx["transports"]
    alerts: AlertDispatcher = ctx["alerts"]
    now = time.time()

    for vps, svc in settings.fleet.all_services():
        try:
            files = await transports[vps.name].list_signal_files(svc.signal_dir)
        except Exception as e:
            log.warning("signal probe failed (%s/%s): %s", vps.name, svc.name, e)
            continue
        if not files:
            # No signals at all — could mean fresh deploy. Don't alert; the
            # service-down monitor covers the "agent isn't running" case.
            continue
        newest = files[0]
        age_min = (now - newest.mtime_epoch) / 60
        if age_min <= svc.signal_freshness_min:
            continue
        await alerts.notify(
            dedup_key=f"stale:{vps.name}:{svc.name}",
            text=(
                f"⏱ *{vps.name}/{svc.name}* — newest signal `{newest.name}` "
                f"is {age_min:.1f} min old (limit {svc.signal_freshness_min} min)"
            ),
        )
