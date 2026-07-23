"""``psutil``-backed volume/disk/partition enumeration.

The only module in this category that imports ``psutil`` (research R1) — quarantined here the
same way :mod:`opskit.ad.directory` quarantines ``ldap3``, so the rest of the category stays
free of the dependency's own type surface.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from typing import Protocol

import psutil

from opskit.storage import linux_block
from opskit.storage.models import Disk, Partition, Volume

_LOGGER = logging.getLogger("opskit")


def _is_linux() -> bool:
    """True on Linux.

    Indirected (rather than a literal ``sys.platform`` check in :func:`list_disks`) so
    mypy's ``sys.platform``-narrowing doesn't mark the Windows/macOS branch unreachable
    on a Linux dev/CI host.
    """
    return sys.platform.startswith("linux")


# Pseudo/virtual filesystem types that don't represent real storage capacity (research R4).
# Primarily relevant on Linux; Windows/macOS rarely surface these via disk_partitions(). Kept
# as opskit's own explicit, tested contract rather than relying on psutil's internal filtering.
_PSEUDO_FSTYPES: frozenset[str] = frozenset(
    {
        "tmpfs",
        "devtmpfs",
        "proc",
        "sysfs",
        "cgroup",
        "cgroup2",
        "overlay",
        "squashfs",
        "autofs",
        "rootfs",
        "rpc_pipefs",
        "binfmt_misc",
        "debugfs",
        "tracefs",
        "mqueue",
        "hugetlbfs",
        "fusectl",
        "configfs",
        "devpts",
        "securityfs",
        "pstore",
        "efivarfs",
        "bpf",
        "nsfs",
    }
)

# Filesystem types known to be network-mounted (research R3). Windows classification is
# additionally derived from `opts` (psutil's `GetDriveTypeW` encoding) in `_is_network`.
_NETWORK_FSTYPES: frozenset[str] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "afpfs",
        "fuse.sshfs",
        "davfs",
        "ftpfs",
        "webdav",
    }
)


class _RawPartition(Protocol):
    """Structural shape of ``psutil``'s partition tuple (no dependency on its private type).

    Declared as read-only properties, not plain attributes: ``psutil``'s ``sdiskpart`` is a
    ``NamedTuple``, whose fields are read-only and only satisfy a :class:`Protocol` member
    that's likewise declared read-only.
    """

    @property
    def device(self) -> str: ...
    @property
    def mountpoint(self) -> str: ...
    @property
    def fstype(self) -> str: ...
    @property
    def opts(self) -> str: ...


def _raw_partitions() -> Sequence[_RawPartition]:
    """Every partition ``psutil`` can see, unfiltered (pseudo filesystems included)."""
    return psutil.disk_partitions(all=True)


def _is_pseudo(fstype: str) -> bool:
    """True when ``fstype`` is a pseudo/virtual filesystem excluded from volumes (FR-002)."""
    return fstype.lower() in _PSEUDO_FSTYPES


def _is_network(fstype: str, opts: str) -> bool:
    """Best-effort local-vs-network classification (research R3)."""
    if fstype.lower() in _NETWORK_FSTYPES:
        return True
    return "remote" in (part.strip() for part in opts.lower().split(","))


def list_volumes() -> tuple[Volume, ...]:
    """Every mounted, non-pseudo volume visible to the current user (FR-001, FR-002, FR-003).

    A mount whose usage can't be read (e.g. it disappeared mid-enumeration, or the current
    user lacks permission to stat it) is skipped rather than aborting the whole list.
    """
    volumes: list[Volume] = []
    for part in _raw_partitions():
        if _is_pseudo(part.fstype):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            _LOGGER.debug("storage: skipping unreadable mount %s", part.mountpoint)
            continue
        volumes.append(
            Volume(
                mountpoint=part.mountpoint,
                device=part.device,
                fstype=part.fstype,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                percent_used=usage.percent,
                is_network=_is_network(part.fstype, part.opts),
            )
        )
    return tuple(volumes)


def _windows_removable(opts: str) -> bool | None:
    """Fixed/removable/CD-ROM, decoded from `psutil`'s ``GetDriveTypeW`` encoding (R2)."""
    tokens = {token.strip() for token in opts.lower().split(",")}
    if "removable" in tokens or "cdrom" in tokens:
        return True
    if "fixed" in tokens:
        return False
    return None


