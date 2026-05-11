"""Handler for `gvfx_outcomes` Redis stream.

Same shape as `conde_outcomes` plus `mode_tag` parsed from the position
comment `GVFX_T{ts}_{A|F|S}` (A=anchor, F=follow, S=scalp).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import GvfxOutcome

log = logging.getLogger(__name__)

_MODE_RE = re.compile(r"^GVFX_T\d+_([AFS])(?:_|$)")


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


def _parse_mode_tag(comment: str | None) -> str | None:
    if not comment:
        return None
    m = _MODE_RE.match(comment)
    if m:
        return m.group(1)
    if comment.startswith("GVFX_"):
        return "?"
    return None


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    closed_at = _ts_to_dt(fields.get("closed_at"))
    if closed_at is None:
        log.warning("gvfx_outcomes missing closed_at; skipping fields=%s", fields)
        return

    comment = fields.get("comment") or None
    mode_tag = fields.get("mode_tag") or _parse_mode_tag(comment)

    inserted = await insert_ignore(
        session,
        GvfxOutcome.__table__,
        {
            "position_id": int(fields["position_id"]),
            "account": int(fields["account"]),
            "symbol": fields["symbol"],
            "signal_ts": _opt_int(fields.get("signal_ts")),
            "direction": fields["direction"].upper(),
            "mode_tag": mode_tag,
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
        log.debug("gvfx_outcomes duplicate position_id=%s", fields.get("position_id"))
