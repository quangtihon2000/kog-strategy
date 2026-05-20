"""Atomic signal file writer — one instance per MT5 account."""

import json
import logging
import os
import time
from pathlib import Path

from models import ZoneSignal

log = logging.getLogger(__name__)


class SignalWriter:
    """
    Writes ZoneSignal to `{base_dir}/{account_id}.json` atomically.

    Write flow:
        1. Serialize to JSON and write to `{account_id}.tmp`.
        2. os.replace(tmp, target) — atomic rename at the OS level.

    `sig.timestamp` is preserved from upstream Redis stream — the EA embeds
    it in every position comment for dedup, and strategy-stats joins
    ZoneOutcome ↔ ZoneSignal on that same value. Re-stamping here would
    break both invariants.

    The EA always sees either the complete old file or the complete new file,
    never a partial write.  No explicit file locking is needed.

    On Windows, MT5 may briefly hold the target file open while reading.
    A single retry after 50 ms handles that edge case.
    """

    def __init__(self, account_id: int, base_dir: Path) -> None:
        self.account_id = account_id
        self.signal_path = base_dir / f"{account_id}.json"
        self._tmp_path = self.signal_path.with_suffix(".tmp")

    def write(self, sig: ZoneSignal) -> None:
        sig.validate()
        self._tmp_path.write_text(sig.to_json(), encoding="ascii")
        self._atomic_replace(sig.timestamp)

    def read(self) -> ZoneSignal | None:
        """Return the signal currently on disk, or None if no/unreadable file.

        Reads the agent-written JSON shape directly (targets are real JSON
        arrays here, unlike the comma-string form `from_dict` expects), so it
        bypasses `from_dict`. Used by the deactivate path.
        """
        if not self.signal_path.exists():
            return None
        try:
            d = json.loads(self.signal_path.read_text(encoding="ascii"))
            return ZoneSignal(
                symbol=d["symbol"],
                redbox_upper=float(d["redbox_upper"]),
                redbox_lower=float(d["redbox_lower"]),
                targets_above=[float(x) for x in d["targets_above"]],
                targets_below=[float(x) for x in d["targets_below"]],
                timestamp=int(d["timestamp"]),
                active=ZoneSignal._parse_bool(d.get("active"), default=True),
                close_all=ZoneSignal._parse_bool(d.get("close_all"), default=False),
            )
        except (ValueError, KeyError, TypeError, OSError) as exc:
            log.warning("[%s] cannot read existing signal: %s", self.account_id, exc)
            return None

    def deactivate(self, *, close_all: bool = False) -> bool:
        """Rewrite the current signal file with active=false (timestamp kept).

        Returns True if a signal file existed and was deactivated, False if
        there was nothing to cancel or it was already inactive. The EA detects
        the same-timestamp rewrite and stops opening new positions; when
        `close_all` is set it also closes open positions + cancels pendings.
        """
        sig = self.read()
        if sig is None:
            return False
        if not sig.active:
            return False   # already inactive — nothing to do
        sig.active = False
        sig.close_all = close_all
        # validate() still holds — the file was a valid signal before.
        self._tmp_path.write_text(sig.to_json(), encoding="ascii")
        self._atomic_replace(sig.timestamp)
        return True

    def _atomic_replace(self, timestamp: int) -> None:
        """os.replace(tmp → target) with a single retry for Windows file locks."""
        for attempt in range(2):
            try:
                os.replace(self._tmp_path, self.signal_path)
                log.debug("[%s] atomic rename OK (ts=%d)", self.account_id, timestamp)
                return
            except PermissionError:
                if attempt == 0:
                    # EA briefly has the file open — wait one tick and retry
                    time.sleep(0.05)
                else:
                    raise
