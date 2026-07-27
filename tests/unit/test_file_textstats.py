"""Tests for opskit.file.textstats: line-ending detection and normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from opskit.file import textstats
from opskit.file.errors import FileNotFoundOnDisk
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
