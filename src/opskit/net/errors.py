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


class ProxyError(NetError):
    """Base class for failures attributable to an HTTP proxy hop.

    ``except ProxyError`` is the documented "was it the proxy?" discriminator;
    :class:`ProxyGatewayError` is the deliberate target-side member of the subtree
    (the proxy answered — the target is the problem), separable by type or exit code.
    """

    code = "proxy_error"


class ProxyResolutionError(ProxyError):
    """The proxy's own host name could not be resolved locally."""

    code = "proxy_resolve_failed"
    exit_code = ExitCode.NXDOMAIN  # same outcome class as any resolution failure


class ProxyConnectRefused(ProxyError):
    """Connecting to the proxy itself was refused (or it was unreachable)."""

    code = "proxy_connect_refused"
    exit_code = ExitCode.CONNECT_FAILED


class ProxyConnectTimeout(ProxyError):
    """The proxy stayed silent — TCP connect or the CONNECT response timed out."""

    code = "proxy_connect_timeout"
    exit_code = ExitCode.TIMEOUT


class ProxyAuthRequired(ProxyError):
    """The proxy demanded credentials that were absent, wrong, or unsupported (407)."""

    code = "proxy_auth_required"
    exit_code = ExitCode.AUTH_FAILED


class ProxyTunnelDenied(ProxyError):
    """The proxy refused to open the tunnel to this destination (policy denial)."""

    code = "proxy_tunnel_denied"
    exit_code = ExitCode.TUNNEL_DENIED


class ProxyGatewayError(ProxyError):
    """The proxy accepted the request but could not reach the target (5xx)."""

    code = "proxy_gateway_failed"
    exit_code = ExitCode.PROXY_GATEWAY


class ProxyProtocolError(ProxyError):
    """The nominated endpoint answered, but not like an HTTP proxy."""

    code = "not_a_proxy"
    exit_code = ExitCode.NOT_A_PROXY


class PortInUse(NetError):
    """The listener could not bind: the port is already in use."""

    code = "port_in_use"
    exit_code = ExitCode.PORT_IN_USE


class BindPermissionDenied(NetError):
    """The listener could not bind: the OS denied permission (privileged port)."""

    code = "bind_permission_denied"
    exit_code = ExitCode.BIND_PERMISSION
