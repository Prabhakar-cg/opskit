"""Tests for opskit.file.hashing: streaming checksums (research R6)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from opskit.core.errors import UsageError
from opskit.file import hashing
from opskit.file.errors import FileNotFoundOnDisk


@pytest.mark.parametrize("algorithm", ["sha256", "sha1", "md5"])
def test_compute_hash_matches_hashlib(tmp_path: Path, algorithm: str):
    path = tmp_path / "data.bin"
    content = b"the quick brown fox jumps over the lazy dog" * 100
    path.write_bytes(content)

    digest = hashing.compute_hash(path, algorithm)

    expected = hashlib.new(algorithm, content).hexdigest()
    assert digest == expected


def test_compute_hash_streams_large_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hashing, "_CHUNK_SIZE", 16)
    path = tmp_path / "large.bin"
    content = b"x" * 10_000
    path.write_bytes(content)

    digest = hashing.compute_hash(path, "sha256")

    assert digest == hashlib.sha256(content).hexdigest()


def test_compute_hash_unsupported_algorithm_is_usage_error(tmp_path: Path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"data")
    with pytest.raises(UsageError):
        hashing.compute_hash(path, "sha512")


def test_compute_hash_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        hashing.compute_hash(tmp_path / "nope.bin", "sha256")


def test_compute_hash_empty_file(tmp_path: Path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert hashing.compute_hash(path, "sha256") == hashlib.sha256(b"").hexdigest()


def test_find_duplicates_groups_by_size_then_hash(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"duplicate")
    (tmp_path / "b.txt").write_bytes(b"duplicate")
    (tmp_path / "c.txt").write_bytes(b"unique-but-same-size!")
    (tmp_path / "d.txt").write_bytes(b"solo")

    groups = hashing.find_duplicates(tmp_path)

    assert len(groups) == 1
    group = groups[0]
    assert group.digest == hashlib.sha256(b"duplicate").hexdigest()
    assert group.size_bytes == len(b"duplicate")
    assert set(group.paths) == {str(tmp_path / "a.txt"), str(tmp_path / "b.txt")}


def test_find_duplicates_same_size_different_content_not_grouped(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"aaaa")
    (tmp_path / "b.txt").write_bytes(b"bbbb")
    assert hashing.find_duplicates(tmp_path) == []


def test_find_duplicates_recursive_off_ignores_subdirectory(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"content")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"content")

    assert hashing.find_duplicates(tmp_path, recursive=False) == []
    groups = hashing.find_duplicates(tmp_path, recursive=True)
    assert len(groups) == 1
    assert len(groups[0].paths) == 2


def test_find_duplicates_empty_directory(tmp_path: Path):
    assert hashing.find_duplicates(tmp_path) == []


def test_find_duplicates_missing_directory_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundOnDisk):
        hashing.find_duplicates(tmp_path / "nope")


def test_find_duplicates_symlinked_file_not_followed(tmp_path: Path):
    original = tmp_path / "a.txt"
    original.write_bytes(b"content")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("symlinks not supported in this environment")

    groups = hashing.find_duplicates(tmp_path)

    assert groups == []
