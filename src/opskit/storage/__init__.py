"""Storage diagnostics — importable API and CLI sub-app.

Public surface (SemVer-governed): :func:`list_volumes`, :func:`list_disks`,
:func:`dir_size`, the typed models, and the storage exception hierarchy. Failures raise;
nothing here prints or exits the process.
"""

from __future__ import annotations

from opskit.storage.api import dir_size, list_disks, list_volumes
from opskit.storage.errors import PathNotFound, PathPermissionDenied, StorageError
from opskit.storage.models import (
    ChildDirSize,
    DirSizeResult,
    Disk,
    InaccessiblePath,
    Partition,
    Volume,
)

__all__ = [
    "ChildDirSize",
    "DirSizeResult",
    "Disk",
    "InaccessiblePath",
    "Partition",
    "PathNotFound",
    "PathPermissionDenied",
    "StorageError",
    "Volume",
    "dir_size",
    "list_disks",
    "list_volumes",
]
