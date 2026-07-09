"""Network-layer exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

These cover the resolve/connect layers shared by every category that opens a socket
(tls today; the future net category). Each type owns its exit code (constitution Art. VII).
"""

from __future__ import annotations

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class NetError(OpskitError):
    """Base class for name-resolution and connection failures."""

    code = "net_error"


class ResolutionError(NetError):
    """The host name could not be resolved to any address."""

    code = "resolve_failed"
    exit_code = ExitCode.NXDOMAIN  # same outcome class as DNS "name does not exist"


class ConnectRefused(NetError):
    """The target actively refused the connection (or was unreachable)."""

    code = "connect_refused"
    exit_code = ExitCode.CONNECT_FAILED


class ConnectTimeout(NetError):
    """No connection could be established before the timeout (possibly filtered)."""

    code = "connect_timeout"
    exit_code = ExitCode.TIMEOUT


class UdpClosed(NetError):
    """The host signaled ICMP port unreachable — the UDP port is closed."""

    code = "udp_closed"
    exit_code = ExitCode.CONNECT_FAILED  # same outcome class as a TCP refusal


class UdpInconclusive(NetError):
    """No reply and no unreachable signal — the UDP port is open or filtered."""

    code = "udp_inconclusive"
    exit_code = ExitCode.TIMEOUT  # no-response class, like a TCP filtered timeout


class PortInUse(NetError):
    """The listener could not bind: the port is already in use."""

    code = "port_in_use"
    exit_code = ExitCode.PORT_IN_USE


class BindPermissionDenied(NetError):
    """The listener could not bind: the OS denied permission (privileged port)."""

    code = "bind_permission_denied"
    exit_code = ExitCode.BIND_PERMISSION
