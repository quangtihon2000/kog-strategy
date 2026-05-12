"""Handler for `conde_signals` Redis stream.

Producer fields:
    timestamp, symbol, direction, entry_price, sl, tps (csv),
    channel_id (BIGINT), channel_name

Signals without `channel_id` are skipped (ack'd, not stored). Backfill of
legacy messages predating the producer's `channel_id` rollout would otherwise
pollute stats with channel-less rows; we'd rather miss them than misattribute.

Cross-contamination observed in prod: some messages on `conde_signals` are
actually zone-formatted (have `type=ZONE_SIGNAL` / `redbox_upper` / no
`direction`). Producer-side bug; we skip+ack with WARNING rather than
strand the PEL.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.idempotency import insert_ignore
from app.models import Channel, CondeSignal

log = logging.getLogger(__name__)


def _parse_floats_csv(s: str) -> list[float]:
    if not s:
        return []
    return [float(x) for x in s.split(",") if x.strip()]


async def _upsert_channel(session: AsyncSession, channel_id: int, name: str) -> None:
    """Insert new channel, or append rename to name_history when the name changed."""
    existing = await session.get(Channel, channel_id)
    now = datetime.now(timezone.utc)
    if existing is None:
        session.add(
            Channel(
                channel_id=channel_id,
                name=name or "",
                first_seen_at=now,
                last_seen_at=now,
                name_history=None,
            )
        )
        return

    existing.last_seen_at = now
    if name and existing.name != name:
        history = list(existing.name_history or [])
        history.append({"old_name": existing.name, "changed_at": now.isoformat()})
        existing.name_history = history
        existing.name = name


async def handle(session: AsyncSession, fields: dict[str, str]) -> None:
    # Cross-contamination guard: zone-formatted messages routed to conde stream.
    msg_type = (fields.get("type") or "").strip().upper()
    if msg_type == "ZONE_SIGNAL" or "redbox_upper" in fields or "redbox_uppper" in fields:
        log.warning(
            "conde_signals: skip zone-formatted message on conde stream type=%r fields=%s",
            msg_type, fields,
        )
        return

    ts_raw = (fields.get("timestamp") or "").strip()
    symbol = (fields.get("symbol") or "").strip()
    direction_raw = (fields.get("direction") or "").strip()
    entry_raw = (fields.get("entry_price") or "").strip()
    sl_raw = (fields.get("sl") or "").strip()

    if not ts_raw or not symbol or not direction_raw or not entry_raw or not sl_raw:
        log.warning(
            "conde_signals: skip malformed (missing required field) fields=%s",
            fields,
        )
        return

    try:
        signal_ts = int(ts_raw)
        entry_price = float(entry_raw)
        sl = float(sl_raw)
    except ValueError:
        log.warning(
            "conde_signals: skip non-numeric (ts=%r entry=%r sl=%r) fields=%s",
            ts_raw, entry_raw, sl_raw, fields,
        )
        return

    direction = direction_raw.upper()
    tps = _parse_floats_csv(fields.get("tps", ""))
    channel_name = fields.get("channel_name") or None

    channel_id_raw = fields.get("channel_id", "")
    if not channel_id_raw.strip():
        log.info(
            "conde_signals: skip signal without channel_id (signal_ts=%d symbol=%s)",
            signal_ts, symbol,
        )
        return
    try:
        channel_id = int(channel_id_raw)
    except ValueError:
        log.warning(
            "conde_signals: skip bad channel_id %r (signal_ts=%d symbol=%s)",
            channel_id_raw, signal_ts, symbol,
        )
        return

    await _upsert_channel(session, channel_id, channel_name or "")

    inserted = await insert_ignore(
        session,
        CondeSignal.__table__,
        {
            "signal_ts": signal_ts,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "sl": sl,
            "tps": tps,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "raw": fields,
        },
    )
    if not inserted:
        log.debug("conde_signals duplicate (signal_ts=%d symbol=%s)", signal_ts, symbol)
