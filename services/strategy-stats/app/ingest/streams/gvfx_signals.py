"""Handler for `gvfx_signals` Redis stream.

Producer (`telegram_monitor/agent/handlers/gvfx_signal.py:139-149`) emits:
    timestamp, symbol, target, direction, step, tp, low, high, use_atr

Field-name mapping: agent's `step`/`tp`/`low`/`high` → DB `step_pts`/`tp_pts`/
`low_price`/`high_price`.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import GvfxSignal

log = logging.getLogger(__name__)


def _opt_float(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _opt_bool(s: str | None) -> bool | None:
    if s is None or s == "":
        return None
    return s.strip().lower() in ("true", "1", "yes", "on")


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    inserted = await insert_ignore(
        session,
        GvfxSignal.__table__,
        {
            "signal_ts": int(fields["timestamp"]),
            "symbol": fields["symbol"],
            "direction": fields["direction"].upper(),
            "target": float(fields["target"]),
            "step_pts": _opt_float(fields.get("step")),
            "tp_pts": _opt_float(fields.get("tp")),
            "low_price": _opt_float(fields.get("low")),
            "high_price": _opt_float(fields.get("high")),
            "use_atr": _opt_bool(fields.get("use_atr")),
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("gvfx_signals duplicate ts=%s symbol=%s", fields.get("timestamp"), fields.get("symbol"))
