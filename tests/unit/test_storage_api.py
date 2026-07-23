"""Tests for opskit.storage.api: the public functions' own validation/delegation."""

from __future__ import annotations

import pytest

from opskit.core.errors import UsageError
from opskit.storage import api
from opskit.storage.errors import PathNotFound, PathPermissionDenied
from opskit.storage.models import DirSizeResult, Volume


def test_list_volumes_delegates_to_enumerate(monkeypatch):
    sentinel = (
        Volume(
            mountpoint="/",
            device="/dev/sda1",
            fstype="ext4",
            total_bytes=100,
            used_bytes=10,
            free_bytes=90,
            percent_used=10.0,
            is_network=False,
        ),
    )
    monkeypatch.setattr("opskit.storage.api.enumerate_.list_volumes", lambda: sentinel)
    assert api.list_volumes() == sentinel


def test_dir_size_rejects_negative_depth_before_any_io(monkeypatch):
    called = False

    def fake_dir_size(path, *, depth, include_hidden):
        nonlocal called
        called = True
        raise AssertionError("scan.dir_size should not be reached")

    monkeypatch.setattr("opskit.storage.api.scan.dir_size", fake_dir_size)
    with pytest.raises(UsageError):
        api.dir_size("/data", depth=-1)
    assert called is False


def test_dir_size_delegates_to_scan(monkeypatch, tmp_path):
    sentinel = DirSizeResult(
        path=str(tmp_path),
        total_bytes=42,
        file_count=1,
        dir_count=1,
        include_hidden=True,
        depth_requested=2,
        breakdown=(),
        inaccessible=(),
    )

    def fake_dir_size(path, *, depth, include_hidden):
        assert depth == 2
        assert include_hidden is True
        return sentinel

    monkeypatch.setattr("opskit.storage.api.scan.dir_size", fake_dir_size)
    result = api.dir_size(tmp_path, depth=2, include_hidden=True)
    assert result is sentinel


def test_dir_size_propagates_path_not_found(monkeypatch):
    def fake_dir_size(path, *, depth, include_hidden):
        raise PathNotFound("path does not exist: x")

    monkeypatch.setattr("opskit.storage.api.scan.dir_size", fake_dir_size)
    with pytest.raises(PathNotFound):
        api.dir_size("/no/such/path")


def test_dir_size_propagates_path_permission_denied(monkeypatch):
    def fake_dir_size(path, *, depth, include_hidden):
        raise PathPermissionDenied("cannot list directory: x")

    monkeypatch.setattr("opskit.storage.api.scan.dir_size", fake_dir_size)
    with pytest.raises(PathPermissionDenied):
        api.dir_size("/blocked")


def test_contracts_python_api_example_runs_as_written(tmp_path, capsys):
    """The exact usage example from contracts/python-api.md, executed unmodified.

    Runs against the real local machine (no mocking) — the same guarantee `tls`'s
    equivalent test gives against its loopback server (SC-005).
    """
    from opskit.storage import PathNotFound, dir_size, list_disks, list_volumes

    for vol in list_volumes():
        print(vol.mountpoint, vol.fstype, f"{vol.percent_used:.1f}%")

    for disk in list_disks():
        print(disk.id, disk.size_bytes, disk.model or "(model unavailable)")
        for part in disk.partitions:
            print(" ", part.device, part.mountpoint or "(not mounted)")

    (tmp_path / "a.bin").write_bytes(b"\0" * 10)
    result = dir_size(tmp_path, depth=1)
    print(result.total_bytes, "incomplete:", result.incomplete)
    for child in result.breakdown:
        print(" ", child.path, child.size_bytes)

    try:
        dir_size(tmp_path / "no" / "such" / "path")
    except PathNotFound as exc:
        print(exc.message, "—", exc.hint)

    out = capsys.readouterr().out
    assert "10 incomplete: False" in out
    assert "does not exist" in out
