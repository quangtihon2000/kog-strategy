"""Background monitors. All run on the bot's JobQueue.

Each monitor is independent and stateless across processes — restart-safe.
Add a new check by writing a `tick(...)` coroutine and registering it in
`register_monitors`. Heartbeat is opt-in per service (Redis key presence).
"""

from __future__ import annotations

from telegram.ext import Application

from ..alerts import AlertDispatcher
from ..config import Settings
from ..transports import Transport
from . import conde_lifecycle, heartbeat, log_errors, service_edges, signals_new


def register_monitors(
    app: Application,
    settings: Settings,
    transports: dict[str, Transport],
    alerts: AlertDispatcher,
) -> None:
    jq = app.job_queue
    ctx = {
        "settings": settings,
        "transports": transports,
        "alerts": alerts,
        "redis": app.bot_data.get("redis"),
    }

    # Service state edges — tight loop, cheap nssm calls.
    jq.run_repeating(service_edges.tick, interval=30, first=10,
                     name="mon:service_edges", data=ctx)
    # Log error scan — slightly slower; reads only new bytes.
    jq.run_repeating(log_errors.tick, interval=30, first=20,
                     name="mon:log_errors", data=ctx)
    # New-signal notifications — push parsed signal body to chat on timestamp
    # change. Notification, not alert: signal *staleness* is still on-demand.
    jq.run_repeating(signals_new.tick, interval=20, first=15,
                     name="mon:signals_new", data=ctx)
    # Heartbeat (opt-in) — only fires for services that have ever published one.
    jq.run_repeating(heartbeat.tick, interval=60, first=45,
                     name="mon:heartbeat", data=ctx)
    # Conde lifecycle: owns new-signal notif + reply-once on first outcome
    # in one tick. Phase ordering inside the tick removes the cross-job race
    # the previous split design (signals_new + conde_replies) had.
    jq.run_repeating(conde_lifecycle.tick, interval=10, first=25,
                     name="mon:conde_lifecycle", data=ctx)
