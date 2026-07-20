"""Rendering of storage results to human-readable (rich) tables.

Category-owned so :mod:`opskit.core` stays free of storage models. OS-derived and
user-supplied strings (paths, device names, disk models) are escaped as rich markup before
printing to avoid markup injection.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from opskit.storage.models import DirSizeResult, Disk, Volume

_BYTES_PER_UNIT = 1024.0


def _human_bytes(size: int) -> str:
    """Render a byte count as a human-readable size (binary units, e.g. ``12.3 GiB``)."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < _BYTES_PER_UNIT:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= _BYTES_PER_UNIT
    return f"{value:.1f} EiB"


def render_volumes(volumes: Sequence[Volume], *, console: Console) -> None:
    """Print mounted volumes as a table (or a plain notice when there are none)."""
    if not volumes:
        console.print("No mounted volumes found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("MOUNT")
    table.add_column("FSTYPE")
    table.add_column("TOTAL", justify="right")
    table.add_column("USED", justify="right")
    table.add_column("FREE", justify="right")
    table.add_column("%USED", justify="right")
    table.add_column("KIND")
    for volume in volumes:
        table.add_row(
            escape(volume.mountpoint),
            escape(volume.fstype),
            _human_bytes(volume.total_bytes),
            _human_bytes(volume.used_bytes),
            _human_bytes(volume.free_bytes),
            f"{volume.percent_used:.1f}%",
            "network" if volume.is_network else "local",
        )
    console.print(table)


def render_dir_size(result: DirSizeResult, *, console: Console) -> None:
    """Print a directory-size result: totals, breakdown table, inaccessible paths."""
    mode = "hidden included" if result.include_hidden else "hidden excluded"
    status = " [yellow](incomplete — lower bound)[/yellow]" if result.incomplete else ""
    console.print(
        f"[bold]{escape(result.path)}[/bold]  "
        f"{_human_bytes(result.total_bytes)}  "
        f"({result.file_count} files, {result.dir_count} dirs, {mode}){status}"
    )
    if result.breakdown:
        table = Table(show_header=True, header_style="bold")
        table.add_column("DEPTH", justify="right")
        table.add_column("PATH")
        table.add_column("SIZE", justify="right")
        for child in result.breakdown:
            path = escape(child.path)
            row_path = (
                f"{path} [yellow](incomplete)[/yellow]" if child.incomplete else path
            )
            table.add_row(str(child.depth), row_path, _human_bytes(child.size_bytes))
        console.print(table)
    if result.inaccessible:
        console.print("[yellow]Inaccessible paths (skipped):[/yellow]")
        for item in result.inaccessible:
            console.print(f"  {escape(item.path)}: {escape(item.reason)}")


def _yes_no_unknown(value: bool | None) -> str:
    if value is None:
        return "—"
    return "yes" if value else "no"


def render_disks(disks: Sequence[Disk], *, console: Console) -> None:
    """Print each disk as a panel-like header with a nested partitions table.

    Best-effort/unavailable fields (research R2) render as ``—``, never fabricated.
    """
    if not disks:
        console.print("No disks found.")
        return
    for disk in disks:
        size = _human_bytes(disk.size_bytes) if disk.size_bytes is not None else "—"
        model = escape(disk.model) if disk.model else "—"
        console.print(
            f"[bold]{escape(disk.id)}[/bold]  {size}  "
            f"model: {model}  removable: {_yes_no_unknown(disk.removable)}"
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("DEVICE")
        table.add_column("SIZE", justify="right")
        table.add_column("MOUNTED")
        table.add_column("MOUNTPOINT")
        table.add_column("FSTYPE")
        for partition in disk.partitions:
            part_size = (
                _human_bytes(partition.size_bytes)
                if partition.size_bytes is not None
                else "—"
            )
            table.add_row(
                escape(partition.device),
                part_size,
                "yes" if partition.mounted else "no",
                escape(partition.mountpoint) if partition.mountpoint else "—",
                escape(partition.fstype) if partition.fstype else "—",
            )
        console.print(table)
