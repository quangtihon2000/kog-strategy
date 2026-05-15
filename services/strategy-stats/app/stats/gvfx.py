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


# ---------------------------------------------------------------------------
# Per-account aggregation (cho dashboard per-account GVFX)
# ---------------------------------------------------------------------------


@dataclass
class GvfxAccountSymbolBucket:
    symbol: str
    mode_tag: str | None
    n_signals: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    close_reasons: Counter = field(default_factory=Counter)


@dataclass
class GvfxAccountSummary:
    account: int
    n_signals: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    buckets: dict[tuple[str, str | None], GvfxAccountSymbolBucket] = field(default_factory=dict)


async def aggregate_by_account(
    session: AsyncSession,
    since_epoch: int,
    account: int | None = None,
) -> dict[int, GvfxAccountSummary]:
    """Aggregate GVFX outcomes by account (top-level) → (symbol, mode_tag) (drilldown).

    - account=None: tất cả accounts (dùng cho overview card "Top accounts").
    - account=N: single account (detail page).

    Outcomes không có account (NULL) bị bỏ qua.
    """
    out_stmt = select(GvfxOutcome).where(GvfxOutcome.signal_ts >= since_epoch)
    if account is not None:
        out_stmt = out_stmt.where(GvfxOutcome.account == account)
    out_rows: list[GvfxOutcome] = (await session.execute(out_stmt)).scalars().all()

    if not out_rows:
        return {}

    result: dict[int, GvfxAccountSummary] = {}

    # Group by (account, signal_ts, symbol) để đếm signal một lần per (account, signal)
    by_acct_sig: dict[tuple[int, int, str], list[GvfxOutcome]] = {}
    for o in out_rows:
        if o.account is None or o.signal_ts is None:
            continue
        by_acct_sig.setdefault((o.account, o.signal_ts, o.symbol), []).append(o)

    for (acct, sig_ts, sym), positions in by_acct_sig.items():
        summary = result.setdefault(acct, GvfxAccountSummary(account=acct))
        summary.n_signals += 1

        pnl = sum(p.profit + (p.swap or 0.0) + (p.commission or 0.0) for p in positions)
        summary.n_positions += len(positions)
        summary.total_pnl += pnl

        # Group positions by mode_tag để drilldown bucket
        by_tag: dict[str | None, list[GvfxOutcome]] = {}
        for p in positions:
            by_tag.setdefault(p.mode_tag, []).append(p)

        for tag, tag_positions in by_tag.items():
            bucket = summary.buckets.setdefault(
                (sym, tag),
                GvfxAccountSymbolBucket(symbol=sym, mode_tag=tag),
            )
            bucket.n_signals += 1
            bucket.n_positions += len(tag_positions)
            tag_pnl = sum(p.profit + (p.swap or 0.0) + (p.commission or 0.0) for p in tag_positions)
            bucket.total_pnl += tag_pnl
            for p in tag_positions:
                bucket.close_reasons[(p.close_reason or "?").upper()] += 1

    return result
