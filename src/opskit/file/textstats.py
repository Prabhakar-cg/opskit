"""Line-ending and text-encoding detection/normalization/transcoding (research R4).

Line-ending detection streams the file in fixed-size chunks so memory stays bounded
regardless of file size (spec edge case); normalization/transcoding operate on already-read
content, since the guarded write commands already hold the full file in memory to write it.
"""

from __future__ import annotations

import re
from pathlib import Path

import charset_normalizer

from opskit.core.errors import UsageError
from opskit.file import formats
from opskit.file.errors import InvalidContent
from opskit.file.models import EncodingReport, LineEnding, LineEndingReport

_LINE_ENDING_RE = re.compile(rb"\r\n|\r|\n")
_CHUNK_SIZE = 1024 * 1024


class _Counts:
    """Mutable running tally threaded through the chunked scan in :func:`detect_line_endings`."""

    __slots__ = ("cr", "crlf", "lf", "pending_cr")

    def __init__(self) -> None:
        r"""Start every counter at zero with no carried-over trailing ``\r``."""
        self.crlf = self.lf = self.cr = 0
        self.pending_cr = False


def _count_chunk(chunk: bytes, counts: _Counts) -> None:
    r"""Update ``counts`` in place with the line endings found in one chunk.

    A ``\r`` at the very end of a chunk is ambiguous until the next chunk's first byte is
    seen (it may be the start of a split ``\r\n``), so it's carried over via ``pending_cr``
    instead of being counted immediately.
    """
    if counts.pending_cr:
        if chunk[:1] == b"\n":
            counts.crlf += 1
            chunk = chunk[1:]
        else:
            counts.cr += 1
        counts.pending_cr = False
    if chunk.endswith(b"\r"):
        counts.pending_cr = True
        chunk = chunk[:-1]
    for match in _LINE_ENDING_RE.finditer(chunk):
        token = match.group()
        if token == b"\r\n":
            counts.crlf += 1
        elif token == b"\n":
            counts.lf += 1
        else:
            counts.cr += 1


def detect_line_endings(path: str | Path) -> LineEndingReport:
    """Stream ``path`` counting CRLF/LF/lone-CR occurrences (FR-001).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: the file could not be read.
    """
    resolved = Path(path)
    counts = _Counts()
    with formats.open_binary(resolved) as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            _count_chunk(chunk, counts)
    if counts.pending_cr:
        counts.cr += 1

    mixed = sum(1 for count in (counts.crlf, counts.lf, counts.cr) if count > 0) > 1
    return LineEndingReport(
        path=str(path),
        crlf_count=counts.crlf,
        lf_count=counts.lf,
        cr_count=counts.cr,
        mixed=mixed,
    )


def normalize_line_endings(content: bytes, to: LineEnding) -> bytes:
    """Rewrite every line ending in ``content`` to the ``to`` style (FR-011)."""
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if to is LineEnding.CRLF:
        return normalized.replace(b"\n", b"\r\n")
    if to is LineEnding.CR:
        return normalized.replace(b"\n", b"\r")
    return normalized


# Ordered longest-prefix-first so the 4-byte UTF-32 BOMs are matched before the 2-byte
# UTF-16 BOMs they otherwise share a prefix with (research R4). ``strip`` marks whether
# the BOM bytes must be sliced off before decoding with that codec — ``utf-8-sig``
# strips its own BOM, the explicit-endianness UTF-16/32 codecs do not.
_BOM_TABLE: tuple[tuple[bytes, str, bool], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le", True),
    (b"\x00\x00\xfe\xff", "utf-32-be", True),
    (b"\xef\xbb\xbf", "utf-8-sig", False),
    (b"\xff\xfe", "utf-16-le", True),
    (b"\xfe\xff", "utf-16-be", True),
)


def _detect_bom(raw: bytes) -> tuple[str, bytes, bool] | None:
    for prefix, name, strip in _BOM_TABLE:
        if raw.startswith(prefix):
            return name, prefix, strip
    return None


