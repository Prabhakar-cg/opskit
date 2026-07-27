"""Tests for opskit.file.api: the public functions' own validation/delegation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.core.errors import UsageError
from opskit.file import api
from opskit.file.errors import ClobberRefused, FileNotFoundOnDisk, InvalidContent
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


# ---------------------------------------------------------------------------
# convert()
# ---------------------------------------------------------------------------


def test_convert_json_to_yaml_stdout_is_non_destructive(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
    before = _sha256(path)

    outcome = api.convert(path, to=StructuredFormat.YAML)

    assert _sha256(path) == before
    assert outcome.result.destination == "-"
    assert b"a: 1" in outcome.stdout_content


def test_convert_round_trip_json_yaml_json(tmp_path: Path):
    data = {"a": 1, "b": [2, 3], "c": {"d": "e"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    yaml_out = tmp_path / "config.yaml"
    api.convert(path, to=StructuredFormat.YAML, output=yaml_out)

    json_out = tmp_path / "roundtrip.json"
    outcome = api.convert(yaml_out, to=StructuredFormat.JSON, output=json_out)

    assert json.loads(json_out.read_text(encoding="utf-8")) == data
    assert outcome.result.lossless is True


def test_convert_round_trip_json_toml_json(tmp_path: Path):
    data = {"a": 1, "b": ["x", "y"], "c": {"d": True}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    toml_out = tmp_path / "config.toml"
    api.convert(path, to=StructuredFormat.TOML, output=toml_out)

    json_out = tmp_path / "roundtrip.json"
    api.convert(toml_out, to=StructuredFormat.JSON, output=json_out)

    assert json.loads(json_out.read_text(encoding="utf-8")) == data


# Exclude surrogates/control/line-separator categories: YAML 1.1 treats U+0085/U+2028/
# U+2029 as line-break characters, so PyYAML's emitter/scanner round-trip normalizes them
# — a documented YAML-spec quirk (see test_file_formats.py's identical exclusion), not a
# bug in convert()'s JSON<->YAML round-trip.
_yaml_safe_alphabet = st.characters(
    blacklist_categories=("Cs", "Cc", "Zl", "Zp"), max_codepoint=0x2FFF
)
_yaml_safe_text = st.text(alphabet=_yaml_safe_alphabet, max_size=15)
_yaml_safe_key = st.text(alphabet=_yaml_safe_alphabet, min_size=1, max_size=8)
_json_safe_scalar = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    _yaml_safe_text,
)
_json_safe_value = st.recursive(
    _json_safe_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(_yaml_safe_key, children, max_size=3),
    ),
    max_leaves=10,
)
_json_safe_dict = st.dictionaries(_yaml_safe_key, _json_safe_value, max_size=4)


@given(data=_json_safe_dict)
def test_convert_property_round_trip_json_yaml_json(tmp_path_factory, data):
    tmp_path = tmp_path_factory.mktemp("convert-roundtrip")
    path = tmp_path / "src.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    yaml_out = tmp_path / "mid.yaml"
    api.convert(path, to=StructuredFormat.YAML, output=yaml_out)
    json_out = tmp_path / "back.json"
    api.convert(yaml_out, to=StructuredFormat.JSON, output=json_out)

    assert json.loads(json_out.read_text(encoding="utf-8")) == data


def test_convert_same_format_is_usage_error(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(UsageError):
        api.convert(path, to=StructuredFormat.JSON)


def test_convert_invalid_source_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text('{"a": 1,}', encoding="utf-8")
    with pytest.raises(InvalidContent):
        api.convert(path, to=StructuredFormat.YAML)


def test_convert_xml_mixed_content_is_lossy(tmp_path: Path):
    path = tmp_path / "mixed.xml"
    path.write_bytes(b"<root>text<child/>tail</root>")
    outcome = api.convert(path, to=StructuredFormat.JSON)
    assert outcome.result.lossless is False
    assert outcome.result.lossy_reason


def test_convert_in_place_backup_preserves_original(tmp_path: Path):
    path = tmp_path / "config.json"
    original = '{"a": 1}'
    path.write_text(original, encoding="utf-8")

    outcome = api.convert(path, to=StructuredFormat.YAML, in_place=True, backup=True)

    backup = Path(outcome.result.backup_path)
    assert backup.read_text(encoding="utf-8") == original
    assert path.read_text(encoding="utf-8").startswith("a: 1")


# ---------------------------------------------------------------------------
# pretty()
# ---------------------------------------------------------------------------


def test_pretty_changes_formatting_only_non_destructive(tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text('{"b":1,"a":2}', encoding="utf-8")
    before = _sha256(path)

    outcome = api.pretty(path, sort_keys=True, indent=4)

    assert _sha256(path) == before
    assert json.loads(outcome.stdout_content) == {"a": 2, "b": 1}
    assert outcome.stdout_content.index(b'"a"') < outcome.stdout_content.index(b'"b"')


def test_pretty_default_indent_two(tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text('{"a":1}', encoding="utf-8")
    outcome = api.pretty(path)
    assert b'\n  "a"' in outcome.stdout_content


def test_pretty_toml_is_usage_error(tmp_path: Path):
    path = tmp_path / "data.toml"
    path.write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(UsageError):
        api.pretty(path)


def test_pretty_in_place_rewrites_source(tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text('{"a":1,"b":2}', encoding="utf-8")

    api.pretty(path, in_place=True, indent=4)

    assert path.read_text(encoding="utf-8") == json.dumps({"a": 1, "b": 2}, indent=4)


# ---------------------------------------------------------------------------
# identify()
# ---------------------------------------------------------------------------


def test_identify_matching_extension(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    result = api.identify(path)
    assert result.detected_type == "json"
    assert result.extension == "json"
    assert result.extension_matches is True


def test_identify_mismatched_extension(tmp_path: Path):
    path = tmp_path / "config.txt"
    path.write_text('{"a": 1}', encoding="utf-8")
    result = api.identify(path)
    assert result.detected_type == "json"
    assert result.extension == "txt"
    assert result.extension_matches is False


def test_identify_no_extension(tmp_path: Path):
    path = tmp_path / "noext"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    result = api.identify(path)
    assert result.detected_type == "png"
    assert result.extension == ""
    assert result.extension_matches is False


# ---------------------------------------------------------------------------
# hash_files()
# ---------------------------------------------------------------------------


def test_hash_files_default_algo_sha256(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    result = api.hash_files(path)
    assert result.algorithm == "sha256"
    assert len(result.digest) == 64


def test_hash_files_explicit_algo(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    result = api.hash_files(path, algo="md5")
    assert result.algorithm == "md5"
    assert len(result.digest) == 32


def test_hash_files_unsupported_algo_is_usage_error(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    with pytest.raises(UsageError):
        api.hash_files(path, algo="sha512")


# ---------------------------------------------------------------------------
# encoding()
# ---------------------------------------------------------------------------


def test_encoding_detects_bom(tmp_path: Path):
    path = tmp_path / "bom.txt"
    path.write_bytes(b"\xef\xbb\xbfhello")
    result = api.encoding(path)
    assert result.encoding == "utf-8-sig"
    assert result.has_bom is True


def test_encoding_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        api.encoding(tmp_path / "nope.txt")


# ---------------------------------------------------------------------------
# reencode()
# ---------------------------------------------------------------------------


def test_reencode_explicit_from_round_trips_text(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    original = "café résumé"
    path.write_bytes(original.encode("latin-1"))

    outcome = api.reencode(path, to="utf-8", from_="latin-1")

    assert outcome.stdout_content.decode("utf-8") == original


def test_reencode_auto_detects_source_encoding(tmp_path: Path):
    path = tmp_path / "bom.txt"
    path.write_bytes("hello".encode("utf-16"))

    outcome = api.reencode(path, to="utf-8")

    assert outcome.stdout_content == b"hello"


def test_reencode_output_leaves_source_untouched(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    original_bytes = "café".encode("latin-1")
    path.write_bytes(original_bytes)
    dest = tmp_path / "out.utf8.txt"

    api.reencode(path, to="utf-8", from_="latin-1", output=dest)

    assert path.read_bytes() == original_bytes
    assert dest.read_text(encoding="utf-8") == "café"


def test_reencode_in_place_backup(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    original_bytes = "café".encode("latin-1")
    path.write_bytes(original_bytes)

    outcome = api.reencode(
        path, to="utf-8", from_="latin-1", in_place=True, backup=True
    )

    backup = Path(outcome.result.backup_path)
    assert backup.read_bytes() == original_bytes
    assert path.read_text(encoding="utf-8") == "café"


def test_reencode_unencodable_character_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    path.write_bytes("café".encode("latin-1"))
    with pytest.raises(InvalidContent):
        api.reencode(path, to="ascii", from_="latin-1")


def test_reencode_output_and_in_place_is_usage_error(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    path.write_bytes(b"hello")
    with pytest.raises(UsageError):
        api.reencode(path, to="utf-8", output=tmp_path / "out.txt", in_place=True)
