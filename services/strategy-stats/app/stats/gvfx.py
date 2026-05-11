"""Per-symbol + per-mode_tag GVFX stats roll-up.

GVFX has no channel concept; the natural breakdown is `symbol` × `mode_tag`
(A=anchor, F=follow, S=scalp, ?=unparsed). For each bucket we report signal
count, executed signal count, position count, total net P&L (profit + swap +
commission) and a `close_reason` histogram (incl. EOD for the cut-window
forced close).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GvfxOutcome, GvfxSignal


@dataclass
class GvfxBucket:
    symbol: str
    mode_tag: Optional[str]
    n_signals: int = 0
    n_executed: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    close_reasons: Counter = field(default_factory=Counter)

    @property
    def avg_pnl_per_position(self) -> Optional[float]:
        return self.total_pnl / self.n_positions if self.n_positions else None


@dataclass
class GvfxSymbolSummary:
    symbol: str
    n_signals: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    buckets: dict[Optional[str], GvfxBucket] = field(default_factory=dict)


async def fetch_signals(session: AsyncSession, since_epoch: int) -> list[GvfxSignal]:
    rows = (
        await session.execute(
            select(GvfxSignal).where(GvfxSignal.signal_ts >= since_epoch)
        )
    ).scalars().all()
    return list(rows)


async def fetch_outcomes(session: AsyncSession, since_epoch: int) -> list[GvfxOutcome]:
    since_dt = datetime.fromtimestamp(since_epoch, tz=timezone.utc)
    rows = (
        await session.execute(
            select(GvfxOutcome).where(GvfxOutcome.closed_at >= since_dt)
        )
    ).scalars().all()
    return list(rows)


def aggregate(
    signals: list[GvfxSignal], outcomes: list[GvfxOutcome]
) -> dict[str, GvfxSymbolSummary]:
    # Index outcomes by (signal_ts, symbol) so we can flag "executed" signals
    # and roll up per-bucket metrics.
    out_by_signal: dict[tuple[int, str], list[GvfxOutcome]] = {}
    for o in outcomes:
        if o.signal_ts is not None:
            out_by_signal.setdefault((o.signal_ts, o.symbol), []).append(o)

    summaries: dict[str, GvfxSymbolSummary] = {}

    for sig in signals:
        summary = summaries.setdefault(sig.symbol, GvfxSymbolSummary(symbol=sig.symbol))
        summary.n_signals += 1
        matched = out_by_signal.get((sig.signal_ts, sig.symbol), [])

        # Group matched positions by mode_tag for per-bucket counts.
        by_tag: dict[Optional[str], list[GvfxOutcome]] = {}
        for o in matched:
            by_tag.setdefault(o.mode_tag, []).append(o)
        if not by_tag:
            # Signal had no executions — count it once with mode_tag=None bucket.
            bucket = summary.buckets.setdefault(
                None, GvfxBucket(symbol=sig.symbol, mode_tag=None)
            )
            bucket.n_signals += 1
            continue

        for tag, positions in by_tag.items():
            bucket = summary.buckets.setdefault(
                tag, GvfxBucket(symbol=sig.symbol, mode_tag=tag)
            )
            bucket.n_signals += 1
            bucket.n_executed += 1
            bucket.n_positions += len(positions)
            pnl = sum(p.profit + (p.swap or 0.0) + (p.commission or 0.0) for p in positions)
            bucket.total_pnl += pnl
            for p in positions:
                bucket.close_reasons[(p.close_reason or "?").upper()] += 1
            summary.n_positions += len(positions)
            summary.total_pnl += pnl

    # Cover orphan outcomes (signal not in window or never ingested) — surface
    # them under symbol/mode_tag so P&L isn't silently lost.
    seen_signal_keys = {(s.signal_ts, s.symbol) for s in signals}
    for (sig_ts, sym), positions in out_by_signal.items():
        if (sig_ts, sym) in seen_signal_keys:
            continue
        summary = summaries.setdefault(sym, GvfxSymbolSummary(symbol=sym))
        by_tag: dict[Optional[str], list[GvfxOutcome]] = {}
        for o in positions:
            by_tag.setdefault(o.mode_tag, []).append(o)
        for tag, group in by_tag.items():
            bucket = summary.buckets.setdefault(
                tag, GvfxBucket(symbol=sym, mode_tag=tag)
            )
            bucket.n_positions += len(group)
            pnl = sum(p.profit + (p.swap or 0.0) + (p.commission or 0.0) for p in group)
            bucket.total_pnl += pnl
            for p in group:
                bucket.close_reasons[(p.close_reason or "?").upper()] += 1
            summary.n_positions += len(group)
            summary.total_pnl += pnl

    return summaries


async def aggregate_since(
    session: AsyncSession, since_epoch: int
) -> dict[str, GvfxSymbolSummary]:
    signals = await fetch_signals(session, since_epoch)
    outcomes = await fetch_outcomes(session, since_epoch)
    return aggregate(signals, outcomes)
