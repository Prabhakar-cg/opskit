"""Tests for the file CLI: envelope shape, exit codes, batch semantics, human output."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.file.api import WriteOutcome
from opskit.file.errors import (
    ClobberRefused,
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
    StatResult,
    StructuralDiffResult,
    StructuredFormat,
    ValidationResult,
)

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


def _convert_outcome(path="/data/config.json", *, destination="-", lossless=True):
    return WriteOutcome(
        result=ConversionResult(
            path=path,
            destination=destination,
            in_place=destination == path,
            lossless=lossless,
        ),
        stdout_content=b"name: svc\n" if destination == "-" else None,
    )


def test_convert_missing_to_is_usage_error():
    result = runner.invoke(app, ["file", "convert", "config.json"])
    assert result.exit_code == 2


def test_convert_output_with_in_place_is_usage_error():
    result = runner.invoke(
        app,
        [
            "file",
            "convert",
            "config.json",
            "--to",
            "yaml",
            "--output",
            "x",
            "--in-place",
        ],
    )
    assert result.exit_code == 2


def test_convert_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.convert",
        lambda p, **kw: _convert_outcome(p, destination="out.yaml"),
    )
    result = runner.invoke(
        app,
        [
            "file",
            "convert",
            "config.json",
            "--to",
            "yaml",
            "--output",
            "out.yaml",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.convert"
    assert payload["query"]["to"] == "yaml"
    assert payload["result"]["destination"] == "out.yaml"


def test_convert_stdout_content(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.convert", lambda p, **kw: _convert_outcome(p)
    )
    result = runner.invoke(app, ["file", "convert", "config.json", "--to", "yaml"])
    assert result.exit_code == 0
    assert result.stdout_bytes == b"name: svc\n"


def test_convert_usage_error_from_api_json_envelope(monkeypatch):
    def fake(p, **kw):
        raise UsageError(
            "source is already json; --to must differ from the source format"
        )

    monkeypatch.setattr("opskit.file.cli.api.convert", fake)
    result = runner.invoke(
        app, ["file", "convert", "config.json", "--to", "json", "--json"]
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result"] is None
    assert payload["error"]["code"] == "usage_error"


def test_convert_invalid_content_exit_code(monkeypatch):
    def fake(p, **kw):
        raise InvalidContent(f"invalid JSON in {p}")

    monkeypatch.setattr("opskit.file.cli.api.convert", fake)
    result = runner.invoke(
        app, ["file", "convert", "broken.json", "--to", "yaml", "--json"]
    )
    assert result.exit_code == 21


def test_convert_clobber_refused_exit_code(monkeypatch):
    def fake(p, **kw):
        raise ClobberRefused(f"refusing to overwrite: {p}.bak")

    monkeypatch.setattr("opskit.file.cli.api.convert", fake)
    result = runner.invoke(
        app,
        [
            "file",
            "convert",
            "config.json",
            "--to",
            "yaml",
            "--in-place",
            "--backup",
            "--json",
        ],
    )
    assert result.exit_code == 22


def test_convert_human_output_error_no_json_mixing(monkeypatch):
    def fake(p, **kw):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.convert", fake)
    result = runner.invoke(app, ["file", "convert", "missing.json", "--to", "yaml"])
    assert result.exit_code == 16
    assert "missing.json" in result.output


def test_pretty_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.pretty",
        lambda p, **kw: _convert_outcome(p, destination=p),
    )
    result = runner.invoke(
        app, ["file", "pretty", "data.json", "--sort-keys", "--in-place", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.pretty"
    assert payload["query"]["sort_keys"] is True
    assert payload["query"]["indent"] == 2


def test_pretty_toml_usage_error_from_api(monkeypatch):
    def fake(p, **kw):
        raise UsageError("TOML has no free-form pretty option")

    monkeypatch.setattr("opskit.file.cli.api.pretty", fake)
    result = runner.invoke(app, ["file", "pretty", "data.toml", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "usage_error"


def test_pretty_backup_without_in_place_is_usage_error():
    result = runner.invoke(app, ["file", "pretty", "data.json", "--backup"])
    assert result.exit_code == 2


def test_pretty_stdout_content(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.pretty", lambda p, **kw: _convert_outcome(p)
    )
    result = runner.invoke(app, ["file", "pretty", "data.json"])
    assert result.exit_code == 0
    assert result.stdout_bytes == b"name: svc\n"


def _identify(
    path="/data/config.json", *, detected="json", extension="json", matches=True
):
    return IdentificationResult(
        path=path,
        detected_type=detected,
        extension=extension,
        extension_matches=matches,
    )


def test_identify_json_envelope(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.identify", _identify)
    result = runner.invoke(app, ["file", "identify", "config.json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.identify"
    assert payload["result"]["detected_type"] == "json"


def test_identify_mismatch_still_exit_zero(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.identify",
        lambda p: _identify(p, extension="txt", matches=False),
    )
    result = runner.invoke(app, ["file", "identify", "renamed.txt", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["extension_matches"] is False


def test_identify_human_output_smoke(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.identify", _identify)
    result = runner.invoke(app, ["file", "identify", "config.json", "--no-color"])
    assert result.exit_code == 0
    assert "config.json" in result.stdout
    assert "json" in result.stdout


def test_identify_not_found_exit_code(monkeypatch):
    def fake(p):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.identify", fake)
    result = runner.invoke(app, ["file", "identify", "missing.txt", "--json"])
    assert result.exit_code == 16


def test_identify_batch_mixed_outcomes(monkeypatch):
    def fake(p):
        if p == "missing.txt":
            raise FileNotFoundOnDisk(f"file not found: {p}")
        return _identify(p)

    monkeypatch.setattr("opskit.file.cli.api.identify", fake)
    result = runner.invoke(
        app, ["file", "identify", "good.json", "missing.txt", "--jsonl"]
    )
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["result"] is not None
    assert lines[1]["result"] is None
    assert result.exit_code == 7


def test_hash_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.hash_files",
        lambda p, algo: ChecksumResult(path=p, algorithm=algo, digest="deadbeef"),
    )
    result = runner.invoke(app, ["file", "hash", "artifact.tar.gz", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.hash"
    assert payload["query"]["algo"] == "sha256"
    assert payload["result"]["digest"] == "deadbeef"


def test_hash_explicit_algo_passed_through(monkeypatch):
    captured = {}

    def fake(p, algo):
        captured["algo"] = algo
        return ChecksumResult(path=p, algorithm=algo, digest="ab")

    monkeypatch.setattr("opskit.file.cli.api.hash_files", fake)
    result = runner.invoke(
        app, ["file", "hash", "artifact.tar.gz", "--algo", "md5", "--json"]
    )
    assert result.exit_code == 0
    assert captured["algo"] == "md5"


def test_hash_unsupported_algo_is_usage_error():
    result = runner.invoke(app, ["file", "hash", "artifact.tar.gz", "--algo", "sha512"])
    assert result.exit_code == 2


def test_hash_human_output_smoke(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.hash_files",
        lambda p, algo: ChecksumResult(path=p, algorithm=algo, digest="deadbeef"),
    )
    result = runner.invoke(app, ["file", "hash", "artifact.tar.gz", "--no-color"])
    assert result.exit_code == 0
    assert "deadbeef" in result.stdout


def test_hash_not_found_exit_code(monkeypatch):
    def fake(p, algo):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.hash_files", fake)
    result = runner.invoke(app, ["file", "hash", "missing.tar.gz", "--json"])
    assert result.exit_code == 16


def _encoding(
    path="/data/legacy.txt", *, encoding="utf-8", has_bom=False, confidence=1.0
):
    return EncodingReport(
        path=path,
        encoding=encoding,
        has_bom=has_bom,
        confidence=confidence,
        invalid_sequences=False,
    )


def test_encoding_json_envelope(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.encoding", _encoding)
    result = runner.invoke(app, ["file", "encoding", "legacy.txt", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.encoding"
    assert payload["result"]["encoding"] == "utf-8"


def test_encoding_human_output_smoke(monkeypatch):
    monkeypatch.setattr("opskit.file.cli.api.encoding", _encoding)
    result = runner.invoke(app, ["file", "encoding", "legacy.txt", "--no-color"])
    assert result.exit_code == 0
    assert "legacy.txt" in result.stdout
    assert "utf-8" in result.stdout


def test_encoding_not_found_exit_code(monkeypatch):
    def fake(p):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.encoding", fake)
    result = runner.invoke(app, ["file", "encoding", "missing.txt", "--json"])
    assert result.exit_code == 16


def _reencode_outcome(path="/data/legacy.txt", *, destination="-"):
    return WriteOutcome(
        result=ConversionResult(
            path=path, destination=destination, in_place=destination == path
        ),
        stdout_content=b"cafe\n" if destination == "-" else None,
    )


def test_reencode_missing_to_is_usage_error():
    result = runner.invoke(app, ["file", "reencode", "legacy.txt"])
    assert result.exit_code == 2


def test_reencode_output_with_in_place_is_usage_error():
    result = runner.invoke(
        app,
        [
            "file",
            "reencode",
            "legacy.txt",
            "--to",
            "utf-8",
            "--output",
            "x",
            "--in-place",
        ],
    )
    assert result.exit_code == 2


def test_reencode_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.reencode",
        lambda p, **kw: _reencode_outcome(p, destination="out.txt"),
    )
    result = runner.invoke(
        app,
        [
            "file",
            "reencode",
            "legacy.txt",
            "--to",
            "utf-8",
            "--from",
            "latin-1",
            "--output",
            "out.txt",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.reencode"
    assert payload["query"]["to"] == "utf-8"
    assert payload["query"]["from"] == "latin-1"
    assert payload["result"]["destination"] == "out.txt"


def test_reencode_stdout_content(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.reencode", lambda p, **kw: _reencode_outcome(p)
    )
    result = runner.invoke(app, ["file", "reencode", "legacy.txt", "--to", "utf-8"])
    assert result.exit_code == 0
    assert result.stdout_bytes == b"cafe\n"


def test_reencode_invalid_content_exit_code(monkeypatch):
    def fake(p, **kw):
        raise InvalidContent("cannot represent this content in ascii")

    monkeypatch.setattr("opskit.file.cli.api.reencode", fake)
    result = runner.invoke(
        app, ["file", "reencode", "legacy.txt", "--to", "ascii", "--json"]
    )
    assert result.exit_code == 21


def test_reencode_clobber_refused_exit_code(monkeypatch):
    def fake(p, **kw):
        raise ClobberRefused(f"refusing to overwrite: {p}.bak")

    monkeypatch.setattr("opskit.file.cli.api.reencode", fake)
    result = runner.invoke(
        app,
        [
            "file",
            "reencode",
            "legacy.txt",
            "--to",
            "utf-8",
            "--in-place",
            "--backup",
            "--json",
        ],
    )
    assert result.exit_code == 22


def test_reencode_human_output_error_no_json_mixing(monkeypatch):
    def fake(p, **kw):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.reencode", fake)
    result = runner.invoke(app, ["file", "reencode", "missing.txt", "--to", "utf-8"])
    assert result.exit_code == 16
    assert "missing.txt" in result.output


def _diff_result(left="left.json", right="right.json", differences=()):
    return StructuralDiffResult(
        left_path=left, right_path=right, differences=differences
    )


def test_diff_json_envelope_equivalent(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.diff", lambda left, right, **kw: _diff_result()
    )
    result = runner.invoke(app, ["file", "diff", "left.json", "right.json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.diff"
    assert payload["result"]["equivalent"] is True
    assert payload["result"]["differences"] == []


def test_diff_json_envelope_with_differences(monkeypatch):
    entry = DiffEntry(key_path="server.port", left_value=80, right_value=443)
    monkeypatch.setattr(
        "opskit.file.cli.api.diff",
        lambda left, right, **kw: _diff_result(differences=(entry,)),
    )
    result = runner.invoke(app, ["file", "diff", "left.json", "right.json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["equivalent"] is False
    assert payload["result"]["differences"][0]["key_path"] == "server.port"
    assert payload["result"]["differences"][0]["left"] == {
        "present": True,
        "value": 80,
    }


def test_diff_human_output_smoke(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.diff", lambda left, right, **kw: _diff_result()
    )
    result = runner.invoke(app, ["file", "diff", "left.json", "right.json"])
    assert result.exit_code == 0
    assert "equivalent" in result.output


def test_diff_bad_format_is_usage_error():
    result = runner.invoke(
        app, ["file", "diff", "left.toml", "right.toml", "--format", "toml"]
    )
    assert result.exit_code == 2


def test_diff_not_found_exit_code(monkeypatch):
    def fake(left, right, **kw):
        raise FileNotFoundOnDisk(f"file not found: {left}")

    monkeypatch.setattr("opskit.file.cli.api.diff", fake)
    result = runner.invoke(
        app, ["file", "diff", "missing.json", "right.json", "--json"]
    )
    assert result.exit_code == 16


def test_diff_invalid_content_exit_code(monkeypatch):
    def fake(left, right, **kw):
        raise InvalidContent(f"invalid JSON in {left}")

    monkeypatch.setattr("opskit.file.cli.api.diff", fake)
    result = runner.invoke(app, ["file", "diff", "broken.json", "right.json", "--json"])
    assert result.exit_code == 21


def test_duplicates_json_envelope_no_duplicates(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.find_duplicates", lambda directory, **kw: ()
    )
    result = runner.invoke(app, ["file", "duplicates", "./vendor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.duplicates"
    assert payload["result"]["groups"] == []


def test_duplicates_json_envelope_with_groups(monkeypatch):
    group = DuplicateGroup(digest="abc123", size_bytes=10, paths=("a.txt", "b.txt"))
    monkeypatch.setattr(
        "opskit.file.cli.api.find_duplicates", lambda directory, **kw: (group,)
    )
    result = runner.invoke(
        app, ["file", "duplicates", "./vendor", "--recursive", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"]["recursive"] is True
    assert payload["result"]["groups"][0]["digest"] == "abc123"
    assert payload["result"]["groups"][0]["paths"] == ["a.txt", "b.txt"]


def test_duplicates_human_output_smoke(monkeypatch):
    group = DuplicateGroup(digest="abc123", size_bytes=10, paths=("a.txt", "b.txt"))
    monkeypatch.setattr(
        "opskit.file.cli.api.find_duplicates", lambda directory, **kw: (group,)
    )
    result = runner.invoke(app, ["file", "duplicates", "./vendor"])
    assert result.exit_code == 0
    assert "abc123" in result.output


def test_duplicates_no_duplicates_human_output_smoke(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.find_duplicates", lambda directory, **kw: ()
    )
    result = runner.invoke(app, ["file", "duplicates", "./vendor"])
    assert result.exit_code == 0
    assert "no duplicates" in result.output


def test_duplicates_not_found_exit_code(monkeypatch):
    def fake(directory, **kw):
        raise FileNotFoundOnDisk(f"directory not found: {directory}")

    monkeypatch.setattr("opskit.file.cli.api.find_duplicates", fake)
    result = runner.invoke(app, ["file", "duplicates", "missing-dir", "--json"])
    assert result.exit_code == 16


def test_duplicates_permission_denied_exit_code(monkeypatch):
    def fake(directory, **kw):
        raise FilePermissionDenied(f"permission denied listing {directory}")

    monkeypatch.setattr("opskit.file.cli.api.find_duplicates", fake)
    result = runner.invoke(app, ["file", "duplicates", "locked-dir", "--json"])
    assert result.exit_code == 15


def _stat_result(
    path="/data/config.json",
    *,
    permissions="644",
    owner="vscode",
    is_symlink=False,
    symlink_target=None,
):
    return StatResult(
        path=path,
        size_bytes=42,
        modified_at="2026-01-01T00:00:00+00:00",
        permissions=permissions,
        owner=owner,
        is_symlink=is_symlink,
        symlink_target=symlink_target,
    )


def test_stat_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.stat_files", lambda p, **kw: _stat_result(p)
    )
    result = runner.invoke(app, ["file", "stat", "config.json", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "file.stat"
    assert payload["result"]["size_bytes"] == 42
    assert payload["result"]["permissions"] == "644"


def test_stat_symlink_json_envelope(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.stat_files",
        lambda p, **kw: _stat_result(p, is_symlink=True, symlink_target="/real/target"),
    )
    result = runner.invoke(app, ["file", "stat", "link.json", "--json"])
    payload = json.loads(result.stdout)
    assert payload["result"]["is_symlink"] is True
    assert payload["result"]["symlink_target"] == "/real/target"


def test_stat_human_output_smoke(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.stat_files", lambda p, **kw: _stat_result(p)
    )
    result = runner.invoke(app, ["file", "stat", "config.json"])
    assert result.exit_code == 0
    assert "config.json" in result.output
    assert "size=42" in result.output


def test_stat_windows_none_fields_human_output(monkeypatch):
    monkeypatch.setattr(
        "opskit.file.cli.api.stat_files",
        lambda p, **kw: _stat_result(p, permissions=None, owner=None),
    )
    result = runner.invoke(app, ["file", "stat", "config.json"])
    assert result.exit_code == 0
    assert "—" in result.output


def test_stat_not_found_exit_code(monkeypatch):
    def fake(p, **kw):
        raise FileNotFoundOnDisk(f"file not found: {p}")

    monkeypatch.setattr("opskit.file.cli.api.stat_files", fake)
    result = runner.invoke(app, ["file", "stat", "missing.json", "--json"])
    assert result.exit_code == 16


def test_stat_batch_mixed_outcomes(monkeypatch):
    def fake(p, **kw):
        if p == "missing.json":
            raise FileNotFoundOnDisk(f"file not found: {p}")
        return _stat_result(p)

    monkeypatch.setattr("opskit.file.cli.api.stat_files", fake)
    result = runner.invoke(
        app, ["file", "stat", "config.json", "missing.json", "--jsonl"]
    )
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[0]["result"]["path"] == "config.json"
    assert lines[1]["result"] is None
    assert result.exit_code == 7
