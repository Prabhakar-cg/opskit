"""Tests for storage/output.py: rendering, including markup-injection escaping."""

from __future__ import annotations

import io

from rich.console import Console

from opskit.storage.models import (
    ChildDirSize,
    DirSizeResult,
    Disk,
    InaccessiblePath,
    Partition,
    Volume,
)
from opskit.storage.output import (
    _human_bytes,
    render_dir_size,
    render_disks,
    render_volumes,
)


def _console():
    buf = io.StringIO()
    return Console(file=buf, no_color=True, width=200), buf


def _volume(mountpoint="/", fstype="ext4", is_network=False):
    return Volume(
        mountpoint=mountpoint,
        device="/dev/sda1",
        fstype=fstype,
        total_bytes=1_000_000_000,
        used_bytes=400_000_000,
        free_bytes=600_000_000,
        percent_used=40.0,
        is_network=is_network,
    )


def test_render_volumes_empty_notice():
    console, buf = _console()
    render_volumes([], console=console)
    assert "No mounted volumes found." in buf.getvalue()


def test_render_volumes_shows_fields():
    console, buf = _console()
    render_volumes([_volume()], console=console)
    out = buf.getvalue()
    assert "/" in out
    assert "ext4" in out
    assert "40.0%" in out
    assert "local" in out


def test_render_volumes_network_tag():
    console, buf = _console()
    render_volumes([_volume(is_network=True)], console=console)
    assert "network" in buf.getvalue()


def test_render_volumes_escapes_markup_injection():
    """A mountpoint/fstype containing rich markup must not be interpreted as styling."""
    console, buf = _console()
    render_volumes(
        [_volume(mountpoint="[bold red]evil[/]", fstype="ext4")], console=console
    )
    out = buf.getvalue()
    assert "[bold red]evil[/]" in out


def test_human_bytes_formatting():
    assert _human_bytes(0) == "0 B"
    assert _human_bytes(1023) == "1023 B"
    assert _human_bytes(1024) == "1.0 KiB"
    assert _human_bytes(1024**3) == "1.0 GiB"


def _disk(
    disk_id="sda", size_bytes=1000, model="ACME SSD", removable=False, partitions=()
):
    return Disk(
        id=disk_id,
        size_bytes=size_bytes,
        model=model,
        removable=removable,
        partitions=partitions,
    )


def test_render_disks_empty_notice():
    console, buf = _console()
    render_disks([], console=console)
    assert "No disks found." in buf.getvalue()


def test_render_disks_shows_fields():
    console, buf = _console()
    partition = Partition(
        device="/dev/sda1", size_bytes=900, mounted=True, mountpoint="/", fstype="ext4"
    )
    render_disks([_disk(partitions=(partition,))], console=console)
    out = buf.getvalue()
    assert "sda" in out
    assert "ACME SSD" in out
    assert "/dev/sda1" in out
    assert "/" in out
    assert "ext4" in out


def test_render_disks_unavailable_fields_show_placeholder():
    console, buf = _console()
    render_disks([_disk(model=None, removable=None, size_bytes=None)], console=console)
    out = buf.getvalue()
    assert "—" in out


def test_render_disks_unmounted_partition_placeholder():
    console, buf = _console()
    partition = Partition(device="/dev/sdb1", size_bytes=500, mounted=False)
    render_disks([_disk("sdb", partitions=(partition,))], console=console)
    out = buf.getvalue()
    assert "no" in out  # mounted column


def test_render_disks_escapes_markup_injection():
    console, buf = _console()
    render_disks([_disk(disk_id="[red]evil[/]", model="[bold]x[/]")], console=console)
    out = buf.getvalue()
    assert "[red]evil[/]" in out
    assert "[bold]x[/]" in out


def _size_result(path="/data", total=100, breakdown=(), inaccessible=()):
    return DirSizeResult(
        path=path,
        total_bytes=total,
        file_count=3,
        dir_count=1,
        include_hidden=False,
        depth_requested=1,
        breakdown=breakdown,
        inaccessible=inaccessible,
    )


def test_render_dir_size_basic_line():
    console, buf = _console()
    render_dir_size(_size_result(), console=console)
    out = buf.getvalue()
    assert "/data" in out
    assert "100 B" in out
    assert "hidden excluded" in out
    assert "incomplete" not in out


def test_render_dir_size_breakdown_table():
    console, buf = _console()
    breakdown = (
        ChildDirSize(path="/data/sub", depth=1, size_bytes=50, incomplete=False),
    )
    render_dir_size(_size_result(breakdown=breakdown), console=console)
    out = buf.getvalue()
    assert "/data/sub" in out
    assert "50 B" in out


def test_render_dir_size_incomplete_breakdown_entry_flagged():
    console, buf = _console()
    breakdown = (
        ChildDirSize(path="/data/sub", depth=1, size_bytes=0, incomplete=True),
    )
    render_dir_size(_size_result(breakdown=breakdown), console=console)
    out = buf.getvalue()
    assert "(incomplete)" in out


def test_render_dir_size_inaccessible_paths_listed():
    console, buf = _console()
    inaccessible = (InaccessiblePath(path="/data/blocked", reason="permission denied"),)
    render_dir_size(_size_result(inaccessible=inaccessible), console=console)
    out = buf.getvalue()
    assert "Inaccessible paths (skipped):" in out
    assert "/data/blocked" in out
    assert "permission denied" in out
    assert "lower bound" in out


def test_render_dir_size_escapes_markup_injection():
    console, buf = _console()
    inaccessible = (InaccessiblePath(path="[red]evil[/]", reason="[bold]bad[/]"),)
    render_dir_size(
        _size_result(path="[red]evil[/]", inaccessible=inaccessible), console=console
    )
    out = buf.getvalue()
    assert "[red]evil[/]" in out
    assert "[bold]bad[/]" in out
