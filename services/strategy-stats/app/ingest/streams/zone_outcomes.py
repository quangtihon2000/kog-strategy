"""Handler for `zone_outcomes` Redis stream.

Parses `tier` + `slot_index` from the position comment:
    ZB_SCALP{n}_{ts}  ZS_SCALP{n}_{ts}  → tier=SCALP, slot=n
    ZB_T{n}_{ts}      ZS_T{n}_{ts}      → tier=NORMAL, slot=n
    ZB_MID_{ts}       ZS_MID_{ts}       → tier=MID,    slot=NULL
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import ZoneOutcome

log = logging.getLogger(__name__)

_TIER_RE = re.compile(r"^Z[BS]_(SCALP(\d+)|T(\d+)|MID)_\d+")


def _opt_int(s: str | None) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _ts_to_dt(s: str | None) -> datetime | None:
    v = _opt_int(s)
    if v is None or v <= 0:
        return None
    return datetime.fromtimestamp(v, tz=timezone.utc)


def _parse_tier(comment: str | None) -> tuple[str | None, int | None]:
    if not comment:
        return None, None
    m = _TIER_RE.match(comment)
    if not m:
        if comment.startswith(("ZB_", "ZS_")):
            return "UNKNOWN", None
        return None, None
    body = m.group(1)
    if body == "MID":
        return "MID", None
    if body.startswith("SCALP"):
        return "SCALP", int(m.group(2))
    return "NORMAL", int(m.group(3))


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    closed_at = _ts_to_dt(fields.get("closed_at"))
    if closed_at is None:
        log.warning("zone_outcomes missing closed_at; skipping fields=%s", fields)
        return

    comment = fields.get("comment") or None
    tier_field = fields.get("tier") or None
    slot_field = _opt_int(fields.get("slot_index"))
    if tier_field is None:
        tier_field, parsed_slot = _parse_tier(comment)
        if slot_field is None:
            slot_field = parsed_slot

    inserted = await insert_ignore(
        session,
        ZoneOutcome.__table__,
        {
            "position_id": int(fields["position_id"]),
            "account": int(fields["account"]),
            "symbol": fields["symbol"],
            "signal_ts": _opt_int(fields.get("signal_ts")),
            "direction": fields["direction"].upper(),
            "tier": tier_field,
            "slot_index": slot_field,
            "magic": int(fields["magic"]),
            "volume": float(fields["volume"]),
            "entry_price": float(fields["entry_price"]),
            "exit_price": float(fields["exit_price"]),
            "profit": float(fields["profit"]),
            "swap": float(fields.get("swap") or 0.0),
            "commission": float(fields.get("commission") or 0.0),
            "opened_at": _ts_to_dt(fields.get("opened_at")),
            "closed_at": closed_at,
            "close_reason": fields.get("close_reason") or None,
            "comment": comment,
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("zone_outcomes duplicate position_id=%s", fields.get("position_id"))
