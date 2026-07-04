"""DNS-specific exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`)."""

from __future__ import annotations

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class DnsError(OpskitError):
    """Base class for DNS resolution failures (SERVFAIL unless a subclass narrows it)."""

    code = "dns_error"
    exit_code = ExitCode.SERVFAIL


class NxDomain(DnsError):
    """The queried name does not exist (NXDOMAIN)."""

    code = "nxdomain"
    exit_code = ExitCode.NXDOMAIN


class ServerFailure(DnsError):
    """The resolver returned SERVFAIL."""

    code = "servfail"
    exit_code = ExitCode.SERVFAIL


class DnsRefused(DnsError):
    """The resolver refused the query (REFUSED)."""

    code = "refused"
    exit_code = ExitCode.REFUSED


class DnsTimeout(DnsError):
    """No response before the timeout elapsed (the resolver may be filtered)."""

    code = "timeout"
    exit_code = ExitCode.TIMEOUT


class DnssecError(DnsError):
    """DNSSEC validation failed for the response."""

    code = "dnssec_error"
