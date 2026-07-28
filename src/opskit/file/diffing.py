"""Structural (semantic) diff of two parsed JSON/YAML trees.

See specs/007-file-operations/research.md R6.
"""

from __future__ import annotations

from typing import cast

from opskit.file.models import MISSING, DiffEntry


def _key_path(parent: str, key: object) -> str:
    if isinstance(key, int) and not isinstance(key, bool):
        return f"{parent}[{key}]" if parent else f"[{key}]"
    return f"{parent}.{key}" if parent else str(key)


def _walk_dict(
    left: dict[object, object],
    right: dict[object, object],
    path: str,
    out: list[DiffEntry],
) -> None:
    for key in sorted(set(left) | set(right), key=str):
        left_val = left.get(key, MISSING)
        right_val = right.get(key, MISSING)
        child_path = _key_path(path, key)
        if left_val is MISSING or right_val is MISSING:
            out.append(DiffEntry(child_path, left_val, right_val))
        else:
            _walk(left_val, right_val, child_path, out)


def _walk_list(
    left: list[object], right: list[object], path: str, out: list[DiffEntry]
) -> None:
    for index in range(max(len(left), len(right))):
        left_val: object = left[index] if index < len(left) else MISSING
        right_val: object = right[index] if index < len(right) else MISSING
        child_path = _key_path(path, index)
        if left_val is MISSING or right_val is MISSING:
            out.append(DiffEntry(child_path, left_val, right_val))
        else:
            _walk(left_val, right_val, child_path, out)


def _walk_scalar(left: object, right: object, path: str, out: list[DiffEntry]) -> None:
    if left != right:
        out.append(DiffEntry(path or "$", left, right))


def _walk(left: object, right: object, path: str, out: list[DiffEntry]) -> None:
    if isinstance(left, dict):
        if isinstance(right, dict):
            _walk_dict(
                cast("dict[object, object]", left),
                cast("dict[object, object]", right),
                path,
                out,
            )
            return
    elif isinstance(left, list):
        if isinstance(right, list):
            _walk_list(
                cast("list[object]", left), cast("list[object]", right), path, out
            )
            return
    else:
        _walk_scalar(left, right, path, out)
        return

    _walk_scalar(cast("object", left), right, path, out)


def compare(left: object, right: object) -> tuple[DiffEntry, ...]:
    """Walk two parsed structures, returning one :class:`DiffEntry` per leaf difference.

    Dicts are compared by key (a key absent on one side is reported with the ``MISSING``
    sentinel rather than recursing into it) and lists by index; any other type mismatch or
    value inequality at a given path is a single leaf difference (research R6).
    """
    differences: list[DiffEntry] = []
    _walk(left, right, "", differences)
    return tuple(differences)
