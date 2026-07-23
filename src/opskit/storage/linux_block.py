"""Linux ``/sys/block`` reader for full-fidelity physical disk/partition detail (research R2).

Pure stdlib file reads (no ``psutil``, no privilege required). Returns empty/best-effort data
on non-Linux platforms so callers don't need platform checks of their own.
"""

from __future__ import annotations

from pathlib import Path

_SECTOR_BYTES = 512
SYS_BLOCK = Path("/sys/block")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_size_bytes(dir_path: Path) -> int | None:
    """A sysfs ``size`` pseudo-file holds a sector count (512-byte sectors)."""
    raw = _read_text(dir_path / "size")
    if raw is None:
        return None
    try:
        return int(raw) * _SECTOR_BYTES
    except ValueError:
        return None


def block_device_names(sys_block: Path = SYS_BLOCK) -> tuple[str, ...]:
    """Every whole-disk block device name (e.g. ``sda``, ``nvme0n1``).

    Empty on any platform without a populated ``/sys/block`` (i.e. everywhere but Linux) —
    callers don't need a separate platform check.
    """
    try:
        return tuple(
            sorted(entry.name for entry in sys_block.iterdir() if entry.is_dir())
        )
    except OSError:
        return ()


def partition_names(disk: str, sys_block: Path = SYS_BLOCK) -> tuple[str, ...]:
    """Partition subdirectory names under ``disk``, identified by their ``partition`` file.

    Sysfs nests a disk's partitions as subdirectories of its own directory (e.g.
    ``/sys/block/sda/sda1``) alongside non-partition entries (``queue``, ``device``,
    ``holders``, …); only entries carrying a ``partition`` file are true partitions.
    """
    disk_dir = sys_block / disk
    try:
        entries = list(disk_dir.iterdir())
    except OSError:
        return ()
    return tuple(
        sorted(
            entry.name
            for entry in entries
            if entry.name.startswith(disk) and (entry / "partition").is_file()
        )
    )


def disk_size_bytes(disk: str, sys_block: Path = SYS_BLOCK) -> int | None:
    """The disk's total size, or ``None`` if the ``size`` pseudo-file is unreadable."""
    return _read_size_bytes(sys_block / disk)


def disk_removable(disk: str, sys_block: Path = SYS_BLOCK) -> bool | None:
    """Whether the disk is removable, or ``None`` if undeterminable."""
    raw = _read_text(sys_block / disk / "removable")
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None


def disk_model(disk: str, sys_block: Path = SYS_BLOCK) -> str | None:
    """Best-effort disk model/name (absent for many virtual/cloud block devices)."""
    return _read_text(sys_block / disk / "device" / "model") or None


def partition_size_bytes(
    disk: str, partition: str, sys_block: Path = SYS_BLOCK
) -> int | None:
    """The partition's size, or ``None`` if the ``size`` pseudo-file is unreadable."""
    return _read_size_bytes(sys_block / disk / partition)
