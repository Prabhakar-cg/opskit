"""Tests for opskit.file.formats: load/dump for JSON/YAML/TOML/XML, detection, lossiness."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.file import formats
from opskit.file.errors import FileNotFoundOnDisk, FilePermissionDenied, InvalidContent
from opskit.file.models import StructuredFormat

# ---------------------------------------------------------------------------
# Basic load/dump per format
# ---------------------------------------------------------------------------


def test_json_load_dump_round_trip(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1, "b": [1, 2, 3]}', encoding="utf-8")
    result = formats.load(path)
    assert result.format is StructuredFormat.JSON
    assert result.data == {"a": 1, "b": [1, 2, 3]}
    assert result.lossless is True

    dumped = formats.dump(result.data, StructuredFormat.JSON)
    assert dumped.lossless is True
    round_tripped = formats.load(path, format=StructuredFormat.JSON)
    assert round_tripped.data == {"a": 1, "b": [1, 2, 3]}


def test_yaml_load_dump_round_trip(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("a: 1\nb:\n  - 1\n  - 2\n", encoding="utf-8")
    result = formats.load(path)
    assert result.format is StructuredFormat.YAML
    assert result.data == {"a": 1, "b": [1, 2]}

    dumped = formats.dump(result.data, StructuredFormat.YAML)
    assert dumped.lossless is True
    reloaded = formats._load_yaml(dumped.content.decode("utf-8"), path)
    assert reloaded[0] == {"a": 1, "b": [1, 2]}


def test_toml_load_dump_round_trip(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('a = 1\n[b]\nc = "x"\n', encoding="utf-8")
    result = formats.load(path)
    assert result.format is StructuredFormat.TOML
    assert result.data == {"a": 1, "b": {"c": "x"}}

    dumped = formats.dump(result.data, StructuredFormat.TOML)
    assert dumped.lossless is True
    reloaded = formats.load(_write(tmp_path, "out.toml", dumped.content))
    assert reloaded.data == {"a": 1, "b": {"c": "x"}}


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_xml_load_element_attribute_text_convention(tmp_path: Path):
    path = tmp_path / "doc.xml"
    path.write_bytes(
        b'<?xml version="1.0"?><root id="7"><item>a</item><item>b</item></root>'
    )
    result = formats.load(path)
    assert result.format is StructuredFormat.XML
    assert result.lossless is True
    assert result.data == {
        "@attributes": {"id": "7"},
        "item": ["a", "b"],
    }


def test_xml_mixed_content_is_flagged_lossy(tmp_path: Path):
    path = tmp_path / "mixed.xml"
    path.write_bytes(b"<root>before<child>x</child>after</root>")
    result = formats.load(path)
    assert result.lossless is False
    assert result.lossy_reason is not None


def test_xml_dump_round_trips_simple_structure(tmp_path: Path):
    data = {"@attributes": {"id": "7"}, "item": ["a", "b"]}
    dumped = formats.dump(data, StructuredFormat.XML)
    path = _write(tmp_path, "out.xml", dumped.content)
    reloaded = formats.load(path)
    assert reloaded.data == data


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        (".json", StructuredFormat.JSON),
        (".yaml", StructuredFormat.YAML),
        (".yml", StructuredFormat.YAML),
        (".toml", StructuredFormat.TOML),
        (".xml", StructuredFormat.XML),
    ],
)
def test_detect_from_extension(suffix, expected):
    assert formats.detect_from_extension(Path(f"config{suffix}")) is expected


def test_detect_from_extension_unknown_returns_none():
    assert formats.detect_from_extension(Path("config.txt")) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', StructuredFormat.JSON),
        ("<root/>", StructuredFormat.XML),
        ("a = 1\n[b]\nc = 2\n", StructuredFormat.TOML),
        ("a:\n  - 1\n  - 2\n", StructuredFormat.YAML),
    ],
)
def test_detect_from_content(text, expected):
    assert formats.detect_from_content(text) is expected


def test_load_without_extension_falls_back_to_content_sniffing(tmp_path: Path):
    path = tmp_path / "noext"
    path.write_text('{"a": 1}', encoding="utf-8")
    result = formats.load(path)
    assert result.format is StructuredFormat.JSON


def test_load_undetectable_content_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "noext"
    path.write_text("just some plain prose, not structured at all", encoding="utf-8")
    with pytest.raises(InvalidContent):
        formats.load(path)


def test_load_empty_yaml_succeeds_as_null(tmp_path: Path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    result = formats.load(path)
    assert result.data is None


def test_load_empty_toml_succeeds_as_empty_table(tmp_path: Path):
    path = tmp_path / "empty.toml"
    path.write_text("", encoding="utf-8")
    result = formats.load(path)
    assert result.data == {}


def test_load_empty_json_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(InvalidContent):
        formats.load(path)


def test_load_empty_xml_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "empty.xml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(InvalidContent):
        formats.load(path)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_load_missing_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        formats.load(tmp_path / "missing.json")


def test_load_directory_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        formats.load(tmp_path)


def test_load_permission_denied_is_normalized(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")

    def _raise(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", _raise)
    with pytest.raises(FilePermissionDenied):
        formats.load(path)


def test_invalid_json_reports_line_and_column(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text('{\n  "a": 1,\n  "b":\n}', encoding="utf-8")
    with pytest.raises(InvalidContent) as excinfo:
        formats.load(path, format=StructuredFormat.JSON)
    assert excinfo.value.line is not None
    assert excinfo.value.column is not None


def test_invalid_yaml_reports_line_and_column(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    path.write_text("a: [1, 2\nb: 3", encoding="utf-8")
    with pytest.raises(InvalidContent) as excinfo:
        formats.load(path, format=StructuredFormat.YAML)
    assert excinfo.value.line is not None


def test_invalid_toml_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "broken.toml"
    path.write_text("a = [1, 2\n", encoding="utf-8")
    with pytest.raises(InvalidContent):
        formats.load(path, format=StructuredFormat.TOML)


def test_invalid_xml_reports_line_and_column(tmp_path: Path):
    path = tmp_path / "broken.xml"
    path.write_bytes(b"<root><unclosed></root>")
    with pytest.raises(InvalidContent) as excinfo:
        formats.load(path, format=StructuredFormat.XML)
    assert excinfo.value.line is not None
    assert excinfo.value.column is not None


def test_xml_disallowed_construct_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "evil.xml"
    path.write_bytes(
        b'<?xml version="1.0"?><!DOCTYPE root [<!ENTITY xxe "boom">]><root>&xxe;</root>'
    )
    with pytest.raises(InvalidContent):
        formats.load(path, format=StructuredFormat.XML)


def test_binary_file_raises_invalid_content(tmp_path: Path):
    path = tmp_path / "binary.json"
    path.write_bytes(b"\xff\xfe\x00\x01binary-not-utf8\x80\x81")
    with pytest.raises(InvalidContent):
        formats.load(path, format=StructuredFormat.JSON)


# ---------------------------------------------------------------------------
# Lossiness (FR-018)
# ---------------------------------------------------------------------------


def test_json_duplicate_keys_flagged_lossy(tmp_path: Path):
    path = tmp_path / "dup.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    result = formats.load(path)
    assert result.data == {"a": 2}
    assert result.lossless is False
    assert result.lossy_reason is not None


def test_json_dump_flags_non_string_keys_and_dates():
    import datetime

    data = {1: "x", "when": datetime.date(2026, 1, 1)}
    dumped = formats.dump(data, StructuredFormat.JSON)
    assert dumped.lossless is False
    assert "non-string" in dumped.lossy_reason
    assert "date" in dumped.lossy_reason


def test_toml_dump_rejects_null_values():
    with pytest.raises(InvalidContent):
        formats.dump({"a": None}, StructuredFormat.TOML)


def test_toml_dump_rejects_non_mapping_root():
    with pytest.raises(InvalidContent):
        formats.dump([1, 2, 3], StructuredFormat.TOML)


# ---------------------------------------------------------------------------
# Round-trip property tests (SC-004)
# ---------------------------------------------------------------------------

_toml_safe_scalar = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.text(
        # Exclude surrogates/control/line-separator categories: YAML 1.1 treats
        # U+0085/U+2028/U+2029 as line-break characters, so PyYAML's emitter/scanner
        # round-trip normalizes them — a documented YAML-spec quirk, not a bug in R1's
        # load/dump adapter, so it's out of scope for this round-trip property.
        alphabet=st.characters(
            blacklist_categories=("Cs", "Cc", "Zl", "Zp"), max_codepoint=0x2FFF
        ),
        max_size=20,
    ),
)


def _toml_safe_tree(children):
    return st.one_of(
        _toml_safe_scalar,
        st.lists(children, max_size=4),
        st.dictionaries(
            st.text(
                alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
                min_size=1,
                max_size=10,
            ),
            children,
            max_size=4,
        ),
    )


_toml_safe_value = st.recursive(_toml_safe_scalar, _toml_safe_tree, max_leaves=20)
_toml_safe_table = st.dictionaries(
    st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=1,
        max_size=10,
    ),
    _toml_safe_value,
    max_size=5,
)


@given(data=_toml_safe_table)
def test_json_yaml_toml_round_trip_no_loss(data):
    json_bytes = formats.dump(data, StructuredFormat.JSON).content
    from_json = formats._load_json(json_bytes.decode("utf-8"), Path("x.json"))[0]
    assert from_json == data

    yaml_bytes = formats.dump(data, StructuredFormat.YAML).content
    from_yaml = formats._load_yaml(yaml_bytes.decode("utf-8"), Path("x.yaml"))[0]
    assert from_yaml == data

    toml_bytes = formats.dump(data, StructuredFormat.TOML).content
    from_toml = formats._load_toml(toml_bytes.decode("utf-8"), Path("x.toml"))[0]
    assert from_toml == data
