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
    ProxyAuthRequired,
    ProxyConnectRefused,
    ProxyConnectTimeout,
    ProxyError,
    ProxyGatewayError,
    ProxyProtocolError,
    ProxyResolutionError,
    ProxyTunnelDenied,
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
    ProxySpec,
    Route,
    StopReason,
    Verdict,
    parse_proxy,
    parse_target,
    proxy_exempt,
)
from opskit.net.proxy import TunnelConnection, connect_via_proxy, resolve_proxy
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
    "ProxyAuthRequired",
    "ProxyConnectRefused",
    "ProxyConnectTimeout",
    "ProxyError",
    "ProxyGatewayError",
    "ProxyProtocolError",
    "ProxyResolutionError",
    "ProxySpec",
    "ProxyTunnelDenied",
    "ResolutionError",
    "Route",
    "StopReason",
    "TcpConnection",
    "TunnelConnection",
    "UdpClosed",
    "UdpInconclusive",
    "Verdict",
    "check",
    "connect",
    "connect_via_proxy",
    "parse_proxy",
    "parse_target",
    "probe",
    "proxy_exempt",
    "resolve",
    "resolve_proxy",
]
