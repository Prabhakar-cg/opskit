"""Rendering of file-operations results to human-readable (rich) output.

Category-owned so :mod:`opskit.core` stays free of file models. File-derived and
user-supplied strings (paths, parse-error messages, detected type names) are escaped as rich
markup before printing to avoid markup injection.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from opskit.file.models import (
    ChecksumResult,
    ConversionResult,
    EncodingReport,
    IdentificationResult,
    LineEndingReport,
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
