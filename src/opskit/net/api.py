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
    Verdict,
    parse_target,
)
from opskit.net.udp import udp_probe

_ERROR_VERDICTS: tuple[tuple[type[NetError], Verdict], ...] = (
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


def check(
    target: str,
    *,
    port: int | None = None,
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,
    timeout: float = 5.0,
    retries: int = 2,
) -> CheckResult:
    """Check whether one TCP/UDP port is reachable (single-shot verdict).

    TCP: connects and closes the socket immediately — no application data is ever
    sent (FR-006). UDP: sends one zero-byte probe datagram and reports open **only**
    on a received reply (FR-008, SC-007).

    Args:
        target: ``host:port``, ``[v6]:port``, or a bare host combined with ``port``.
        port: Port for targets given without shorthand (must agree with shorthand).
        protocol: :attr:`Protocol.TCP` (default) or :attr:`Protocol.UDP`.
        family: Restrict addresses to ``"ipv4"``/``"ipv6"``; ``None`` = both.
        timeout: Per-attempt timeout, seconds.
        retries: Retries on timeout/silence (a refusal/unreachable is definitive).

    Returns:
        The OPEN :class:`CheckResult` (address, family, timing).

    Raises:
        UsageError: For a bad/missing port or target before any network I/O.
        ResolutionError: If the name does not resolve (or not in the requested family).
        ConnectRefused: TCP — the target actively refused.
        ConnectTimeout: TCP — nothing answered (possibly filtered).
        UdpClosed: UDP — the host signaled port unreachable.
        UdpInconclusive: UDP — no response; the port is open or filtered.
    """
    parsed = parse_target(target, port=port, protocol=protocol, family=family)
    return _check_parsed(parsed, timeout=timeout, retries=retries)


def _check_parsed(parsed: NetTarget, *, timeout: float, retries: int) -> CheckResult:
    """Run one classification attempt for an already-parsed target."""
    if parsed.protocol is Protocol.UDP:
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
    on_attempt: Callable[[ProbeAttempt], None] | None = None,
) -> ProbeResult:
    """Measure latency/stability with repeated probes (ping-style, FR-009).

    Every attempt runs regardless of prior failures — failures are data
    (:class:`ProbeAttempt`), never raised. Statistics (min/avg/max over answered
    attempts; the UDP replies/closed/silent split) are computed here, never by
    callers. A ``KeyboardInterrupt`` during the run stops it and the result is
    finalized over the completed attempts (R9); callers can detect the interruption
    by comparing ``completed`` to ``requested``.

    Args:
        target: ``host:port``, ``[v6]:port``, or a bare host combined with ``port``.
        port: Port for targets given without shorthand (must agree with shorthand).
        protocol: :attr:`Protocol.TCP` (default) or :attr:`Protocol.UDP`.
        family: Restrict addresses to ``"ipv4"``/``"ipv6"``; ``None`` = both.
        count: Number of attempts.
        interval: Seconds between attempt starts.
        timeout: Per-attempt timeout, seconds.
        retries: Retries within one attempt (the count is the retry story).
        on_attempt: Optional streaming hook fired after each attempt completes.

    Returns:
        The :class:`ProbeResult` aggregate.

    Raises:
        UsageError: For a bad target/port/count/interval before any network I/O.
        ResolutionError: If the name does not resolve, before the first attempt.
    """
    parsed = parse_target(target, port=port, protocol=protocol, family=family)
    if count < 1:
        raise UsageError(f"count must be at least 1: {count}")
    if interval < 0:
        raise UsageError(f"interval must not be negative: {interval}")
    # Pre-flight: an unresolvable name fails the run before the first attempt (R9).
    tcp.resolve(parsed.host, parsed.port, timeout=timeout, family=parsed.family)

    attempts: list[ProbeAttempt] = []
    run_start = time.perf_counter()
    try:
        for index in range(1, count + 1):
            attempt_start = time.perf_counter()
            attempt = _run_attempt(parsed, index, timeout=timeout, retries=retries)
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
    return _finalize_probe(parsed, tuple(attempts), count, elapsed_ms)


def _run_attempt(
    parsed: NetTarget, index: int, *, timeout: float, retries: int
) -> ProbeAttempt:
    """Run one probe attempt, capturing any failure as attempt data (FR-009)."""
    try:
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
    )
