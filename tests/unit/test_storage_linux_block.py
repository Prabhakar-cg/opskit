"""Tests for storage/linux_block.py: /sys/block parsing against a fixture sysfs tree."""

from __future__ import annotations

from opskit.storage import linux_block


def _make_disk(sys_block, name, *, size=2000, removable="0", model="ACME 2000"):
    disk_dir = sys_block / name
    disk_dir.mkdir(parents=True)
    (disk_dir / "size").write_text(str(size))
    (disk_dir / "removable").write_text(removable)
    if model is not None:
        (disk_dir / "device").mkdir()
        (disk_dir / "device" / "model").write_text(model)
    return disk_dir


def _make_partition(disk_dir, disk_name, num, *, size=1000):
    part_name = f"{disk_name}{num}"
    part_dir = disk_dir / part_name
    part_dir.mkdir()
    (part_dir / "size").write_text(str(size))
    (part_dir / "partition").write_text(str(num))
    return part_dir


def test_block_device_names_lists_top_level_disks(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "sda")
    _make_disk(sys_block, "sdb")
    assert linux_block.block_device_names(sys_block) == ("sda", "sdb")


def test_block_device_names_empty_when_sys_block_missing(tmp_path):
    assert linux_block.block_device_names(tmp_path / "does-not-exist") == ()


def test_partition_names_filters_by_partition_file(tmp_path):
    sys_block = tmp_path / "sys_block"
    disk_dir = _make_disk(sys_block, "sda")
    _make_partition(disk_dir, "sda", 1)
    _make_partition(disk_dir, "sda", 2)
    # Non-partition sibling directories (no `partition` file) must be excluded.
    (disk_dir / "queue").mkdir()
    (disk_dir / "holders").mkdir()

    assert linux_block.partition_names("sda", sys_block) == ("sda1", "sda2")


def test_partition_names_empty_for_unpartitioned_disk(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "sdd")
    assert linux_block.partition_names("sdd", sys_block) == ()


def test_disk_size_bytes_converts_sectors(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "sda", size=2048)  # 2048 sectors * 512 = 1_048_576 bytes
    assert linux_block.disk_size_bytes("sda", sys_block) == 1_048_576


def test_disk_size_bytes_none_when_unreadable(tmp_path):
    sys_block = tmp_path / "sys_block"
    assert linux_block.disk_size_bytes("nope", sys_block) is None


def test_disk_removable_true_and_false(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "usb0", removable="1")
    _make_disk(sys_block, "sda", removable="0")
    assert linux_block.disk_removable("usb0", sys_block) is True
    assert linux_block.disk_removable("sda", sys_block) is False


def test_disk_removable_none_when_unreadable(tmp_path):
    sys_block = tmp_path / "sys_block"
    assert linux_block.disk_removable("nope", sys_block) is None


def test_disk_model_present_and_absent(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "sda", model="Samsung SSD 970")
    _make_disk(sys_block, "loop0", model=None)
    assert linux_block.disk_model("sda", sys_block) == "Samsung SSD 970"
    assert linux_block.disk_model("loop0", sys_block) is None


def test_partition_size_bytes(tmp_path):
    sys_block = tmp_path / "sys_block"
    disk_dir = _make_disk(sys_block, "sda")
    _make_partition(disk_dir, "sda", 1, size=500)
    assert linux_block.partition_size_bytes("sda", "sda1", sys_block) == 500 * 512


def test_partition_size_bytes_none_when_missing(tmp_path):
    sys_block = tmp_path / "sys_block"
    _make_disk(sys_block, "sda")
    assert linux_block.partition_size_bytes("sda", "sda9", sys_block) is None
