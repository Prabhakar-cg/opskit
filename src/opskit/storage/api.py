"""Public storage diagnostics API — the CLI is a thin client over this module.

Functions return typed results on success and raise :class:`opskit.storage.errors.StorageError`
subclasses (or :class:`opskit.core.errors.UsageError`) on failure. Nothing here prints or
calls ``sys.exit``.
"""

from __future__ import annotations

from pathlib import Path

from opskit.core.errors import UsageError
from opskit.storage import enumerate_, scan
from opskit.storage.models import DirSizeResult, Disk, Volume


def list_volumes() -> tuple[Volume, ...]:
    """Every mounted, non-pseudo volume visible to the current user.

    Returns:
        One :class:`~opskit.storage.models.Volume` per mounted volume: mount point,
        filesystem type, total/used/free/percent-used capacity, and a local/network tag.
        Pseudo/virtual filesystems (``tmpfs``, ``proc``, ``sysfs``, …) are excluded.
    """
    return enumerate_.list_volumes()


def list_disks() -> tuple[Disk, ...]:
    """Every physical/logical disk, each with its nested partitions.

    Returns:
        One :class:`~opskit.storage.models.Disk` per disk. Full fidelity on Linux
        (`/sys/block`); on Windows/macOS, disks are derived one-to-one from mounted
        volumes with best-effort fields — unavailable data is ``None``, never fabricated.
    """
    return enumerate_.list_disks()


def dir_size(
    path: str | Path, *, depth: int = 0, include_hidden: bool = False
) -> DirSizeResult:
    """Recursive size of ``path``, optionally with a depth-limited child breakdown.

    Args:
        path: The directory to measure.
        depth: Child-directory breakdown levels below ``path``; ``0`` = total only.
        include_hidden: Include hidden files/directories in the size calculation
            (dotfiles on Linux/macOS, the hidden attribute on Windows). Excluded by default.

    Returns:
        A :class:`~opskit.storage.models.DirSizeResult`, even when nested subdirectories
        were inaccessible — ``result.incomplete``/``result.inaccessible`` name them.

    Raises:
        UsageError: When ``depth`` is negative (before any filesystem I/O).
        PathNotFound: ``path`` does not exist, or is not a directory.
        PathPermissionDenied: ``path`` itself cannot be listed at all.
    """
    if depth < 0:
        raise UsageError("--depth must be >= 0")
    return scan.dir_size(path, depth=depth, include_hidden=include_hidden)
