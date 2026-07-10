"""Structured process exit codes and the exception-to-code mapping.

Exit codes are part of the public, SemVer-governed contract (see contracts/cli.md) so scripts
can branch on the outcome class without parsing text.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opskit.core.errors import OpskitError


class ExitCode(IntEnum):
    """Documented exit codes returned by the CLI."""

    OK = 0
    ERROR = 1
    USAGE = 2
    NXDOMAIN = 3
    SERVFAIL = 4
    REFUSED = 5
    TIMEOUT = 6
    PARTIAL = 7
    CONNECT_FAILED = 8
    HANDSHAKE_FAILED = 9
    CERT_INVALID = 10
    CERT_EXPIRING = 11
    PORT_IN_USE = 12
    BIND_PERMISSION = 13
    AUTH_FAILED = 14
    PERMISSION_DENIED = 15
    NOT_FOUND = 16
    NOT_MEMBER = 17


def exit_code_for(error: OpskitError) -> ExitCode:
    """Return the :class:`ExitCode` an error declares.

    Each error type owns its ``exit_code`` (set on the class), so this mapping stays
    category-agnostic — adding a new category (net/tls/ad) never touches ``core``.
    """
    return error.exit_code
