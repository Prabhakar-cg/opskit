"""HTTP CONNECT tunnel primitive: reach a target through a user-nominated proxy.

The proxied analog of :func:`opskit.net.tcp.connect` and the seam future categories
(e.g. ``tls`` via proxy) build on. Pure stdlib: the proxy hop reuses
:func:`tcp.connect` (which normalizes refused/timeout/resolution), then a minimal
CONNECT exchange is spoken by hand so every proxy answer classifies onto the
:class:`opskit.net.errors.ProxyError` hierarchy (research R1/R4). Read-only by
construction: nothing is ever sent beyond the CONNECT request itself, and callers
close the tunnel immediately after their verdict. Nothing here prints or exits
(Art. VII); every message names the proxy via its redacted ``display`` only.
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from typing import Any

from opskit.core.errors import UsageError
from opskit.net import tcp
from opskit.net.errors import (
    ConnectRefused,
    ConnectTimeout,
    ProxyAuthRequired,
    ProxyConnectRefused,
    ProxyConnectTimeout,
    ProxyError,
    ProxyGatewayError,
    ProxyProtocolError,
    ProxyResolutionError,
    ProxyTunnelDenied,
    ResolutionError,
)
from opskit.net.models import ProxySpec

_MAX_RESPONSE_BYTES = 65536
_STATUS_LINE = re.compile(r"^HTTP/\d+(?:\.\d+)?\s+(\d{3})\s*(.*)$")
_HTTP_OK = 200
_HTTP_REDIRECT = 300
_HTTP_SERVER_ERROR = 500
_HTTP_PROXY_AUTH_REQUIRED = 407
_HTTP_GATEWAY_TIMEOUT = 504


@dataclass(frozen=True)
class TunnelConnection:
    """Facts about an established CONNECT tunnel (returned alongside the socket)."""

    proxy_address: str  # proxy IP actually connected to
    family: str  # "ipv4" | "ipv6" — the family of the proxy hop
    port: int  # target port requested in the CONNECT
    tunnel_ms: float  # proxy TCP connect + CONNECT exchange, wall-clock

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this tunnel."""
        return {
            "proxy_address": self.proxy_address,
            "family": self.family,
            "port": self.port,
            "tunnel_ms": round(self.tunnel_ms, 3),
        }


def _ascii_host(host: str) -> str:
    """Return ``host`` as ASCII for the request line (IDNA-encoding when needed)."""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        try:
            return host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UsageError(
                f"cannot encode host for the proxy request: {host}"
            ) from exc
    return host


def _connect_target(host: str, port: int) -> str:
    """Format the CONNECT authority (bracketing IPv6 literals)."""
    ascii_host = _ascii_host(host)
    return f"[{ascii_host}]:{port}" if ":" in ascii_host else f"{ascii_host}:{port}"


def _build_request(proxy: ProxySpec, host: str, port: int) -> bytes:
    """Build the CONNECT request head — the only bytes this feature ever sends."""
    target = _connect_target(host, port)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    authorization = proxy.authorization
    if authorization is not None:
        lines.append(f"Proxy-Authorization: {authorization}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def _read_response_head(
    sock: socket.socket, proxy: ProxySpec, timeout: float
) -> tuple[str, bool]:
    """Read the response head under the per-stage timeout.

    Returns:
        ``(head, complete)`` — ``complete`` is False when the connection closed
        before the blank-line terminator (a truncated head is still classified,
        e.g. a non-HTTP banner, but can never count as tunnel success).

    Raises:
        socket.timeout: When the proxy stays silent (caller maps to timeout class).
        ProxyProtocolError: When the connection closes without any response or the
            head exceeds a sane size.
    """
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            if data:
                # Closed mid-head: classify whatever arrived (garbage banner or a
                # truncated status) — the caller must treat it as incomplete.
                return data.decode("latin-1"), False
            raise ProxyProtocolError(
                f"proxy {proxy.display} closed the connection without a response",
                hint="check the proxy address and port — the endpoint does not "
                "behave like an HTTP proxy",
            )
        data += chunk
        if len(data) > _MAX_RESPONSE_BYTES:
            raise ProxyProtocolError(
                f"proxy {proxy.display} sent an oversized response head",
                hint="the endpoint does not behave like an HTTP proxy",
            )
    return data.split(b"\r\n\r\n", 1)[0].decode("latin-1"), True


