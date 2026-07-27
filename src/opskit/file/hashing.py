"""Streaming checksums and size-then-hash duplicate-file detection.

See specs/007-file-operations/research.md R6.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from opskit.core.errors import UsageError
from opskit.file import formats

_CHUNK_SIZE = 1024 * 1024

_ALGORITHMS = {
    "sha256": hashlib.sha256,
    "sha1": hashlib.sha1,
    "md5": hashlib.md5,
}


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
