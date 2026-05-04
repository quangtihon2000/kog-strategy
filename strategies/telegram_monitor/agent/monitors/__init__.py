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
from . import heartbeat, log_errors, service_edges


def register_monitors(
    app: Application,
    settings: Settings,
    transports: dict[str, Transport],
    alerts: AlertDispatcher,
) -> None:
    jq = app.job_queue
    ctx = {"settings": settings, "transports": transports, "alerts": alerts}

    # Service state edges — tight loop, cheap nssm calls.
    jq.run_repeating(service_edges.tick, interval=30, first=10,
                     name="mon:service_edges", data=ctx)
    # Log error scan — slightly slower; reads only new bytes.
    jq.run_repeating(log_errors.tick, interval=30, first=20,
                     name="mon:log_errors", data=ctx)
    # Signal freshness is surfaced on-demand via /status (no auto-alert) —
    # service_edges already pages when the agent process is the actual problem.
    # Heartbeat (opt-in) — only fires for services that have ever published one.
    jq.run_repeating(heartbeat.tick, interval=60, first=45,
                     name="mon:heartbeat", data=ctx)
