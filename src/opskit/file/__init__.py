"""File operations — importable API and CLI sub-app.

Read-only file diagnostics (line-ending/encoding detection, structured-format validation,
type sniffing, checksums, structural diff, stat, duplicate detection) plus guarded, opt-in
write commands (format conversion, line-ending normalization, encoding transcoding,
pretty-printing) under constitution Art. X's file-utility exception (v1.3.0).
"""

from __future__ import annotations

from opskit.file.api import (
    convert,
    diff,
    encoding,
    eol,
    find_duplicates,
    hash_files,
    identify,
    lineendings,
    pretty,
    reencode,
    stat_files,
    validate,
)
from opskit.file.errors import (
    ClobberRefused,
    FileError,
    FileNotFoundOnDisk,
    FilePermissionDenied,
    InvalidContent,
)
from opskit.file.models import (
    ChecksumResult,
    ConversionResult,
    DiffEntry,
    DuplicateGroup,
    EncodingReport,
    IdentificationResult,
    LineEnding,
    LineEndingReport,
    StatResult,
    StructuralDiffResult,
    StructuredFormat,
    ValidationResult,
)

__all__ = [
    "ChecksumResult",
    "ClobberRefused",
    "ConversionResult",
    "DiffEntry",
    "DuplicateGroup",
    "EncodingReport",
    "FileError",
    "FileNotFoundOnDisk",
    "FilePermissionDenied",
    "IdentificationResult",
    "InvalidContent",
    "LineEnding",
    "LineEndingReport",
    "StatResult",
    "StructuralDiffResult",
    "StructuredFormat",
    "ValidationResult",
    "convert",
    "diff",
    "encoding",
    "eol",
    "find_duplicates",
    "hash_files",
    "identify",
    "lineendings",
    "pretty",
    "reencode",
    "stat_files",
    "validate",
]
