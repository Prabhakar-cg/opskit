"""File-operations exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

Two exit codes are new to this category (research R9); the rest reuse existing classes.
"""

from __future__ import annotations

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class FileError(OpskitError):
    """Base class for file-operations failures (unclassified file error)."""

    code = "file_error"
    exit_code = ExitCode.ERROR


class FileNotFoundOnDisk(FileError):
    """The requested path does not exist, or is not a regular file where one is required."""

    code = "file_not_found"
    exit_code = ExitCode.NOT_FOUND  # reused class (research R9)


class FilePermissionDenied(FileError):
    """The requested path cannot be read, or a write destination cannot be written."""

    code = "file_permission_denied"
    exit_code = ExitCode.PERMISSION_DENIED  # reused class (research R9)


class InvalidContent(FileError):
    """The file is not valid for its (detected or declared) format/encoding.

    Covers a `validate` failure, a `convert`/`pretty`/`diff`/`reencode` source that fails to
    parse/decode, and a text-oriented command given a binary file.
    """

    code = "invalid_content"
    exit_code = ExitCode.INVALID_CONTENT  # new (research R9)

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        """Initialize with an optional parse-error location."""
        super().__init__(message, hint=hint)
        self.line = line
        self.column = column


class ClobberRefused(FileError):
    """An in-place write would silently overwrite a pre-existing, unrelated file.

    Most commonly an existing ``.bak`` at the computed backup destination.
    """

    code = "clobber_refused"
    exit_code = ExitCode.CLOBBER_REFUSED  # new (research R9)
