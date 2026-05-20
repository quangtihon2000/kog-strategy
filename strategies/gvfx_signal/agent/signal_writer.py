"""Atomic signal file writer — one instance per (MT5 account, symbol) pair."""

import json
import logging
import os
import time
from pathlib import Path

from models import GvfxSignal

log = logging.getLogger(__name__)


class SignalWriter:
    """
    Writes GvfxSignal to `{base_dir}/{account_id}_{symbol}.json` atomically.

    Write flow:
        1. Serialize to JSON (timestamp preserved as-received — NOT re-stamped).
        2. Write to `{account_id}_{symbol}.tmp`.
        3. os.replace(tmp, target) — atomic rename at the OS level.

    The EA uses sig.timestamp as part of its dedup comment (GVFX_T{ts}),
    so the producer-supplied timestamp must be preserved end-to-end.
    """

    def __init__(self, account_id: int, symbol: str, base_dir: Path) -> None:
        self.account_id = account_id
        self.symbol = symbol
        self.signal_path = base_dir / f"{account_id}_{symbol}.json"
        self._tmp_path = self.signal_path.with_suffix(".tmp")

    def write(self, sig: GvfxSignal) -> None:
        sig.validate()

        if sig.symbol != self.symbol:
            raise ValueError(
                f"symbol mismatch: writer={self.symbol} signal={sig.symbol}"
            )

        self._tmp_path.write_text(sig.to_json(), encoding="ascii")

        self._atomic_replace(sig.timestamp)

    def read(self) -> GvfxSignal | None:
        """Return the signal currently on disk, or None if no/invalid file.

        Used by the deactivate path — the operator cancels "the current
        signal", so the agent reads back whatever the EA is acting on rather
        than carrying signal state in memory.
        """
        if not self.signal_path.exists():
            return None
        try:
            d = json.loads(self.signal_path.read_text(encoding="ascii"))
            return GvfxSignal.from_dict(d)
        except (ValueError, KeyError, OSError) as exc:
            log.warning(
                "[%s/%s] cannot read existing signal: %s",
                self.account_id, self.symbol, exc,
            )
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
        self._tmp_path.write_text(sig.to_json(), encoding="ascii")
        self._atomic_replace(sig.timestamp)
        return True

    def _atomic_replace(self, timestamp: int) -> None:
        """os.replace(tmp → target) with a single retry for Windows file locks."""
        for attempt in range(2):
            try:
                os.replace(self._tmp_path, self.signal_path)
                log.debug(
                    "[%s/%s] atomic rename OK (ts=%d)",
                    self.account_id, self.symbol, timestamp,
                )
                return
            except PermissionError:
                if attempt == 0:
                    time.sleep(0.05)
                else:
                    raise