def _list_disks_linux() -> tuple[Disk, ...]:
    """Full-fidelity disk/partition inventory via `/sys/block` (research R2)."""
    mounted_by_device = {part.device: part for part in _raw_partitions() if part.device}
    disks: list[Disk] = []
    for disk_name in linux_block.block_device_names():
        partitions: list[Partition] = []
        for part_name in linux_block.partition_names(disk_name):
            device = f"/dev/{part_name}"
            raw = mounted_by_device.get(device)
            partitions.append(
                Partition(
                    device=device,
                    size_bytes=linux_block.partition_size_bytes(disk_name, part_name),
                    mounted=raw is not None,
                    mountpoint=raw.mountpoint if raw is not None else None,
                    fstype=raw.fstype if raw is not None else None,
                )
            )
        disk_size = linux_block.disk_size_bytes(disk_name)
        if not partitions:
            # No OS-level partition table (common for cloud/VM disks mounted whole, e.g.
            # /dev/sdd mounted directly as `/`): surface the disk's own mount, if any,
            # as a single partition rather than showing an empty table for a used disk.
            whole_disk_mount = mounted_by_device.get(f"/dev/{disk_name}")
            if whole_disk_mount is not None:
                partitions.append(
                    Partition(
                        device=f"/dev/{disk_name}",
                        size_bytes=disk_size,
                        mounted=True,
                        mountpoint=whole_disk_mount.mountpoint,
                        fstype=whole_disk_mount.fstype,
                    )
                )
        disks.append(
            Disk(
                id=disk_name,
                size_bytes=disk_size,
                model=linux_block.disk_model(disk_name),
                removable=linux_block.disk_removable(disk_name),
                partitions=tuple(partitions),
            )
        )
    return tuple(disks)


def _list_disks_from_partitions() -> tuple[Disk, ...]:
    """Windows/macOS v1: one Disk-equivalent per mounted volume (research R2).

    True physical-disk grouping and model/serial need WMI (Windows) or IOKit (macOS),
    both deferred; `size_bytes` uses the volume's own capacity as the best-effort stand-in,
    `removable` is decoded from `psutil`'s Windows-only `opts` encoding, and `model` stays
    explicitly unavailable everywhere here (FR-006).
    """
    is_windows = sys.platform.startswith("win")
    disks: list[Disk] = []
    for part in _raw_partitions():
        if _is_pseudo(part.fstype):
            continue
        try:
            size_bytes: int | None = psutil.disk_usage(part.mountpoint).total
        except OSError:
            size_bytes = None
        removable = _windows_removable(part.opts) if is_windows else None
        partition = Partition(
            device=part.device,
            size_bytes=size_bytes,
            mounted=True,
            mountpoint=part.mountpoint,
            fstype=part.fstype,
        )
        disks.append(
            Disk(
                id=part.device or part.mountpoint,
                size_bytes=size_bytes,
                model=None,
                removable=removable,
                partitions=(partition,),
            )
        )
    return tuple(disks)


def list_disks() -> tuple[Disk, ...]:
    """Every physical/logical disk, each with its nested partitions (FR-004, FR-005, FR-006).

    Full fidelity on Linux (`/sys/block`); on Windows/macOS, disks are derived one-to-one
    from mounted volumes with best-effort fields (research R2) — never fabricated, always
    explicitly ``None`` where the platform doesn't expose the data.
    """
    if _is_linux():
        return _list_disks_linux()
    return _list_disks_from_partitions()
