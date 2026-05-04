"""Transport layer — abstracts how the bot reaches each VPS.

Phase 0 ships LocalTransport only (bot runs on the same VPS as the agents).
Phase 1 will add SSHTransport with the same interface so handlers/monitors
do not change. Pick the transport per-Vps based on `vps.transport`.
"""

from .base import LogFile, ServiceState, ServiceStatus, SignalFile, Transport
from .local import LocalTransport

__all__ = [
    "Transport",
    "ServiceState",
    "ServiceStatus",
    "SignalFile",
    "LogFile",
    "LocalTransport",
    "get_transport",
]


def get_transport(transport_name: str) -> Transport:
    """Pick a Transport implementation by name (matches Vps.transport)."""
    if transport_name == "local":
        return LocalTransport()
    if transport_name == "ssh":
        raise NotImplementedError("ssh transport arrives in Phase 1")
    raise ValueError(f"unknown transport: {transport_name!r}")
