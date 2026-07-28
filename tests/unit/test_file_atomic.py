"""Tests for opskit.file.atomic: the shared write-safety helper (research R7, SC-002/SC-003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opskit.file import atomic
from opskit.file.errors import ClobberRefused, FilePermissionDenied


def test_write_in_place_replaces_content(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("old", encoding="utf-8")
    backup = atomic.write_in_place(path, b"new", backup=False, force=False)
    assert backup is None
    assert path.read_text(encoding="utf-8") == "new"


def test_write_in_place_with_backup_preserves_original(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("original content", encoding="utf-8")
    backup = atomic.write_in_place(path, b"new content", backup=True, force=False)
    assert backup == str(path.with_name("target.txt.bak"))
    assert Path(backup).read_text(encoding="utf-8") == "original content"
    assert path.read_text(encoding="utf-8") == "new content"


def test_write_in_place_refuses_to_clobber_existing_backup(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("original", encoding="utf-8")
    bak = tmp_path / "target.txt.bak"
    bak.write_text("someone else's backup", encoding="utf-8")

    with pytest.raises(ClobberRefused):
        atomic.write_in_place(path, b"new", backup=True, force=False)

    # Neither the pre-existing backup nor the source were touched.
    assert bak.read_text(encoding="utf-8") == "someone else's backup"
    assert path.read_text(encoding="utf-8") == "original"


def test_write_in_place_force_overwrites_existing_backup(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("original", encoding="utf-8")
    bak = tmp_path / "target.txt.bak"
    bak.write_text("stale backup", encoding="utf-8")

    atomic.write_in_place(path, b"new", backup=True, force=True)
    assert bak.read_text(encoding="utf-8") == "original"
    assert path.read_text(encoding="utf-8") == "new"


def test_write_in_place_fault_injection_leaves_original_intact(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "target.txt"
    path.write_text("untouched", encoding="utf-8")
    original_mtime = path.stat().st_mtime_ns

    def _boom(self, target):
        raise OSError("simulated failure mid-write")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(FilePermissionDenied):
        atomic.write_in_place(path, b"never written", backup=False, force=False)

    assert path.read_text(encoding="utf-8") == "untouched"
    assert path.stat().st_mtime_ns == original_mtime
    # The temp file used for the failed write is cleaned up, not left behind.
    leftovers = [p for p in tmp_path.iterdir() if p.name != "target.txt"]
    assert leftovers == []


def test_write_new_file_creates_destination(tmp_path: Path):
    path = tmp_path / "out.txt"
    atomic.write_new_file(path, b"hello", force=False)
    assert path.read_text(encoding="utf-8") == "hello"


def test_write_new_file_refuses_to_clobber_existing(tmp_path: Path):
    path = tmp_path / "out.txt"
    path.write_text("already here", encoding="utf-8")
    with pytest.raises(ClobberRefused):
        atomic.write_new_file(path, b"replacement", force=False)
    assert path.read_text(encoding="utf-8") == "already here"


def test_write_new_file_force_overwrites_existing(tmp_path: Path):
    path = tmp_path / "out.txt"
    path.write_text("already here", encoding="utf-8")
    atomic.write_new_file(path, b"replacement", force=True)
    assert path.read_text(encoding="utf-8") == "replacement"


def test_atomic_replace_does_not_leave_temp_file_on_success(tmp_path: Path):
    path = tmp_path / "target.txt"
    path.write_text("v1", encoding="utf-8")
    atomic.write_in_place(path, b"v2", backup=False, force=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.txt"]


def test_write_new_file_does_not_leave_temp_file_on_success(tmp_path: Path):
    path = tmp_path / "out.txt"
    atomic.write_new_file(path, b"hello", force=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.txt"]


def test_write_new_file_does_not_leave_temp_file_on_clobber_refused(tmp_path: Path):
    path = tmp_path / "out.txt"
    path.write_text("already here", encoding="utf-8")
    with pytest.raises(ClobberRefused):
        atomic.write_new_file(path, b"replacement", force=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.txt"]


def test_write_new_file_no_force_race_does_not_clobber_concurrent_creation(
    tmp_path: Path, monkeypatch
):
    """A file created between the (removed) exists() check and the write is not clobbered.

    Simulates the TOCTOU window by creating the destination *inside* the temp-file-write
    step (monkeypatching the internal helper), proving `_create_exclusive`'s atomic
    create-if-absent — not a preflight `exists()` check — is what prevents the clobber.
    """
    path = tmp_path / "out.txt"

    real_write_temp = atomic._write_temp

    def _write_temp_then_race(directory, name, content):
        tmp_path_ = real_write_temp(directory, name, content)
        path.write_text("concurrently created", encoding="utf-8")
        return tmp_path_

    monkeypatch.setattr(atomic, "_write_temp", _write_temp_then_race)
    with pytest.raises(ClobberRefused):
        atomic.write_new_file(path, b"replacement", force=False)
    assert path.read_text(encoding="utf-8") == "concurrently created"
