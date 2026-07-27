"""Shared write-safety helper for every guarded write command (research R7).

Every in-place write goes through :func:`write_in_place`; every ``--output`` write goes
through :func:`write_new_file`. Both funnel into :func:`_atomic_replace`, so the "atomic,
same-directory temp file + ``os.replace()``" guarantee (constitution Art. X) is implemented
and tested exactly once rather than per command.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from opskit.file.errors import ClobberRefused, FilePermissionDenied


def _atomic_replace(path: Path, content: bytes) -> None:
    """Write ``content`` via a same-directory temp file + ``os.replace()``.

    Same-directory placement is what makes the final rename atomic on every supported OS —
    a cross-filesystem rename is not guaranteed atomic anywhere.
    """
    directory = path.parent
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=f".{path.name}.", suffix=".tmp"
        )
    except OSError as exc:
        raise FilePermissionDenied(
            f"cannot create a temp file in {directory}: {exc}"
        ) from exc
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        tmp_path.replace(path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise FilePermissionDenied(f"cannot write {path}: {exc}") from exc


def write_in_place(
    path: Path, content: bytes, *, backup: bool, force: bool
) -> str | None:
    """Atomically replace ``path``'s content, optionally backing up the original first.

    Returns:
        The backup file's path when ``backup`` was requested, else ``None``.

    Raises:
        ClobberRefused: ``backup`` was requested but ``<path>.bak`` already exists and
            ``force`` was not passed.
        FilePermissionDenied: the backup or the replacement itself could not be written.
    """
    backup_path: str | None = None
    if backup:
        bak = path.with_name(path.name + ".bak")
        if bak.exists() and not force:
            raise ClobberRefused(
                f"refusing to overwrite existing backup file: {bak}",
                hint="pass --force to overwrite it",
            )
        try:
            shutil.copy2(path, bak)
        except OSError as exc:
            raise FilePermissionDenied(f"cannot write backup {bak}: {exc}") from exc
        backup_path = str(bak)
    _atomic_replace(path, content)
    return backup_path


def write_new_file(path: Path, content: bytes, *, force: bool) -> None:
    """Write ``content`` to a brand-new destination ``path`` (the ``--output`` case).

    Raises:
        ClobberRefused: ``path`` already exists and ``force`` was not passed.
        FilePermissionDenied: the destination could not be written.
    """
    if path.exists() and not force:
        raise ClobberRefused(
            f"refusing to overwrite existing file: {path}",
            hint="pass --force to overwrite it, or choose a different --output path",
        )
    _atomic_replace(path, content)
