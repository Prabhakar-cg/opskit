"""Typed data model for TLS verification diagnostics.

Frozen stdlib dataclasses (no Pydantic) with ``to_dict()`` for the JSON envelope.
See specs/002-tls-verification/data-model.md.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opskit.core.errors import UsageError

DEFAULT_PORT = 443
_MAX_PORT = 65535


class TlsOutcome(str, Enum):
    """Overall verdict class — the first failing layer wins (spec US3)."""

    OK = "ok"
    EXPIRING_SOON = "expiring_soon"
    RESOLVE_FAILED = "resolve_failed"
    CONNECT_REFUSED = "connect_refused"
    CONNECT_TIMEOUT = "connect_timeout"
    HANDSHAKE_FAILED = "handshake_failed"
    CERT_INVALID = "cert_invalid"
    USAGE_ERROR = "usage_error"


class FindingCode(str, Enum):
    """One distinct validation condition (FR-007/009/010)."""

    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    NAME_MISMATCH = "name_mismatch"
    SELF_SIGNED = "self_signed"
    UNTRUSTED_CHAIN = "untrusted_chain"
    INCOMPLETE_CHAIN = "incomplete_chain"
    NO_SANS = "no_sans"
    EXPIRING_SOON = "expiring_soon"
    LEGACY_PROTOCOL = "legacy_protocol"


@dataclass(frozen=True)
class TlsTarget:
    """What the user asked to check (host, port, effective SNI)."""

    host: str
    port: int
    server_name: str | None  # SNI actually sent; None for IP targets
    is_ip: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this target."""
        return {
            "host": self.host,
            "port": self.port,
            "server_name": self.server_name,
            "is_ip": self.is_ip,
        }


def parse_target(
    raw: str,
    *,
    port: int | None = None,
    server_name: str | None = None,
) -> TlsTarget:
    """Parse ``host``, ``host:port``, IP literals, or ``[v6]:port`` into a TlsTarget.

    Args:
        raw: The user-supplied target string.
        port: Explicit ``--port`` value; must agree with any shorthand port.
        server_name: Explicit SNI override (``--sni``).

    Raises:
        UsageError: For empty targets, invalid ports, or a shorthand/option conflict.
    """
    text = raw.strip()
    if not text:
        raise UsageError("a target host is required")

    host, shorthand_port = _split_host_port(text, raw)
    host = host.strip().rstrip(".")  # normalize trailing-dot hostnames
    if not host:
        raise UsageError(f"invalid target (empty host): {raw}")

    if port is not None and shorthand_port is not None and port != shorthand_port:
        raise UsageError(
            f"conflicting ports: --port {port} vs target '{raw}'",
            hint="give the port once (either --port or host:port)",
        )
    effective_port = shorthand_port if shorthand_port is not None else port
    if effective_port is None:
        effective_port = DEFAULT_PORT
    elif not 1 <= effective_port <= _MAX_PORT:
        raise UsageError(f"port must be between 1 and {_MAX_PORT}: {effective_port}")
    is_ip = _is_ip_literal(host)

    if server_name:
        effective_sni: str | None = server_name
    elif is_ip:
        effective_sni = None  # SNI does not apply to IP targets
    else:
        effective_sni = host
    return TlsTarget(
        host=host, port=effective_port, server_name=effective_sni, is_ip=is_ip
    )


def _split_host_port(text: str, raw: str) -> tuple[str, int | None]:
    """Split a target into (host, shorthand-port); handles `[v6]:port` and bare IPv6."""
    if text.startswith("["):  # [v6]:port or [v6]
        closing = text.find("]")
        if closing < 0:
            raise UsageError(f"invalid target (unclosed '['): {raw}")
        rest = text[closing + 1 :]
        if rest.startswith(":"):
            return text[1:closing], _parse_port(rest[1:], raw)
        if rest:
            raise UsageError(f"invalid target: {raw}")
        return text[1:closing], None
    if text.count(":") == 1:  # host:port (a single colon cannot be bare IPv6)
        host, _, port_text = text.partition(":")
        return host, _parse_port(port_text, raw)
    return text, None  # bare hostname, IPv4, or bare IPv6 literal (multiple colons)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _parse_port(text: str, raw: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise UsageError(f"invalid port in target: {raw}") from exc
    if not 1 <= value <= _MAX_PORT:
        raise UsageError(f"port must be between 1 and {_MAX_PORT}: {raw}")
    return value


@dataclass(frozen=True)
class CertificateInfo:
    """Descriptive attributes of one certificate plus derived facts."""

    subject: str
    issuer: str
    sans: tuple[str, ...]  # "dns:<name>" / "ip:<addr>"
    not_before: str  # ISO 8601 UTC
    not_after: str  # ISO 8601 UTC
    days_until_expiry: int  # negative when expired
    serial: str  # hex
    signature_algorithm: str
    key_type: str
    key_bits: int
    fingerprint_sha256: str
    is_self_signed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this certificate."""
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "sans": list(self.sans),
            "not_before": self.not_before,
            "not_after": self.not_after,
            "days_until_expiry": self.days_until_expiry,
            "serial": self.serial,
            "signature_algorithm": self.signature_algorithm,
            "key_type": self.key_type,
            "key_bits": self.key_bits,
            "fingerprint_sha256": self.fingerprint_sha256,
            "is_self_signed": self.is_self_signed,
        }


@dataclass(frozen=True)
class ValidationFinding:
    """One failed or warned validation condition, with explanation and hint."""

    code: FindingCode
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this finding."""
        return {"code": self.code.value, "message": self.message, "hint": self.hint}


@dataclass(frozen=True)
class TlsCheckResult:
    """The layered outcome of one TLS check (see data-model.md)."""

    target: TlsTarget
    outcome: TlsOutcome
    connection: Any | None = (
        None  # opskit.net.TcpConnection (kept loose to avoid cycle)
    )
    tls_version: str | None = None
    cipher: str | None = None
    leaf: CertificateInfo | None = None
    chain: tuple[CertificateInfo, ...] = ()
    findings: tuple[ValidationFinding, ...] = field(default=())
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        """True when the check passed cleanly."""
        return self.outcome is TlsOutcome.OK

    def __bool__(self) -> bool:
        """Truthiness mirrors :attr:`ok`."""
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the CLI envelope's result."""
        return {
            "outcome": self.outcome.value,
            "connection": self.connection.to_dict() if self.connection else None,
            "tls_version": self.tls_version,
            "cipher": self.cipher,
            "leaf": self.leaf.to_dict() if self.leaf else None,
            "chain": [cert.to_dict() for cert in self.chain],
            "findings": [finding.to_dict() for finding in self.findings],
            "elapsed_ms": round(self.elapsed_ms, 3),
        }
