"""Handler for `conde_outcomes` Redis stream.

Producer is `strategies/conde_auto_entry/agent/outcome_publisher.py`, which
flattens the EA's JSON file (`CondeAutoEntryEA/outcomes/{position_id}.json`)
into a Redis Stream message with all values coerced to strings.

EA schema (`CondeAutoEntryEA.mq5:175-186`):
    position_id, deal_out_ticket, signal_ts, comment, account, symbol, direction,
    magic, volume, entry_price, exit_price, profit, swap, commission,
    opened_at (unix s), closed_at (unix s), close_reason
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import CondeOutcome

log = logging.getLogger(__name__)


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


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    closed_at = _ts_to_dt(fields.get("closed_at"))
    if closed_at is None:
        log.warning("conde_outcomes missing closed_at; skipping fields=%s", fields)
        return

    inserted = await insert_ignore(
        session,
        CondeOutcome.__table__,
        {
            "position_id": int(fields["position_id"]),
            "account": int(fields["account"]),
            "symbol": fields["symbol"],
            "signal_ts": _opt_int(fields.get("signal_ts")),
            "direction": fields["direction"].upper(),
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
            "comment": fields.get("comment") or None,
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("conde_outcomes duplicate position_id=%s", fields.get("position_id"))
