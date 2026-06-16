"""Per-channel quality tiering — auto-rank layer (Phase 1).

Pure scoring over `ChannelStats` (from `app.stats.conde`). No I/O, so it stays
trivially testable. Turns the existing effectiveness metrics into an actionable
tier + human-readable reasons, giving operators a ranked "quality channel" list
to review. This layer only *suggests*; the operator verdict (Phase 2) is stored
separately and never overwritten here.

Tiers:
- ``QUALITY``      : enough data and passes every gate (Wilson-LB win rate,
                     avg R, net-positive P&L).
- ``WATCH``        : enough data but neither clearly good nor clearly bad.
- ``POOR``         : enough data and actively bad (high loss rate or net-negative
                     P&L).
- ``INSUFFICIENT`` : not enough classified signals to judge yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.stats.conde import ChannelStats

TIER_QUALITY = "QUALITY"
TIER_WATCH = "WATCH"
TIER_POOR = "POOR"
TIER_INSUFFICIENT = "INSUFFICIENT"

# Display/sort priority — higher floats to the top of the ranked list. Clearly
# bad channels (POOR) sink below "not enough data yet" (INSUFFICIENT).
_TIER_RANK = {
    TIER_QUALITY: 3,
    TIER_WATCH: 2,
    TIER_INSUFFICIENT: 1,
    TIER_POOR: 0,
}


@dataclass(frozen=True)
class QualityThresholds:
    """Gate values for `evaluate()`. Defaults mirror `app.settings`."""

    min_classified: int = 20      # need this many classified signals to judge
    win_lo95_floor: float = 0.50  # Wilson lower-bound win rate must clear this
    avg_r_floor: float = 0.0      # average R must clear this
    loss_rate_ceil: float = 0.50  # at/above this loss rate ⇒ POOR


@dataclass(frozen=True)
class QualityVerdict:
    tier: str
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0  # in-tier sort key (Wilson-LB for judged tiers)


def evaluate(cs: ChannelStats, t: QualityThresholds) -> QualityVerdict:
    """Classify one channel's stats into a tier with reasons."""
    n = cs.n_classified
    if n < t.min_classified:
        return QualityVerdict(
            tier=TIER_INSUFFICIENT,
            reasons=[f"only {n} classified (need >={t.min_classified})"],
            score=float(n),
        )

    lo95 = cs.confidence_lo95
    avg_r = cs.avg_r if cs.avg_r is not None else 0.0
    loss_rate = cs.loss_rate if cs.loss_rate is not None else 0.0
    pnl = cs.total_pnl

    # POOR gate first — actively bad beats every other label.
    if loss_rate >= t.loss_rate_ceil or pnl < 0:
        reasons: list[str] = []
        if loss_rate >= t.loss_rate_ceil:
            reasons.append(f"loss rate {loss_rate:.0%} >= {t.loss_rate_ceil:.0%}")
        if pnl < 0:
            reasons.append(f"net P&L {pnl:+.2f}")
        return QualityVerdict(TIER_POOR, reasons, score=lo95)

    # QUALITY gate — all three must hold.
    if lo95 >= t.win_lo95_floor and avg_r >= t.avg_r_floor and pnl > 0:
        return QualityVerdict(
            TIER_QUALITY,
            [
                f"Lo95 {lo95:.0%} >= {t.win_lo95_floor:.0%}",
                f"avg R {avg_r:+.2f} >= {t.avg_r_floor:+.2f}",
                f"net P&L {pnl:+.2f}",
            ],
            score=lo95,
        )

    # WATCH — enough data, not clearly good. Surface which gate it missed.
    reasons = []
    if lo95 < t.win_lo95_floor:
        reasons.append(f"Lo95 {lo95:.0%} < {t.win_lo95_floor:.0%}")
    if avg_r < t.avg_r_floor:
        reasons.append(f"avg R {avg_r:+.2f} < {t.avg_r_floor:+.2f}")
    return QualityVerdict(TIER_WATCH, reasons or ["borderline"], score=lo95)


def rank(
    stats: dict[int | None, ChannelStats],
    t: QualityThresholds,
) -> list[tuple[ChannelStats, QualityVerdict]]:
    """Evaluate every channel and sort best-first for display."""
    pairs = [(cs, evaluate(cs, t)) for cs in stats.values()]
    pairs.sort(
        key=lambda p: (
            _TIER_RANK[p[1].tier],
            p[1].score,
            p[0].avg_r if p[0].avg_r is not None else 0.0,
            p[0].n_classified,
        ),
        reverse=True,
    )
    return pairs
