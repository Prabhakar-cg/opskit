"""Base exception hierarchy for opskit.

The library layer raises these; only the CLI catches them and maps them to exit codes
(see :mod:`opskit.core.exit_codes`). No raw third-party exception should reach the user.
"""

from __future__ import annotations


class OpskitError(Exception):
    """Base class for all opskit errors.

    Attributes:
        message: Human-readable summary of what went wrong.
        hint: Optional actionable next step for the user (e.g. "try a different --server").
    """

    code: str = "error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """Initialize the error with a message and an optional remediation hint."""
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(OpskitError):
    """Invalid user input (bad name/IP, unknown option). Raised before any network I/O."""

    code = "usage_error"