def _parse_head(head: str, proxy: ProxySpec) -> tuple[int, str, list[str]]:
    """Parse (status_code, reason, Proxy-Authenticate schemes) from a response head.

    Raises:
        ProxyProtocolError: When the first line is not an HTTP status line.
    """
    lines = head.splitlines()
    match = _STATUS_LINE.match(lines[0]) if lines else None
    if match is None:
        raise ProxyProtocolError(
            f"{proxy.display} does not behave like an HTTP proxy",
            hint="the endpoint answered, but not with an HTTP response; "
            "check the proxy address and port",
        )
    schemes: list[str] = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "proxy-authenticate":
            schemes.append(value.strip())
    return int(match.group(1)), match.group(2).strip(), schemes


def _auth_error(proxy: ProxySpec, schemes: list[str], target: str) -> ProxyAuthRequired:
    """Build the 407 outcome: absent vs rejected vs unsupported-scheme (FR-015)."""
    scheme_names = [scheme.split()[0] for scheme in schemes if scheme.strip()]
    basic_offered = any(name.lower() == "basic" for name in scheme_names)
    if scheme_names and not basic_offered:
        return ProxyAuthRequired(
            f"proxy {proxy.display} requires an unsupported authentication "
            f"method ({', '.join(scheme_names)}) for {target}",
            hint="only Basic authentication is supported (http://user:pass@proxy:port)",
        )
    if proxy.authorization is None:
        return ProxyAuthRequired(
            f"proxy {proxy.display} requires authentication for {target}",
            hint="supply credentials in the proxy spec (http://user:pass@proxy:port)",
        )
    return ProxyAuthRequired(
        f"proxy {proxy.display} rejected the supplied credentials for {target}",
        hint="check the proxy username and password",
    )


def _classify_status(
    proxy: ProxySpec, code: int, reason: str, schemes: list[str], target: str
) -> ProxyError:
    """Map a non-2xx CONNECT status onto the typed hierarchy (research R4 table)."""
    detail = f"{code} {reason}".strip()
    if code == _HTTP_PROXY_AUTH_REQUIRED:
        return _auth_error(proxy, schemes, target)
    if code < _HTTP_SERVER_ERROR:
        return ProxyTunnelDenied(
            f"proxy {proxy.display} denied the tunnel to {target}: {detail}",
            hint="the destination or port may not be allowed by proxy policy",
        )
    if code == _HTTP_GATEWAY_TIMEOUT:
        return ProxyGatewayError(
            f"target {target} did not answer the proxy: {detail} (via {proxy.display})",
            hint="the proxy hop is healthy; the target may be down or filtered "
            "from the proxy",
        )
    return ProxyGatewayError(
        f"target {target} is unreachable from proxy {proxy.display}: {detail}",
        hint="the proxy hop is healthy; the target may be down, filtered, or "
        "unresolvable at the proxy",
    )


def _resolution_error(proxy: ProxySpec, exc: ResolutionError) -> ProxyResolutionError:
    """Re-attribute a resolution failure to the proxy hop (shared wording)."""
    return ProxyResolutionError(
        f"cannot resolve proxy {proxy.display}: {exc.message}",
        hint="check the proxy name, or diagnose with: opskit dns lookup " + proxy.host,
    )


def resolve_proxy(
    proxy: ProxySpec, *, timeout: float = 5.0, family: str | None = None
) -> list[tcp.AddressCandidate]:
    """Resolve the proxy's own name (the proxied pre-flight check).

    On a proxied run the target is resolved by the proxy, so the only name this
    tool must resolve locally is the proxy's.

    Raises:
        ProxyResolutionError: If the proxy name does not resolve (or not in the
            requested family).
    """
    try:
        return tcp.resolve(proxy.host, proxy.port, timeout=timeout, family=family)
    except ResolutionError as exc:
        raise _resolution_error(proxy, exc) from exc


def _connect_proxy_hop(
    proxy: ProxySpec, *, timeout: float, family: str | None
) -> tuple[socket.socket, tcp.TcpConnection]:
    """Open the TCP connection to the proxy, re-attributing failures to the hop."""
    try:
        return tcp.connect(
            proxy.host, proxy.port, timeout=timeout, retries=0, family=family
        )
    except ResolutionError as exc:
        raise _resolution_error(proxy, exc) from exc
    except ConnectRefused as exc:
        raise ProxyConnectRefused(
            f"cannot connect to proxy {proxy.display}: {exc.message}",
            hint="check the proxy address and port; the proxy itself is "
            "unreachable — the target was never tried",
        ) from exc
    except ConnectTimeout as exc:
        raise ProxyConnectTimeout(
            f"no response from proxy {proxy.display} within {timeout}s",
            hint="the proxy may be down or filtered; verify the proxy address "
            "and your network path to it",
        ) from exc


