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
