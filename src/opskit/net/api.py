"""Public API for network connectivity diagnostics: :func:`check` and :func:`probe`.

Orchestrates the tcp/udp primitives over the typed model. The raise/return split (see
data-model.md): ``check`` returns only the OPEN verdict and raises typed errors for every
other single-shot outcome; ``probe`` captures per-attempt failures as data and raises only
pre-flight. Nothing here prints or exits (Art. VII).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from opskit.core.errors import UsageError
from opskit.net import tcp
from opskit.net.errors import (
    ConnectRefused,
    ConnectTimeout,
    NetError,
    ProxyAuthRequired,
    ProxyConnectRefused,
    ProxyConnectTimeout,
    ProxyGatewayError,
    ProxyProtocolError,
    ProxyResolutionError,
    ProxyTunnelDenied,
    ResolutionError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.models import (
    CheckResult,
    NetTarget,
    ProbeAttempt,
    ProbeResult,
    Protocol,
    ProxySpec,
    Route,
    Verdict,
    parse_proxy,
    parse_target,
)
from opskit.net.proxy import connect_via_proxy, resolve_proxy
from opskit.net.udp import udp_probe

_ERROR_VERDICTS: tuple[tuple[type[NetError], Verdict], ...] = (
    # Proxy-hop outcomes first (the subtree is disjoint from the direct types).
    (ProxyAuthRequired, Verdict.AUTH_REQUIRED),
    (ProxyTunnelDenied, Verdict.TUNNEL_DENIED),
    (ProxyGatewayError, Verdict.GATEWAY_FAILED),
    (ProxyProtocolError, Verdict.NOT_A_PROXY),
    (ProxyResolutionError, Verdict.RESOLVE_FAILED),
    (ProxyConnectRefused, Verdict.REFUSED),
    (ProxyConnectTimeout, Verdict.TIMEOUT),
    (ConnectRefused, Verdict.REFUSED),
    (ConnectTimeout, Verdict.TIMEOUT),
    (UdpClosed, Verdict.CLOSED),
    (UdpInconclusive, Verdict.INCONCLUSIVE),
    (ResolutionError, Verdict.RESOLVE_FAILED),
)


def verdict_for(error: NetError) -> Verdict:
    """Map a typed check failure onto its :class:`Verdict` class."""
    for error_type, verdict in _ERROR_VERDICTS:
        if isinstance(error, error_type):
            return verdict
    return Verdict.TIMEOUT  # generic NetError: nothing answered


def _resolve_proxy_arg(
    proxy: ProxySpec | str | None, protocol: Protocol
) -> ProxySpec | None:
    """Normalize the ``proxy=`` argument and enforce the UDP guard (FR-007)."""
    spec = parse_proxy(proxy) if isinstance(proxy, str) else proxy
    if spec is not None and protocol is Protocol.UDP:
        raise UsageError(
            "cannot check a UDP port through an HTTP proxy",
            hint="HTTP CONNECT tunnels are TCP-only; drop UDP mode or check "
            "directly (--direct)",
        )
    return spec


def check(
    target: str,
    *,
    port: int | None = None,
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,
    timeout: float = 5.0,
    retries: int = 2,
    proxy: ProxySpec | str | None = None,
) -> CheckResult:
    """Check whether one TCP/UDP port is reachable (single-shot verdict).

    TCP: connects and closes the socket immediately — no application data is ever
    sent (FR-006). UDP: sends one zero-byte probe datagram and reports open **only**
    on a received reply (FR-008, SC-007). With ``proxy=``, an HTTP CONNECT tunnel is
    established through the proxy instead of a direct connection; the tunnel is
    closed immediately after the verdict and nothing is ever sent through it
    (FR-008 of 005-net-proxy-checks). The proxy is always an explicit argument —
    this layer never reads environment variables (FR-005).

    Args:
        target: ``host:port``, ``[v6]:port``, or a bare host combined with ``port``.
        port: Port for targets given without shorthand (must agree with shorthand).
        protocol: :attr:`Protocol.TCP` (default) or :attr:`Protocol.UDP`.
        family: Restrict addresses to ``"ipv4"``/``"ipv6"``; ``None`` = both.
            With a proxy this constrains the proxy hop (the connection made here).
        timeout: Per-attempt (per-stage, when proxied) timeout, seconds.
        retries: Retries on timeout/silence (a definitive answer is not retried).
        proxy: HTTP proxy to tunnel through — a :class:`ProxySpec` or a spec string
            (parsed via :func:`opskit.net.models.parse_proxy`); ``None`` = direct.

    Returns:
        The OPEN :class:`CheckResult` (address, family, timing, route). When
        proxied, ``address``/``family`` describe the proxy hop and ``time_ms`` is
        tunnel establishment time.

    Raises:
        UsageError: For a bad/missing port, target, or proxy spec — or UDP mode
            combined with a proxy — before any network I/O.
        ResolutionError: If the name does not resolve (or not in the requested family).
        ConnectRefused: TCP — the target actively refused.
        ConnectTimeout: TCP — nothing answered (possibly filtered).
        UdpClosed: UDP — the host signaled port unreachable.
        UdpInconclusive: UDP — no response; the port is open or filtered.
        ProxyError: Any proxied-check failure — see
            :func:`opskit.net.proxy.connect_via_proxy` for the subtree.
    """
    proxy_spec = _resolve_proxy_arg(proxy, protocol)
    parsed = parse_target(target, port=port, protocol=protocol, family=family)
    return _check_parsed(parsed, timeout=timeout, retries=retries, proxy=proxy_spec)


def _check_parsed(
    parsed: NetTarget,
    *,
    timeout: float,
    retries: int,
    proxy: ProxySpec | None = None,
) -> CheckResult:
    """Run one classification attempt for an already-parsed target."""
    route = Route.direct()
    if proxy is not None:
        sock, tunnel = connect_via_proxy(
            proxy,
            parsed.host,
            parsed.port,
            timeout=timeout,
            retries=retries,
            family=parsed.family,
        )
        sock.close()  # verdict only: nothing is ever sent through the tunnel
        address, family, time_ms = (
            tunnel.proxy_address,
            tunnel.family,
            tunnel.tunnel_ms,
        )
        route = Route.via_proxy(proxy, source="explicit")
    elif parsed.protocol is Protocol.UDP:
        reply = udp_probe(
            parsed.host,
            parsed.port,
            timeout=timeout,
            retries=retries,
            family=parsed.family,
        )
        address, family, time_ms = reply.address, reply.family, reply.time_ms
    else:
        sock, connection = tcp.connect(
            parsed.host,
            parsed.port,
            timeout=timeout,
            retries=retries,
            family=parsed.family,
        )
        sock.close()  # verdict only: no application data is ever sent (FR-006)
        address, family, time_ms = (
            connection.address,
            connection.family,
            connection.connect_ms,
        )
    return CheckResult(
        target=parsed,
        verdict=Verdict.OPEN,
        address=address,
        family=family,
        port=parsed.port,
        time_ms=time_ms,
        route=route,
    )


def probe(
    target: str,
    *,
    port: int | None = None,
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,
    count: int = 4,
    interval: float = 1.0,
    timeout: float = 5.0,
    retries: int = 0,
    proxy: ProxySpec | str | None = None,
    on_attempt: Callable[[ProbeAttempt], None] | None = None,
) -> ProbeResult:
    """Measure latency/stability with repeated probes (ping-style, FR-009).

    Every attempt runs regardless of prior failures — failures are data
    (:class:`ProbeAttempt`), never raised. Statistics (min/avg/max over answered
    attempts; the UDP replies/closed/silent split) are computed here, never by
    callers. A ``KeyboardInterrupt`` during the run stops it and the result is
    finalized over the completed attempts (R9); callers can detect the interruption
    by comparing ``completed`` to ``requested``. With ``proxy=``, every attempt
    establishes (and immediately closes) a fresh CONNECT tunnel; timings are tunnel
    establishment times and the pre-flight resolution check targets the **proxy**
    (the proxy resolves the target).

    Args:
        target: ``host:port``, ``[v6]:port``, or a bare host combined with ``port``.
        port: Port for targets given without shorthand (must agree with shorthand).
        protocol: :attr:`Protocol.TCP` (default) or :attr:`Protocol.UDP`.
        family: Restrict addresses to ``"ipv4"``/``"ipv6"``; ``None`` = both.
        count: Number of attempts.
        interval: Seconds between attempt starts.
        timeout: Per-attempt (per-stage, when proxied) timeout, seconds.
        retries: Retries within one attempt (the count is the retry story).
        proxy: HTTP proxy to tunnel through — a :class:`ProxySpec` or spec string;
            ``None`` = direct. Always explicit; this layer never reads env (FR-005).
        on_attempt: Optional streaming hook fired after each attempt completes.

    Returns:
        The :class:`ProbeResult` aggregate (with its :class:`Route`).

    Raises:
        UsageError: For a bad target/port/count/interval/proxy — or UDP mode with
            a proxy — before any network I/O.
        ResolutionError: If the target name does not resolve, before attempt 1.
        ProxyResolutionError: If the proxy name does not resolve, before attempt 1.
    """
    proxy_spec = _resolve_proxy_arg(proxy, protocol)
    parsed = parse_target(target, port=port, protocol=protocol, family=family)
    if count < 1:
        raise UsageError(f"count must be at least 1: {count}")
    if interval < 0:
        raise UsageError(f"interval must not be negative: {interval}")
    # Pre-flight: an unresolvable name fails the run before the first attempt (R9).
    # Proxied runs resolve the proxy — the target is resolved by the proxy.
    if proxy_spec is not None:
        resolve_proxy(proxy_spec, timeout=timeout, family=parsed.family)
    else:
        tcp.resolve(parsed.host, parsed.port, timeout=timeout, family=parsed.family)

    attempts: list[ProbeAttempt] = []
    run_start = time.perf_counter()
    try:
        for index in range(1, count + 1):
            attempt_start = time.perf_counter()
            attempt = _run_attempt(
                parsed, index, timeout=timeout, retries=retries, proxy=proxy_spec
            )
            attempts.append(attempt)
            if on_attempt is not None:
                on_attempt(attempt)
            if index < count:
                remaining = interval - (time.perf_counter() - attempt_start)
                if remaining > 0:
                    time.sleep(remaining)
    except KeyboardInterrupt:
        pass  # finalize over completed attempts; the summary is the answer (R9)
    elapsed_ms = (time.perf_counter() - run_start) * 1000.0
    route = (
        Route.via_proxy(proxy_spec, source="explicit")
        if proxy_spec is not None
        else Route.direct()
    )
    return _finalize_probe(parsed, tuple(attempts), count, elapsed_ms, route)


def _run_attempt(
    parsed: NetTarget,
    index: int,
    *,
    timeout: float,
    retries: int,
    proxy: ProxySpec | None = None,
) -> ProbeAttempt:
    """Run one probe attempt, capturing any failure as attempt data (FR-009)."""
    try:
        if proxy is not None:
            result = _check_parsed(
                parsed, timeout=timeout, retries=retries, proxy=proxy
            )
        else:
            result = _check_parsed(parsed, timeout=timeout, retries=retries)
    except NetError as exc:
        return ProbeAttempt(index=index, verdict=verdict_for(exc), error=exc.message)
    return ProbeAttempt(
        index=index,
        verdict=result.verdict,
        address=result.address,
        family=result.family,
        time_ms=result.time_ms,
    )


def _finalize_probe(
    parsed: NetTarget,
    attempts: tuple[ProbeAttempt, ...],
    requested: int,
    elapsed_ms: float,
    route: Route,
) -> ProbeResult:
    """Compute the run statistics (data-model derivation rules)."""
    answered = [a.time_ms for a in attempts if a.time_ms is not None]
    successes = sum(1 for a in attempts if a.verdict is Verdict.OPEN)
    is_udp = parsed.protocol is Protocol.UDP
    return ProbeResult(
        target=parsed,
        attempts=attempts,
        requested=requested,
        completed=len(attempts),
        successes=successes,
        failures=len(attempts) - successes,
        replies=successes if is_udp else 0,
        closed_signals=(
            sum(1 for a in attempts if a.verdict is Verdict.CLOSED) if is_udp else 0
        ),
        silent=(
            sum(1 for a in attempts if a.verdict is Verdict.INCONCLUSIVE)
            if is_udp
            else 0
        ),
        min_ms=min(answered) if answered else None,
        avg_ms=(sum(answered) / len(answered)) if answered else None,
        max_ms=max(answered) if answered else None,
        elapsed_ms=elapsed_ms,
        route=route,
    )
