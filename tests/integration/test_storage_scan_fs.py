"""End-to-end filesystem tests for opskit.storage.scan.dir_size (research R5, quickstart).

Real temp trees (no mocking): known sizes/nesting, a permission-denied subdirectory
(POSIX), hidden files/directories, and a symlink loop — proves the walk end-to-end rather
than unit-testing its pieces in isolation (that's tests/unit/test_storage_scan.py).
"""

from __future__ import annotations

import sys

import pytest

from opskit.storage import scan

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits used to build this fixture tree",
)


def _write(path, size):
    path.write_bytes(b"\0" * size)


@pytest.fixture
def fixture_tree(tmp_path):
    """A tree mixing normal, hidden, permission-denied, and symlinked-loop content.

    root/
      visible.bin        (10 bytes)
      .hidden.bin         (100 bytes, excluded by default)
      normal/
        a.bin             (20 bytes)
        b.bin             (30 bytes)
      blocked/            (chmod 000 -> inaccessible; would-be 999 bytes never counted)
        c.bin             (999 bytes)
      looped/
        self -> looped     (symlink cycle; must not hang)
        d.bin             (5 bytes)
    """
    _write(tmp_path / "visible.bin", 10)
    _write(tmp_path / ".hidden.bin", 100)

    normal = tmp_path / "normal"
    normal.mkdir()
    _write(normal / "a.bin", 20)
    _write(normal / "b.bin", 30)

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _write(blocked / "c.bin", 999)

    looped = tmp_path / "looped"
    looped.mkdir()
    _write(looped / "d.bin", 5)
    (looped / "self").symlink_to(looped, target_is_directory=True)

    blocked.chmod(0o000)
    yield tmp_path
    blocked.chmod(0o755)  # restore so pytest can clean up tmp_path afterwards


def test_full_tree_scan_end_to_end(fixture_tree):
    result = scan.dir_size(fixture_tree)

    # visible(10) + normal/a(20) + normal/b(30) + looped/d(5) = 65; hidden(100) and
    # blocked/c(999) both excluded — the former by default, the latter by permission.
    assert result.total_bytes == 65
    assert result.include_hidden is False
    assert result.incomplete is True
    assert len(result.inaccessible) == 1
    assert result.inaccessible[0].path == str(fixture_tree / "blocked")

    # the symlink loop under looped/self did not cause infinite recursion / a hang
    assert result.file_count == 4  # visible, normal/a, normal/b, looped/d


def test_full_tree_scan_with_hidden_and_depth(fixture_tree):
    result = scan.dir_size(fixture_tree, depth=1, include_hidden=True)

    # now .hidden.bin(100) is included too: 65 + 100 = 165; blocked/c still excluded
    assert result.total_bytes == 165
    by_name = {c.path.split("/")[-1]: c.size_bytes for c in result.breakdown}
    assert by_name["normal"] == 50
    assert by_name["blocked"] == 0  # inaccessible -> lower-bound 0, not fabricated
    assert by_name["looped"] == 5

    blocked_entry = next(c for c in result.breakdown if c.path.endswith("blocked"))
    assert blocked_entry.incomplete is True
    normal_entry = next(c for c in result.breakdown if c.path.endswith("normal"))
    assert normal_entry.incomplete is False
