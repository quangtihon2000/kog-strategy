"""Transport interface — every VPS reach-out path implements this.

Keep the surface small and read-mostly. Adding a new transport (SSH, WinRM,
HTTP probe, …) means subclassing Transport and writing the four methods
below. Handlers/monitors depend only on this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class ServiceState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"
    START_PENDING = "START_PENDING"
    STOP_PENDING = "STOP_PENDING"
    NOT_INSTALLED = "NOT_INSTALLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    raw: str               # original nssm output, useful for /status verbose


@dataclass(frozen=True)
class SignalFile:
    name: str              # filename only
    size_bytes: int
    mtime_epoch: float     # unix seconds — caller computes age


@dataclass(frozen=True)
class LogFile:
    path: str              # absolute path on the target VPS
    name: str
    size_bytes: int
    mtime_epoch: float


class Transport(ABC):
    """Read-only fleet access. Restart is opt-in (Phase 1+)."""

    @abstractmethod
    async def get_service_status(self, nssm_service: str) -> ServiceStatus: ...

    @abstractmethod
    async def read_log_tail(self, log_path: str, n_lines: int) -> list[str]: ...

    @abstractmethod
    async def read_log_since(self, log_path: str, byte_offset: int) -> tuple[str, int]:
        """Return (new_content, new_offset). On rotation (file shrank), the
        transport reads from the start and returns the full content."""

    @abstractmethod
    async def list_log_files(self, log_dir: str) -> list[LogFile]: ...

    @abstractmethod
    async def list_signal_files(self, signal_dir: str) -> list[SignalFile]: ...

    @abstractmethod
    async def read_signal_json(self, signal_dir: str, name: str) -> dict | None:
        """Read and parse a signal JSON file. Returns None if missing/invalid."""

    async def restart_service(self, nssm_service: str) -> str:
        # Phase 0 = read-only. Override in a write-capable transport later.
        raise NotImplementedError("restart is disabled in read-only Phase 0")
