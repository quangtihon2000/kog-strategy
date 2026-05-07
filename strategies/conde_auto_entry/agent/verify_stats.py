"""Dump raw signal→outcome join for /stats reconciliation.

Usage (on VPS, in the conde_auto_entry venv):
    python verify_stats.py            # default 30d window
    python verify_stats.py 7d
    python verify_stats.py 24h

Prints, per signal in the window:
- signal_ts (epoch + ISO), channel, symbol, direction
- list of matched outcomes with per-position SL kind (TP/SL_ORIGINAL/SL_BE/
  SL_TRAIL/OTHER), profit, exit_price
- signal class (WIN_CLEAN/WIN_TRAIL/WIN_MIXED/SAVED/LOSS/MANUAL/NO_EXEC)
  computed by the same logic as `aggregate()`

Then prints a per-channel roll-up identical to /stats so you can verify the
counts match.
"""

import os
import sys
from datetime import datetime, timezone

import redis as redis_lib
from dotenv import load_dotenv

from stats import (
    aggregate,
    classify_outcome,
    classify_signal,
    fetch_outcomes,
    fetch_signals,
    format_report,
    now_ms,
    parse_duration,
)

load_dotenv()


def _iso(epoch_s: int) -> str:
    if not epoch_s:
        return "-"
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    window = sys.argv[1] if len(sys.argv) > 1 else "30d"
    seconds = parse_duration(window)
    since_ms = now_ms() - seconds * 1000

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    r = redis_lib.from_url(redis_url, decode_responses=True)

    signals = fetch_signals(r, since_ms)
    outcomes = fetch_outcomes(r, since_ms)

    print(f"Window: last {window} (since_ms={since_ms})")
    print(f"Signals: {len(signals)}    Outcomes: {len(outcomes)}\n")

    # Index outcomes by signal_ts (across accounts) for display
    by_sig_ts: dict[int, list[dict]] = {}
    for o in outcomes:
        by_sig_ts.setdefault(o["signal_ts"], []).append(o)

    # Index signals by signal_ts so orphan outcomes can also be classified
    sig_by_ts: dict[int, dict] = {s["signal_ts"]: s for s in signals}

    # Track which outcomes were claimed by some signal (for orphan detection)
    claimed_sig_ts: set[int] = set()

    print("=" * 96)
    print("PER-SIGNAL DUMP (sorted by signal_ts)")
    print("=" * 96)

    for sig in sorted(signals, key=lambda s: s["signal_ts"]):
        sig_ts = sig["signal_ts"]
        matched = by_sig_ts.get(sig_ts, [])
        if matched:
            claimed_sig_ts.add(sig_ts)

        kinds = [classify_outcome(o, sig) for o in matched]
        klass = classify_signal(kinds)

        print(
            f"\n[{klass:10}] sig_ts={sig_ts} ({_iso(sig_ts)})  "
            f"channel={sig['channel_name']!r}  "
            f"{sig['symbol']} {sig['direction']}  "
            f"entry={sig['entry_price']} sl={sig['sl']}  "
            f"positions={len(matched)}"
        )
        if not matched:
            print("            (no matched outcomes)")
            continue
        for o, kind in zip(matched, kinds):
            print(
                f"            [{kind:11}] pos={o['position_id']:<12} acct={o['account']:<10} "
                f"reason={o['close_reason']:<6} vol={o['volume']:.2f} "
                f"exit={o['exit_price']:.3f} profit={o['profit']:+.2f} swap={o['swap']:+.2f} "
                f"opened={_iso(o['opened_at'])} closed={_iso(o['closed_at'])}"
            )

    # Orphan outcomes (signal_ts not present in signals window)
    orphan_sig_ts = set(by_sig_ts.keys()) - claimed_sig_ts
    if orphan_sig_ts:
        print("\n" + "=" * 96)
        print(f"ORPHAN OUTCOMES — {len(orphan_sig_ts)} signal_ts not found in signals stream")
        print("(signal pre-dates window, or signal entry never published)")
        print("=" * 96)
        for sig_ts in sorted(orphan_sig_ts):
            sig = sig_by_ts.get(sig_ts)  # None for true orphans
            for o in by_sig_ts[sig_ts]:
                kind = classify_outcome(o, sig)
                print(
                    f"  [{kind:11}] sig_ts={sig_ts} ({_iso(sig_ts)})  "
                    f"pos={o['position_id']} acct={o['account']} "
                    f"reason={o['close_reason']} exit={o['exit_price']:.3f} "
                    f"profit={o['profit']:+.2f}"
                )

    # Final per-channel rollup (same logic as /stats)
    print("\n" + "=" * 96)
    print("PER-CHANNEL ROLLUP (same logic as /stats)")
    print("=" * 96)
    stats_map = aggregate(signals, outcomes)
    print(format_report(stats_map, since_label=f"last {window}"))


if __name__ == "__main__":
    main()
