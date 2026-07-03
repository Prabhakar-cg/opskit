"""Contract tests for the versioned --json envelope."""

from __future__ import annotations

import json

from opskit.core.result import SCHEMA_VERSION, build_envelope, to_json
from opskit.dns.errors import NxDomain


def test_envelope_shape_and_roundtrip():
    envelope = build_envelope(
        command="dns.lookup",
        query={"target": "example.com", "record_types": ["A"]},
        result={"outcome": "ok", "records": []},
        error=None,
        elapsed_ms=1.2345,
    )
    for key in ("schema_version", "command", "query", "result", "error", "elapsed_ms"):
        assert key in envelope
    assert envelope["schema_version"] == SCHEMA_VERSION
    assert envelope["error"] is None
    assert envelope["elapsed_ms"] == 1.234  # rounded to 3 dp
    assert json.loads(to_json(envelope)) == envelope


def test_envelope_serializes_errors():
    envelope = build_envelope(
        command="dns.lookup",
        query={},
        result=None,
        error=NxDomain("no such name", hint="check the spelling"),
        elapsed_ms=0.0,
    )
    assert envelope["result"] is None
    assert envelope["error"] == {
        "code": "nxdomain",
        "message": "no such name",
        "hint": "check the spelling",
    }
