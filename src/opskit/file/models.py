"""Typed data model for file operations.

All models are frozen stdlib dataclasses (no Pydantic in core) with ``to_dict()`` for the
JSON envelope. See specs/007-file-operations/data-model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, cast


class StructuredFormat(str, Enum):
    """A structured data format `validate`/`convert`/`pretty`/`diff` understand."""

    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    XML = "xml"


class LineEnding(str, Enum):
    """A line-ending style `lineendings`/`eol` reason about."""

    LF = "lf"
    CRLF = "crlf"
    CR = "cr"


class _MissingType:
    """Sentinel marking a key absent on one side of a :class:`DiffEntry` (research R6).

    Distinguishes "this key does not exist" from "this key exists and is ``None``/``null``".
    """

    _instance: _MissingType | None = None

    def __new__(cls) -> _MissingType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingType()


def _json_safe(value: object) -> object:
    """Recursively coerce a loaded (JSON/YAML/TOML/XML) value into JSON-serializable form.

    A YAML/TOML source can produce ``date``/``datetime``/``time`` values that ``json.dumps``
    can't serialize on its own — mirrors ``formats._json_default``'s ISO-8601 handling for
    the ``--json`` diff envelope (research R6).
    """
    if isinstance(value, dict):
        items = cast("dict[object, object]", value).items()
        return {str(k): _json_safe(v) for k, v in items}
    if isinstance(value, list):
        return [_json_safe(v) for v in cast("list[object]", value)]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _side_to_dict(value: object) -> dict[str, Any]:
    """Serialize one side of a :class:`DiffEntry` as ``{"present": bool, "value": ...}``."""
    if value is MISSING:
        return {"present": False}
    return {"present": True, "value": _json_safe(value)}


@dataclass(frozen=True)
class LineEndingReport:
    """The outcome of a line-ending check, one per file (FR-001)."""

    path: str
    crlf_count: int
    lf_count: int
    cr_count: int
    mixed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "crlf_count": self.crlf_count,
            "lf_count": self.lf_count,
            "cr_count": self.cr_count,
            "mixed": self.mixed,
        }


@dataclass(frozen=True)
class EncodingReport:
    """The outcome of an encoding check, one per file (FR-002)."""

    path: str
    encoding: str
    has_bom: bool
    confidence: float | None
    invalid_sequences: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "encoding": self.encoding,
            "has_bom": self.has_bom,
            "confidence": self.confidence,
            "invalid_sequences": self.invalid_sequences,
        }


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of a syntax check, one per file (FR-003).

    ``format`` is ``None`` only when the format could not be determined at all (no
    extension/content match) — itself reported as ``valid=False``, not raised, since it's
    the same class of "this file failed validation" outcome as a syntax error.
    """

    path: str
    format: StructuredFormat | None
    valid: bool
    error_line: int | None = None
    error_column: int | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "format": self.format.value if self.format else None,
            "valid": self.valid,
            "error_line": self.error_line,
            "error_column": self.error_column,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class IdentificationResult:
    """The outcome of a type-sniffing check, one per file (FR-004)."""

    path: str
    detected_type: str
    extension: str
    extension_matches: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "detected_type": self.detected_type,
            "extension": self.extension,
            "extension_matches": self.extension_matches,
        }


@dataclass(frozen=True)
class ChecksumResult:
    """A file's checksum, one per file (FR-005)."""

    path: str
    algorithm: str
    digest: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {"path": self.path, "algorithm": self.algorithm, "digest": self.digest}


@dataclass(frozen=True)
class DiffEntry:
    """One differing key path between two structured files (research R6)."""

    key_path: str
    left_value: object
    right_value: object

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping (each side as ``{present, value}``)."""
        return {
            "key_path": self.key_path,
            "left": _side_to_dict(self.left_value),
            "right": _side_to_dict(self.right_value),
        }


@dataclass(frozen=True)
class StructuralDiffResult:
    """The outcome of comparing two structured files (FR-006)."""

    left_path: str
    right_path: str
    differences: tuple[DiffEntry, ...] = ()

    @property
    def equivalent(self) -> bool:
        """True iff no differences were found."""
        return not self.differences

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "left_path": self.left_path,
            "right_path": self.right_path,
            "equivalent": self.equivalent,
            "differences": [d.to_dict() for d in self.differences],
        }


@dataclass(frozen=True)
class StatResult:
    """A file's normalized metadata, one per file (FR-007)."""

    path: str
    size_bytes: int
    modified_at: str
    permissions: str | None
    owner: str | None
    is_symlink: bool
    symlink_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "permissions": self.permissions,
            "owner": self.owner,
            "is_symlink": self.is_symlink,
            "symlink_target": self.symlink_target,
        }


@dataclass(frozen=True)
class DuplicateGroup:
    """A set of files sharing identical content (FR-008)."""

    digest: str
    size_bytes: int
    paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "paths": list(self.paths),
        }


@dataclass(frozen=True)
class ConversionResult:
    """The outcome of a guarded write command (FR-010 through FR-013, FR-018)."""

    path: str
    destination: str
    in_place: bool
    backup_path: str | None = None
    lossless: bool = True
    lossy_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping."""
        return {
            "path": self.path,
            "destination": self.destination,
            "in_place": self.in_place,
            "backup_path": self.backup_path,
            "lossless": self.lossless,
            "lossy_reason": self.lossy_reason,
        }