def _attempt(
    proxy: ProxySpec,
    host: str,
    port: int,
    *,
    timeout: float,
    family: str | None,
) -> tuple[socket.socket, tcp.TcpConnection]:
    """One full tunnel attempt: proxy TCP connect + CONNECT exchange."""
    # Validate/encode the target and build the request BEFORE opening the proxy
    # socket: a UsageError here must never leak an open socket.
    target = _connect_target(host, port)
    request = _build_request(proxy, host, port)
    sock, hop = _connect_proxy_hop(proxy, timeout=timeout, family=family)
    # The socket survives only the success return; every raise path closes it.
    try:
        sock.sendall(request)
        head, complete = _read_response_head(sock, proxy, timeout)
        code, reason, schemes = _parse_head(head, proxy)
        if _HTTP_OK <= code < _HTTP_REDIRECT:
            if not complete:
                # A 2xx whose head never completed and whose connection is already
                # closed cannot be a usable tunnel — never report OPEN for it.
                raise ProxyProtocolError(
                    f"proxy {proxy.display} closed the connection before "
                    f"completing the tunnel response for {target}",
                    hint="the endpoint does not behave like an HTTP proxy",
                )
            return sock, hop
        raise _classify_status(proxy, code, reason, schemes, target)
    except socket.timeout as exc:
        sock.close()
        raise ProxyConnectTimeout(
            f"proxy {proxy.display} accepted the connection but did not answer "
            f"the tunnel request for {target} within {timeout}s",
            hint="the proxy may be overloaded or not a CONNECT proxy; verify "
            "the proxy address and port",
        ) from exc
    except ProxyError:
        sock.close()
        raise
    except OSError as exc:
        sock.close()
        raise ProxyProtocolError(
            f"proxy {proxy.display} dropped the connection during the tunnel "
            f"request: {exc}",
            hint="the endpoint does not behave like an HTTP proxy",
        ) from exc


def connect_via_proxy(
    proxy: ProxySpec,
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    family: str | None = None,
) -> tuple[socket.socket, TunnelConnection]:
    """Establish an HTTP CONNECT tunnel to ``host:port`` through ``proxy``.

    The caller owns (and must close) the returned tunnel socket. Only silence is
    retried (proxy connect timeout / no CONNECT response); every answered outcome
    — refusal, 407, 4xx, 5xx, non-HTTP — is definitive and raised immediately
    (FR-011). The per-attempt ``timeout`` applies per stage: once to the proxy TCP
    connect and once to the CONNECT exchange (research R8).

    Args:
        proxy: The parsed proxy specification (see :func:`opskit.net.models.parse_proxy`).
        host: Target hostname or IP literal (resolved by the proxy, not locally).
        port: Target port, carried in the CONNECT request.
        timeout: Per-stage timeout, seconds.
        retries: Retries on silence only.
        family: Restrict the **proxy hop** to one address family.

    Returns:
        The open tunnel socket and a :class:`TunnelConnection` report.

    Raises:
        ProxyResolutionError: The proxy's own name did not resolve locally.
        ProxyConnectRefused: The proxy refused the TCP connection.
        ProxyConnectTimeout: The proxy stayed silent, after all retries.
        ProxyAuthRequired: 407 — credentials absent, rejected, or unsupported.
        ProxyTunnelDenied: 4xx policy denial.
        ProxyGatewayError: 5xx — the proxy could not reach the target.
        ProxyProtocolError: The endpoint did not answer like an HTTP proxy.
    """
    start = time.perf_counter()
    timeout_exc: ProxyConnectTimeout | None = None
    for _ in range(retries + 1):
        try:
            sock, hop = _attempt(proxy, host, port, timeout=timeout, family=family)
        except ProxyConnectTimeout as exc:
            timeout_exc = exc  # silence: retry (R8); every other outcome raised above
            continue
        tunnel_ms = (time.perf_counter() - start) * 1000.0
        return sock, TunnelConnection(
            proxy_address=hop.address,
            family=hop.family,
            port=port,
            tunnel_ms=tunnel_ms,
        )
    if timeout_exc is None:  # only reachable with retries < 0
        raise ProxyConnectTimeout(
            f"no response from proxy {proxy.display} within {timeout}s",
            hint="the proxy may be down or filtered",
        )
    raise timeout_exc
