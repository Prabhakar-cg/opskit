"""Typed data model for storage diagnostics.

All models are frozen stdlib dataclasses (no Pydantic in core) with ``to_dict()`` for the
JSON envelope. See specs/006-storage-diagnostics/data-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Volume:
    """A mounted, usable filesystem — the unit `storage volumes` reports (FR-001, FR-002, FR-003)."""

    mountpoint: str
    device: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float
    is_network: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "mountpoint": self.mountpoint,
            "device": self.device,
            "fstype": self.fstype,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "percent_used": self.percent_used,
            "is_network": self.is_network,
        }


@dataclass(frozen=True)
class Partition:
    """A partition on a :class:`Disk`, nested under it (FR-005)."""

    device: str
    size_bytes: int | None
    mounted: bool
    mountpoint: str | None = None
    fstype: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "device": self.device,
            "size_bytes": self.size_bytes,
            "mounted": self.mounted,
            "mountpoint": self.mountpoint,
            "fstype": self.fstype,
        }


@dataclass(frozen=True)
class Disk:
    """A physical/logical disk with its nested partitions (FR-004, FR-006).

    ``size_bytes``/``model``/``removable`` are ``None`` when the platform doesn't expose
    them (research R2) — explicitly unavailable, never fabricated or omitted.
    """

    id: str
    size_bytes: int | None
    model: str | None
    removable: bool | None
    partitions: tuple[Partition, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "id": self.id,
            "size_bytes": self.size_bytes,
            "model": self.model,
            "removable": self.removable,
            "partitions": [p.to_dict() for p in self.partitions],
        }


@dataclass(frozen=True)
class InaccessiblePath:
    """One subdirectory skipped during a size scan because it could not be listed."""

    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True)
class ChildDirSize:
    """One entry in a depth-limited breakdown (FR-008)."""

    path: str
    depth: int
    size_bytes: int
    incomplete: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "depth": self.depth,
            "size_bytes": self.size_bytes,
            "incomplete": self.incomplete,
        }


@dataclass(frozen=True)
class DirSizeResult:
    """The outcome of a directory-size scan (FR-007 through FR-011), one per requested path."""

    path: str
    total_bytes: int
    file_count: int
    dir_count: int
    include_hidden: bool
    depth_requested: int
    breakdown: tuple[ChildDirSize, ...] = ()
    inaccessible: tuple[InaccessiblePath, ...] = ()

    @property
    def incomplete(self) -> bool:
        """True when any subdirectory was inaccessible — the total is a lower bound."""
        return bool(self.inaccessible)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "total_bytes": self.total_bytes,
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "include_hidden": self.include_hidden,
            "depth_requested": self.depth_requested,
            "breakdown": [c.to_dict() for c in self.breakdown],
            "inaccessible": [i.to_dict() for i in self.inaccessible],
            "incomplete": self.incomplete,
        }
