"""Tests for opskit.file.api: the public functions' own validation/delegation."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from opskit.core.errors import UsageError
from opskit.file import api
from opskit.file.errors import ClobberRefused
from opskit.file.models import LineEnding, StructuredFormat


def test_validate_valid_file_reports_valid_and_detected_format(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    result = api.validate(path)
    assert result.valid is True
    assert result.format is StructuredFormat.JSON
    assert result.error_message is None


def test_validate_invalid_file_reports_line_column_and_best_guess_format(
    tmp_path: Path,
):
    path = tmp_path / "broken.json"
    path.write_text('{"a": 1,}', encoding="utf-8")
    result = api.validate(path)
    assert result.valid is False
    assert (
        result.format is StructuredFormat.JSON
    )  # inferred from extension despite failure
    assert result.error_line is not None
    assert result.error_column is not None
    assert result.error_message is not None


def test_validate_explicit_format_overrides_extension(tmp_path: Path):
    path = tmp_path / "config.txt"
    path.write_text("a: 1\n", encoding="utf-8")
    result = api.validate(path, format=StructuredFormat.YAML)
    assert result.valid is True
    assert result.format is StructuredFormat.YAML


def test_validate_undetectable_content_reports_invalid_with_none_format(tmp_path: Path):
    path = tmp_path / "mystery.txt"
    path.write_text("not structured at all, just prose", encoding="utf-8")
    result = api.validate(path)
    assert result.valid is False
    assert result.format is None


def test_lineendings_reports_counts(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\n")
    result = api.lineendings(path)
    assert result.crlf_count == 1
    assert result.lf_count == 1
    assert result.mixed is True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_eol_without_write_options_is_non_destructive(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\nc\r")
    before = _sha256(path)

    outcome = api.eol(path, to=LineEnding.LF)

    assert _sha256(path) == before  # SC-002: source untouched
    assert outcome.stdout_content == b"a\nb\nc\n"
    assert outcome.result.destination == "-"
    assert outcome.result.in_place is False


def test_eol_in_place_rewrites_source(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\n")

    outcome = api.eol(path, to=LineEnding.LF, in_place=True)

    assert path.read_bytes() == b"a\nb\n"
    assert outcome.result.in_place is True
    assert outcome.result.backup_path is None
    assert outcome.stdout_content is None


def test_eol_in_place_backup_preserves_original_bytes(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\n")
    original = path.read_bytes()

    outcome = api.eol(path, to=LineEnding.LF, in_place=True, backup=True)

    backup = Path(outcome.result.backup_path)
    assert backup.read_bytes() == original
    assert path.read_bytes() == b"a\nb\n"


def test_eol_in_place_backup_refuses_to_clobber_existing_backup(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\n")
    (tmp_path / "mixed.txt.bak").write_bytes(b"someone else's backup")

    with pytest.raises(ClobberRefused):
        api.eol(path, to=LineEnding.LF, in_place=True, backup=True)


def test_eol_output_writes_new_file_leaves_source_untouched(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\n")
    before = _sha256(path)
    dest = tmp_path / "out.txt"

    outcome = api.eol(path, to=LineEnding.LF, output=dest)

    assert dest.read_bytes() == b"a\nb\n"
    assert _sha256(path) == before
    assert outcome.result.destination == str(dest)


def test_eol_output_and_in_place_is_usage_error(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\n")
    with pytest.raises(UsageError):
        api.eol(path, to=LineEnding.LF, output=tmp_path / "out.txt", in_place=True)


def test_eol_backup_without_in_place_is_usage_error(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\n")
    with pytest.raises(UsageError):
        api.eol(path, to=LineEnding.LF, backup=True)
