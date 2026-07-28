"""Tests for opskit.file.api: the public functions' own validation/delegation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.core.errors import UsageError
from opskit.file import api
from opskit.file.errors import ClobberRefused, FileNotFoundOnDisk, InvalidContent
from opskit.file.models import MISSING, LineEnding, StructuredFormat


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


def test_diff_reformatted_yaml_is_equivalent(tmp_path: Path):
    left = tmp_path / "left.yaml"
    right = tmp_path / "right.yaml"
    left.write_text("a: 1\nb:\n  - x\n  - y\n", encoding="utf-8")
    right.write_text("b: [x, y]\na: 1\n", encoding="utf-8")

    result = api.diff(left, right)

    assert result.equivalent is True
    assert result.differences == ()


def test_diff_pinpoints_differing_key_path(tmp_path: Path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text('{"server": {"port": 80}}', encoding="utf-8")
    right.write_text('{"server": {"port": 443}}', encoding="utf-8")

    result = api.diff(left, right)

    assert result.equivalent is False
    assert len(result.differences) == 1
    entry = result.differences[0]
    assert entry.key_path == "server.port"
    assert entry.left_value == 80
    assert entry.right_value == 443


def test_diff_identical_paths_is_equivalent(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    result = api.diff(path, path)
    assert result.equivalent is True


def test_diff_missing_key_uses_missing_sentinel(tmp_path: Path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text('{"a": 1, "b": 2}', encoding="utf-8")
    right.write_text('{"a": 1}', encoding="utf-8")

    result = api.diff(left, right)

    assert len(result.differences) == 1
    entry = result.differences[0]
    assert entry.key_path == "b"
    assert entry.right_value is MISSING
    assert entry.left_value == 2


def test_diff_invalid_source_raises_invalid_content(tmp_path: Path):
    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_text('{"a": 1,}', encoding="utf-8")
    right.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(InvalidContent):
        api.diff(left, right)


def test_diff_missing_file_raises_not_found(tmp_path: Path):
    right = tmp_path / "right.json"
    right.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(FileNotFoundOnDisk):
        api.diff(tmp_path / "missing.json", right)


def test_find_duplicates_groups_identical_content(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"same content")
    (tmp_path / "b.txt").write_bytes(b"same content")
    (tmp_path / "c.txt").write_bytes(b"different")

    groups = api.find_duplicates(tmp_path)

    assert len(groups) == 1
    assert set(groups[0].paths) == {str(tmp_path / "a.txt"), str(tmp_path / "b.txt")}
    assert groups[0].size_bytes == len(b"same content")


def test_find_duplicates_empty_directory_reports_no_groups(tmp_path: Path):
    assert api.find_duplicates(tmp_path) == ()


def test_find_duplicates_no_duplicates_reports_no_groups(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"one")
    (tmp_path / "b.txt").write_bytes(b"two")
    assert api.find_duplicates(tmp_path) == ()


def test_find_duplicates_non_recursive_ignores_subdirectories(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"same content")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"same content")

    assert api.find_duplicates(tmp_path, recursive=False) == ()

    groups = api.find_duplicates(tmp_path, recursive=True)
    assert len(groups) == 1
    assert set(groups[0].paths) == {str(tmp_path / "a.txt"), str(sub / "b.txt")}


def test_find_duplicates_missing_directory_raises_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        api.find_duplicates(tmp_path / "nope")


def test_find_duplicates_file_path_raises_not_found(tmp_path: Path):
    path = tmp_path / "not_a_dir.txt"
    path.write_bytes(b"data")
    with pytest.raises(FileNotFoundOnDisk):
        api.find_duplicates(path)


def test_stat_files_reports_size_and_mtime(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello world")

    result = api.stat_files(path)

    assert result.path == str(path)
    assert result.size_bytes == len(b"hello world")
    assert result.modified_at
    assert result.is_symlink is False
    assert result.symlink_target is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only permissions/owner")
def test_stat_files_permissions_and_owner_populated_on_posix(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    path.chmod(0o644)

    result = api.stat_files(path)

    assert result.permissions == "644"
    assert result.owner is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only None fields")
def test_stat_files_permissions_and_owner_none_on_windows(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")

    result = api.stat_files(path)

    assert result.permissions is None
    assert result.owner is None


def test_stat_files_symlink_reports_target(tmp_path: Path):
    target = tmp_path / "real.txt"
    target.write_bytes(b"content")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    result = api.stat_files(link)

    assert result.is_symlink is True
    assert result.symlink_target == str(target)


def test_stat_files_missing_raises_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        api.stat_files(tmp_path / "nope.bin")


def test_contracts_python_api_example_runs_as_written(tmp_path, monkeypatch, capsys):
    """The exact usage example from contracts/python-api.md, executed unmodified.

    The two `try`/`except` blocks each need their own precondition on `config.json`
    (invalid content to trigger `InvalidContent`, then valid content plus a pre-existing
    `.bak` to trigger `ClobberRefused`) — fixture setup between them mirrors a user fixing
    the file and retrying, matching `storage`'s equivalent SC-006 test's precedent.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text('{"a": 1,}', encoding="utf-8")
    (tmp_path / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "script.sh").write_text("echo hi\r\n", encoding="utf-8")

    from opskit.file import ClobberRefused, InvalidContent, convert, eol, validate

    for path in ("config.json", "config.yaml"):
        result = validate(path)
        status = (
            "OK"
            if result.valid
            else f"INVALID @ {result.error_line}:{result.error_column}"
        )
        print(result.path, result.format, status)

    eol_outcome = eol("script.sh", to="lf", in_place=True, backup=True)
    print(eol_outcome.result.backup_path, eol_outcome.result.lossless)

    try:
        convert("config.json", to="yaml", output="config.yaml")
    except InvalidContent as exc:
        print(exc.message, "—", exc.hint)

    (tmp_path / "config.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "config.json.bak").write_text("stale backup", encoding="utf-8")

    try:
        convert("config.json", to="yaml", in_place=True, backup=True)
    except ClobberRefused as exc:
        print(exc.message, "—", exc.hint)

    out = capsys.readouterr().out
    assert "INVALID @ 1:8" in out
    assert "config.yaml StructuredFormat.YAML OK" in out
    assert "script.sh.bak True" in out
    assert "invalid JSON in config.json" in out
    assert "pass --force to overwrite it" in out
