"""Tests for opskit.file.sniff: magic-byte signatures + structural sniffing (research R5)."""

from __future__ import annotations

from pathlib import Path

from opskit.file import sniff


def test_png_signature_detected(tmp_path: Path):
    path = tmp_path / "image.dat"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    assert sniff.identify_type(path) == "png"


def test_gzip_signature_detected(tmp_path: Path):
    path = tmp_path / "archive.dat"
    path.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 10)
    assert sniff.identify_type(path) == "gzip"


def test_zip_signature_detected(tmp_path: Path):
    path = tmp_path / "archive.dat"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    assert sniff.identify_type(path) == "zip"


def test_json_content_detected_regardless_of_extension(tmp_path: Path):
    path = tmp_path / "renamed.txt"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert sniff.identify_type(path) == "json"


def test_xml_content_detected_regardless_of_extension(tmp_path: Path):
    path = tmp_path / "renamed.txt"
    path.write_text("<root><a>1</a></root>", encoding="utf-8")
    assert sniff.identify_type(path) == "xml"


def test_toml_content_detected(tmp_path: Path):
    path = tmp_path / "renamed.txt"
    path.write_text("a = 1\nb = 2\n", encoding="utf-8")
    assert sniff.identify_type(path) == "toml"


def test_yaml_mapping_content_detected(tmp_path: Path):
    path = tmp_path / "renamed.txt"
    path.write_text("a: 1\nb: 2\n", encoding="utf-8")
    assert sniff.identify_type(path) == "yaml"


def test_prose_is_undetermined(tmp_path: Path):
    """A plain-prose file is technically valid bare-scalar YAML — must not be reported as such."""
    path = tmp_path / "notes.txt"
    path.write_text("just some prose, not structured at all", encoding="utf-8")
    assert sniff.identify_type(path) == "undetermined"


def test_empty_file_is_toml_not_ambiguous(tmp_path: Path):
    """An empty file parses as an empty TOML table but not as YAML (bare-scalar guard)."""
    path = tmp_path / "empty.dat"
    path.write_bytes(b"")
    assert sniff.identify_type(path) == "toml"


def test_ambiguous_toml_and_yaml_reported_as_both(tmp_path: Path, monkeypatch):
    path = tmp_path / "ambiguous.dat"
    path.write_text("a = 1\n", encoding="utf-8")

    monkeypatch.setattr(sniff, "_parses_as", lambda p, fmt: True)
    assert sniff.identify_type(path) == "toml/yaml"


def test_too_short_binary_file_is_undetermined(tmp_path: Path):
    path = tmp_path / "tiny.bin"
    path.write_bytes(b"\x00\x01")
    assert sniff.identify_type(path) == "undetermined"
