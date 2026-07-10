"""Typed data model for Active Directory / LDAP diagnostics.

Frozen stdlib dataclasses (no Pydantic) with ``to_dict()`` for the JSON envelope, plus
identifier-form detection and RFC 4515 filter-value escaping. The bind password is
excluded from ``repr()`` and has **no serialization path** — it can never reach an
envelope or log (Art. III). See specs/004-ad-diagnostics/data-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from opskit.ad.errors import CleartextRefused
from opskit.core.errors import UsageError
from opskit.net.models import normalize_host, split_host_port

SECURITY_MODES = ("ldaps", "starttls", "plaintext")

# Default port per connection-security mode (R2). An explicit :port always wins.
SECURITY_PORTS = {"ldaps": 636, "starttls": 389, "plaintext": 389}


class IdentifierKind(str, Enum):
    """How a principal identifier will be matched (R6)."""

    DN = "dn"  # contains '=' -> read that DN directly
    UPN = "upn"  # contains '@' -> userPrincipalName equality
    SAM = "sam"  # anything else -> sAMAccountName/cn equality


def classify_identifier(raw: str) -> tuple[IdentifierKind, str]:
    r"""Detect an identifier's form: DN, UPN, or account name (deterministic, R6).

    A ``DOMAIN\\name`` input has its netbios prefix stripped and matches as an
    account name.

    Raises:
        UsageError: For an empty identifier.
    """
    text = raw.strip()
    if not text:
        raise UsageError("a principal name is required")
    if "\\" in text and "=" not in text:
        text = text.split("\\", 1)[1].strip()
        if not text:
            raise UsageError(f"invalid principal: {raw}")
    if "=" in text:
        return IdentifierKind.DN, text
    if "@" in text:
        return IdentifierKind.UPN, text
    return IdentifierKind.SAM, text


def escape_filter_value(value: str) -> str:
    """Escape a value for interpolation into an LDAP filter (RFC 4515).

    Every user- or directory-derived value MUST pass through here before filter
    interpolation, making LDAP injection structurally impossible (R6).
    """
    escaped: list[str] = []
    for char in value:
        if char in "\\*()\0":
            escaped.append(f"\\{ord(char):02x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def parse_server(raw: str) -> tuple[str, int | None]:
    """Split a ``host``/``host:port``/``[v6]:port`` server string.

    Raises:
        UsageError: For an empty or malformed server string.
    """
    text = raw.strip()
    if not text:
        raise UsageError("a server host is required")
    host, port = split_host_port(text, raw)
    host = normalize_host(host)
    if not host:
        raise UsageError(f"invalid server (empty host): {raw}")
    return host, port


@dataclass(frozen=True)
class DirectoryConfig:
    r"""How to reach and authenticate to the directory (input model, never serialized).

    Built explicitly by callers (the CLI from flags/env; library users in code) — the
    API never auto-reads environment or config files (Art. VII).

    Attributes:
        server: Explicit ``host``/``host:port``; wins over ``domain``.
        domain: Domain name for SRV-based DC discovery (used when ``server`` is unset).
        security: ``"ldaps"`` (default), ``"starttls"``, or ``"plaintext"``.
        port: Explicit port; defaults per security mode (636/389/389).
        bind_user: Bind account (UPN, ``DOMAIN\\name``, or DN); ``None`` = anonymous.
        password: Bind secret — excluded from ``repr()``; never serialized anywhere.
        allow_cleartext: Explicit opt-in required to send a password without TLS.
        ca_file: PEM bundle replacing the platform trust store (private PKI).
        base_dn: Search-base override (else the server's ``defaultNamingContext``).
        timeout: Connect and per-operation timeout, seconds.
    """

    server: str | None = None
    domain: str | None = None
    security: str = "ldaps"
    port: int | None = None
    bind_user: str | None = None
    password: str | None = field(default=None, repr=False)
    allow_cleartext: bool = False
    ca_file: Path | None = None
    base_dn: str | None = None
    timeout: float = 5.0

    def __post_init__(self) -> None:
        """Validate the configuration before any network I/O.

        Raises:
            UsageError: For a missing server/domain, unknown security mode, or
                non-positive timeout.
            CleartextRefused: When a password would travel unencrypted without the
                explicit ``allow_cleartext`` opt-in.
        """
        if self.security not in SECURITY_MODES:
            raise UsageError(
                f"unknown security mode: {self.security}",
                hint="use one of: " + ", ".join(SECURITY_MODES),
            )
        if not self.server and not self.domain:
            raise UsageError(
                "no directory given",
                hint="pass a server (--server/-s) or a domain to discover (--domain/-d)",
            )
        if self.timeout <= 0:
            raise UsageError(f"timeout must be positive: {self.timeout}")
        if (
            self.password is not None
            and self.security == "plaintext"
            and not self.allow_cleartext
        ):
            raise CleartextRefused(
                "refusing to send a password over an unencrypted connection",
                hint="pass --plaintext to explicitly accept cleartext (lab use only)",
            )

    @property
    def encrypted(self) -> bool:
        """True unless the connection mode is plaintext (FR-002)."""
        return self.security != "plaintext"

    @property
    def effective_port(self) -> int:
        """The explicit port, or the security mode's default (R2)."""
        return self.port if self.port is not None else SECURITY_PORTS[self.security]


def _iso(value: datetime | None) -> str | None:
    """Render an aware datetime as an ISO-8601 string (``None`` passes through)."""
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class Stage:
    """One stage of the connectivity check (reached / secured / authenticated)."""

    name: str
    ok: bool
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this stage."""
        return {
            "name": self.name,
            "ok": self.ok,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


@dataclass(frozen=True)
class ServerInfo:
    """Basic server identity read from the rootDSE (best effort; fields may be None)."""

    default_naming_context: str | None = None
    dns_host_name: str | None = None
    supports_starttls: bool | None = None
    vendor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the server identity."""
        return {
            "default_naming_context": self.default_naming_context,
            "dns_host_name": self.dns_host_name,
            "supports_starttls": self.supports_starttls,
            "vendor": self.vendor,
        }


@dataclass(frozen=True)
class ConnectivityReport:
    """Staged verdict of ``ad check``: reached -> secured -> authenticated."""

    server_used: str
    port: int
    security: str
    encrypted: bool
    discovered: bool
    candidates_tried: tuple[str, ...]
    stages: tuple[Stage, ...]
    bind_user: str | None
    server_info: ServerInfo

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the envelope's result."""
        return {
            "server_used": self.server_used,
            "port": self.port,
            "security": self.security,
            "encrypted": self.encrypted,
            "discovered": self.discovered,
            "candidates_tried": list(self.candidates_tried),
            "stages": [stage.to_dict() for stage in self.stages],
            "bind_user": self.bind_user,
            "server_info": self.server_info.to_dict(),
        }


@dataclass(frozen=True)
class AccountStatusReport:
    """One principal's sign-in status facts (tri-state: value / None+unavailable)."""

    principal: str
    dn: str
    sam_account_name: str | None
    user_principal_name: str | None
    enabled: bool | None
    locked: bool | None
    lockout_time: datetime | None
    lockout_stale_possible: bool
    password_expired: bool | None
    password_expires_at: datetime | None
    password_never_expires: bool | None
    must_change_password: bool | None
    password_last_set: datetime | None
    account_expires_at: datetime | None
    account_never_expires: bool | None
    account_expired: bool | None
    blockers: tuple[str, ...]
    facts_unavailable: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the envelope's result."""
        return {
            "principal": self.principal,
            "dn": self.dn,
            "sam_account_name": self.sam_account_name,
            "user_principal_name": self.user_principal_name,
            "enabled": self.enabled,
            "locked": self.locked,
            "lockout_time": _iso(self.lockout_time),
            "lockout_stale_possible": self.lockout_stale_possible,
            "password_expired": self.password_expired,
            "password_expires_at": _iso(self.password_expires_at),
            "password_never_expires": self.password_never_expires,
            "must_change_password": self.must_change_password,
            "password_last_set": _iso(self.password_last_set),
            "account_expires_at": _iso(self.account_expires_at),
            "account_never_expires": self.account_never_expires,
            "account_expired": self.account_expired,
            "blockers": list(self.blockers),
            "facts_unavailable": list(self.facts_unavailable),
        }


@dataclass(frozen=True)
class MembershipEntry:
    """One group a principal belongs to, and how membership was acquired."""

    name: str
    dn: str
    via: str  # "direct" | "nested" | "primary"
    path: tuple[
        str, ...
    ] = ()  # granting chain of group names (empty for direct/primary)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this membership entry."""
        return {
            "name": self.name,
            "dn": self.dn,
            "via": self.via,
            "path": list(self.path),
        }


@dataclass(frozen=True)
class MembershipReport:
    """A principal's group memberships (direct, or effective with nesting resolved)."""

    principal: str
    dn: str
    effective: bool
    groups: tuple[MembershipEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the envelope's result."""
        return {
            "principal": self.principal,
            "dn": self.dn,
            "effective": self.effective,
            "groups": [group.to_dict() for group in self.groups],
        }


@dataclass(frozen=True)
class MembershipVerdict:
    """The explicit answer to "is principal P in group G?" (a verdict, not an error)."""

    principal: str
    principal_dn: str
    group: str
    group_dn: str
    member: bool
    via: str | None = None  # "direct" | "nested" | "primary" | None (not a member)
    path: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the envelope's result."""
        return {
            "principal": self.principal,
            "principal_dn": self.principal_dn,
            "group": self.group,
            "group_dn": self.group_dn,
            "member": self.member,
            "via": self.via,
            "path": list(self.path),
        }


@dataclass(frozen=True)
class ObjectSummary:
    """Key attributes of one named directory object (user, group, or computer)."""

    name: str
    dn: str
    object_type: str  # "user" | "group" | "computer"
    identifiers: dict[str, str | None]
    created: datetime | None
    changed: datetime | None
    description: str | None
    type_facts: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the envelope's result."""
        return {
            "name": self.name,
            "dn": self.dn,
            "object_type": self.object_type,
            "identifiers": dict(self.identifiers),
            "created": _iso(self.created),
            "changed": _iso(self.changed),
            "description": self.description,
            "type_facts": dict(self.type_facts),
        }
