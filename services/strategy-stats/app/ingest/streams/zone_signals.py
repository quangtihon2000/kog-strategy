"""Handler for `zone_signals` Redis stream.

Producer (`telegram_monitor/agent/handlers/zone_signal.py:91-98`) emits:
    timestamp, symbol, redbox_upper, redbox_lower,
    targets_above (csv), targets_below (csv)
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import ZoneSignal

log = logging.getLogger(__name__)


def _parse_floats_csv(s: str) -> list[float]:
    if not s:
        return []
    return [float(x) for x in s.split(",") if x.strip()]


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    inserted = await insert_ignore(
        session,
        ZoneSignal.__table__,
        {
            "signal_ts": int(fields["timestamp"]),
            "symbol": fields["symbol"],
            "redbox_upper": float(fields["redbox_upper"]),
            "redbox_lower": float(fields["redbox_lower"]),
            "targets_above": _parse_floats_csv(fields.get("targets_above", "")),
            "targets_below": _parse_floats_csv(fields.get("targets_below", "")),
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("zone_signals duplicate ts=%s symbol=%s", fields.get("timestamp"), fields.get("symbol"))
