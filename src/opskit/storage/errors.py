"""Storage-specific exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

Every exit code here is a **reused** existing class (research R6) — no ``core`` changes.
"""

from __future__ import annotations

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class StorageError(OpskitError):
    """Base class for storage diagnostics failures (unclassified storage error)."""

    code = "storage_error"
    exit_code = ExitCode.ERROR


class PathNotFound(StorageError):
    """The requested path does not exist, or is not a directory."""

    code = "path_not_found"
    exit_code = ExitCode.NOT_FOUND  # reused class, like ad's "not found" (research R6)


class PathPermissionDenied(StorageError):
    """The requested top-level path cannot be listed at all.

    Nested subdirectory failures are recorded, not raised — see
    :class:`opskit.storage.models.InaccessiblePath`.
    """

    code = "path_permission_denied"
    exit_code = ExitCode.PERMISSION_DENIED  # reused class (research R6)
