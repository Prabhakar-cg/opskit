"""Tests for the file CLI: envelope shape, exit codes, batch semantics, human output."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.file.api import WriteOutcome
from opskit.file.errors import ClobberRefused, FileNotFoundOnDisk, FilePermissionDenied
from opskit.file.models import ConversionResult, StructuredFormat, ValidationResult

runner = CliRunner()


def _valid(path="/data/config.json"):
    return ValidationResult(path=path, format=StructuredFormat.JSON, valid=True)


def _invalid(path="/data/broken.json"):
    return ValidationResult(
        path=path,
        format=StructuredFormat.JSON,
        valid=False,
        error_line=1,
        error_column=8,
        error_message="trailing comma",
    )


def test_validate_json_envelope_shape_valid(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.validate", lambda p, **kw: _valid(p))
    result = runner.invoke(app, ["file", "validate", "config.json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.validate"
    assert payload["query"]["path"] == "config.json"
    assert payload["result"]["valid"] is True
    assert payload["error"] is None


def test_validate_invalid_content_exit_code(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.validate", lambda p, **kw: _invalid(p))
    result = runner.invoke(app, ["file", "validate", "broken.json", "--json"])
    assert result.exit_code == 21
    payload = json.loads(result.stdout)
    assert payload["result"]["valid"] is False
    assert payload["result"]["error_line"] == 1


def test_validate_batch_mixed_outcomes_every_target_in_output(monkeypatch):
    def fake(p, **kw):
        if p == "missing.json":
            raise FileNotFoundOnDisk("file not found: missing.json")
        if p == "broken.json":
            return _invalid(p)
        return _valid(p)

    monkeypatch.setattr("opskit.file.cli.api.validate", fake)
    result = runner.invoke(
        app, ["file", "validate", "good.json", "broken.json", "missing.json", "--jsonl"]
    )
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [line["query"]["path"] for line in lines] == [
        "good.json",
        "broken.json",
        "missing.json",
    ]
    assert lines[0]["result"]["valid"] is True
    assert lines[1]["result"]["valid"] is False
    assert lines[2]["result"] is None
    assert lines[2]["error"]["code"] == "file_not_found"
    assert result.exit_code == 7  # PARTIAL: mixed outcome classes


def test_validate_all_invalid_uniform_exit_code(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.validate", lambda p, **kw: _invalid(p))
    result = runner.invoke(app, ["file", "validate", "a.json", "b.json", "--json"])
    assert result.exit_code == 21


def test_validate_human_output_smoke(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.validate", lambda p, **kw: _valid(p))
    result = runner.invoke(app, ["file", "validate", "config.json", "--no-color"])
    assert result.exit_code == 0
    assert "config.json" in result.stdout
    assert "valid" in result.stdout


def test_validate_no_targets_is_usage_error():
    result = runner.invoke(app, ["file", "validate"])
    assert result.exit_code == 2


def test_validate_format_option_passed_through(monkeypatch):
    captured = {}

    def fake(p, **kw):
        captured.update(kw)
        return _valid(p)

    monkeypatch.setattr("opskit.file.cli.api.validate", fake)
    result = runner.invoke(app, ["file", "validate", "config.txt", "--format", "yaml"])
    assert result.exit_code == 0
    assert captured["format"] is StructuredFormat.YAML


def _write_outcome(path="/data/script.sh", *, destination=None, backup_path=None):
    return WriteOutcome(
        result=ConversionResult(
            path=path,
            destination=destination or path,
            in_place=destination is None,
            backup_path=backup_path,
        ),
        stdout_content=None,
    )


def test_eol_missing_to_is_usage_error():
    result = runner.invoke(app, ["file", "eol", "script.sh"])
    assert result.exit_code == 2


def test_eol_output_with_in_place_is_usage_error():
    result = runner.invoke(
        app, ["file", "eol", "script.sh", "--to", "lf", "--output", "x", "--in-place"]
    )
    assert result.exit_code == 2


def test_eol_backup_without_in_place_is_usage_error():
    result = runner.invoke(app, ["file", "eol", "script.sh", "--to", "lf", "--backup"])
    assert result.exit_code == 2


def test_eol_output_with_multiple_targets_is_usage_error():
    result = runner.invoke(
        app,
        ["file", "eol", "a.sh", "b.sh", "--to", "lf", "--output", "x"],
    )
    assert result.exit_code == 2


def test_eol_in_place_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.eol",
        lambda p, **kw: _write_outcome(p, backup_path=f"{p}.bak"),
    )
    result = runner.invoke(
        app,
        ["file", "eol", "script.sh", "--to", "lf", "--in-place", "--backup", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.eol"
    assert payload["query"] == {
        "path": "script.sh",
        "to": "lf",
        "in_place": True,
        "backup": True,
    }
    assert payload["result"]["backup_path"] == "script.sh.bak"


def test_eol_non_destructive_writes_bytes_to_stdout(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.eol",
        lambda p, **kw: WriteOutcome(
            result=ConversionResult(path=p, destination="-", in_place=False),
            stdout_content=b"a\nb\n",
        ),
    )
    result = runner.invoke(app, ["file", "eol", "script.sh", "--to", "lf"])
    assert result.exit_code == 0
    assert result.stdout_bytes == b"a\nb\n"


def test_eol_not_found_exit_code(monkeypatch):
    def fake(p, **kw):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.eol", fake)
    result = runner.invoke(app, ["file", "eol", "missing.sh", "--to", "lf", "--json"])
    assert result.exit_code == 16


def test_eol_permission_denied_exit_code(monkeypatch):
    def fake(p, **kw):
        raise FilePermissionDenied(f"permission denied: {p}")

    monkeypatch.setattr("opskit.file.cli.api.eol", fake)
    result = runner.invoke(app, ["file", "eol", "locked.sh", "--to", "lf", "--json"])
    assert result.exit_code == 15


def test_eol_clobber_refused_exit_code(monkeypatch):
    def fake(p, **kw):
        raise ClobberRefused(f"refusing to overwrite: {p}.bak")

    monkeypatch.setattr("opskit.file.cli.api.eol", fake)
    result = runner.invoke(
        app,
        ["file", "eol", "script.sh", "--to", "lf", "--in-place", "--backup", "--json"],
    )
    assert result.exit_code == 22


def test_eol_batch_mixed_outcomes_partial_exit(monkeypatch):
    def fake(p, **kw):
        if p == "missing.sh":
            raise FileNotFoundOnDisk(f"file not found: {p}")
        return _write_outcome(p)

    monkeypatch.setattr("opskit.file.cli.api.eol", fake)
    result = runner.invoke(
        app, ["file", "eol", "good.sh", "missing.sh", "--to", "lf", "--jsonl"]
    )
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["result"] is not None
    assert lines[1]["result"] is None
    assert result.exit_code == 7


def test_lineendings_json_envelope(monkeypatch):
    from opskit.file.models import LineEndingReport

    monkeypatch.setattr(
        "opskit.file.cli.api.lineendings",
        lambda p: LineEndingReport(
            path=p, crlf_count=1, lf_count=2, cr_count=0, mixed=True
        ),
    )
    result = runner.invoke(app, ["file", "lineendings", "mixed.txt", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.lineendings"
    assert payload["result"]["mixed"] is True


def test_lineendings_not_found_exit_code(monkeypatch):
    def fake(p):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.lineendings", fake)
    result = runner.invoke(app, ["file", "lineendings", "missing.txt", "--json"])
    assert result.exit_code == 16


def test_lineendings_human_output_smoke(monkeypatch):
    from opskit.file.models import LineEndingReport

    monkeypatch.setattr(
        "opskit.file.cli.api.lineendings",
        lambda p: LineEndingReport(
            path=p, crlf_count=0, lf_count=3, cr_count=0, mixed=False
        ),
    )
    result = runner.invoke(app, ["file", "lineendings", "unix.txt", "--no-color"])
    assert result.exit_code == 0
    assert "unix.txt" in result.stdout
    assert "consistent" in result.stdout


def test_lineendings_human_output_error_smoke(monkeypatch):
    def fake(p):
        raise FileNotFoundOnDisk(f"file not found: {p}", hint="check the path")

    monkeypatch.setattr("opskit.file.cli.api.lineendings", fake)
    result = runner.invoke(app, ["file", "lineendings", "missing.txt", "--no-color"])
    assert result.exit_code == 16
    assert "missing.txt" in result.output


def test_eol_human_output_in_place_smoke(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.eol",
        lambda p, **kw: _write_outcome(p, backup_path=f"{p}.bak"),
    )
    result = runner.invoke(
        app,
        [
            "file",
            "eol",
            "script.sh",
            "--to",
            "lf",
            "--in-place",
            "--backup",
            "--no-color",
        ],
    )
    assert result.exit_code == 0
    assert "script.sh" in result.stdout
    assert "backup" in result.stdout


def test_eol_human_output_error_smoke(monkeypatch):
    def fake(p, **kw):
        raise FileNotFoundOnDisk(f"file not found: {p}", hint="check the path")

    monkeypatch.setattr("opskit.file.cli.api.eol", fake)
    result = runner.invoke(
        app, ["file", "eol", "missing.sh", "--to", "lf", "--no-color"]
    )
    assert result.exit_code == 16
    assert "missing.sh" in result.output
