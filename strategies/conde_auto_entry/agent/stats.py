"""Per-channel signal effectiveness stats — Cách 3 Hybrid.

Source of truth: Redis Streams `conde_signals` (signals with channel_name) and
`conde_outcomes` (one entry per closed position). Join key is `signal_ts`
(broadcast across accounts) — `channel_name` only lives signal-side because the
MT5 position-comment field is too short to carry it.

Per-position outcome is classified into TP / SL_ORIGINAL / SL_BE / SL_TRAIL /
OTHER using `exit_price` vs the signal's `entry`/`sl` (no EA change needed —
broker's DEAL_REASON_SL doesn't distinguish original SL from BE/trail-moved SL).

Per-signal class is then derived from the multiset of its positions' kinds:
- WIN_CLEAN  : all TP, no BE/trail moves
- WIN_TRAIL  : at least one TP + some BE/trail (no original SL hit)
- WIN_MIXED  : at least one TP + at least one original SL (partial win)
- SAVED      : no TP, no original SL — all closes at BE+ / trail
- LOSS       : at least one original SL hit, no TP
- MANUAL     : every position was closed by user (DEAL_REASON_EXPERT/CLIENT/...)
- NO_EXEC    : signal had zero matching positions in window

This module is split for testability:
- `fetch_*` functions wrap Redis I/O and return list[dict].
- `aggregate()` is a pure function over those lists.
- `format_report()` only formats — no I/O.
"""

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import redis as redis_lib

from models import _clean_channel_name as clean_channel_name

log = logging.getLogger(__name__)

SIGNALS_STREAM  = "conde_signals"
OUTCOMES_STREAM = "conde_outcomes"

# SL classification thresholds (fraction of |entry-sl|, signed in profit dir).
# rel > _SL_TRAIL_MIN     → trail (closed in profit territory beyond entry)
# _SL_BE_MIN < rel <= _SL_TRAIL_MIN  → BE-zone (entry ± small noise)
# rel <= _SL_BE_MIN       → original SL (full risk taken)
_SL_TRAIL_MIN = 0.05   # > 5% of SL distance into profit → trailing took us out
_SL_BE_MIN    = -0.20  # within 20% of SL distance from entry → BE-zone


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _ms_to_stream_id(ms: int) -> str:
    return f"{int(ms)}-0"


def fetch_signals(r: redis_lib.Redis, since_ms: int) -> List[dict]:
    """Pull all signals with stream-id timestamp >= since_ms."""
    raw = r.xrange(SIGNALS_STREAM, min=_ms_to_stream_id(since_ms), max="+")
    out: List[dict] = []
    for _msg_id, fields in raw:
        try:
            tps = [float(x) for x in str(fields.get("tps", "")).split(",") if x.strip()]
            out.append({
                "signal_ts":    int(fields.get("timestamp", 0)),
                "channel_name": str(fields.get("channel_name", "") or "unknown"),
                "symbol":       str(fields.get("symbol", "")),
                "direction":    str(fields.get("direction", "")),
                "entry_price":  float(fields.get("entry_price", 0) or 0),
                "sl":           float(fields.get("sl", 0) or 0),
                "tps":          tps,
            })
        except (ValueError, TypeError) as exc:
            log.warning("Skipping malformed signal entry: %s (%s)", fields, exc)
    return out


def fetch_outcomes(r: redis_lib.Redis, since_ms: int) -> List[dict]:
    """Pull all outcomes with stream-id timestamp >= since_ms."""
    raw = r.xrange(OUTCOMES_STREAM, min=_ms_to_stream_id(since_ms), max="+")
    out: List[dict] = []
    for _msg_id, fields in raw:
        try:
            out.append(_parse_outcome(fields))
        except (ValueError, TypeError) as exc:
            log.warning("Skipping malformed outcome entry: %s (%s)", fields, exc)
    return out


