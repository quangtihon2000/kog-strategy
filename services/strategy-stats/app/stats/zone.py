"""Per-account + per-tier Zone stats roll-up.

Zone breakouts fan out into 3 tiers per signal: SCALP (slot 1..N quick exits),
NORMAL (slot 1..N regular targets) and MID (single mid-zone position, no slot).
Reporting dimensions therefore are `account` × `tier`, with `slot_index`
drilldown surfaced for SCALP/NORMAL so we can see which slots actually carry
the P&L. MID has slot_index=NULL by design.

Orphan outcomes (outcome rows whose `(signal_ts, symbol)` does not appear in
the signal window — e.g. ingested before the signal, or signal aged out of
Redis retention) are surfaced under their `account`/`tier` bucket so P&L is
never silently dropped.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ZoneOutcome, ZoneSignal


@dataclass
class ZoneSlotBucket:
    slot_index: Optional[int]
    n_positions: int = 0
    total_pnl: float = 0.0
    close_reasons: Counter = field(default_factory=Counter)


@dataclass
class ZoneTierBucket:
    account: int
    tier: Optional[str]  # SCALP / NORMAL / MID / UNKNOWN / None
    n_signals: int = 0
    n_executed: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    close_reasons: Counter = field(default_factory=Counter)
    # slot drilldown only meaningful for SCALP/NORMAL; MID always has one bucket
    # keyed by slot_index=None
    slots: dict[Optional[int], ZoneSlotBucket] = field(default_factory=dict)

    @property
    def avg_pnl_per_position(self) -> Optional[float]:
        return self.total_pnl / self.n_positions if self.n_positions else None


@dataclass
class ZoneAccountSummary:
    account: int
    n_signals: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    tiers: dict[Optional[str], ZoneTierBucket] = field(default_factory=dict)


async def fetch_signals(session: AsyncSession, since_epoch: int) -> list[ZoneSignal]:
    rows = (
        await session.execute(
            select(ZoneSignal).where(ZoneSignal.signal_ts >= since_epoch)
        )
    ).scalars().all()
    return list(rows)


async def fetch_outcomes(session: AsyncSession, since_epoch: int) -> list[ZoneOutcome]:
    since_dt = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
    rows = (
        await session.execute(
            select(ZoneOutcome).where(ZoneOutcome.closed_at >= since_dt)
        )
    ).scalars().all()
    return list(rows)


def _record(bucket: ZoneTierBucket, o: ZoneOutcome) -> None:
    pnl = o.profit + (o.swap or 0.0) + (o.commission or 0.0)
    bucket.n_positions += 1
    bucket.total_pnl += pnl
    reason = (o.close_reason or "?").upper()
    bucket.close_reasons[reason] += 1
    slot = bucket.slots.setdefault(o.slot_index, ZoneSlotBucket(slot_index=o.slot_index))
    slot.n_positions += 1
    slot.total_pnl += pnl
    slot.close_reasons[reason] += 1


def aggregate(
    signals: list[ZoneSignal], outcomes: list[ZoneOutcome]
) -> dict[int, ZoneAccountSummary]:
    # Group outcomes by their signal join key for the signal-driven pass and by
    # (account, tier) for the orphan pass.
    out_by_signal: dict[tuple[int, str], list[ZoneOutcome]] = {}
    for o in outcomes:
        if o.signal_ts is not None:
            out_by_signal.setdefault((o.signal_ts, o.symbol), []).append(o)

    summaries: dict[int, ZoneAccountSummary] = {}

    # Signal-driven pass: count signal once per (account, tier) bucket it
    # actually executed in. Signals with zero executions are not attributable
    # to any account → they're counted only via the orphan pass below if
    # outcomes show up later; here we just track "saw the signal".
    seen_signal_keys: set[tuple[int, str]] = set()
    for sig in signals:
        seen_signal_keys.add((sig.signal_ts, sig.symbol))
        matched = out_by_signal.get((sig.signal_ts, sig.symbol), [])
        if not matched:
            continue
        # Group matched outcomes by (account, tier) so each (acct, tier) gets
        # n_signals += 1 exactly once per signal.
        by_acct_tier: dict[tuple[int, Optional[str]], list[ZoneOutcome]] = {}
        for o in matched:
            by_acct_tier.setdefault((o.account, o.tier), []).append(o)
        for (acct, tier), positions in by_acct_tier.items():
            summary = summaries.setdefault(acct, ZoneAccountSummary(account=acct))
            summary.n_signals += 1  # signal counted per-tier slice that fired
            bucket = summary.tiers.setdefault(
                tier, ZoneTierBucket(account=acct, tier=tier)
            )
            bucket.n_signals += 1
            bucket.n_executed += 1
            for o in positions:
                _record(bucket, o)
                summary.n_positions += 1
                summary.total_pnl += o.profit + (o.swap or 0.0) + (o.commission or 0.0)

    # Orphan-outcome pass: outcomes whose signal didn't land in the window.
    for (sig_ts, sym), positions in out_by_signal.items():
        if (sig_ts, sym) in seen_signal_keys:
            continue
        by_acct_tier: dict[tuple[int, Optional[str]], list[ZoneOutcome]] = {}
        for o in positions:
            by_acct_tier.setdefault((o.account, o.tier), []).append(o)
        for (acct, tier), group in by_acct_tier.items():
            summary = summaries.setdefault(acct, ZoneAccountSummary(account=acct))
            bucket = summary.tiers.setdefault(
                tier, ZoneTierBucket(account=acct, tier=tier)
            )
            for o in group:
                _record(bucket, o)
                summary.n_positions += 1
                summary.total_pnl += o.profit + (o.swap or 0.0) + (o.commission or 0.0)

    return summaries


async def aggregate_since(
    session: AsyncSession, since_epoch: int
) -> dict[int, ZoneAccountSummary]:
    signals = await fetch_signals(session, since_epoch)
    outcomes = await fetch_outcomes(session, since_epoch)
    return aggregate(signals, outcomes)
