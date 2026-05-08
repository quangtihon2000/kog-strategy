"""Render the per-channel /stats data as a static HTML dashboard.

Pulls signals + outcomes from Redis (with optional file fallback for outcomes),
runs the same aggregation logic as `/stats` and `verify_stats.py`, and dumps:

    {out_dir}/index.html   — editorial-style dashboard (Tailwind CDN, no build step)
    {out_dir}/stats.json   — full machine-readable snapshot (channels + positions)

Usage:
    python -m dump_html --window 30d --out ./public
    python -m dump_html --window 7d  --out /var/www/conde-stats --redis-url redis://...

The HTML is self-contained and can be served by any static host (NGINX,
GitHub Pages, S3). Re-run the script periodically (cron / Windows scheduled
task) to refresh; the agent itself doesn't serve traffic.
"""

import argparse
import html
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import redis as redis_lib
from dotenv import load_dotenv

from stats import (
    ChannelStats,
    aggregate,
    classify_outcome,
    classify_signal,
    fetch_outcomes,
    fetch_outcomes_from_files,
    fetch_signals,
    now_ms,
    parse_duration,
)

load_dotenv()

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def _fmt_int(v: Optional[int]) -> str:
    return f"{v:,}" if v is not None else "—"


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else "−"
    return f"{sign}${abs(v):,.2f}"


def _fmt_r(v: Optional[float]) -> str:
    return f"{v:+.2f}R" if v is not None else "—"


def _fmt_ts(epoch_s: int) -> str:
    if not epoch_s:
        return "—"
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_ts_short(epoch_s: int) -> str:
    if not epoch_s:
        return "—"
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime("%m-%d %H:%M")


def _kind_color(kind: str) -> str:
    """Tailwind arbitrary-value color class for a position outcome kind."""
    return {
        "TP":          "text-[#2d7a3a]",
        "SL_TRAIL":    "text-[#14797f]",
        "SL_BE":       "text-[#b8731f]",
        "SL_ORIGINAL": "text-[#b83a2e]",
        "OTHER":       "text-[#8e8e85]",
    }.get(kind, "text-[#1a1a17]")


def _klass_color(klass: str) -> str:
    return {
        "WIN_CLEAN": "text-[#2d7a3a]",
        "WIN_TRAIL": "text-[#14797f]",
        "WIN_MIXED": "text-[#b8731f]",
        "SAVED":     "text-[#b8731f]",
        "LOSS":      "text-[#b83a2e]",
        "MANUAL":    "text-[#8e8e85]",
        "NO_EXEC":   "text-[#8e8e85]",
    }.get(klass, "text-[#1a1a17]")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(
    redis_url: str,
    window_seconds: int,
    outcomes_dir: Optional[Path],
) -> Tuple[List[dict], List[dict]]:
    """Fetch signals + outcomes for the window. Falls back to file outcomes if Redis fails."""
    since_ms = now_ms() - window_seconds * 1000
    since_epoch = since_ms // 1000

    try:
        r = redis_lib.from_url(redis_url, decode_responses=True)
        r.ping()
    except (redis_lib.exceptions.RedisError, OSError) as exc:
        log.warning("Redis unavailable (%s) — falling back to file outcomes", exc)
        if outcomes_dir is None:
            raise RuntimeError("Redis unavailable and no --outcomes-dir provided") from exc
        return [], fetch_outcomes_from_files(outcomes_dir, since_epoch)

    signals = fetch_signals(r, since_ms)
    try:
        outcomes = fetch_outcomes(r, since_ms)
    except redis_lib.exceptions.RedisError as exc:
        log.warning("Redis xrange outcomes failed (%s) — file fallback", exc)
        outcomes = fetch_outcomes_from_files(outcomes_dir, since_epoch) if outcomes_dir else []
    return signals, outcomes


# ---------------------------------------------------------------------------
# Position log enrichment
# ---------------------------------------------------------------------------

