"""Tests for storage/enumerate_.py: psutil boundary, pseudo-fs filter, network classifier."""

from __future__ import annotations

from types import SimpleNamespace

from opskit.storage import enumerate_


def _part(device="", mountpoint="/", fstype="ext4", opts="rw,relatime"):
    return SimpleNamespace(
        device=device, mountpoint=mountpoint, fstype=fstype, opts=opts
    )


def test_raw_partitions_calls_psutil_with_all_true(monkeypatch):
    captured = {}

    def fake_disk_partitions(all=False):
        captured["all"] = all
        return [_part()]

    monkeypatch.setattr(enumerate_.psutil, "disk_partitions", fake_disk_partitions)
    result = enumerate_._raw_partitions()
    assert captured["all"] is True
    assert result[0].mountpoint == "/"


def test_is_pseudo_true_for_known_pseudo_filesystems():
    for fstype in ("tmpfs", "proc", "sysfs", "cgroup2", "overlay", "devtmpfs", "TMPFS"):
        assert enumerate_._is_pseudo(fstype), fstype


def test_is_pseudo_false_for_real_filesystems():
    for fstype in ("ext4", "ntfs", "apfs", "xfs", "nfs4", "btrfs"):
        assert not enumerate_._is_pseudo(fstype), fstype


def test_is_network_true_for_known_network_fstypes():
    for fstype in ("nfs", "nfs4", "cifs", "smbfs"):
        assert enumerate_._is_network(fstype, "rw,relatime")


def test_is_network_false_for_local_filesystem():
    assert not enumerate_._is_network("ext4", "rw,relatime,discard")


def test_is_network_true_for_windows_remote_opts():
    """Windows: psutil encodes GetDriveTypeW's DRIVE_REMOTE into opts (research R2/R3)."""
    assert enumerate_._is_network("NTFS", "rw,remote")


def test_is_network_false_for_windows_fixed_opts():
    assert not enumerate_._is_network("NTFS", "rw,fixed")


def _usage(total=1000, used=400, free=600, percent=40.0):
    return SimpleNamespace(total=total, used=used, free=free, percent=percent)


def test_list_volumes_excludes_pseudo_filesystems(monkeypatch):
    parts = [
        _part(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        _part(device="", mountpoint="/proc", fstype="proc"),
        _part(device="", mountpoint="/mnt/ramdisk", fstype="tmpfs"),
    ]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", lambda mp: _usage())
    result = enumerate_.list_volumes()
    assert [v.mountpoint for v in result] == ["/"]


def test_list_volumes_tags_network_mount(monkeypatch):
    parts = [_part(device="nfs-server:/export", mountpoint="/mnt/nfs", fstype="nfs4")]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", lambda mp: _usage())
    result = enumerate_.list_volumes()
    assert result[0].is_network is True


def test_list_volumes_reports_utilization_fields(monkeypatch):
    parts = [_part(device="/dev/sda1", mountpoint="/", fstype="ext4")]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(
        enumerate_.psutil,
        "disk_usage",
        lambda mp: _usage(total=1000, used=250, free=750, percent=25.0),
    )
    volume = enumerate_.list_volumes()[0]
    assert (volume.total_bytes, volume.used_bytes, volume.free_bytes) == (
        1000,
        250,
        750,
    )
    assert volume.percent_used == 25.0
    assert volume.device == "/dev/sda1"
    assert volume.fstype == "ext4"


def test_list_volumes_skips_unreadable_mount_without_aborting(monkeypatch):
    parts = [
        _part(device="/dev/sda1", mountpoint="/broken", fstype="ext4"),
        _part(device="/dev/sdb1", mountpoint="/ok", fstype="ext4"),
    ]

    def fake_disk_usage(mountpoint):
        if mountpoint == "/broken":
            raise OSError("stale mount")
        return _usage()

    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", fake_disk_usage)
    result = enumerate_.list_volumes()
    assert [v.mountpoint for v in result] == ["/ok"]


# --- list_disks() -------------------------------------------------------------------


def test_list_disks_linux_full_fidelity(monkeypatch):
    monkeypatch.setattr(enumerate_.sys, "platform", "linux")
    monkeypatch.setattr(
        enumerate_,
        "_raw_partitions",
        lambda: [_part(device="/dev/sda1", mountpoint="/")],
    )
    monkeypatch.setattr(enumerate_.linux_block, "block_device_names", lambda: ("sda",))
    monkeypatch.setattr(
        enumerate_.linux_block, "partition_names", lambda disk: ("sda1",)
    )
    monkeypatch.setattr(enumerate_.linux_block, "disk_size_bytes", lambda disk: 1000)
    monkeypatch.setattr(enumerate_.linux_block, "disk_model", lambda disk: "ACME SSD")
    monkeypatch.setattr(enumerate_.linux_block, "disk_removable", lambda disk: False)
    monkeypatch.setattr(
        enumerate_.linux_block, "partition_size_bytes", lambda disk, part: 900
    )

    disks = enumerate_.list_disks()
    assert len(disks) == 1
    disk = disks[0]
    assert disk.id == "sda"
    assert disk.size_bytes == 1000
    assert disk.model == "ACME SSD"
    assert disk.removable is False
    assert len(disk.partitions) == 1
    partition = disk.partitions[0]
    assert partition.device == "/dev/sda1"
    assert partition.size_bytes == 900
    assert partition.mounted is True
    assert partition.mountpoint == "/"
    assert partition.fstype == "ext4"


def test_list_disks_linux_unmounted_partition(monkeypatch):
    monkeypatch.setattr(enumerate_.sys, "platform", "linux")
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: [])
    monkeypatch.setattr(enumerate_.linux_block, "block_device_names", lambda: ("sdb",))
    monkeypatch.setattr(
        enumerate_.linux_block, "partition_names", lambda disk: ("sdb1",)
    )
    monkeypatch.setattr(enumerate_.linux_block, "disk_size_bytes", lambda disk: 2000)
    monkeypatch.setattr(enumerate_.linux_block, "disk_model", lambda disk: None)
    monkeypatch.setattr(enumerate_.linux_block, "disk_removable", lambda disk: None)
    monkeypatch.setattr(
        enumerate_.linux_block, "partition_size_bytes", lambda disk, part: 2000
    )

    disk = enumerate_.list_disks()[0]
    assert disk.model is None
    assert disk.removable is None
    partition = disk.partitions[0]
    assert partition.mounted is False
    assert partition.mountpoint is None
    assert partition.fstype is None


