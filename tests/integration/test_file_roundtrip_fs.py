"""End-to-end filesystem tests for `opskit file`, driven through the real CLI.

Real temp files (no mocking): a full CLI invocation round-trip for `convert`, and
`--in-place --backup` end-to-end for `convert`/`eol`/`pretty`/`reencode` — proves the guarded
write path works through the whole stack (parsing, atomic write, backup) rather than
unit-testing its pieces in isolation (that's tests/unit/test_file_api.py and
test_file_atomic.py). Also covers an interrupted in-place write through the full CLI (SC-003).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from opskit.cli import app

runner = CliRunner()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_convert_json_to_yaml_to_json_round_trip_end_to_end(tmp_path: Path):
    data = {"name": "svc", "port": 8080, "tags": ["a", "b"]}
    source = tmp_path / "config.json"
    source.write_text(json.dumps(data), encoding="utf-8")

    yaml_out = tmp_path / "config.yaml"
    result = runner.invoke(
        app, ["file", "convert", str(source), "--to", "yaml", "--output", str(yaml_out)]
    )
    assert result.exit_code == 0
    assert yaml_out.exists()

    json_out = tmp_path / "roundtrip.json"
    result = runner.invoke(
        app,
        ["file", "convert", str(yaml_out), "--to", "json", "--output", str(json_out)],
    )
    assert result.exit_code == 0
    assert json.loads(json_out.read_text(encoding="utf-8")) == data

    diff_result = runner.invoke(app, ["file", "validate", str(json_out)])
    assert diff_result.exit_code == 0


def test_convert_in_place_backup_end_to_end(tmp_path: Path):
    source = tmp_path / "config.json"
    original_bytes = b'{"a": 1}'
    source.write_bytes(original_bytes)

    result = runner.invoke(
        app, ["file", "convert", str(source), "--to", "yaml", "--in-place", "--backup"]
    )
    assert result.exit_code == 0

    backup = tmp_path / "config.json.bak"
    assert backup.read_bytes() == original_bytes
    assert source.read_bytes().startswith(b"a: 1")


def test_eol_non_destructive_then_in_place_backup_end_to_end(tmp_path: Path):
    source = tmp_path / "script.sh"
    source.write_bytes(b"echo a\r\necho b\n")
    before = _sha256(source)

    non_destructive = runner.invoke(app, ["file", "eol", str(source), "--to", "lf"])
    assert non_destructive.exit_code == 0
    assert non_destructive.stdout_bytes == b"echo a\necho b\n"
    assert _sha256(source) == before  # SC-002: source untouched by the dry run

    in_place = runner.invoke(
        app, ["file", "eol", str(source), "--to", "lf", "--in-place", "--backup"]
    )
    assert in_place.exit_code == 0

    backup = tmp_path / "script.sh.bak"
    assert backup.read_bytes() == b"echo a\r\necho b\n"
    assert source.read_bytes() == b"echo a\necho b\n"


def test_pretty_in_place_backup_end_to_end(tmp_path: Path):
    source = tmp_path / "data.json"
    source.write_text('{"b":1,"a":2}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["file", "pretty", str(source), "--sort-keys", "--in-place", "--backup"],
    )
    assert result.exit_code == 0

    backup = tmp_path / "data.json.bak"
    assert backup.read_text(encoding="utf-8") == '{"b":1,"a":2}'
    reformatted = json.loads(source.read_text(encoding="utf-8"))
    assert reformatted == {"a": 2, "b": 1}


def test_reencode_in_place_backup_end_to_end(tmp_path: Path):
    source = tmp_path / "legacy.txt"
    original_bytes = "café".encode("latin-1")
    source.write_bytes(original_bytes)

    result = runner.invoke(
        app,
        [
            "file",
            "reencode",
            str(source),
            "--from",
            "latin-1",
            "--to",
            "utf-8",
            "--in-place",
            "--backup",
        ],
    )
    assert result.exit_code == 0

    backup = tmp_path / "legacy.txt.bak"
    assert backup.read_bytes() == original_bytes
    assert source.read_text(encoding="utf-8") == "café"


def test_convert_in_place_fault_injection_leaves_source_intact(
    tmp_path: Path, monkeypatch
):
    """An interrupted in-place write must never leave the target partially written (SC-003)."""
    source = tmp_path / "config.json"
    original_bytes = b'{"a": 1}'
    source.write_bytes(original_bytes)

    def _boom(self, target):
        raise OSError("simulated failure mid-write")

    monkeypatch.setattr(Path, "replace", _boom)
    result = runner.invoke(
        app, ["file", "convert", str(source), "--to", "yaml", "--in-place"]
    )

    assert result.exit_code == 15  # FilePermissionDenied
    assert source.read_bytes() == original_bytes
