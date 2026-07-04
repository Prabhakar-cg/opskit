"""Versioned JSON output envelope shared by every command.

The envelope shape is the public ``--json`` contract (contracts/json-envelope.md); its schema
changes are governed by SemVer. The current ``schema_version`` is ``"1"``.
"""

from __future__ import annotations

import json
from typing import Any

from opskit.core.errors import OpskitError

SCHEMA_VERSION = "1"


def build_envelope(
    *,
    command: str,
    query: dict[str, Any],
    result: dict[str, Any] | None,
    error: OpskitError | None,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Assemble the versioned envelope for a single command invocation."""
    error_obj: dict[str, Any] | None = None
    if error is not None:
        error_obj = {"code": error.code, "message": error.message, "hint": error.hint}
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "query": query,
        "result": result,
        "error": error_obj,
        "elapsed_ms": round(elapsed_ms, 3),
    }


def to_json(envelope: dict[str, Any], *, indent: int | None = 2) -> str:
    """Serialize an envelope to a JSON string (single object)."""
    return json.dumps(envelope, indent=indent)
