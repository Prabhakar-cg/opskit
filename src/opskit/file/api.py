"""Public file-operations API — the CLI is a thin client over this module.

Functions return typed results on success and raise :class:`opskit.file.errors.FileError`
subclasses (or :class:`opskit.core.errors.UsageError`) on failure. Nothing here prints or
calls ``sys.exit``.

Every function here takes a **single** target path — matching the ``storage.dir_size``/
``net.probe`` precedent — batching over multiple targets is the CLI layer's job
(:mod:`opskit.core.cliutils`), keeping this module free of batch/output concerns.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from opskit.core.errors import UsageError
from opskit.file import atomic, formats, textstats
from opskit.file.errors import InvalidContent
from opskit.file.models import (
    ConversionResult,
    LineEnding,
    LineEndingReport,
    StructuredFormat,
    ValidationResult,
)


class WriteOutcome(NamedTuple):
    """The outcome of a guarded write command.

    ``stdout_content`` is populated only when neither ``output`` nor ``in_place`` was
    requested — the CLI (never the library layer) is what actually prints it.
    """

    result: ConversionResult
    stdout_content: bytes | None


def _validate_write_destination(
    *, output: str | Path | None, in_place: bool, backup: bool
) -> None:
    """Shared usage-level validation for every guarded write command (FR-014, FR-015)."""
    if in_place and output is not None:
        raise UsageError("--output and --in-place are mutually exclusive")
    if backup and not in_place:
        raise UsageError("--backup requires --in-place")


def validate(
    path: str | Path, *, format: StructuredFormat | None = None
) -> ValidationResult:
    """Check whether ``path`` is syntactically valid JSON/YAML/TOML/XML (FR-003).

    Args:
        path: The file to validate.
        format: Force this format instead of auto-detecting from extension/content.

    Returns:
        A :class:`~opskit.file.models.ValidationResult`. A syntax error, or a format that
        could not be determined at all, is reported as ``valid=False`` — never raised, since
        that's precisely what this command exists to detect.

    Raises:
        FileNotFoundOnDisk: ``path`` does not exist, or is not a regular file.
        FilePermissionDenied: ``path`` could not be read.
    """
    try:
        result = formats.load(path, format=format)
    except InvalidContent as exc:
        best_guess = format or formats.detect_from_extension(Path(path))
        return ValidationResult(
            path=str(path),
            format=best_guess,
            valid=False,
            error_line=exc.line,
            error_column=exc.column,
            error_message=exc.message,
        )
    return ValidationResult(path=str(path), format=result.format, valid=True)


def lineendings(path: str | Path) -> LineEndingReport:
    """Report CRLF/LF/lone-CR counts and mixed-style detection for ``path`` (FR-001).

    Raises:
        FileNotFoundOnDisk: ``path`` does not exist, or is not a regular file.
        FilePermissionDenied: ``path`` could not be read.
    """
    return textstats.detect_line_endings(path)


def _write_guarded(
    source: Path,
    content: bytes,
    *,
    output: str | Path | None,
    in_place: bool,
    backup: bool,
    force: bool,
    lossless: bool = True,
    lossy_reason: str | None = None,
) -> WriteOutcome:
    """Route ``content`` through the write-safety helper for every guarded write command.

    Shared by :func:`eol`, :func:`convert`, and :func:`pretty` so the
    output/in_place/backup/force contract (research R7) is implemented exactly once.
    """
    if in_place:
        backup_path = atomic.write_in_place(source, content, backup=backup, force=force)
        result = ConversionResult(
            path=str(source),
            destination=str(source),
            in_place=True,
            backup_path=backup_path,
            lossless=lossless,
            lossy_reason=lossy_reason,
        )
        return WriteOutcome(result=result, stdout_content=None)
    if output is not None:
        destination = Path(output)
        atomic.write_new_file(destination, content, force=force)
        result = ConversionResult(
            path=str(source),
            destination=str(destination),
            in_place=False,
            lossless=lossless,
            lossy_reason=lossy_reason,
        )
        return WriteOutcome(result=result, stdout_content=None)

    result = ConversionResult(
        path=str(source),
        destination="-",
        in_place=False,
        lossless=lossless,
        lossy_reason=lossy_reason,
    )
    return WriteOutcome(result=result, stdout_content=content)


def eol(
    path: str | Path,
    *,
    to: LineEnding,
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Normalize line endings in ``path`` to ``to`` (FR-011).

    Non-destructive by default (FR-015): without ``output``/``in_place``, ``path`` is only
    read — the normalized content comes back via ``stdout_content`` for the caller to print,
    rather than being written anywhere by this function.

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source, or writing the
            destination/backup, failed.
        ClobberRefused: ``backup`` was requested but ``<path>.bak`` already exists (or
            ``output`` already exists), and ``force`` was not passed.
    """
    _validate_write_destination(output=output, in_place=in_place, backup=backup)
    source = Path(path)
    content = formats.read_bytes(source)
    normalized = textstats.normalize_line_endings(content, to)
    return _write_guarded(
        source, normalized, output=output, in_place=in_place, backup=backup, force=force
    )


def _lossy_reason(*reasons: str | None) -> str | None:
    combined = [reason for reason in reasons if reason]
    return "; ".join(combined) if combined else None


def convert(
    path: str | Path,
    *,
    to: StructuredFormat,
    format: StructuredFormat | None = None,
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Convert ``path``'s structured data to ``to`` (FR-010).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source, or writing the
            destination/backup, failed.
        InvalidContent: the source fails to parse as its (detected/declared) format.
        ClobberRefused: ``backup`` was requested but ``<path>.bak`` already exists (or
            ``output`` already exists), and ``force`` was not passed.
        UsageError: ``to`` equals the detected/declared source format.
    """
    _validate_write_destination(output=output, in_place=in_place, backup=backup)
    source = Path(path)
    loaded = formats.load(source, format=format)
    if to is loaded.format:
        raise UsageError(
            f"source is already {to.value}; --to must differ from the source format"
        )
    dumped = formats.dump(loaded.data, to)
    return _write_guarded(
        source,
        dumped.content,
        output=output,
        in_place=in_place,
        backup=backup,
        force=force,
        lossless=loaded.lossless and dumped.lossless,
        lossy_reason=_lossy_reason(loaded.lossy_reason, dumped.lossy_reason),
    )


def pretty(
    path: str | Path,
    *,
    format: StructuredFormat | None = None,
    indent: int = 2,
    sort_keys: bool = False,
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Reformat ``path``'s layout without changing its data (FR-013).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source, or writing the
            destination/backup, failed.
        InvalidContent: the source fails to parse as its (detected/declared) format.
        ClobberRefused: as :func:`convert`.
        UsageError: the (detected/declared) format is TOML — its layout is already canonical.
    """
    _validate_write_destination(output=output, in_place=in_place, backup=backup)
    source = Path(path)
    loaded = formats.load(source, format=format)
    if loaded.format is StructuredFormat.TOML:
        raise UsageError(
            "TOML has no free-form pretty option",
            hint="its layout is already canonical",
        )
    dumped = formats.dump(
        loaded.data, loaded.format, indent=indent, sort_keys=sort_keys
    )
    return _write_guarded(
        source,
        dumped.content,
        output=output,
        in_place=in_place,
        backup=backup,
        force=force,
        lossless=loaded.lossless and dumped.lossless,
        lossy_reason=_lossy_reason(loaded.lossy_reason, dumped.lossy_reason),
    )
