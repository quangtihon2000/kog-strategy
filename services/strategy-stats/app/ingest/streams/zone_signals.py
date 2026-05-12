"""Handler for `zone_signals` Redis stream.

Producer (`telegram_monitor/agent/handlers/zone_signal.py:91-98`) emits:
    timestamp, symbol, redbox_upper, redbox_lower,
    targets_above (csv), targets_below (csv)

Malformed messages observed in prod (skip + ack with WARNING, don't strand PEL):
- `timestamp` field missing entirely
- producer typo `redbox_uppper` (3 p's) instead of `redbox_upper`
- literal string `'None'` in numeric fields
- two timestamp formats coexist: unix int (`'1778205600'`) and ISO datetime (`'2026-04-20 11:53:18'`)
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import ZoneSignal

log = logging.getLogger(__name__)


def _parse_floats_csv(s: str) -> list[float]:
    if not s:
        return []
    out: list[float] = []
    for x in s.split(","):
        x = x.strip()
        if not x or x.lower() == "none":
            continue
        try:
            out.append(float(x))
        except ValueError:
            continue
    return out


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    if not s or s.lower() == "none":
        return None
    return s


def _parse_ts(raw: str) -> int | None:
    """Accept unix int or ISO datetime; return unix seconds or None."""
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(raw).timestamp())
    except ValueError:
        return None


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    ts_raw = _clean(fields.get("timestamp"))
    symbol = _clean(fields.get("symbol"))
    upper_raw = _clean(fields.get("redbox_upper")) or _clean(fields.get("redbox_uppper"))
    lower_raw = _clean(fields.get("redbox_lower"))

    if not ts_raw or not symbol or not upper_raw or not lower_raw:
        log.warning(
            "zone_signals: skip malformed (missing required field) fields=%s",
            fields,
        )
        return

    signal_ts = _parse_ts(ts_raw)
    if signal_ts is None:
        log.warning("zone_signals: skip unparseable timestamp=%r fields=%s", ts_raw, fields)
        return

    try:
        upper = float(upper_raw)
        lower = float(lower_raw)
    except ValueError:
        log.warning(
            "zone_signals: skip non-numeric redbox (upper=%r lower=%r) fields=%s",
            upper_raw, lower_raw, fields,
        )
        return

    inserted = await insert_ignore(
        session,
        ZoneSignal.__table__,
        {
            "signal_ts": signal_ts,
            "symbol": symbol,
            "redbox_upper": upper,
            "redbox_lower": lower,
            "targets_above": _parse_floats_csv(fields.get("targets_above", "")),
            "targets_below": _parse_floats_csv(fields.get("targets_below", "")),
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("zone_signals duplicate ts=%s symbol=%s", signal_ts, symbol)
