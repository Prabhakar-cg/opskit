"""Tests for opskit.file.textstats: line-ending detection and normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from opskit.core.errors import UsageError
from opskit.file import textstats
from opskit.file.errors import FileNotFoundOnDisk, InvalidContent
from opskit.file.models import LineEnding


def test_detect_all_lf(tmp_path: Path):
    path = tmp_path / "unix.txt"
    path.write_bytes(b"a\nb\nc\n")
    report = textstats.detect_line_endings(path)
    assert report.lf_count == 3
    assert report.crlf_count == 0
    assert report.cr_count == 0
    assert report.mixed is False


def test_detect_all_crlf(tmp_path: Path):
    path = tmp_path / "dos.txt"
    path.write_bytes(b"a\r\nb\r\nc\r\n")
    report = textstats.detect_line_endings(path)
    assert report.crlf_count == 3
    assert report.lf_count == 0
    assert report.cr_count == 0
    assert report.mixed is False


def test_detect_lone_cr(tmp_path: Path):
    path = tmp_path / "mac.txt"
    path.write_bytes(b"a\rb\rc\r")
    report = textstats.detect_line_endings(path)
    assert report.cr_count == 3
    assert report.mixed is False


def test_detect_mixed_endings(tmp_path: Path):
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"a\r\nb\nc\r")
    report = textstats.detect_line_endings(path)
    assert report.crlf_count == 1
    assert report.lf_count == 1
    assert report.cr_count == 1
    assert report.mixed is True


def test_detect_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        textstats.detect_line_endings(tmp_path / "nope.txt")


def test_detect_directory_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        textstats.detect_line_endings(tmp_path)


def test_detect_crlf_split_across_chunk_boundary(tmp_path: Path, monkeypatch):
    """A \\r\\n split by the chunk boundary must still count as one CRLF, not CR+LF."""
    monkeypatch.setattr(textstats, "_CHUNK_SIZE", 4)
    path = tmp_path / "boundary.txt"
    # "aaa" fills the first 4-byte chunk to end in \r; \n starts the next chunk.
    path.write_bytes(b"aaa\r\nbbb\n")
    report = textstats.detect_line_endings(path)
    assert report.crlf_count == 1
    assert report.lf_count == 1
    assert report.cr_count == 0


def test_detect_lone_cr_at_chunk_boundary_not_followed_by_lf(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(textstats, "_CHUNK_SIZE", 4)
    path = tmp_path / "boundary2.txt"
    # The \r lands as the last byte of a chunk but the next chunk does not start with \n.
    path.write_bytes(b"aaa\rbbb\n")
    report = textstats.detect_line_endings(path)
    assert report.cr_count == 1
    assert report.lf_count == 1
    assert report.crlf_count == 0


def test_detect_trailing_lone_cr_at_end_of_file(tmp_path: Path):
    path = tmp_path / "trailing_cr.txt"
    path.write_bytes(b"abc\r")
    report = textstats.detect_line_endings(path)
    assert report.cr_count == 1


def test_detect_large_file_streaming(tmp_path: Path, monkeypatch):
    """Force a tiny chunk size so a moderately sized file exercises multiple reads."""
    monkeypatch.setattr(textstats, "_CHUNK_SIZE", 16)
    path = tmp_path / "large.txt"
    path.write_bytes(b"line\n" * 500 + b"tail\r\n" * 500)
    report = textstats.detect_line_endings(path)
    assert report.lf_count == 500
    assert report.crlf_count == 500
    assert report.mixed is True


@pytest.mark.parametrize(
    ("to", "expected"),
    [
        (LineEnding.LF, b"a\nb\nc\n"),
        (LineEnding.CRLF, b"a\r\nb\r\nc\r\n"),
        (LineEnding.CR, b"a\rb\rc\r"),
    ],
)
def test_normalize_from_mixed(to: LineEnding, expected: bytes):
    content = b"a\r\nb\nc\r"
    assert textstats.normalize_line_endings(content, to) == expected


def test_normalize_is_idempotent():
    content = b"a\nb\nc\n"
    once = textstats.normalize_line_endings(content, LineEnding.LF)
    twice = textstats.normalize_line_endings(once, LineEnding.LF)
    assert once == twice == content


# ---------------------------------------------------------------------------
# Encoding detection (research R4)
# ---------------------------------------------------------------------------


def test_detect_encoding_utf8_bom():
    report = textstats.detect_encoding_bytes(b"\xef\xbb\xbfhello")
    assert report.encoding == "utf-8-sig"
    assert report.has_bom is True
    assert report.confidence is None
    assert report.invalid_sequences is False


def test_detect_encoding_utf16_le_bom():
    raw = "hello".encode("utf-16-le")
    report = textstats.detect_encoding_bytes(b"\xff\xfe" + raw)
    assert report.encoding == "utf-16-le"
    assert report.has_bom is True
    assert report.invalid_sequences is False


def test_detect_encoding_utf16_be_bom():
    raw = "hello".encode("utf-16-be")
    report = textstats.detect_encoding_bytes(b"\xfe\xff" + raw)
    assert report.encoding == "utf-16-be"
    assert report.has_bom is True
    assert report.invalid_sequences is False


def test_detect_encoding_utf32_le_bom():
    raw = "hello".encode("utf-32-le")
    report = textstats.detect_encoding_bytes(b"\xff\xfe\x00\x00" + raw)
    assert report.encoding == "utf-32-le"
    assert report.has_bom is True
    assert report.invalid_sequences is False


def test_detect_encoding_utf32_be_bom():
    raw = "hello".encode("utf-32-be")
    report = textstats.detect_encoding_bytes(b"\x00\x00\xfe\xff" + raw)
    assert report.encoding == "utf-32-be"
    assert report.has_bom is True
    assert report.invalid_sequences is False


def test_detect_encoding_bom_with_invalid_sequence_flagged():
    # A UTF-16-LE BOM followed by an odd number of trailing bytes cannot decode cleanly.
    report = textstats.detect_encoding_bytes(b"\xff\xfe" + b"a")
    assert report.encoding == "utf-16-le"
    assert report.invalid_sequences is True


def test_detect_encoding_no_bom_uses_charset_normalizer():
    report = textstats.detect_encoding_bytes(b"plain ascii text, nothing special here")
    assert report.has_bom is False
    assert report.encoding is not None
    assert report.confidence is not None
    assert 0.0 <= report.confidence <= 1.0


def test_detect_encoding_undecodable_binary_is_unknown(monkeypatch):
    class _NoMatch:
        def best(self):
            return None

    monkeypatch.setattr(
        textstats.charset_normalizer, "from_bytes", lambda raw: _NoMatch()
    )
    report = textstats.detect_encoding_bytes(b"\x00\x01\x02")
    assert report.encoding == "unknown"
    assert report.invalid_sequences is True


def test_detect_encoding_path_reads_from_disk(tmp_path: Path):
    path = tmp_path / "bom.txt"
    path.write_bytes(b"\xef\xbb\xbfhello")
    report = textstats.detect_encoding(path)
    assert report.path == str(path)
    assert report.encoding == "utf-8-sig"


def test_detect_encoding_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        textstats.detect_encoding(tmp_path / "nope.txt")


# ---------------------------------------------------------------------------
# transcode() (research R4, FR-012)
# ---------------------------------------------------------------------------


def test_transcode_latin1_to_utf8_round_trips_text():
    original = "café résumé"
    content = original.encode("latin-1")
    transcoded = textstats.transcode(
        content, from_encoding="latin-1", to_encoding="utf-8"
    )
    assert transcoded.decode("utf-8") == original


def test_transcode_unencodable_character_raises_invalid_content():
    content = "café".encode("latin-1")
    with pytest.raises(InvalidContent):
        textstats.transcode(content, from_encoding="latin-1", to_encoding="ascii")


def test_transcode_undecodable_source_raises_invalid_content():
    # 0xFF is not valid in the middle of a UTF-8 sequence.
    content = b"hello \xff world"
    with pytest.raises(InvalidContent):
        textstats.transcode(content, from_encoding="utf-8", to_encoding="ascii")


def test_transcode_unknown_source_codec_is_usage_error():
    with pytest.raises(UsageError):
        textstats.transcode(b"hello", from_encoding="bogus-codec", to_encoding="utf-8")


def test_transcode_unknown_target_codec_is_usage_error():
    with pytest.raises(UsageError):
        textstats.transcode(b"hello", from_encoding="utf-8", to_encoding="bogus-codec")
