"""Line-ending and text-encoding detection/normalization/transcoding (research R4).

Line-ending detection streams the file in fixed-size chunks so memory stays bounded
regardless of file size (spec edge case); normalization/transcoding operate on already-read
content, since the guarded write commands already hold the full file in memory to write it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import BinaryIO

from opskit.file.errors import FileError, FileNotFoundOnDisk, FilePermissionDenied
from opskit.file.models import LineEnding, LineEndingReport

_LINE_ENDING_RE = re.compile(rb"\r\n|\r|\n")
_CHUNK_SIZE = 1024 * 1024


class _Counts:
    """Mutable running tally threaded through the chunked scan in :func:`detect_line_endings`."""

    __slots__ = ("cr", "crlf", "lf", "pending_cr")

    def __init__(self) -> None:
        r"""Start every counter at zero with no carried-over trailing ``\r``."""
        self.crlf = self.lf = self.cr = 0
        self.pending_cr = False


def _open_binary(path: Path) -> BinaryIO:
    """Open ``path`` for binary reads, normalizing OS errors into the typed hierarchy."""
    try:
        return path.open("rb")
    except FileNotFoundError as exc:
        raise FileNotFoundOnDisk(
            f"file not found: {path}", hint="check the path and try again"
        ) from exc
    except IsADirectoryError as exc:
        raise FileNotFoundOnDisk(
            f"not a file: {path}", hint="pass a file path, not a directory"
        ) from exc
    except PermissionError as exc:
        raise FilePermissionDenied(f"permission denied reading {path}") from exc
    except OSError as exc:
        raise FileError(f"cannot read {path}: {exc}") from exc


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
    with _open_binary(resolved) as handle:
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
