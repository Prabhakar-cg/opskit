"""Network connectivity diagnostics — checks, probes, and a temporary listener.

The typed API the ``opskit net`` CLI is a thin client of (constitution Art. VII):
:func:`check` (single-shot TCP/UDP port verdict), :func:`probe` (repeated ping-style
probes with statistics), and :class:`Listener` (metadata-only inbound reporting), plus
the ``resolve``/``connect`` primitives shared with other socket-using categories.
Public surface is SemVer-governed. Failures raise; nothing here prints or exits the
process.
"""

from __future__ import annotations

from opskit.net.api import check, probe
from opskit.net.errors import (
    BindPermissionDenied,
    ConnectRefused,
    ConnectTimeout,
    NetError,
    PortInUse,
    ResolutionError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.listener import Listener
from opskit.net.models import (
    CheckResult,
    InboundEvent,
    ListenerSession,
    NetTarget,
    ProbeAttempt,
    ProbeResult,
    Protocol,
    StopReason,
    Verdict,
    parse_target,
)
from opskit.net.tcp import AddressCandidate, TcpConnection, connect, resolve

__all__ = [
    "AddressCandidate",
    "BindPermissionDenied",
    "CheckResult",
    "ConnectRefused",
    "ConnectTimeout",
    "InboundEvent",
    "Listener",
    "ListenerSession",
    "NetError",
    "NetTarget",
    "PortInUse",
    "ProbeAttempt",
    "ProbeResult",
    "Protocol",
    "ResolutionError",
    "StopReason",
    "TcpConnection",
    "UdpClosed",
    "UdpInconclusive",
    "Verdict",
    "check",
    "connect",
    "parse_target",
    "probe",
    "resolve",
]
