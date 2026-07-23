"""Tests for storage/scan.py: totals, depth breakdown, hidden toggle, symlinks, errors."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.storage import scan
from opskit.storage.errors import PathNotFound, PathPermissionDenied

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits don't apply on Windows"
)


def _write(path: Path, size: int) -> None:
    path.write_bytes(b"\0" * size)


def test_dir_size_total_matches_known_fixture(tmp_path):
    _write(tmp_path / "a.bin", 10)
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub" / "b.bin", 20)
    (tmp_path / "sub" / "nested").mkdir()
    _write(tmp_path / "sub" / "nested" / "c.bin", 30)

    result = scan.dir_size(tmp_path)
    assert result.total_bytes == 60
    assert result.file_count == 3
    assert result.dir_count == 3  # root + sub + nested
    assert result.breakdown == ()
    assert result.inaccessible == ()
    assert not result.incomplete


def test_dir_size_depth_breakdown_levels(tmp_path):
    _write(tmp_path / "a.bin", 10)
    (tmp_path / "sub1").mkdir()
    _write(tmp_path / "sub1" / "b.bin", 20)
    (tmp_path / "sub1" / "sub2").mkdir()
    _write(tmp_path / "sub1" / "sub2" / "c.bin", 30)
    (tmp_path / "sub3").mkdir()
    _write(tmp_path / "sub3" / "d.bin", 5)

    result = scan.dir_size(tmp_path, depth=2)
    by_path = {Path(c.path).name: c.size_bytes for c in result.breakdown}
    assert by_path == {"sub1": 50, "sub2": 30, "sub3": 5}
    depths = {Path(c.path).name: c.depth for c in result.breakdown}
    assert depths == {"sub1": 1, "sub2": 2, "sub3": 1}


def test_dir_size_depth_deeper_than_tree_is_not_an_error(tmp_path):
    _write(tmp_path / "a.bin", 10)
    result = scan.dir_size(tmp_path, depth=50)
    assert result.total_bytes == 10
    assert result.breakdown == ()


def test_dir_size_depth_zero_has_no_breakdown(tmp_path):
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub" / "a.bin", 5)
    result = scan.dir_size(tmp_path)  # depth defaults to 0
    assert result.depth_requested == 0
    assert result.breakdown == ()
    assert result.total_bytes == 5


def test_dir_size_hidden_excluded_by_default(tmp_path):
    _write(tmp_path / "visible.bin", 10)
    _write(tmp_path / ".hidden.bin", 90)

    excluded = scan.dir_size(tmp_path)
    assert excluded.total_bytes == 10
    assert excluded.include_hidden is False

    included = scan.dir_size(tmp_path, include_hidden=True)
    assert included.total_bytes == 100
    assert included.include_hidden is True


def test_dir_size_hidden_directory_entirely_excluded(tmp_path):
    (tmp_path / ".hiddendir").mkdir()
    _write(tmp_path / ".hiddendir" / "x.bin", 500)
    _write(tmp_path / "visible.bin", 1)

    result = scan.dir_size(tmp_path)
    assert result.total_bytes == 1
    assert result.dir_count == 1  # hidden dir never pushed onto the walk stack


@_POSIX_ONLY
def test_dir_size_does_not_follow_symlinked_directory(tmp_path_factory):
    scanned = tmp_path_factory.mktemp("scanned")
    outside = tmp_path_factory.mktemp("outside")
    _write(outside / "big.bin", 1000)
    link = scanned / "link"
    link.symlink_to(outside, target_is_directory=True)
    _write(scanned / "small.bin", 1)

    result = scan.dir_size(scanned)
    assert (
        result.total_bytes == 1
    )  # the symlinked subtree (outside the scan root) is untouched
    assert result.dir_count == 1  # root only; "link" is not pushed as a directory


@_POSIX_ONLY
def test_dir_size_symlink_loop_does_not_hang(tmp_path):
    loop = tmp_path / "loop"
    loop.mkdir()
    (loop / "self").symlink_to(loop, target_is_directory=True)
    _write(tmp_path / "a.bin", 3)

    result = scan.dir_size(tmp_path)
    assert result.total_bytes == 3
    assert result.inaccessible == ()


@_POSIX_ONLY
def test_dir_size_permission_denied_subdirectory_is_skipped_not_fatal(tmp_path):
    (tmp_path / "ok").mkdir()
    _write(tmp_path / "ok" / "a.bin", 10)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _write(blocked / "b.bin", 999)
    blocked.chmod(0o000)
    try:
        result = scan.dir_size(tmp_path)
    finally:
        blocked.chmod(0o755)

    assert result.total_bytes == 10  # blocked subtree contributes 0, not a crash
    assert result.incomplete is True
    assert len(result.inaccessible) == 1
    assert result.inaccessible[0].path == str(blocked)


def test_dir_size_path_not_found(tmp_path):
    with pytest.raises(PathNotFound):
        scan.dir_size(tmp_path / "does-not-exist")


def test_dir_size_path_is_a_file_not_a_directory(tmp_path):
    file_path = tmp_path / "f.txt"
    file_path.write_text("hi")
    with pytest.raises(PathNotFound):
        scan.dir_size(file_path)


@_POSIX_ONLY
def test_dir_size_top_level_permission_denied_is_raised(tmp_path):
    blocked = tmp_path / "blocked-root"
    blocked.mkdir()
    blocked.chmod(0o000)
    try:
        with pytest.raises(PathPermissionDenied):
            scan.dir_size(blocked)
    finally:
        blocked.chmod(0o755)


class _FakeStat:
    def __init__(self, attributes: int) -> None:
        self.st_file_attributes = attributes


class _FakeEntry:
    """A minimal os.DirEntry stand-in for exercising the Windows hidden-attribute branch
    on any platform (the branch itself is gated on `scan._WINDOWS`, not the real OS)."""

    def __init__(self, name: str, attributes: int = 0) -> None:
        self.name = name
        self._attributes = attributes

    def stat(self, *, follow_symlinks=False):
        return _FakeStat(self._attributes)


def test_is_hidden_windows_attribute_branch(monkeypatch):
    monkeypatch.setattr(scan, "_WINDOWS", True)
    import stat as stat_module

    hidden = _FakeEntry("normal_name.txt", attributes=stat_module.FILE_ATTRIBUTE_HIDDEN)
    visible = _FakeEntry("normal_name.txt", attributes=0)
    assert scan._is_hidden(hidden) is True
    assert scan._is_hidden(visible) is False


def test_is_hidden_dotfile_wins_on_any_platform(monkeypatch):
    monkeypatch.setattr(scan, "_WINDOWS", False)
    assert scan._is_hidden(_FakeEntry(".dotfile")) is True


# --- property test over the pure aggregation logic (no filesystem) ------------------


@st.composite
def _tree(draw):
    """A random valid tree: node 0 is root; every other node's parent has a smaller index."""
    n = draw(st.integers(min_value=1, max_value=12))
    parent_index: list[int | None] = [None]
    for i in range(1, n):
        parent_index.append(draw(st.integers(min_value=0, max_value=i - 1)))
    own_bytes = draw(
        st.lists(st.integers(min_value=0, max_value=1000), min_size=n, max_size=n)
    )
    failed = draw(st.sets(st.integers(min_value=0, max_value=n - 1), max_size=n))
    return parent_index, own_bytes, failed


@given(_tree())
def test_aggregate_total_equals_sum_of_own_bytes(tree):
    parent_index, own_bytes, failed = tree
    paths = [Path(f"/node{i}") for i in range(len(parent_index))]
    state = scan._ScanState()
    state.preorder = paths
    state.parent_of = {
        paths[i]: (paths[p] if p is not None else None)
        for i, p in enumerate(parent_index)
    }
    state.own_bytes = dict(zip(paths, own_bytes))
    state.failed = {paths[i] for i in failed}

    total_of, incomplete_of = scan._aggregate(state)

    assert total_of[paths[0]] == sum(own_bytes)
    if failed:
        assert incomplete_of[paths[0]] is True
    else:
        assert incomplete_of[paths[0]] is False
