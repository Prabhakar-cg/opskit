"""In-house file-type identification: magic-byte signatures + structural sniffing.

See specs/007-file-operations/research.md R5.
"""

from __future__ import annotations

from pathlib import Path

from opskit.file import formats
from opskit.file.errors import InvalidContent
from opskit.file.models import StructuredFormat

UNDETERMINED = "undetermined"

# Ordered binary magic-byte prefixes, checked before any structural sniffing.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),  # also the container for docx/xlsx/jar/apk (zip-family)
    (b"PK\x05\x06", "zip"),  # empty zip archive
    (b"\x1f\x8b", "gzip"),
    (b"\x7fELF", "elf"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"BM", "bmp"),
)


def _sniff_binary(prefix: bytes) -> str | None:
    for signature, type_name in _SIGNATURES:
        if prefix.startswith(signature):
            return type_name
    return None


def _parses_as(path: Path, fmt: StructuredFormat) -> bool:
    try:
        result = formats.load(path, format=fmt)
    except InvalidContent:
        return False
    if fmt is StructuredFormat.YAML:
        # A bare scalar ("just some prose") is technically valid YAML — require an
        # actual mapping/sequence, matching formats.detect_from_content's own guard.
        return isinstance(result.data, (dict, list))
    return True


def _sniff_structural(path: Path, raw: bytes) -> str | None:
    stripped = raw.lstrip()
    if stripped.startswith(b"<") and _parses_as(path, StructuredFormat.XML):
        return "xml"
    if stripped[:1] in (b"{", b"[") and _parses_as(path, StructuredFormat.JSON):
        return "json"

    # TOML/YAML have no unambiguous leading marker; try both and report either one
    # match, or both when a file is genuinely valid under both grammars (research R5).
    matches = [
        fmt.value
        for fmt in (StructuredFormat.TOML, StructuredFormat.YAML)
        if _parses_as(path, fmt)
    ]
    return "/".join(matches) if matches else None


def identify_type(path: str | Path) -> str:
    """Best-guess type of ``path``'s content, independent of its extension (FR-004).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: the file could not be read.
    """
    resolved = Path(path)
    raw = formats.read_bytes(resolved)
    binary = _sniff_binary(raw[:8])
    if binary is not None:
        return binary
    structural = _sniff_structural(resolved, raw)
    if structural is not None:
        return structural
    return UNDETERMINED