def _probe(raw: bytes) -> tuple[str, bytes, float | None, bool]:
    """Return ``(encoding, decodable_payload, confidence, has_bom)`` for ``raw``.

    ``decodable_payload`` is ``raw`` with any non-self-stripping BOM sliced off, so
    ``decodable_payload.decode(encoding)`` reproduces the file's actual text — the single
    place this feature decides "what encoding is this," reused by both the read-only
    :func:`detect_encoding_bytes` report and :func:`decode_auto`'s actual decode.
    """
    bom_match = _detect_bom(raw)
    if bom_match is not None:
        encoding, prefix, strip = bom_match
        payload = raw[len(prefix) :] if strip else raw
        return encoding, payload, None, True

    best = charset_normalizer.from_bytes(raw).best()
    if best is None:
        return "unknown", raw, None, False
    confidence = max(0.0, min(1.0, 1.0 - best.chaos))
    return best.encoding, raw, confidence, False


def detect_encoding_bytes(raw: bytes, *, path: str = "") -> EncodingReport:
    """Detect the encoding of already-read ``raw`` bytes (FR-002).

    BOM byte sequences are checked first; without one, ``charset-normalizer`` supplies a
    best-guess encoding and a confidence signal derived from its "mess ratio" (lower chaos
    = higher confidence). ``path`` is cosmetic only, for embedding in the returned report.
    """
    encoding, payload, confidence, has_bom = _probe(raw)
    if encoding == "unknown":
        invalid_sequences = True
    else:
        try:
            payload.decode(encoding)
        except UnicodeDecodeError:
            invalid_sequences = True
        else:
            invalid_sequences = False
    return EncodingReport(
        path=path,
        encoding=encoding,
        has_bom=has_bom,
        confidence=confidence,
        invalid_sequences=invalid_sequences,
    )


def detect_encoding(path: str | Path) -> EncodingReport:
    """Detect ``path``'s text encoding, BOM presence, and invalid-sequence flag (FR-002).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: the file could not be read.
    """
    resolved = Path(path)
    raw = formats.read_bytes(resolved)
    return detect_encoding_bytes(raw, path=str(path))


def decode_auto(raw: bytes) -> str:
    """Decode ``raw`` as text via the same detection :func:`detect_encoding_bytes` uses.

    BOM-first, then charset-normalizer, correctly stripping any BOM before decoding.

    Raises:
        InvalidContent: the encoding could not be determined, or decoding fails.
    """
    encoding, payload, _confidence, _has_bom = _probe(raw)
    if encoding == "unknown":
        raise InvalidContent(
            "cannot determine the text encoding of this content",
            hint="pass --from to declare it explicitly",
        )
    try:
        return payload.decode(encoding)
    except UnicodeDecodeError as exc:
        raise InvalidContent(f"cannot decode content as {encoding}: {exc}") from exc


def transcode(content: bytes, *, from_encoding: str | None, to_encoding: str) -> bytes:
    """Decode ``content`` and re-encode as ``to_encoding`` (FR-012).

    ``from_encoding=None`` auto-detects the source encoding (BOM-first, then
    charset-normalizer) via :func:`decode_auto`; an explicit name is decoded directly.

    Raises:
        UsageError: ``from_encoding``/``to_encoding`` names an unknown codec.
        InvalidContent: ``content`` cannot be decoded using ``from_encoding`` (or
            auto-detection fails), or contains a character ``to_encoding`` cannot represent.
    """
    if from_encoding is None:
        text = decode_auto(content)
    else:
        try:
            text = content.decode(from_encoding)
        except LookupError as exc:
            raise UsageError(f"unknown encoding: {from_encoding}") from exc
        except UnicodeDecodeError as exc:
            raise InvalidContent(
                f"cannot decode content as {from_encoding}: {exc}",
                hint="use `opskit file encoding` to inspect the actual encoding, or pass a "
                "different --from",
            ) from exc

    try:
        return text.encode(to_encoding)
    except LookupError as exc:
        raise UsageError(f"unknown encoding: {to_encoding}") from exc
    except UnicodeEncodeError as exc:
        raise InvalidContent(
            f"cannot represent this content in {to_encoding}: {exc}"
        ) from exc
