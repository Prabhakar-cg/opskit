"""Public TLS verification API — the CLI is a thin client over this module.

:func:`check` orchestrates the layers (resolve → connect → handshake → validate). Failures
that preclude a report **raise** (usage, resolve, connect, handshake); completed handshakes
**return** a :class:`TlsCheckResult` whose findings carry certificate conditions, so a bad
certificate's details stay inspectable (FR-006). Nothing here prints or calls ``sys.exit``.
"""

from __future__ import annotations

import time
from pathlib import Path

from opskit.core.errors import UsageError
from opskit.net import connect as net_connect
from opskit.tls.errors import CertificateExpiring, CertificateInvalid
from opskit.tls.handshake import perform_handshake
from opskit.tls.inspect import build_findings, match_hostname, parse_certificate
from opskit.tls.models import (
    FindingCode,
    TlsCheckResult,
    TlsOutcome,
    parse_target,
)

_INVALID_FINDINGS = frozenset(
    {
        FindingCode.EXPIRED,
        FindingCode.NOT_YET_VALID,
        FindingCode.NAME_MISMATCH,
        FindingCode.SELF_SIGNED,
        FindingCode.UNTRUSTED_CHAIN,
        FindingCode.INCOMPLETE_CHAIN,
        FindingCode.NO_SANS,
    }
)


def _validate_controls(timeout: float, retries: int, warn_days: int) -> None:
    if timeout <= 0:
        raise UsageError("timeout must be positive")
    if retries < 0:
        raise UsageError("retries must be >= 0")
    if warn_days < 0:
        raise UsageError("warn-days must be >= 0")


def check(
    target: str,
    *,
    port: int | None = None,
    server_name: str | None = None,
    ca_file: str | Path | None = None,
    warn_days: int = 30,
    timeout: float = 5.0,
    retries: int = 2,
    raise_on_invalid: bool = False,
) -> TlsCheckResult:
    """Verify the TLS health of ``target`` (``host``, ``host:port``, IP, ``[v6]:port``).

    Args:
        target: Endpoint to check; the port defaults to 443.
        port: Explicit port (must agree with any ``host:port`` shorthand).
        server_name: SNI override; defaults to the hostname, omitted for IP targets.
        ca_file: PEM bundle replacing the platform trust store (private PKI).
        warn_days: Expiring-soon threshold in days (0 disables the warning class).
        timeout: Per-attempt timeout for connect and handshake, seconds.
        retries: Retries on timeout.
        raise_on_invalid: Raise :class:`CertificateInvalid`/:class:`CertificateExpiring`
            instead of returning a result whose outcome carries those conditions.

    Returns:
        A :class:`TlsCheckResult` with the layered outcome, negotiated protocol/cipher,
        parsed leaf + chain, and validation findings.

    Raises:
        UsageError: Invalid target or controls (before any network I/O).
        ResolutionError: The host does not resolve.
        ConnectRefused: The port refused / was unreachable.
        ConnectTimeout: Connect or handshake timed out (after retries).
        HandshakeError: The TLS handshake failed (e.g. non-TLS service).
        CertificateInvalid: Only with ``raise_on_invalid=True``.
        CertificateExpiring: Only with ``raise_on_invalid=True``.
    """
    parsed = parse_target(target, port=port, server_name=server_name)
    _validate_controls(timeout, retries, warn_days)

    start = time.perf_counter()
    sock, connection = net_connect(
        parsed.host, parsed.port, timeout=timeout, retries=retries
    )
    try:
        outcome = perform_handshake(
            sock,
            server_name=parsed.server_name,
            timeout=timeout,
            ca_file=ca_file,
        )
    finally:
        sock.close()

    chain = tuple(parse_certificate(cert) for cert in outcome.chain)
    leaf_raw = outcome.chain[0] if outcome.chain else None
    leaf = chain[0] if chain else None
    if leaf is None or leaf_raw is None:
        # A handshake without a peer certificate is not a usable TLS endpoint.
        raise CertificateInvalid(
            "server presented no certificate",
            hint="the endpoint may require a protocol opskit does not speak",
        )

    findings = build_findings(
        parsed,
        leaf,
        name_matched=match_hostname(parsed, leaf_raw),
        verify_errors=outcome.verify_errors,
        chain_length=len(chain),
        tls_version=outcome.tls_version,
        warn_days=warn_days,
    )

    codes = {finding.code for finding in findings}
    if codes & _INVALID_FINDINGS:
        verdict = TlsOutcome.CERT_INVALID
    elif FindingCode.EXPIRING_SOON in codes:
        verdict = TlsOutcome.EXPIRING_SOON
    else:
        verdict = TlsOutcome.OK

    result = TlsCheckResult(
        target=parsed,
        outcome=verdict,
        connection=connection,
        tls_version=outcome.tls_version,
        cipher=outcome.cipher,
        leaf=leaf,
        chain=chain,
        findings=findings,
        elapsed_ms=(time.perf_counter() - start) * 1000.0,
    )

    if raise_on_invalid and verdict is TlsOutcome.CERT_INVALID:
        first = next(f for f in findings if f.code in _INVALID_FINDINGS)
        raise CertificateInvalid(first.message, hint=first.hint, findings=findings)
    if raise_on_invalid and verdict is TlsOutcome.EXPIRING_SOON:
        raise CertificateExpiring(
            f"certificate expires in {leaf.days_until_expiry} day(s)",
            hint="schedule renewal",
            days_remaining=leaf.days_until_expiry,
        )
    return result