def test_list_disks_linux_whole_disk_mount_synthesizes_partition(monkeypatch):
    """A disk with no OS partition table, mounted directly (common on cloud/VM disks)."""
    monkeypatch.setattr(enumerate_.sys, "platform", "linux")
    monkeypatch.setattr(
        enumerate_,
        "_raw_partitions",
        lambda: [_part(device="/dev/sdd", mountpoint="/", fstype="ext4")],
    )
    monkeypatch.setattr(enumerate_.linux_block, "block_device_names", lambda: ("sdd",))
    monkeypatch.setattr(enumerate_.linux_block, "partition_names", lambda disk: ())
    monkeypatch.setattr(enumerate_.linux_block, "disk_size_bytes", lambda disk: 5000)
    monkeypatch.setattr(enumerate_.linux_block, "disk_model", lambda disk: None)
    monkeypatch.setattr(enumerate_.linux_block, "disk_removable", lambda disk: False)

    disk = enumerate_.list_disks()[0]
    assert len(disk.partitions) == 1
    assert disk.partitions[0].device == "/dev/sdd"
    assert disk.partitions[0].mountpoint == "/"
    assert disk.partitions[0].mounted is True


def test_list_disks_windows_derives_one_per_volume(monkeypatch):
    monkeypatch.setattr(enumerate_.sys, "platform", "win32")
    parts = [
        _part(device="C:\\", mountpoint="C:\\", fstype="NTFS", opts="rw,fixed"),
        _part(device="D:\\", mountpoint="D:\\", fstype="NTFS", opts="rw,removable"),
    ]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", lambda mp: _usage(total=12345))

    disks = enumerate_.list_disks()
    assert len(disks) == 2
    fixed, removable = disks
    assert fixed.id == "C:\\"
    assert fixed.removable is False
    assert fixed.model is None
    assert fixed.size_bytes == 12345
    assert fixed.partitions[0].mounted is True
    assert removable.removable is True


def test_list_disks_macos_removable_always_unavailable(monkeypatch):
    monkeypatch.setattr(enumerate_.sys, "platform", "darwin")
    parts = [_part(device="/dev/disk1s1", mountpoint="/", fstype="apfs", opts="rw")]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", lambda mp: _usage(total=999))

    disk = enumerate_.list_disks()[0]
    assert disk.removable is None
    assert disk.model is None
    assert disk.size_bytes == 999


def test_list_disks_windows_macos_excludes_pseudo_filesystems(monkeypatch):
    monkeypatch.setattr(enumerate_.sys, "platform", "darwin")
    parts = [_part(device="", mountpoint="/proc", fstype="proc", opts="rw")]
    monkeypatch.setattr(enumerate_, "_raw_partitions", lambda: parts)
    monkeypatch.setattr(enumerate_.psutil, "disk_usage", lambda mp: _usage())
    assert enumerate_.list_disks() == ()
