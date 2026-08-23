"""Streaming checksums and size-then-hash duplicate-file detection.

See specs/007-file-operations/research.md R6.
"""

from __future__ import annotations

import functools
import hashlib
import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, Protocol

from opskit.core.errors import UsageError
from opskit.file import formats
from opskit.file.errors import FileNotFoundOnDisk, FilePermissionDenied
from opskit.file.models import DuplicateGroup

_CHUNK_SIZE = 1024 * 1024


class _Hasher(Protocol):
    """Structural shape of a ``hashlib`` digest object (the subset we use)."""

    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


# sha1/md5 are offered only as file-identity checksums (dedup/verification), never for
# password or signature security — usedforsecurity=False records that intent so hashlib
# (and scanners) don't flag them as weak crypto (Sonar python:S4790).
_ALGORITHMS: dict[str, Callable[[], _Hasher]] = {
    "sha256": hashlib.sha256,
    "sha1": functools.partial(hashlib.sha1, usedforsecurity=False),
    "md5": functools.partial(hashlib.md5, usedforsecurity=False),
}

_MIN_GROUP_SIZE = 2


def compute_hash(path: str | Path, algorithm: str) -> str:
    """Stream ``path`` through ``algorithm``, returning the lowercase hex digest (FR-005).

    Raises:
        UsageError: ``algorithm`` is not one of the supported names.
        FileNotFoundOnDisk / FilePermissionDenied: the file could not be read.
    """
    try:
        factory = _ALGORITHMS[algorithm]
    except KeyError:
        raise UsageError(
            f"unsupported algorithm: {algorithm}",
            hint="choose one of: " + ", ".join(sorted(_ALGORITHMS)),
        ) from None

    digest = factory()
    with formats.open_binary(Path(path)) as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _try_list(directory: Path) -> list[os.DirEntry[str]]:
    try:
        return list(os.scandir(directory))
    except OSError:
        return []


def _iter_files(directory: Path, *, recursive: bool) -> list[Path]:
    """Files directly under ``directory`` (recursively if ``recursive``).

    Symlinks — files and directories alike — are never followed (research R6/spec Assumptions),
    matching the ``storage`` precedent. A subdirectory that can't be listed during a recursive
    walk is silently skipped rather than aborting the whole search; ``directory`` itself was
    already validated by the caller.
    """
    files: list[Path] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        for entry in _try_list(current):
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if recursive:
                    stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                files.append(Path(entry.path))
    return files


def find_duplicates(
    directory: str | Path, *, recursive: bool = False
) -> list[DuplicateGroup]:
    """Group files under ``directory`` sharing identical content (FR-008).

    Groups by size first (cheap pre-filter), then by streaming SHA-256 content hash within
    each size group — research R6.

    Raises:
        FileNotFoundOnDisk: ``directory`` does not exist, or is not a directory.
        FilePermissionDenied: ``directory`` itself cannot be listed.
    """
    resolved = Path(directory)
    if not resolved.exists():
        raise FileNotFoundOnDisk(
            f"directory not found: {resolved}", hint="check the path and try again"
        )
    if not resolved.is_dir():
        raise FileNotFoundOnDisk(
            f"not a directory: {resolved}", hint="pass a directory path"
        )
    try:
        os.scandir(resolved).close()
    except PermissionError as exc:
        raise FilePermissionDenied(f"permission denied listing {resolved}") from exc

    by_size: dict[int, list[Path]] = defaultdict(list)
    for file_path in _iter_files(resolved, recursive=recursive):
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        by_size[size].append(file_path)

    groups: list[DuplicateGroup] = []
    for size, paths in by_size.items():
        if len(paths) < _MIN_GROUP_SIZE:
            continue
        by_digest: dict[str, list[Path]] = defaultdict(list)
        for file_path in paths:
            try:
                digest = compute_hash(file_path, "sha256")
            except (FileNotFoundOnDisk, FilePermissionDenied):
                continue
            by_digest[digest].append(file_path)
        for digest, matches in by_digest.items():
            if len(matches) >= _MIN_GROUP_SIZE:
                groups.append(
                    DuplicateGroup(
                        digest=digest,
                        size_bytes=size,
                        paths=tuple(sorted(str(p) for p in matches)),
                    )
                )
    groups.sort(key=lambda g: (g.size_bytes, g.digest))
    return groups