def fetch_outcomes_from_files(outcomes_dir: Path, since_epoch: int) -> List[dict]:
    """Fallback when Redis is unavailable — read outcome JSONs from disk."""
    if not outcomes_dir.exists():
        return []
    out: List[dict] = []
    for path in outcomes_dir.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if int(payload.get("closed_at", 0)) < since_epoch:
                continue
            out.append(_parse_outcome(payload))
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            log.warning("Skipping unreadable outcome %s: %s", path.name, exc)
    return out


def _parse_outcome(fields: dict) -> dict:
    return {
        "position_id":  int(fields.get("position_id", 0)),
        "signal_ts":    int(fields.get("signal_ts", 0)),
        "account":      int(fields.get("account", 0)),
        "symbol":       str(fields.get("symbol", "")),
        "direction":    str(fields.get("direction", "")),
        "volume":       float(fields.get("volume", 0) or 0),
        "entry_price":  float(fields.get("entry_price", 0) or 0),
        "exit_price":   float(fields.get("exit_price", 0) or 0),
        "profit":       float(fields.get("profit", 0) or 0),
        "swap":         float(fields.get("swap", 0) or 0),
        "commission":   float(fields.get("commission", 0) or 0),
        "opened_at":    int(fields.get("opened_at", 0)),
        "closed_at":    int(fields.get("closed_at", 0)),
        "close_reason": str(fields.get("close_reason", "OTHER")),
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_outcome(outcome: dict, signal: Optional[dict]) -> str:
    """Map one closed position to TP / SL_ORIGINAL / SL_BE / SL_TRAIL / OTHER.

    SL kind is inferred from `exit_price` vs the signal's `entry`/`sl` because
    the MT5 broker reports DEAL_REASON_SL identically for original SL, BE-moved
    SL, and trailing SL.
    """
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


def classify_signal(kinds: List[str]) -> str:
    """Classify a signal from the multiset of its positions' outcome kinds."""
    if not kinds:
        return "NO_EXEC"
    s = set(kinds)

    if s == {"OTHER"}:
        return "MANUAL"

    has_tp     = "TP" in s
    has_orig   = "SL_ORIGINAL" in s
    has_be_tr  = bool(s & {"SL_BE", "SL_TRAIL"})

    if has_tp and has_orig:
        return "WIN_MIXED"
    if has_tp and has_be_tr:
        return "WIN_TRAIL"
    if has_tp:
        return "WIN_CLEAN"
    if has_orig:
        return "LOSS"
    # No TP, no original SL → BE/trail (or pure OTHER mixed in).
    return "SAVED"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class ChannelStats:
    channel:        str
    n_signals:      int   = 0
    n_executed:     int   = 0   # signals with at least one matching outcome
    n_positions:    int   = 0   # total closed positions across executed signals
    n_win_clean:    int   = 0   # all positions hit TP, no BE/trail moves
    n_win_trail:    int   = 0   # TP after BE/trail move
    n_win_mixed:    int   = 0   # partial TP + partial original SL
    n_saved:        int   = 0   # no TP, all BE+/trail (no original SL)
    n_loss:         int   = 0   # at least one original SL, no TP
    n_manual:       int   = 0   # all positions closed manually by user
    total_pnl:      float = 0.0
    r_values:       List[float] = field(default_factory=list)

    # ------------------------------ derived ------------------------------
    @property
    def n_win(self) -> int:
        return self.n_win_clean + self.n_win_trail + self.n_win_mixed

    @property
    def n_classified(self) -> int:
        """Executed signals excluding pure-manual (where we can't judge)."""
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
        """Wilson score interval lower bound on win_rate (proxy for trust)."""
        n = self.n_classified
        if n == 0:
            return 0.0
        p = self.n_win / n
        z = 1.96
        denom = 1 + z * z / n
        center = p + z * z / (2 * n)
        margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        return max(0.0, (center - margin) / denom)


def aggregate(signals: List[dict], outcomes: List[dict]) -> Dict[str, ChannelStats]:
    """Join signals→outcomes and roll up per-channel metrics.

    Join key: `signal_ts` (one signal can fan out to multiple accounts and
    produce multiple positions, but we treat the signal as one effectiveness
    sample classified by the union of its positions' kinds).
    """
    by_sig_ts: Dict[int, List[dict]] = {}
    for o in outcomes:
        by_sig_ts.setdefault(o["signal_ts"], []).append(o)

    stats: Dict[str, ChannelStats] = {}
    for sig in signals:
        ch = sig["channel_name"] or "unknown"
        cs = stats.setdefault(ch, ChannelStats(channel=ch))
        cs.n_signals += 1

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

        # R = pnl / risk where risk ≈ |entry-sl| × total_volume (relative scale).
        sl_dist = abs(sig["entry_price"] - sig["sl"])
        total_vol = sum(o["volume"] for o in matched)
        if sl_dist > 0 and total_vol > 0:
            cs.r_values.append(pnl / (sl_dist * total_vol))

    return stats


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_NAME_W = 15


def _fmt_name(s: str) -> str:
    s = clean_channel_name(s) or "unknown"
    return (s[:_NAME_W - 1] + "…") if len(s) > _NAME_W else s.ljust(_NAME_W)


def _pct(v: Optional[float]) -> str:
    return f"{v * 100:.0f}" if v is not None else "-"


def format_report(stats: Dict[str, ChannelStats], since_label: str) -> str:
    """Render a mobile-friendly per-channel report.

    Header shows totals: `N sig · N pos · N exec`.
    Each row: `channel sig pos ex w/l c95` then an indented detail line with
    `R ±n.nn · clean N% trail N% saved N%` (only when n_classified > 0).
    Pure-manual signals are surfaced as `manual: N` on a third line so they
    don't silently disappear from the count.
    """
    if not stats:
        return f"KOG /stats — {since_label}\n\n(no signals in window)"

    rows = sorted(
        stats.values(),
        key=lambda c: (c.n_executed > 0, c.confidence_lo95, c.n_signals),
        reverse=True,
    )
    total_sig = sum(c.n_signals for c in rows)
    total_pos = sum(c.n_positions for c in rows)
    total_exec = sum(c.n_executed for c in rows)

    lines = [
        f"KOG /stats — {since_label}",
        f"{total_sig} sig · {total_pos} pos · {total_exec} exec",
        "",
        f"{'channel':<{_NAME_W}} {'sig':>3} {'pos':>3} {'ex':>2} {'w/l':>5} {'c95':>3}",
    ]
    for cs in rows:
        if cs.n_classified > 0:
            wl = f"{_pct(cs.win_rate)}/{_pct(cs.loss_rate)}"
            c95 = f"{int(round(cs.confidence_lo95 * 100)):>3d}"
        else:
            wl = "-"
            c95 = "  -"
        lines.append(
            f"{_fmt_name(cs.channel)} "
            f"{cs.n_signals:>3} {cs.n_positions:>3} {cs.n_executed:>2} "
            f"{wl:>5} {c95:>3}"
        )
        if cs.n_classified > 0:
            r_part = f"R {cs.avg_r:+.2f}" if cs.avg_r is not None else "R -"
            lines.append(
                f"  {r_part} · "
                f"clean {_pct(cs.clean_rate)}% "
                f"trail {_pct(cs.trail_rate)}% "
                f"saved {_pct(cs.save_rate)}%"
            )
        if cs.n_manual > 0:
            lines.append(f"  manual: {cs.n_manual}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Duration parsing — used by /stats handler too
# ---------------------------------------------------------------------------

def parse_duration(s: str) -> int:
    """Parse '7d', '24h', '90m' → seconds. Default unit: days."""
    s = (s or "").strip().lower()
    if not s:
        return 7 * 86400
    unit = s[-1]
    if unit in ("s", "m", "h", "d"):
        n = int(s[:-1])
    else:
        n = int(s)
        unit = "d"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def now_ms() -> int:
    return int(time.time() * 1000)
