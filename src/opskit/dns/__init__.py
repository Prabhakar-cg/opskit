"""DNS diagnostics — importable API and CLI sub-app.

Public surface (SemVer-governed): :func:`lookup`, the typed models, and the DNS exception
hierarchy. Failures raise; nothing here prints or exits the process.
"""

from __future__ import annotations

from opskit.dns.api import lookup, reverse
from opskit.dns.errors import (
    DnsError,
    DnsRefused,
    DnssecError,
    DnsTimeout,
    NxDomain,
    ServerFailure,
)
from opskit.dns.models import (
    DnsQuery,
    DnsRecord,
    LookupResult,
    Outcome,
    RecordType,
    Resolver,
    ResolverComparison,
    Transport,
)

__all__ = [
    "DnsError",
    "DnsQuery",
    "DnsRecord",
    "DnsRefused",
    "DnsTimeout",
    "DnssecError",
    "LookupResult",
    "NxDomain",
    "Outcome",
    "RecordType",
    "Resolver",
    "ResolverComparison",
    "ServerFailure",
    "Transport",
    "lookup",
    "reverse",
]
