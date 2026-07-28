"""Rendering of file-operations results to human-readable (rich) output.

Category-owned so :mod:`opskit.core` stays free of file models. File-derived and
user-supplied strings (paths, parse-error messages, detected type names) are escaped as rich
markup before printing to avoid markup injection.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from opskit.file.models import (
    MISSING,
    ChecksumResult,
    ConversionResult,
    DuplicateGroup,
    EncodingReport,
    IdentificationResult,
    LineEndingReport,
    StatResult,
    StructuralDiffResult,
    ValidationResult,
)


def render_validation(result: ValidationResult, *, console: Console) -> None:
    """Print one file's validation outcome."""
    fmt = result.format.value if result.format else "unknown"
    path = escape(result.path)
    if result.valid:
        console.print(f"[green]valid[/green]  {path}  ({fmt})")
        return
    location = ""
    if result.error_line is not None:
        location = f" @ {result.error_line}:{result.error_column}"
    message = (
        escape(result.error_message) if result.error_message else "invalid content"
    )
    console.print(f"[red]invalid[/red]  {path}  ({fmt}){location}  {message}")


def render_lineendings(result: LineEndingReport, *, console: Console) -> None:
    """Print one file's line-ending counts."""
    path = escape(result.path)
    style = "[yellow]mixed[/yellow]" if result.mixed else "[green]consistent[/green]"
    console.print(
        f"{style}  {path}  crlf={result.crlf_count} lf={result.lf_count} "
        f"cr={result.cr_count}"
    )


def render_conversion(result: ConversionResult, *, console: Console) -> None:
    """Print one guarded write command's outcome (`eol`/`convert`/`pretty`/`reencode`)."""
    path = escape(result.path)
    destination = escape(result.destination)
    note = f"  (backup: {escape(result.backup_path)})" if result.backup_path else ""
    if not result.lossless:
        reason = (
            escape(result.lossy_reason)
            if result.lossy_reason
            else "data may not round-trip exactly"
        )
        note += f"  [yellow]lossy: {reason}[/yellow]"
    console.print(f"[green]ok[/green]  {path} -> {destination}{note}")


def render_identify(result: IdentificationResult, *, console: Console) -> None:
    """Print one file's sniffed type, flagging an extension mismatch."""
    path = escape(result.path)
    detected = escape(result.detected_type)
    extension = escape(result.extension) or "(none)"
    if result.extension_matches:
        console.print(f"[green]{detected}[/green]  {path}  (.{extension})")
    else:
        console.print(
            f"[yellow]{detected}[/yellow]  {path}  "
            f"(.{extension} — [yellow]extension mismatch[/yellow])"
        )


def render_checksum(result: ChecksumResult, *, console: Console) -> None:
    """Print one file's checksum."""
    path = escape(result.path)
    console.print(f"{result.algorithm}:{result.digest}  {path}")


def render_encoding(result: EncodingReport, *, console: Console) -> None:
    """Print one file's detected encoding, BOM status, and any invalid sequences."""
    path = escape(result.path)
    encoding = escape(result.encoding)
    bits = ["bom" if result.has_bom else "no-bom"]
    if result.confidence is not None:
        bits.append(f"confidence={result.confidence:.2f}")
    if result.invalid_sequences:
        bits.append("[red]invalid sequences[/red]")
    console.print(f"{encoding}  {path}  ({', '.join(bits)})")


def _render_side(value: object) -> str:
    if value is MISSING:
        return "[dim](missing)[/dim]"
    return escape(repr(value))


def render_diff(result: StructuralDiffResult, *, console: Console) -> None:
    """Print a structural-diff outcome: equivalence, or each differing key path."""
    left = escape(result.left_path)
    right = escape(result.right_path)
    if result.equivalent:
        console.print(f"[green]equivalent[/green]  {left} == {right}")
        return
    console.print(f"[yellow]differs[/yellow]  {left} != {right}")
    for entry in result.differences:
        key_path = escape(entry.key_path)
        console.print(
            f"  {key_path}: {_render_side(entry.left_value)} "
            f"!= {_render_side(entry.right_value)}"
        )


def render_duplicates(groups: tuple[DuplicateGroup, ...], *, console: Console) -> None:
    """Print each group of duplicate files, or a "no duplicates" message."""
    if not groups:
        console.print("[green]no duplicates found[/green]")
        return
    for group in groups:
        console.print(
            f"[yellow]{group.digest}[/yellow]  "
            f"({group.size_bytes} bytes, {len(group.paths)} files)"
        )
        for path in group.paths:
            console.print(f"  {escape(path)}")


def render_stat(result: StatResult, *, console: Console) -> None:
    """Print one file's normalized metadata: size, permissions, owner, mtime, symlink target."""
    path = escape(result.path)
    permissions = escape(result.permissions) if result.permissions else "—"
    owner = escape(result.owner) if result.owner else "—"
    console.print(
        f"{path}  size={result.size_bytes}  perms={permissions}  owner={owner}  "
        f"modified={result.modified_at}"
    )
    if result.is_symlink:
        target = escape(result.symlink_target) if result.symlink_target else "?"
        console.print(f"  -> {target}")
