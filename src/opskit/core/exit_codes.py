"""Structured process exit codes and the exception-to-code mapping.

Exit codes are part of the public, SemVer-governed contract (see contracts/cli.md) so scripts
can branch on the outcome class without parsing text.
"""

from __future__ import annotations

from enum import IntEnum

from opskit.core.errors import OpskitError, UsageError


class ExitCode(IntEnum):
    """Documented exit codes returned by the CLI."""

    OK = 0
    USAGE = 2
    NXDOMAIN = 3
    SERVFAIL = 4
    REFUSED = 5
    TIMEOUT = 6
    PARTIAL = 7


def exit_code_for(error: OpskitError) -> ExitCode:
    """Map an :class:`OpskitError` to its :class:`ExitCode`.

    Imported lazily to avoid a core→dns import cycle.
    """
    from opskit.dns.errors import (  # noqa: PLC0415
        DnsError,
        DnsRefused,
        DnsTimeout,
        NxDomain,
    )

    if isinstance(error, UsageError):
        return ExitCode.USAGE
    if isinstance(error, NxDomain):
        return ExitCode.NXDOMAIN
    if isinstance(error, DnsRefused):
        return ExitCode.REFUSED
    if isinstance(error, DnsTimeout):
        return ExitCode.TIMEOUT
    if isinstance(error, DnsError):
        # ServerFailure, DnssecError, and any other DNS failure.
        return ExitCode.SERVFAIL
    return ExitCode.USAGE
