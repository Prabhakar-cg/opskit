"""Base exception hierarchy for opskit.

The library layer raises these; only the CLI catches them and maps them to exit codes
(see :mod:`opskit.core.exit_codes`). No raw third-party exception should reach the user.
"""

from __future__ import annotations

from opskit.core.exit_codes import ExitCode


class OpskitError(Exception):
    """Base class for all opskit errors.

    Each subclass declares the process ``exit_code`` it maps to, so the CLI's exit-code
    resolution stays category-agnostic (see :func:`opskit.core.exit_codes.exit_code_for`).

    Attributes:
        message: Human-readable summary of what went wrong.
        hint: Optional actionable next step for the user (e.g. "try a different --server").
    """

    code: str = "error"
    exit_code: ExitCode = ExitCode.ERROR  # generic failure unless a subclass narrows it

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        """Initialize the error with a message and an optional remediation hint."""
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(OpskitError):
    """Invalid user input (bad name/IP, unknown option). Raised before any network I/O."""

    code = "usage_error"
    exit_code = ExitCode.USAGE
