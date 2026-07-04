"""Typed data model for DNS diagnostics.

All models are frozen stdlib dataclasses (no Pydantic in core) with ``to_dict()`` for the
JSON envelope. See specs/001-dns-diagnostics/data-model.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


class RecordType(str, Enum):
    """DNS record types opskit can request."""

    A = "A"
    AAAA = "AAAA"
    MX = "MX"
    TXT = "TXT"
    CNAME = "CNAME"
    NS = "NS"
    SOA = "SOA"
    SRV = "SRV"
    CAA = "CAA"
    PTR = "PTR"


class Transport(str, Enum):
    """Query transport: AUTO uses UDP then falls back to TCP on truncation."""

    AUTO = "auto"
    UDP = "udp"
    TCP = "tcp"


class Outcome(str, Enum):
    """The outcome class of a single query (mirrors the exit-code classes)."""

    OK = "ok"
    NXDOMAIN = "nxdomain"
    SERVFAIL = "servfail"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    USAGE_ERROR = "usage_error"


@dataclass(frozen=True)
class DnsRecord:
    """A single returned record: its type, rendered value, and TTL in seconds."""

    type: RecordType
    value: str
    ttl: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {"type": self.type.value, "value": self.value, "ttl": self.ttl}


@dataclass(frozen=True)
class Resolver:
    """A DNS server that answered a query (``"system"`` denotes the OS default)."""

    address: str
    label: str | None = None


@dataclass(frozen=True)
class DnsQuery:
    """What the user asked: target, record types, resolver(s), and query controls."""

    target: str
    record_types: tuple[RecordType, ...]
    servers: tuple[str, ...] = ()
    transport: Transport = Transport.AUTO
    timeout_s: float = 5.0
    retries: int = 2
    port: int = 53
    trace: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the request parameters."""
        return {
            "target": self.target,
            "record_types": [t.value for t in self.record_types],
            "servers": list(self.servers),
            "transport": self.transport.value,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "port": self.port,
        }


@dataclass(frozen=True)
class LookupResult:
    """The successful outcome of a query against a single resolver.

    Failures are raised as :class:`opskit.dns.errors.DnsError` subclasses, not represented
    here; an empty ``records`` tuple means the name exists but has no record of that type.
    """

    query: DnsQuery
    resolver: Resolver
    records: tuple[DnsRecord, ...] = ()
    elapsed_ms: float = 0.0
    outcome: Outcome = Outcome.OK

    @property
    def ok(self) -> bool:
        """True when the query succeeded."""
        return self.outcome is Outcome.OK

    def __bool__(self) -> bool:
        """A result is truthy when it succeeded."""
        return self.ok

    def __iter__(self) -> Iterator[DnsRecord]:
        """Iterating a result yields its records."""
        return iter(self.records)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the result."""
        return {
            "outcome": self.outcome.value,
            "resolver": self.resolver.address,
            "records": [r.to_dict() for r in self.records],
        }


@dataclass(frozen=True)
class TraceStep:
    """One hop in an iterative resolution: which server was asked and what it returned."""

    server: str
    zone: str
    response: Literal["referral", "answer", "error"]
    referrals: tuple[str, ...] = ()
    records: tuple[DnsRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this trace step."""
        return {
            "server": self.server,
            "zone": self.zone,
            "response": self.response,
            "referrals": list(self.referrals),
            "records": [r.to_dict() for r in self.records],
        }


@dataclass(frozen=True)
class ResolverAnswer:
    """What one resolver returned for a compared query (records, or a failure)."""

    server: str
    outcome: Outcome
    records: tuple[DnsRecord, ...] = ()
    error: str | None = None
    elapsed_ms: float = 0.0

    def signature(self) -> tuple[Outcome, frozenset[tuple[RecordType, str]]]:
        """Identity for agreement checks: outcome + record set, ignoring TTLs and order."""
        return (self.outcome, frozenset((r.type, r.value) for r in self.records))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this resolver's answer."""
        return {
            "server": self.server,
            "outcome": self.outcome.value,
            "records": [r.to_dict() for r in self.records],
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


@dataclass(frozen=True)
class ResolverComparison:
    """The same query asked of several resolvers, with agreement analysis.

    ``consistent`` is True only when every resolver returned the same outcome and record set.
    """

    target: str
    record_types: tuple[RecordType, ...]
    answers: tuple[ResolverAnswer, ...]
    consistent: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the comparison."""
        return {
            "target": self.target,
            "record_types": [t.value for t in self.record_types],
            "consistent": self.consistent,
            "answers": [a.to_dict() for a in self.answers],
        }
