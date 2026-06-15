"""Unit tests for the pure channel-quality scoring (app.stats.quality)."""
from __future__ import annotations

from app.stats.conde import ChannelStats
from app.stats.quality import (
    TIER_INSUFFICIENT,
    TIER_POOR,
    TIER_QUALITY,
    TIER_WATCH,
    QualityThresholds,
    evaluate,
    rank,
)

T = QualityThresholds()  # defaults: min 20, Lo95 0.50, R 0.0, loss 0.50


def _cs(channel: str, **over) -> ChannelStats:
    cs = ChannelStats(channel=channel, channel_id=over.pop("channel_id", None))
    # ChannelStats derives win_rate/loss_rate/avg_r/lo95 from raw counters, so
    # set the raw fields the metrics are computed from.
    cs.n_executed = over.pop("n_executed", 0)
    cs.n_win_clean = over.pop("n_win_clean", 0)
    cs.n_loss = over.pop("n_loss", 0)
    cs.total_pnl = over.pop("total_pnl", 0.0)
    cs.r_values = over.pop("r_values", [])
    assert not over, f"unexpected kwargs: {over}"
    return cs


def test_quality_passes_all_gates():
    cs = _cs("good", n_executed=30, n_win_clean=24, total_pnl=500.0, r_values=[0.8] * 5)
    assert evaluate(cs, T).tier == TIER_QUALITY


def test_poor_on_high_loss_rate():
    cs = _cs("bad", n_executed=30, n_win_clean=5, n_loss=20, total_pnl=300.0)
    assert evaluate(cs, T).tier == TIER_POOR


def test_poor_on_negative_pnl_even_if_winning():
    cs = _cs("negpnl", n_executed=30, n_win_clean=22, total_pnl=-1.0, r_values=[0.3] * 5)
    assert evaluate(cs, T).tier == TIER_POOR


def test_insufficient_below_min_classified():
    cs = _cs("new", n_executed=5, n_win_clean=4, total_pnl=50.0)
    assert evaluate(cs, T).tier == TIER_INSUFFICIENT


def test_watch_when_enough_data_but_below_floor():
    # 25 classified, 12 wins → win rate ~48%, Lo95 below 0.50, pnl>0, loss<ceil.
    cs = _cs("mid", n_executed=25, n_win_clean=12, n_loss=7, total_pnl=20.0, r_values=[0.1] * 3)
    assert evaluate(cs, T).tier == TIER_WATCH


def test_rank_orders_quality_first_poor_last():
    good = _cs("good", n_executed=30, n_win_clean=24, total_pnl=500.0, r_values=[0.8] * 5)
    poor = _cs("poor", n_executed=30, n_win_clean=5, n_loss=20, total_pnl=-300.0)
    new = _cs("new", n_executed=5, n_win_clean=4, total_pnl=50.0)
    ranked = rank({1: poor, 2: good, 3: new}, T)
    assert ranked[0][0].channel == "good"
    assert ranked[-1][1].tier == TIER_POOR
