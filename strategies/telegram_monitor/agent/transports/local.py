"""LocalTransport — bot runs on the same VPS as the agents.

Shells out to `nssm status` for service state and reads files via pathlib.
All blocking IO is offloaded to a thread (asyncio.to_thread) so the bot's
event loop never stalls when a log file is large or a disk is slow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from .base import LogFile, ServiceState, ServiceStatus, SignalFile, Transport

_LOG_SUFFIXES = {".log", ".out", ".err", ".txt"}

log = logging.getLogger(__name__)

# nssm prints the state as a UTF-16 string with NULs between chars on some
# Windows builds. Normalize defensively before matching.
_STATE_MAP = {
    "SERVICE_RUNNING": ServiceState.RUNNING,
    "SERVICE_STOPPED": ServiceState.STOPPED,
    "SERVICE_PAUSED": ServiceState.PAUSED,
    "SERVICE_START_PENDING": ServiceState.START_PENDING,
    "SERVICE_STOP_PENDING": ServiceState.STOP_PENDING,
}


def _parse_nssm_status(raw: str) -> ServiceState:
    cleaned = raw.replace("\x00", "").strip().upper()
    for key, state in _STATE_MAP.items():
        if key in cleaned:
            return state
    if "CAN'T OPEN SERVICE" in cleaned or "DOES NOT EXIST" in cleaned:
        return ServiceState.NOT_INSTALLED
    return ServiceState.UNKNOWN


def _run_nssm_status(nssm_service: str) -> ServiceStatus:
    try:
        proc = subprocess.run(
            ["nssm", "status", nssm_service],
            capture_output=True, text=True, timeout=10,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return ServiceStatus(ServiceState.UNKNOWN, "nssm not on PATH")
    except subprocess.TimeoutExpired:
        return ServiceStatus(ServiceState.UNKNOWN, "nssm status timed out")
    return ServiceStatus(_parse_nssm_status(raw), raw.strip())


def _detect_encoding(p: Path) -> str:
    """Sniff BOM. MT5 daily logs are written as UTF-16 LE; agent logs UTF-8."""
    try:
        with p.open("rb") as f:
            head = f.read(4)
    except OSError:
        return "utf-8"
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def _read_tail(log_path: str, n_lines: int) -> list[str]:
    p = Path(log_path)
    if not p.exists():
        return []
    # Small log files dominate; a naive read is fine and avoids encoding
    # surprises that arise with seek-based tail on UTF-16 / CRLF logs.
    enc = _detect_encoding(p)
    try:
        with p.open("r", encoding=enc, errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        log.warning("read_log_tail failed for %s: %s", log_path, e)
        return []
    return [ln.rstrip("\r\n") for ln in lines[-n_lines:]]


def _read_since(log_path: str, byte_offset: int) -> tuple[str, int]:
    p = Path(log_path)
    if not p.exists():
        return "", byte_offset
    try:
        size = p.stat().st_size
        # Rotation / truncate detected — restart from the top.
        start = 0 if size < byte_offset else byte_offset
        with p.open("rb") as f:
            f.seek(start)
            data = f.read()
        return data.decode("utf-8", errors="replace"), start + len(data)
    except OSError as e:
        log.warning("read_log_since failed for %s: %s", log_path, e)
        return "", byte_offset


def _list_logs(log_dir: str) -> list[LogFile]:
    d = Path(log_dir)
    if not d.is_dir():
        return []
    out: list[LogFile] = []
    for f in d.iterdir():
        if not f.is_file() or f.suffix.lower() not in _LOG_SUFFIXES:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        out.append(LogFile(path=str(f), name=f.name,
                           size_bytes=st.st_size, mtime_epoch=st.st_mtime))
    out.sort(key=lambda l: l.mtime_epoch, reverse=True)
    return out


def _read_signal_json(signal_dir: str, name: str) -> dict | None:
    p = Path(signal_dir) / name
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("read_signal_json failed for %s: %s", p, e)
        return None
    return data if isinstance(data, dict) else None


def _list_signals(signal_dir: str) -> list[SignalFile]:
    d = Path(signal_dir)
    if not d.is_dir():
        return []
    out: list[SignalFile] = []
    for f in d.iterdir():
        if not f.is_file() or f.suffix.lower() != ".json":
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        out.append(SignalFile(name=f.name, size_bytes=st.st_size, mtime_epoch=st.st_mtime))
    out.sort(key=lambda s: s.mtime_epoch, reverse=True)
    return out


class LocalTransport(Transport):
    async def get_service_status(self, nssm_service: str) -> ServiceStatus:
        return await asyncio.to_thread(_run_nssm_status, nssm_service)

    async def read_log_tail(self, log_path: str, n_lines: int) -> list[str]:
        return await asyncio.to_thread(_read_tail, log_path, n_lines)

    async def read_log_since(self, log_path: str, byte_offset: int) -> tuple[str, int]:
        return await asyncio.to_thread(_read_since, log_path, byte_offset)

    async def list_log_files(self, log_dir: str) -> list[LogFile]:
        return await asyncio.to_thread(_list_logs, log_dir)

    async def list_signal_files(self, signal_dir: str) -> list[SignalFile]:
        return await asyncio.to_thread(_list_signals, signal_dir)

    async def read_signal_json(self, signal_dir: str, name: str) -> dict | None:
        return await asyncio.to_thread(_read_signal_json, signal_dir, name)
