"""Per-channel conde-signal effectiveness stats.

Ported from `strategies/conde_auto_entry/agent/stats.py` (classification +
aggregation algorithm preserved verbatim). Only the I/O layer is changed —
instead of Redis xrange we run async SQLAlchemy queries against
`conde_signals` / `conde_outcomes`.

Grouping key is `channel_name` (snapshot stored on the signal row at ingest
time). Rename history is tracked separately in `channels.name_history` so
historic stats stay correlatable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CondeOutcome, CondeSignal

# SL classification thresholds — must match the source module.
_SL_TRAIL_MIN = 0.05
_SL_BE_MIN = -0.20


# ---------------------------------------------------------------------------
# Fetchers (async Postgres)
# ---------------------------------------------------------------------------

async def fetch_signals(session: AsyncSession, since_epoch: int) -> list[dict]:
    rows = (
        await session.execute(
            select(CondeSignal).where(CondeSignal.signal_ts >= since_epoch)
        )
    ).scalars().all()
    return [
        {
            "signal_ts":    r.signal_ts,
            "channel_name": r.channel_name or "unknown",
            "channel_id":   r.channel_id,
            "symbol":       r.symbol,
            "direction":    r.direction,
            "entry_price":  r.entry_price,
            "sl":           r.sl,
            "tps":          list(r.tps or []),
        }
        for r in rows
    ]


async def fetch_outcomes(session: AsyncSession, since_epoch: int) -> list[dict]:
    rows = (
        await session.execute(
            select(CondeOutcome).where(CondeOutcome.signal_ts >= since_epoch)
        )
    ).scalars().all()
    return [
        {
            "position_id":  r.position_id,
            "signal_ts":    r.signal_ts or 0,
            "account":      r.account,
            "symbol":       r.symbol,
            "direction":    r.direction,
            "volume":       r.volume,
            "entry_price":  r.entry_price,
            "exit_price":   r.exit_price,
            "profit":       r.profit,
            "swap":         r.swap or 0.0,
            "commission":   r.commission or 0.0,
            "closed_at":    r.closed_at,
            "close_reason": (r.close_reason or "OTHER"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Classification (verbatim port)
# ---------------------------------------------------------------------------

def classify_outcome(outcome: dict, signal: Optional[dict]) -> str:
    reason = (outcome.get("close_reason") or "").upper()
    if reason == "TP":
        return "TP"
    if reason != "SL":
        return "OTHER"

    if not signal:
        return "SL_ORIGINAL"
    entry = signal.get("entry_price") or 0.0
    sl = signal.get("sl") or 0.0
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return "SL_ORIGINAL"

    is_buy = (outcome.get("direction") or "").upper() == "BUY"
    exit_p = outcome.get("exit_price") or 0.0
    profit_dist = (exit_p - entry) if is_buy else (entry - exit_p)
    rel = profit_dist / sl_dist

    if rel > _SL_TRAIL_MIN:
        return "SL_TRAIL"
    if rel > _SL_BE_MIN:
        return "SL_BE"
    return "SL_ORIGINAL"


def classify_signal(kinds: list[str]) -> str:
    if not kinds:
        return "NO_EXEC"
    s = set(kinds)

    if s == {"OTHER"}:
        return "MANUAL"

    has_tp = "TP" in s
    has_orig = "SL_ORIGINAL" in s
    has_be_tr = bool(s & {"SL_BE", "SL_TRAIL"})

    if has_tp and has_orig:
        return "WIN_MIXED"
    if has_tp and has_be_tr:
        return "WIN_TRAIL"
    if has_tp:
        return "WIN_CLEAN"
    if has_orig:
        return "LOSS"
    return "SAVED"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class ChannelStats:
    channel: str
    channel_id: int | None = None
    n_signals: int = 0
    n_executed: int = 0
    n_positions: int = 0
    n_win_clean: int = 0
    n_win_trail: int = 0
    n_win_mixed: int = 0
    n_saved: int = 0
    n_loss: int = 0
    n_manual: int = 0
    total_pnl: float = 0.0
    r_values: list[float] = field(default_factory=list)

    @property
    def n_win(self) -> int:
        return self.n_win_clean + self.n_win_trail + self.n_win_mixed

    @property
    def n_classified(self) -> int:
        return self.n_executed - self.n_manual

    @property
    def win_rate(self) -> Optional[float]:
        return self.n_win / self.n_classified if self.n_classified else None

    @property
    def loss_rate(self) -> Optional[float]:
        return self.n_loss / self.n_classified if self.n_classified else None

    @property
    def save_rate(self) -> Optional[float]:
        return self.n_saved / self.n_classified if self.n_classified else None

    @property
    def clean_rate(self) -> Optional[float]:
        return self.n_win_clean / self.n_classified if self.n_classified else None

    @property
    def trail_rate(self) -> Optional[float]:
        return self.n_win_trail / self.n_classified if self.n_classified else None

    @property
    def avg_r(self) -> Optional[float]:
        return sum(self.r_values) / len(self.r_values) if self.r_values else None

    @property
    def confidence_lo95(self) -> float:
        n = self.n_classified
        if n == 0:
            return 0.0
        p = self.n_win / n
        z = 1.96
        denom = 1 + z * z / n
        center = p + z * z / (2 * n)
        margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        return max(0.0, (center - margin) / denom)


def aggregate(signals: list[dict], outcomes: list[dict]) -> dict[str | None, ChannelStats]:
    by_sig_ts: dict[int, list[dict]] = {}
    for o in outcomes:
        by_sig_ts.setdefault(o["signal_ts"], []).append(o)

    # Track latest channel_name per channel_id for display name resolution
    latest_name_by_id: dict[int | None, tuple[int, str]] = {}

    stats: dict[int | None, ChannelStats] = {}
    for sig in signals:
        cid = sig.get("channel_id")
        ch_name = sig["channel_name"] or "unknown"
        if cid not in stats:
            stats[cid] = ChannelStats(channel=ch_name, channel_id=cid)
        cs = stats[cid]
        cs.n_signals += 1

        # Track the most recent channel_name for this channel_id
        ts = sig["signal_ts"]
        prev = latest_name_by_id.get(cid)
        if prev is None or ts > prev[0]:
            latest_name_by_id[cid] = (ts, ch_name)
            cs.channel = ch_name

        matched = by_sig_ts.get(sig["signal_ts"], [])
        if not matched:
            continue

        cs.n_executed += 1
        cs.n_positions += len(matched)

        kinds = [classify_outcome(o, sig) for o in matched]
        klass = classify_signal(kinds)
        if klass == "WIN_CLEAN":
            cs.n_win_clean += 1
        elif klass == "WIN_TRAIL":
            cs.n_win_trail += 1
        elif klass == "WIN_MIXED":
            cs.n_win_mixed += 1
        elif klass == "SAVED":
            cs.n_saved += 1
        elif klass == "LOSS":
            cs.n_loss += 1
        elif klass == "MANUAL":
            cs.n_manual += 1

        pnl = sum(o["profit"] + o["swap"] + o["commission"] for o in matched)
        cs.total_pnl += pnl

        sl_dist = abs(sig["entry_price"] - sig["sl"])
        total_vol = sum(o["volume"] for o in matched)
        if sl_dist > 0 and total_vol > 0:
            cs.r_values.append(pnl / (sl_dist * total_vol))

    return stats


async def aggregate_since(session: AsyncSession, since_epoch: int) -> dict[int | None, ChannelStats]:
    signals = await fetch_signals(session, since_epoch)
    outcomes = await fetch_outcomes(session, since_epoch)
    return aggregate(signals, outcomes)
