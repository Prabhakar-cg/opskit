"""DNS-specific exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`)."""

from __future__ import annotations

from opskit.core.errors import OpskitError


class DnsError(OpskitError):
    """Base class for DNS resolution failures."""

    code = "dns_error"


class NxDomain(DnsError):
    """The queried name does not exist (NXDOMAIN)."""

    code = "nxdomain"


class ServerFailure(DnsError):
    """The resolver returned SERVFAIL."""

    code = "servfail"


class DnsRefused(DnsError):
    """The resolver refused the query (REFUSED)."""

    code = "refused"


class DnsTimeout(DnsError):
    """No response before the timeout elapsed (the resolver may be filtered)."""

    code = "timeout"


class DnssecError(DnsError):
    """DNSSEC validation failed for the response."""

    code = "dnssec_error"
