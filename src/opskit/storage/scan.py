"""Directory-size scanning: an iterative ``os.scandir()`` walk (research R5).

Pure stdlib, identical code path on every platform (only the hidden-attribute check
branches on ``os.name``). No new dependency.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from opskit.storage.errors import PathNotFound, PathPermissionDenied
from opskit.storage.models import ChildDirSize, DirSizeResult, InaccessiblePath

_WINDOWS = os.name == "nt"


def _reason(exc: OSError) -> str:
    """A short human-readable summary of an OSError (e.g. "Permission denied")."""
    return exc.strerror or str(exc)


def _is_hidden(entry: os.DirEntry[str]) -> bool:
    """Dotfile name on POSIX; ``FILE_ATTRIBUTE_HIDDEN`` via ``os.stat`` on Windows (R5)."""
    if entry.name.startswith("."):
        return True
    if not _WINDOWS:
        return False
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


@dataclass
class _ScanState:
    """Mutable bookkeeping built by one iterative walk, then aggregated bottom-up."""

    preorder: list[Path] = field(default_factory=list[Path])
    # The default_factory value (unlike the annotation) is a real runtime expression, not
    # deferred by `from __future__ import annotations` — `Path | None` there would break on
    # the project's Python 3.9 floor, so `Optional[...]` stays for that one call only.
    parent_of: dict[Path, Path | None] = field(
        default_factory=dict[Path, Optional[Path]]
    )
    depth_of: dict[Path, int] = field(default_factory=dict[Path, int])
    own_bytes: dict[Path, int] = field(default_factory=dict[Path, int])
    failed: set[Path] = field(default_factory=set[Path])
    inaccessible: list[InaccessiblePath] = field(default_factory=list[InaccessiblePath])
    file_count: int = 0
    dir_count: int = 0


def _walk(root: Path, *, include_hidden: bool) -> _ScanState:
    """Iterative (explicit-stack) pre-order walk of ``root``.

    Symlinks/junctions/reparse points are never descended into (FR-010). A subdirectory
    that can't be listed is recorded in ``inaccessible`` and the walk continues into every
    other branch (FR-011) — except ``root`` itself, whose failure is fatal (raised by the
    caller, not recorded here, since there is nothing to report without it).

    Raises:
        PathPermissionDenied: When ``root`` itself cannot be listed.
    """
    state = _ScanState()
    stack: list[tuple[Path, Path | None, int]] = [(root, None, 0)]
    while stack:
        current, parent, depth = stack.pop()
        state.preorder.append(current)
        state.parent_of[current] = parent
        state.depth_of[current] = depth
        state.dir_count += 1

        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            if current == root:
                raise PathPermissionDenied(
                    f"cannot list directory: {current}", hint=_reason(exc)
                ) from exc
            state.failed.add(current)
            state.inaccessible.append(
                InaccessiblePath(path=str(current), reason=_reason(exc))
            )
            state.own_bytes[current] = 0
            continue

        own = 0
        for entry in entries:
            try:
                if not include_hidden and _is_hidden(entry):
                    continue
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), current, depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    own += entry.stat(follow_symlinks=False).st_size
                    state.file_count += 1
            except OSError:
                continue  # a single unreadable entry doesn't abort the directory
        state.own_bytes[current] = own
    return state


def _aggregate(state: _ScanState) -> tuple[dict[Path, int], dict[Path, bool]]:
    """Bottom-up rollup: each directory's total size and whether its subtree is incomplete.

    Processing ``reversed(state.preorder)`` guarantees every descendant of a directory is
    aggregated before the directory itself, since a stack-based pre-order's reverse always
    orders descendants ahead of their ancestor.
    """
    total_of: dict[Path, int] = dict(state.own_bytes)
    incomplete_of: dict[Path, bool] = {p: (p in state.failed) for p in state.preorder}
    for current in reversed(state.preorder):
        parent = state.parent_of[current]
        if parent is None:
            continue
        total_of[parent] += total_of[current]
        if incomplete_of[current]:
            incomplete_of[parent] = True
    return total_of, incomplete_of


def dir_size(
    path: str | Path, *, depth: int = 0, include_hidden: bool = False
) -> DirSizeResult:
    """Recursive size of ``path``, optionally with a depth-limited child breakdown.

    Raises:
        PathNotFound: ``path`` does not exist, or is not a directory.
        PathPermissionDenied: ``path`` itself cannot be listed at all.

    Returns:
        A :class:`~opskit.storage.models.DirSizeResult`, even when nested subdirectories
        were inaccessible — ``result.incomplete``/``result.inaccessible`` name them (FR-011).
    """
    root = Path(path)
    if not root.exists():
        raise PathNotFound(
            f"path does not exist: {path}", hint="check the path and retry"
        )
    if not root.is_dir():
        raise PathNotFound(
            f"not a directory: {path}", hint="storage size expects a directory"
        )

    state = _walk(root, include_hidden=include_hidden)
    total_of, incomplete_of = _aggregate(state)

    breakdown = tuple(
        ChildDirSize(
            path=str(child),
            depth=state.depth_of[child],
            size_bytes=total_of[child],
            incomplete=incomplete_of[child],
        )
        for child in state.preorder
        if 1 <= state.depth_of[child] <= depth
    )
    return DirSizeResult(
        path=str(root),
        total_bytes=total_of[root],
        file_count=state.file_count,
        dir_count=state.dir_count,
        include_hidden=include_hidden,
        depth_requested=depth,
        breakdown=breakdown,
        inaccessible=tuple(state.inaccessible),
    )
