"""Per-channel signal effectiveness stats.

Source of truth: Redis Streams `conde_signals` (signals with channel_name) and
`conde_outcomes` (one entry per closed position). Join key is
`(signal_ts, account_id)` — `channel_name` only lives signal-side because the
MT5 position-comment field is too short to carry it.

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
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class ChannelStats:
    channel:        str
    n_signals:      int   = 0
    n_executed:     int   = 0   # signals with at least one matching outcome
    n_tp:           int   = 0   # all positions of signal closed at TP
    n_sl:           int   = 0   # all positions of signal closed at SL
    n_mixed:        int   = 0   # neither all-TP nor all-SL
    total_pnl:      float = 0.0
    r_values:       List[float] = field(default_factory=list)

    @property
    def tp_rate(self) -> Optional[float]:
        return self.n_tp / self.n_executed if self.n_executed else None

    @property
    def sl_rate(self) -> Optional[float]:
        return self.n_sl / self.n_executed if self.n_executed else None

    @property
    def avg_r(self) -> Optional[float]:
        return sum(self.r_values) / len(self.r_values) if self.r_values else None

    @property
    def confidence_lo95(self) -> float:
        """Wilson score interval lower bound on tp_rate (proxy for trust)."""
        n = self.n_executed
        if n == 0:
            return 0.0
        p = self.n_tp / n
        z = 1.96
        denom = 1 + z * z / n
        center = p + z * z / (2 * n)
        margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
        return max(0.0, (center - margin) / denom)


def aggregate(signals: List[dict], outcomes: List[dict]) -> Dict[str, ChannelStats]:
    """Join signals→outcomes and roll up per-channel metrics.

    Join key: (signal_ts, account). One signal can yield N outcomes (one per
    position). Per EA logic, all positions of one signal target TP1 — so we
    classify the *signal* by the unanimous close_reason of its outcomes.
    """
    # Index outcomes by (signal_ts, account) → list[outcome]
    by_key: Dict[tuple, List[dict]] = {}
    for o in outcomes:
        by_key.setdefault((o["signal_ts"], o["account"]), []).append(o)

    stats: Dict[str, ChannelStats] = {}
    for sig in signals:
        ch = sig["channel_name"] or "unknown"
        cs = stats.setdefault(ch, ChannelStats(channel=ch))
        cs.n_signals += 1

        # A signal can land on multiple accounts — count it as one "executed"
        # if ANY account took the trade. But TP/SL classification is per-account
        # collapsed: a signal counts as TP only if every matched account is all-TP.
        matched_outcomes: List[dict] = []
        for (sig_ts, _acct), olist in by_key.items():
            if sig_ts == sig["signal_ts"]:
                matched_outcomes.extend(olist)
        if not matched_outcomes:
            continue

        cs.n_executed += 1

        reasons = {o["close_reason"] for o in matched_outcomes}
        if reasons == {"TP"}:
            cs.n_tp += 1
        elif reasons == {"SL"}:
            cs.n_sl += 1
        else:
            cs.n_mixed += 1

        pnl = sum(o["profit"] + o["swap"] + o["commission"] for o in matched_outcomes)
        cs.total_pnl += pnl

        # R = pnl / risk where risk = |entry - sl| × total volume × point-value-per-unit
        # We don't know per-symbol point value here, so approximate:
        # risk ≈ |entry - sl| × total_volume (in price units × lots — relative scale only).
        sl_dist = abs(sig["entry_price"] - sig["sl"])
        total_vol = sum(o["volume"] for o in matched_outcomes)
        if sl_dist > 0 and total_vol > 0:
            cs.r_values.append(pnl / (sl_dist * total_vol))

    return stats


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_NAME_W = 22


def _fmt_name(s: str) -> str:
    s = clean_channel_name(s) or "unknown"
    return (s[:_NAME_W - 1] + "…") if len(s) > _NAME_W else s.ljust(_NAME_W)


def format_report(stats: Dict[str, ChannelStats], since_label: str) -> str:
    """Render a mobile-friendly per-channel report.

    Two sections: "Executed" (channels with closed positions, sorted by Wilson
    lower-bound on TP rate — proxy for trust) and "Pending" (no executions yet,
    sorted by signal count). Each row fits one mobile line; executed rows append
    a second indented line with TP%, avg_R, and conf95 so unused stats columns
    don't clutter pending rows.
    """
    if not stats:
        return f"KOG /stats — {since_label}\n\n(no signals in window)"

    rows = list(stats.values())
    total_sig = sum(c.n_signals for c in rows)
    total_exec = sum(c.n_executed for c in rows)

    executed = sorted(
        (c for c in rows if c.n_executed > 0),
        key=lambda c: (c.confidence_lo95, c.n_signals),
        reverse=True,
    )
    pending = sorted(
        (c for c in rows if c.n_executed == 0),
        key=lambda c: c.n_signals,
        reverse=True,
    )

    lines = [
        f"KOG /stats — {since_label}",
        f"{total_sig} sig · {total_exec} exec",
    ]

    if executed:
        lines += ["", "Executed:"]
        for cs in executed:
            tp = f"TP {cs.tp_rate * 100:.0f}%" if cs.tp_rate is not None else "TP -"
            ar = f"R {cs.avg_r:+.2f}" if cs.avg_r is not None else "R -"
            c95 = f"c95 {cs.confidence_lo95:.2f}"
            lines.append(f"{_fmt_name(cs.channel)} {cs.n_signals:>3}/{cs.n_executed}")
            lines.append(f"  {tp} · {ar} · {c95}")

    if pending:
        lines += ["", "Pending:"]
        for cs in pending:
            lines.append(f"{_fmt_name(cs.channel)} {cs.n_signals:>3}")

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
