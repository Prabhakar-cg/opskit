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


def _write_temp(directory: Path, name: str, content: bytes) -> Path:
    """Write ``content`` to a new temp file in ``directory``, returning its path.

    Same-directory placement is what makes both :func:`_atomic_replace`'s rename and
    :func:`_create_exclusive`'s hardlink atomic on every supported OS — a cross-filesystem
    rename/link is not guaranteed atomic (or even supported) anywhere.
    """
    try:
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    except OSError as exc:
        raise FilePermissionDenied(
            f"cannot create a temp file in {directory}: {exc}"
        ) from exc
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise FilePermissionDenied(f"cannot write {tmp_path}: {exc}") from exc
    return tmp_path


def _atomic_replace(path: Path, content: bytes) -> None:
    """Write ``content`` via a same-directory temp file + ``os.replace()``.

    Unconditionally replaces ``path`` if it exists — the caller is responsible for any
    no-clobber check (:func:`write_in_place`'s backup rule; :func:`_create_exclusive` for
    the non-``--force`` ``--output`` case).
    """
    tmp_path = _write_temp(path.parent, path.name, content)
    try:
        tmp_path.replace(path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise FilePermissionDenied(f"cannot write {path}: {exc}") from exc


def _create_exclusive(path: Path, content: bytes) -> None:
    """Atomically create ``path`` with ``content``, refusing if it already exists.

    ``path.exists()`` followed by a separate write is TOCTOU-vulnerable — a file created by
    another process in that window would be silently clobbered. Writing the content to a
    same-directory temp file first, then hardlinking it into place, makes existence and
    creation a single atomic OS operation (``link()`` fails with ``FileExistsError`` if the
    destination is already there, on every supported OS).

    Raises:
        ClobberRefused: ``path`` already exists.
        FilePermissionDenied: the temp file or the link could not be written.
    """
    tmp_path = _write_temp(path.parent, path.name, content)
    try:
        os.link(tmp_path, path)
    except FileExistsError as exc:
        raise ClobberRefused(
            f"refusing to overwrite existing file: {path}",
            hint="pass --force to overwrite it, or choose a different --output path",
        ) from exc
    except OSError as exc:
        raise FilePermissionDenied(f"cannot write {path}: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


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

    Without ``force``, creation is atomic-exclusive (:func:`_create_exclusive`) — no
    existence-check-then-write race with a concurrently created file at ``path``.

    Raises:
        ClobberRefused: ``path`` already exists and ``force`` was not passed.
        FilePermissionDenied: the destination could not be written.
    """
    if force:
        _atomic_replace(path, content)
    else:
        _create_exclusive(path, content)