def build_position_log(signals: List[dict], outcomes: List[dict]) -> List[dict]:
    """Annotate each closed position with its parent signal + outcome kind."""
    sig_by_ts: Dict[int, dict] = {s["signal_ts"]: s for s in signals}
    rows: List[dict] = []
    for o in outcomes:
        sig = sig_by_ts.get(o["signal_ts"])
        kind = classify_outcome(o, sig)
        rows.append({
            "position_id": o["position_id"],
            "signal_ts":   o["signal_ts"],
            "account":     o["account"],
            "channel":     (sig["channel_name"] if sig else "—"),
            "symbol":      o["symbol"],
            "direction":   o["direction"],
            "volume":      o["volume"],
            "entry_price": o["entry_price"],
            "exit_price":  o["exit_price"],
            "profit":      o["profit"] + o["swap"] + o["commission"],
            "opened_at":   o["opened_at"],
            "closed_at":   o["closed_at"],
            "close_reason": o["close_reason"],
            "kind":        kind,
        })
    rows.sort(key=lambda x: x["closed_at"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Headline KPI roll-up
# ---------------------------------------------------------------------------

def headline_kpis(stats_map: Dict[str, ChannelStats]) -> dict:
    rows = list(stats_map.values())
    n_signals    = sum(c.n_signals for c in rows)
    n_executed   = sum(c.n_executed for c in rows)
    n_positions  = sum(c.n_positions for c in rows)
    n_win        = sum(c.n_win for c in rows)
    n_loss       = sum(c.n_loss for c in rows)
    n_saved      = sum(c.n_saved for c in rows)
    n_classified = sum(c.n_classified for c in rows)
    total_pnl    = sum(c.total_pnl for c in rows)
    all_r = [r for c in rows for r in c.r_values]
    avg_r = sum(all_r) / len(all_r) if all_r else None

    win_rate  = (n_win / n_classified) if n_classified else None
    loss_rate = (n_loss / n_classified) if n_classified else None
    save_rate = (n_saved / n_classified) if n_classified else None

    n_tracked = sum(1 for c in rows if c.n_signals > 0)
    n_with_signal = sum(1 for c in rows if c.n_executed > 0)

    return {
        "n_channels_tracked": n_tracked,
        "n_channels_active": n_with_signal,
        "n_signals": n_signals,
        "n_executed": n_executed,
        "n_positions": n_positions,
        "n_win": n_win,
        "n_loss": n_loss,
        "n_saved": n_saved,
        "n_classified": n_classified,
        "total_pnl": total_pnl,
        "avg_r": avg_r,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "save_rate": save_rate,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_kpi_cards(kpi: dict) -> str:
    cards = [
        ("Signals",       _fmt_int(kpi["n_signals"]),   f"{kpi['n_executed']} executed"),
        ("Positions",     _fmt_int(kpi["n_positions"]), f"{kpi['n_classified']} classified"),
        ("Win rate",      _fmt_pct(kpi["win_rate"]),    f"loss {_fmt_pct(kpi['loss_rate'])} · saved {_fmt_pct(kpi['save_rate'])}"),
        ("Net P&L",       _fmt_money(kpi["total_pnl"]), f"avg {_fmt_r(kpi['avg_r'])}"),
        ("Channels",      _fmt_int(kpi["n_channels_active"]), f"of {kpi['n_channels_tracked']} tracked"),
    ]
    out = []
    for label, value, caption in cards:
        out.append(f"""
    <div class="bg-white border border-[#e2e0d8] p-6">
      <div class="text-[10px] uppercase tracking-[0.18em] text-[#8e8e85]">{_esc(label)}</div>
      <div class="mt-3 text-3xl font-light text-[#1a1a17] tracking-[-0.035em]">{_esc(value)}</div>
      <div class="mt-2 text-xs text-[#5a5a55]">{_esc(caption)}</div>
    </div>""")
    return "".join(out)


def render_channel_table(stats_map: Dict[str, ChannelStats]) -> str:
    rows = sorted(
        stats_map.values(),
        key=lambda c: (c.n_executed > 0, c.confidence_lo95, c.n_signals),
        reverse=True,
    )

    head = """
    <thead>
      <tr class="border-b border-[#1a1a17] text-[10px] uppercase tracking-[0.18em] text-[#5a5a55]">
        <th class="text-left  py-3 px-3 font-normal">Channel</th>
        <th class="text-right py-3 px-3 font-normal">Sig</th>
        <th class="text-right py-3 px-3 font-normal">Exec</th>
        <th class="text-right py-3 px-3 font-normal">Pos</th>
        <th class="text-right py-3 px-3 font-normal">Win</th>
        <th class="text-right py-3 px-3 font-normal">Loss</th>
        <th class="text-right py-3 px-3 font-normal">Save</th>
        <th class="text-right py-3 px-3 font-normal">Clean</th>
        <th class="text-right py-3 px-3 font-normal">Trail</th>
        <th class="text-right py-3 px-3 font-normal">Mixed</th>
        <th class="text-right py-3 px-3 font-normal">Manual</th>
        <th class="text-right py-3 px-3 font-normal">avg R</th>
        <th class="text-right py-3 px-3 font-normal">P&amp;L</th>
        <th class="text-right py-3 px-3 font-normal">c95</th>
      </tr>
    </thead>"""

    body_parts = ["<tbody>"]
    for c in rows:
        wr = c.win_rate
        wr_color = ""
        if wr is not None:
            if wr >= 0.6:
                wr_color = "text-[#2d7a3a]"
            elif wr <= 0.4:
                wr_color = "text-[#b83a2e]"
        pnl_color = "text-[#2d7a3a]" if c.total_pnl > 0 else ("text-[#b83a2e]" if c.total_pnl < 0 else "text-[#5a5a55]")

        body_parts.append(f"""
      <tr class="border-b border-[#e2e0d8] hover:bg-[#fafaf7]">
        <td class="py-3 px-3 text-[#1a1a17]">{_esc(c.channel)}</td>
        <td class="py-3 px-3 text-right text-[#1a1a17] tabular-nums">{c.n_signals}</td>
        <td class="py-3 px-3 text-right text-[#1a1a17] tabular-nums">{c.n_executed}</td>
        <td class="py-3 px-3 text-right text-[#1a1a17] tabular-nums">{c.n_positions}</td>
        <td class="py-3 px-3 text-right tabular-nums {wr_color}">{_fmt_pct(c.win_rate)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{_fmt_pct(c.loss_rate)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{_fmt_pct(c.save_rate)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{_fmt_pct(c.clean_rate)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{_fmt_pct(c.trail_rate)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{c.n_win_mixed}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#8e8e85]">{c.n_manual}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#1a1a17]">{_fmt_r(c.avg_r)}</td>
        <td class="py-3 px-3 text-right tabular-nums {pnl_color}">{_fmt_money(c.total_pnl)}</td>
        <td class="py-3 px-3 text-right tabular-nums text-[#5a5a55]">{_fmt_pct(c.confidence_lo95)}</td>
      </tr>""")
    if not rows:
        body_parts.append("""
      <tr><td colspan="14" class="py-10 text-center text-[#8e8e85] italic">No signals in window.</td></tr>""")
    body_parts.append("</tbody>")

    return f"""
<div class="overflow-x-auto">
  <table class="w-full text-sm">
    {head}
    {"".join(body_parts)}
  </table>
</div>"""


def render_position_log(rows: List[dict], limit: int = 200) -> str:
    head = """
    <thead>
      <tr class="border-b border-[#1a1a17] text-[10px] uppercase tracking-[0.18em] text-[#5a5a55]">
        <th class="text-left  py-3 px-3 font-normal">Closed</th>
        <th class="text-left  py-3 px-3 font-normal">Channel</th>
        <th class="text-left  py-3 px-3 font-normal">Acct</th>
        <th class="text-left  py-3 px-3 font-normal">Sym</th>
        <th class="text-left  py-3 px-3 font-normal">Dir</th>
        <th class="text-right py-3 px-3 font-normal">Vol</th>
        <th class="text-right py-3 px-3 font-normal">Entry</th>
        <th class="text-right py-3 px-3 font-normal">Exit</th>
        <th class="text-left  py-3 px-3 font-normal">Reason</th>
        <th class="text-left  py-3 px-3 font-normal">Kind</th>
        <th class="text-right py-3 px-3 font-normal">P&amp;L</th>
      </tr>
    </thead>"""

    body_parts = ["<tbody>"]
    shown = rows[:limit]
    for r in shown:
        pnl = r["profit"]
        pnl_color = "text-[#2d7a3a]" if pnl > 0 else ("text-[#b83a2e]" if pnl < 0 else "text-[#5a5a55]")
        dir_color = "text-[#14797f]" if r["direction"] == "BUY" else "text-[#b8731f]"
        body_parts.append(f"""
      <tr class="border-b border-[#e2e0d8] hover:bg-[#fafaf7]">
        <td class="py-2.5 px-3 text-[#5a5a55] tabular-nums whitespace-nowrap">{_esc(_fmt_ts_short(r["closed_at"]))}</td>
        <td class="py-2.5 px-3 text-[#1a1a17]">{_esc(r["channel"])}</td>
        <td class="py-2.5 px-3 text-[#5a5a55] tabular-nums">{r["account"]}</td>
        <td class="py-2.5 px-3 text-[#1a1a17]">{_esc(r["symbol"])}</td>
        <td class="py-2.5 px-3 {dir_color}">{_esc(r["direction"])}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{r["volume"]:.2f}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{r["entry_price"]:.3f}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{r["exit_price"]:.3f}</td>
        <td class="py-2.5 px-3 text-[#8e8e85] uppercase text-xs tracking-wider">{_esc(r["close_reason"])}</td>
        <td class="py-2.5 px-3 uppercase text-xs tracking-wider {_kind_color(r["kind"])}">{_esc(r["kind"])}</td>
        <td class="py-2.5 px-3 text-right tabular-nums {pnl_color}">{_fmt_money(pnl)}</td>
      </tr>""")
    if not shown:
        body_parts.append("""
      <tr><td colspan="11" class="py-10 text-center text-[#8e8e85] italic">No closed positions in window.</td></tr>""")
    body_parts.append("</tbody>")

    footer = ""
    if len(rows) > limit:
        footer = f"""<div class="mt-3 text-xs text-[#8e8e85] italic">Showing latest {limit} of {len(rows)} positions. Full set in <code class="text-[#5a5a55]">stats.json</code>.</div>"""

    return f"""
<div class="overflow-x-auto">
  <table class="w-full text-sm">
    {head}
    {"".join(body_parts)}
  </table>
</div>
{footer}"""


def render_signal_log(signals: List[dict], outcomes: List[dict], limit: int = 100) -> str:
    by_sig_ts: Dict[int, List[dict]] = {}
    for o in outcomes:
        by_sig_ts.setdefault(o["signal_ts"], []).append(o)

    items = []
    for s in signals:
        matched = by_sig_ts.get(s["signal_ts"], [])
        kinds = [classify_outcome(o, s) for o in matched]
        klass = classify_signal(kinds)
        pnl = sum(o["profit"] + o["swap"] + o["commission"] for o in matched)
        items.append({**s, "matched": matched, "kinds": kinds, "klass": klass, "pnl": pnl})

    items.sort(key=lambda x: x["signal_ts"], reverse=True)
    shown = items[:limit]

    head = """
    <thead>
      <tr class="border-b border-[#1a1a17] text-[10px] uppercase tracking-[0.18em] text-[#5a5a55]">
        <th class="text-left  py-3 px-3 font-normal">Signal time</th>
        <th class="text-left  py-3 px-3 font-normal">Channel</th>
        <th class="text-left  py-3 px-3 font-normal">Sym</th>
        <th class="text-left  py-3 px-3 font-normal">Dir</th>
        <th class="text-right py-3 px-3 font-normal">Entry</th>
        <th class="text-right py-3 px-3 font-normal">SL</th>
        <th class="text-left  py-3 px-3 font-normal">TPs</th>
        <th class="text-right py-3 px-3 font-normal">Pos</th>
        <th class="text-left  py-3 px-3 font-normal">Class</th>
        <th class="text-right py-3 px-3 font-normal">P&amp;L</th>
      </tr>
    </thead>"""

    body_parts = ["<tbody>"]
    for s in shown:
        dir_color = "text-[#14797f]" if s["direction"] == "BUY" else "text-[#b8731f]"
        pnl = s["pnl"]
        pnl_color = "text-[#2d7a3a]" if pnl > 0 else ("text-[#b83a2e]" if pnl < 0 else "text-[#5a5a55]")
        tps_str = ", ".join(f"{tp:g}" for tp in s["tps"])
        body_parts.append(f"""
      <tr class="border-b border-[#e2e0d8] hover:bg-[#fafaf7]">
        <td class="py-2.5 px-3 text-[#5a5a55] tabular-nums whitespace-nowrap">{_esc(_fmt_ts_short(s["signal_ts"]))}</td>
        <td class="py-2.5 px-3 text-[#1a1a17]">{_esc(s["channel_name"])}</td>
        <td class="py-2.5 px-3 text-[#1a1a17]">{_esc(s["symbol"])}</td>
        <td class="py-2.5 px-3 {dir_color}">{_esc(s["direction"])}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{s["entry_price"]:.3f}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{s["sl"]:.3f}</td>
        <td class="py-2.5 px-3 text-[#8e8e85] tabular-nums text-xs">{_esc(tps_str)}</td>
        <td class="py-2.5 px-3 text-right text-[#5a5a55] tabular-nums">{len(s["matched"])}</td>
        <td class="py-2.5 px-3 uppercase text-xs tracking-wider {_klass_color(s["klass"])}">{_esc(s["klass"])}</td>
        <td class="py-2.5 px-3 text-right tabular-nums {pnl_color}">{_fmt_money(pnl) if s["matched"] else "—"}</td>
      </tr>""")
    if not shown:
        body_parts.append("""
      <tr><td colspan="10" class="py-10 text-center text-[#8e8e85] italic">No signals in window.</td></tr>""")
    body_parts.append("</tbody>")

    footer = ""
    if len(items) > limit:
        footer = f"""<div class="mt-3 text-xs text-[#8e8e85] italic">Showing latest {limit} of {len(items)} signals. Full set in <code class="text-[#5a5a55]">stats.json</code>.</div>"""

    return f"""
<div class="overflow-x-auto">
  <table class="w-full text-sm">
    {head}
    {"".join(body_parts)}
  </table>
</div>
{footer}"""


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render_html(
    window_label: str,
    generated_at_epoch: int,
    stats_map: Dict[str, ChannelStats],
    signals: List[dict],
    outcomes: List[dict],
    position_log: List[dict],
) -> str:
    kpi = headline_kpis(stats_map)
    win_pct  = _fmt_pct(kpi["win_rate"])
    pnl_str  = _fmt_money(kpi["total_pnl"])
    avg_r    = _fmt_r(kpi["avg_r"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Conde · Channel Effectiveness — last {_esc(window_label)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; font-feature-settings: "ss01", "cv11"; }}
    .serif {{ font-family: 'Fraunces', Georgia, serif; font-optical-sizing: auto; }}
    .pulse {{ animation: pulse 2s ease-in-out infinite; }}
    @keyframes pulse {{
      0%,100% {{ opacity: 0.5; }}
      50%     {{ opacity: 1; }}
    }}
    .hairline {{ border-color: #e2e0d8; }}
    code {{ font-family: 'JetBrains Mono', ui-monospace, monospace; }}
  </style>
</head>
<body class="bg-[#fafaf7] text-[#1a1a17] antialiased">

  <!-- ============================== HEADER ============================== -->
  <header class="border-b border-[#1a1a17]">
    <div class="max-w-[1400px] mx-auto px-8 py-5 flex items-center justify-between">
      <div class="flex items-center gap-4">
        <div class="text-[11px] uppercase tracking-[0.22em] text-[#1a1a17] font-medium">Conde&nbsp;·&nbsp;Channel Effectiveness</div>
        <div class="hidden sm:flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-[#8e8e85]">
          <span class="inline-block w-1.5 h-1.5 rounded-full bg-[#2d7a3a] pulse"></span>
          <span>Static snapshot</span>
        </div>
      </div>
      <div class="text-[10px] uppercase tracking-[0.18em] text-[#8e8e85]">Generated {_esc(_fmt_ts(generated_at_epoch))}</div>
    </div>
  </header>

  <!-- ============================== HERO ================================ -->
  <section class="border-b border-[#e2e0d8]">
    <div class="max-w-[1400px] mx-auto px-8 py-16 grid grid-cols-1 lg:grid-cols-12 gap-10">
      <div class="lg:col-span-8">
        <div class="text-[10px] uppercase tracking-[0.22em] text-[#b8731f] font-medium mb-6">Issue 01 · last {_esc(window_label)}</div>
        <h1 class="serif text-5xl md:text-6xl lg:text-7xl font-light tracking-[-0.04em] leading-[1.02] text-[#1a1a17]">
          Which channels<br>
          <span class="italic text-[#b8731f]">actually move money</span>,<br>
          and which only move noise.
        </h1>
        <div class="mt-8 pl-6 border-l-2 border-[#b8731f]">
          <p class="text-base text-[#5a5a55] leading-relaxed max-w-2xl">
            A per-channel rollup of every signal published to the <code class="text-[#1a1a17]">conde_signals</code> stream,
            joined to its closed positions on <code class="text-[#1a1a17]">conde_outcomes</code>. SL kind is inferred from
            <em>exit price vs original SL distance</em> — TP, trailing, break-even, and original SL are all separable
            without instrumentation in the EA.
          </p>
        </div>
      </div>
      <div class="lg:col-span-4 flex flex-col justify-end">
        <div class="border border-[#1a1a17] p-8 bg-white">
          <div class="text-[10px] uppercase tracking-[0.22em] text-[#8e8e85] mb-4">Period summary</div>
          <div class="space-y-4">
            <div class="flex items-baseline justify-between border-b border-[#e2e0d8] pb-3">
              <span class="text-xs uppercase tracking-[0.15em] text-[#5a5a55]">Win rate</span>
              <span class="serif text-3xl font-light text-[#1a1a17] tracking-[-0.03em]">{_esc(win_pct)}</span>
            </div>
            <div class="flex items-baseline justify-between border-b border-[#e2e0d8] pb-3">
              <span class="text-xs uppercase tracking-[0.15em] text-[#5a5a55]">Net P&amp;L</span>
              <span class="serif text-3xl font-light tracking-[-0.03em] {('text-[#2d7a3a]' if kpi['total_pnl'] >= 0 else 'text-[#b83a2e]')}">{_esc(pnl_str)}</span>
            </div>
            <div class="flex items-baseline justify-between">
              <span class="text-xs uppercase tracking-[0.15em] text-[#5a5a55]">Avg R</span>
              <span class="serif text-3xl font-light text-[#1a1a17] tracking-[-0.03em]">{_esc(avg_r)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ============================== §01 OVERVIEW ============================== -->
  <section class="border-b border-[#e2e0d8]">
    <div class="max-w-[1400px] mx-auto px-8 py-16">
      <div class="flex items-baseline gap-6 mb-10">
        <div class="text-[10px] uppercase tracking-[0.22em] text-[#b8731f] font-medium">§01</div>
        <h2 class="serif text-3xl md:text-4xl font-light tracking-[-0.035em] text-[#1a1a17]">Overview</h2>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-px bg-[#e2e0d8]">
        {render_kpi_cards(kpi)}
      </div>
    </div>
  </section>

  <!-- ============================== §02 CHANNELS ============================== -->
  <section class="border-b border-[#e2e0d8]">
    <div class="max-w-[1400px] mx-auto px-8 py-16">
      <div class="flex items-baseline gap-6 mb-3">
        <div class="text-[10px] uppercase tracking-[0.22em] text-[#b8731f] font-medium">§02</div>
        <h2 class="serif text-3xl md:text-4xl font-light tracking-[-0.035em] text-[#1a1a17]">Per-channel breakdown</h2>
      </div>
      <p class="text-sm text-[#5a5a55] mb-8 max-w-3xl">
        Sorted by Wilson 95% confidence-lower-bound on win rate, then signal volume. Channels with too few classified
        samples drop to the bottom — high <em>raw</em> win rate on three signals isn't trustworthy yet.
      </p>
      {render_channel_table(stats_map)}
      <div class="mt-6 text-xs text-[#8e8e85] leading-relaxed max-w-3xl">
        <strong class="text-[#5a5a55]">Glossary —</strong>
        <span class="text-[#2d7a3a]">Clean</span>: every position hit TP without BE/trail moving SL.
        <span class="text-[#14797f]">Trail</span>: TP after BE/trail moved.
        <span class="text-[#b8731f]">Mixed</span>: partial TP + partial original SL.
        <span class="text-[#b8731f]">Save</span>: no TP, no original SL — closed at BE+ / trail.
        <span class="text-[#b83a2e]">Loss</span>: at least one original SL hit, no TP.
        <span class="text-[#8e8e85]">Manual</span>: closed by user.
        <strong class="text-[#5a5a55]">c95</strong>: lower bound of 95% Wilson interval on win rate (trust proxy).
      </div>
    </div>
  </section>

  <!-- ============================== §03 SIGNAL LOG ============================== -->
  <section class="border-b border-[#e2e0d8]">
    <div class="max-w-[1400px] mx-auto px-8 py-16">
      <div class="flex items-baseline gap-6 mb-3">
        <div class="text-[10px] uppercase tracking-[0.22em] text-[#b8731f] font-medium">§03</div>
        <h2 class="serif text-3xl md:text-4xl font-light tracking-[-0.035em] text-[#1a1a17]">Signal log</h2>
      </div>
      <p class="text-sm text-[#5a5a55] mb-8 max-w-3xl">
        Each signal as one effectiveness sample, classified from the multiset of its positions' SL kinds.
        Latest first.
      </p>
      {render_signal_log(signals, outcomes)}
    </div>
  </section>

  <!-- ============================== §04 POSITION LOG ============================== -->
  <section class="border-b border-[#e2e0d8]">
    <div class="max-w-[1400px] mx-auto px-8 py-16">
      <div class="flex items-baseline gap-6 mb-3">
        <div class="text-[10px] uppercase tracking-[0.22em] text-[#b8731f] font-medium">§04</div>
        <h2 class="serif text-3xl md:text-4xl font-light tracking-[-0.035em] text-[#1a1a17]">Position log</h2>
      </div>
      <p class="text-sm text-[#5a5a55] mb-8 max-w-3xl">
        Every closed position with its inferred SL kind. Color legend:
        <span class="text-[#2d7a3a]">TP</span> ·
        <span class="text-[#14797f]">SL_TRAIL</span> ·
        <span class="text-[#b8731f]">SL_BE</span> ·
        <span class="text-[#b83a2e]">SL_ORIGINAL</span> ·
        <span class="text-[#8e8e85]">OTHER</span>.
      </p>
      {render_position_log(position_log)}
    </div>
  </section>

  <!-- ============================== FOOTER ============================== -->
  <footer class="border-t border-[#1a1a17]">
    <div class="max-w-[1400px] mx-auto px-8 py-10 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
      <div class="text-[10px] uppercase tracking-[0.22em] text-[#5a5a55]">
        Conde Auto Entry · channel effectiveness rollup
      </div>
      <div class="text-[10px] uppercase tracking-[0.18em] text-[#8e8e85]">
        Source: <code class="text-[#5a5a55]">conde_signals</code> · <code class="text-[#5a5a55]">conde_outcomes</code> ·
        Snapshot {_esc(_fmt_ts(generated_at_epoch))}
      </div>
    </div>
  </footer>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# JSON dump
# ---------------------------------------------------------------------------

def render_stats_json(
    window_label: str,
    window_seconds: int,
    generated_at_epoch: int,
    stats_map: Dict[str, ChannelStats],
    signals: List[dict],
    outcomes: List[dict],
    position_log: List[dict],
) -> str:
    channels_payload = []
    for c in sorted(
        stats_map.values(),
        key=lambda x: (x.n_executed > 0, x.confidence_lo95, x.n_signals),
        reverse=True,
    ):
        d = asdict(c)
        d.update({
            "n_win":           c.n_win,
            "n_classified":    c.n_classified,
            "win_rate":        c.win_rate,
            "loss_rate":       c.loss_rate,
            "save_rate":       c.save_rate,
            "clean_rate":      c.clean_rate,
            "trail_rate":      c.trail_rate,
            "avg_r":           c.avg_r,
            "confidence_lo95": c.confidence_lo95,
        })
        channels_payload.append(d)

    payload = {
        "generated_at":   generated_at_epoch,
        "window_label":   window_label,
        "window_seconds": window_seconds,
        "kpi":            headline_kpis(stats_map),
        "channels":       channels_payload,
        "signals":        signals,
        "outcomes":       outcomes,
        "position_log":   position_log,
    }
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render the conde /stats data as static HTML.")
    parser.add_argument("--window", default="30d",
                        help="Lookback window: e.g. 7d, 24h, 90m. Default: 30d.")
    parser.add_argument("--out", default="./public",
                        help="Output directory. Default: ./public/")
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
                        help="Redis URL. Default: $REDIS_URL or redis://localhost:6379.")
    parser.add_argument("--outcomes-dir", default=None,
                        help="Optional: directory of *.json outcome files for fallback when Redis unavailable.")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    seconds = parse_duration(args.window)
    outcomes_dir = Path(args.outcomes_dir) if args.outcomes_dir else None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Loading data: window=%s (%ds) redis=%s", args.window, seconds, args.redis_url)
    signals, outcomes = load_data(args.redis_url, seconds, outcomes_dir)
    log.info("Loaded %d signals, %d outcomes", len(signals), len(outcomes))

    stats_map = aggregate(signals, outcomes)
    position_log = build_position_log(signals, outcomes)
    generated_at = int(now_ms() / 1000)

    html_path = out_dir / "index.html"
    json_path = out_dir / "stats.json"

    html_doc = render_html(args.window, generated_at, stats_map, signals, outcomes, position_log)
    html_path.write_text(html_doc, encoding="utf-8")
    log.info("Wrote %s (%d bytes)", html_path, html_path.stat().st_size)

    json_doc = render_stats_json(args.window, seconds, generated_at, stats_map, signals, outcomes, position_log)
    json_path.write_text(json_doc, encoding="utf-8")
    log.info("Wrote %s (%d bytes)", json_path, json_path.stat().st_size)

    return 0


if __name__ == "__main__":
    sys.exit(main())
