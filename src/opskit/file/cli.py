"""Thin Typer sub-app for file operations: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.file.api` and turns typed
results/exceptions into human or JSON output and structured exit codes.

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer reads
   the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them to strings
   (PEP 563) makes Typer silently drop the ``Argument``/``Option`` metadata on Python 3.9, turning
   positional arguments into ``--options``. Keep annotations eager here.
"""

import sys
import time
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from opskit.core.cliutils import (
    aggregate_exit,
    collect_outcomes,
    collect_target_list,
    emit_envelopes,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import build_envelope
from opskit.file import api
from opskit.file.models import (
    ChecksumResult,
    ConversionResult,
    EncodingReport,
    IdentificationResult,
    LineEnding,
    LineEndingReport,
    StatResult,
    StructuredFormat,
    ValidationResult,
)
from opskit.file.output import (
    render_checksum,
    render_conversion,
    render_diff,
    render_duplicates,
    render_encoding,
    render_identify,
    render_lineendings,
    render_stat,
    render_validation,
)

app = typer.Typer(
    name="file",
    help="File operations: read-only diagnostics + guarded, opt-in conversions.",
    no_args_is_help=True,
)


def _error_exit(error: OpskitError) -> typer.Exit:
    """Report a failure to stderr and build its typed exit signal."""
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)
    return typer.Exit(int(exit_code_for(error)))


_VALIDATE_EPILOG = """\
[bold]Examples[/bold]

  opskit file validate config.json
  opskit file validate config.json config.yaml settings.toml
  opskit file validate config.txt --format json
  opskit file validate -i paths.txt --jsonl
"""


def _validate_envelope(
    path: str,
    result: Optional[ValidationResult],
    error: Optional[OpskitError],
    elapsed_ms: float,
    *,
    format: Optional[StructuredFormat],
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path, "format": format.value if format else None}
    if result is not None:
        return build_envelope(
            command="file.validate",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.validate",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


def _validate_exit_code(
    result: Optional[ValidationResult], error: Optional[OpskitError]
) -> ExitCode:
    """A target's outcome class: raised error > returned-but-invalid > OK.

    ``valid=False`` is a *returned* result, not a raised error, so it needs its own mapping
    to ``INVALID_CONTENT`` here — mirroring how storage's ``size`` maps a returned
    ``incomplete=True`` result to ``PARTIAL`` (research R9).
    """
    if error is not None:
        return exit_code_for(error)
    if result is not None and not result.valid:
        return ExitCode.INVALID_CONTENT
    return ExitCode.OK


@app.command(name="validate", epilog=_VALIDATE_EPILOG)
def validate_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to validate (or use --input-file)."),
    ] = None,
    format: Annotated[
        Optional[StructuredFormat],
        typer.Option(
            "--format",
            help="Force this format instead of auto-detecting.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Validate JSON/YAML/TOML/XML syntax, reporting the line/column of any error."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_validate(p: str) -> ValidationResult:
        start = time.perf_counter()
        try:
            return api.validate(p, format=format)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_validate)

    if as_json or jsonl:
        envelopes = [
            _validate_envelope(t, r, e, timings.get(t, 0.0), format=format)
            for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_validation(result, console=console)

    codes = [_validate_exit_code(r, e) for _, r, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_LINEENDINGS_EPILOG = """\
[bold]Examples[/bold]

  opskit file lineendings script.sh
  opskit file lineendings *.sh --json
  opskit file lineendings -i paths.txt --jsonl
"""


def _lineendings_envelope(
    path: str,
    result: Optional[LineEndingReport],
    error: Optional[OpskitError],
    elapsed_ms: float,
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path}
    if result is not None:
        return build_envelope(
            command="file.lineendings",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.lineendings",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.command(name="lineendings", epilog=_LINEENDINGS_EPILOG)
def lineendings_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to inspect (or use --input-file)."),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Report CRLF/LF/lone-CR counts and mixed-style detection per file."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_lineendings(p: str) -> LineEndingReport:
        start = time.perf_counter()
        try:
            return api.lineendings(p)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_lineendings)

    if as_json or jsonl:
        envelopes = [
            _lineendings_envelope(t, r, e, timings.get(t, 0.0)) for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_lineendings(result, console=console)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_EOL_EPILOG = """\
[bold]Examples[/bold]

  opskit file eol script.sh --to lf > fixed.sh
  opskit file eol script.sh --to lf --in-place --backup
  opskit file eol *.sh --to lf --in-place --jsonl
"""


def _eol_envelope(
    path: str,
    result: Optional[ConversionResult],
    error: Optional[OpskitError],
    elapsed_ms: float,
    *,
    to: LineEnding,
    in_place: bool,
    backup: bool,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "path": path,
        "to": to.value,
        "in_place": in_place,
        "backup": backup,
    }
    if result is not None:
        return build_envelope(
            command="file.eol",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.eol",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


def _check_write_flags(*, output: Optional[Path], in_place: bool, backup: bool) -> None:
    """Validate the write-destination flags shared by every guarded write command."""
    if output is not None and in_place:
        raise UsageError("--output and --in-place are mutually exclusive")
    if backup and not in_place:
        raise UsageError("--backup requires --in-place")


def _report_write_failure(
    error: OpskitError,
    *,
    command: str,
    query: dict[str, Any],
    elapsed_ms: float,
    as_json: bool,
) -> None:
    """Report a single-target write command's failure: an envelope in JSON mode, else stderr."""
    if as_json:
        envelope = build_envelope(
            command=command,
            query=query,
            result=None,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
        return
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)


def _check_eol_flags(
    *,
    to: Optional[LineEnding],
    output: Optional[Path],
    in_place: bool,
    backup: bool,
    target_count: int,
) -> LineEnding:
    """Validate `eol`'s flag combination before any file I/O; return the narrowed `--to`."""
    if to is None:
        raise UsageError("--to is required")
    _check_write_flags(output=output, in_place=in_place, backup=backup)
    if output is not None and target_count > 1:
        raise UsageError("--output is only valid with a single file target")
    return to


def _render_eol_outcomes(
    outcomes: list[tuple[str, Optional[api.WriteOutcome], Optional[OpskitError]]],
    *,
    no_color: bool,
) -> None:
    console = make_console(no_color=no_color)
    for _, outcome, error in outcomes:
        if error is not None:
            message = f"error: {error.message}"
            if error.hint:
                message += f"\nhint: {error.hint}"
            typer.echo(message, err=True)
        elif outcome is not None and outcome.stdout_content is not None:
            sys.stdout.buffer.write(outcome.stdout_content)
        elif outcome is not None:
            render_conversion(outcome.result, console=console)


@app.command(name="eol", epilog=_EOL_EPILOG)
def eol_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to normalize (or use --input-file)."),
    ] = None,
    to: Annotated[
        Optional[LineEnding],
        typer.Option("--to", help="Target line-ending style.", rich_help_panel="Query"),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            help="Write result to this new file instead of stdout. Only valid with a "
            "single file target.",
            rich_help_panel="Write",
        ),
    ] = None,
    in_place: Annotated[
        bool,
        typer.Option(
            "--in-place",
            help="Overwrite the source file atomically.",
            rich_help_panel="Write",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup",
            help="With --in-place: write <path>.bak before replacing.",
            rich_help_panel="Write",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --in-place --backup: overwrite an existing .bak.",
            rich_help_panel="Write",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Normalize line endings, non-destructively by default (--in-place to rewrite)."""
    try:
        targets = collect_target_list(paths, input_file)
        checked_to = _check_eol_flags(
            to=to,
            output=output,
            in_place=in_place,
            backup=backup,
            target_count=len(targets),
        )
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_eol(p: str) -> api.WriteOutcome:
        start = time.perf_counter()
        try:
            return api.eol(
                p,
                to=checked_to,
                output=output,
                in_place=in_place,
                backup=backup,
                force=force,
            )
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_eol)

    if as_json or jsonl:
        envelopes = [
            _eol_envelope(
                t,
                o.result if o is not None else None,
                e,
                timings.get(t, 0.0),
                to=checked_to,
                in_place=in_place,
                backup=backup,
            )
            for t, o, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        _render_eol_outcomes(outcomes, no_color=no_color)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_CONVERT_EPILOG = """\
[bold]Examples[/bold]

  opskit file convert config.json --to yaml > config.yaml
  opskit file convert config.json --to yaml --output config.yaml
  opskit file convert config.json --to yaml --in-place --backup
"""


@app.command(name="convert", epilog=_CONVERT_EPILOG)
def convert_cmd(
    path: Annotated[str, typer.Argument(help="File to convert.")],
    to: Annotated[
        Optional[StructuredFormat],
        typer.Option(
            "--to", help="Target structured-data format.", rich_help_panel="Query"
        ),
    ] = None,
    format: Annotated[
        Optional[StructuredFormat],
        typer.Option(
            "--format",
            help="Force this source format instead of auto-detecting.",
            rich_help_panel="Query",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            help="Write result to this new file instead of stdout.",
            rich_help_panel="Write",
        ),
    ] = None,
    in_place: Annotated[
        bool,
        typer.Option(
            "--in-place",
            help="Overwrite the source file atomically.",
            rich_help_panel="Write",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup",
            help="With --in-place: write <path>.bak before replacing.",
            rich_help_panel="Write",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --in-place --backup: overwrite an existing .bak.",
            rich_help_panel="Write",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Convert a JSON/YAML/TOML/XML file to another of those formats."""
    try:
        if to is None:
            raise UsageError("--to is required")
        _check_write_flags(output=output, in_place=in_place, backup=backup)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    query: dict[str, Any] = {
        "path": path,
        "to": to.value,
        "format": format.value if format else None,
        "in_place": in_place,
        "backup": backup,
    }
    start = time.perf_counter()
    try:
        outcome = api.convert(
            path,
            to=to,
            format=format,
            output=output,
            in_place=in_place,
            backup=backup,
            force=force,
        )
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _report_write_failure(
            error,
            command="file.convert",
            query=query,
            elapsed_ms=elapsed_ms,
            as_json=as_json,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if as_json:
        envelope = build_envelope(
            command="file.convert",
            query=query,
            result=outcome.result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
    elif outcome.stdout_content is not None:
        sys.stdout.buffer.write(outcome.stdout_content)
    else:
        render_conversion(outcome.result, console=make_console(no_color=no_color))
    raise typer.Exit(0)


_PRETTY_EPILOG = """\
[bold]Examples[/bold]

  opskit file pretty data.json --sort-keys --indent 4
  opskit file pretty data.json --in-place --backup
"""


@app.command(name="pretty", epilog=_PRETTY_EPILOG)
def pretty_cmd(
    path: Annotated[str, typer.Argument(help="File to reformat.")],
    format: Annotated[
        Optional[StructuredFormat],
        typer.Option(
            "--format",
            help="Force this format instead of auto-detecting.",
            rich_help_panel="Query",
        ),
    ] = None,
    indent: Annotated[
        int,
        typer.Option("--indent", help="Indent width.", rich_help_panel="Query"),
    ] = 2,
    sort_keys: Annotated[
        bool,
        typer.Option("--sort-keys", help="Sort mapping keys.", rich_help_panel="Query"),
    ] = False,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            help="Write result to this new file instead of stdout.",
            rich_help_panel="Write",
        ),
    ] = None,
    in_place: Annotated[
        bool,
        typer.Option(
            "--in-place",
            help="Overwrite the source file atomically.",
            rich_help_panel="Write",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup",
            help="With --in-place: write <path>.bak before replacing.",
            rich_help_panel="Write",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --in-place --backup: overwrite an existing .bak.",
            rich_help_panel="Write",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Reformat a JSON/YAML/XML file's layout without changing its data."""
    try:
        _check_write_flags(output=output, in_place=in_place, backup=backup)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    query: dict[str, Any] = {
        "path": path,
        "format": format.value if format else None,
        "indent": indent,
        "sort_keys": sort_keys,
        "in_place": in_place,
        "backup": backup,
    }
    start = time.perf_counter()
    try:
        outcome = api.pretty(
            path,
            format=format,
            indent=indent,
            sort_keys=sort_keys,
            output=output,
            in_place=in_place,
            backup=backup,
            force=force,
        )
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _report_write_failure(
            error,
            command="file.pretty",
            query=query,
            elapsed_ms=elapsed_ms,
            as_json=as_json,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if as_json:
        envelope = build_envelope(
            command="file.pretty",
            query=query,
            result=outcome.result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
    elif outcome.stdout_content is not None:
        sys.stdout.buffer.write(outcome.stdout_content)
    else:
        render_conversion(outcome.result, console=make_console(no_color=no_color))
    raise typer.Exit(0)


_DIFF_EPILOG = """\
[bold]Examples[/bold]

  opskit file diff config.old.yaml config.new.yaml
  opskit file diff a.json b.json --json
"""

_DIFF_FORMATS = (StructuredFormat.JSON, StructuredFormat.YAML)


@app.command(name="diff", epilog=_DIFF_EPILOG)
def diff_cmd(
    left: Annotated[str, typer.Argument(help="First file to compare.")],
    right: Annotated[str, typer.Argument(help="Second file to compare.")],
    format: Annotated[
        Optional[StructuredFormat],
        typer.Option(
            "--format",
            help="Force this format (json or yaml) instead of auto-detecting; applies "
            "to both sides.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Structurally compare two JSON/YAML files, ignoring formatting/key order."""
    try:
        if format is not None and format not in _DIFF_FORMATS:
            raise UsageError(
                f"--format must be json or yaml for diff, not {format.value}"
            )
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    query: dict[str, Any] = {
        "left": left,
        "right": right,
        "format": format.value if format else None,
    }
    start = time.perf_counter()
    try:
        result = api.diff(left, right, format=format)
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _report_write_failure(
            error,
            command="file.diff",
            query=query,
            elapsed_ms=elapsed_ms,
            as_json=as_json,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if as_json:
        envelope = build_envelope(
            command="file.diff",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
    else:
        render_diff(result, console=make_console(no_color=no_color))
    raise typer.Exit(0)


_DUPLICATES_EPILOG = """\
[bold]Examples[/bold]

  opskit file duplicates ./vendor
  opskit file duplicates ./vendor --recursive --json
"""


@app.command(name="duplicates", epilog=_DUPLICATES_EPILOG)
def duplicates_cmd(
    directory: Annotated[str, typer.Argument(help="Directory to search.")],
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive", help="Descend into subdirectories.", rich_help_panel="Query"
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Search a directory for files with identical content."""
    query: dict[str, Any] = {"directory": directory, "recursive": recursive}
    start = time.perf_counter()
    try:
        groups = api.find_duplicates(directory, recursive=recursive)
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _report_write_failure(
            error,
            command="file.duplicates",
            query=query,
            elapsed_ms=elapsed_ms,
            as_json=as_json,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if as_json:
        envelope = build_envelope(
            command="file.duplicates",
            query=query,
            result={"groups": [g.to_dict() for g in groups]},
            error=None,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
    else:
        render_duplicates(groups, console=make_console(no_color=no_color))
    raise typer.Exit(0)


_STAT_EPILOG = """\
[bold]Examples[/bold]

  opskit file stat config.json
  opskit file stat *.json --json
  opskit file stat -i paths.txt --jsonl
"""


def _stat_envelope(
    path: str,
    result: Optional[StatResult],
    error: Optional[OpskitError],
    elapsed_ms: float,
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path}
    if result is not None:
        return build_envelope(
            command="file.stat",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.stat",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.command(name="stat", epilog=_STAT_EPILOG)
def stat_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to inspect (or use --input-file)."),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Report cross-platform-normalized metadata for each file."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_stat(p: str) -> StatResult:
        start = time.perf_counter()
        try:
            return api.stat_files(p)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_stat)

    if as_json or jsonl:
        envelopes = [
            _stat_envelope(t, r, e, timings.get(t, 0.0)) for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_stat(result, console=console)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_IDENTIFY_EPILOG = """\
[bold]Examples[/bold]

  opskit file identify downloaded_file
  opskit file identify *.bin --json
  opskit file identify -i paths.txt --jsonl
"""


def _identify_envelope(
    path: str,
    result: Optional[IdentificationResult],
    error: Optional[OpskitError],
    elapsed_ms: float,
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path}
    if result is not None:
        return build_envelope(
            command="file.identify",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.identify",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.command(name="identify", epilog=_IDENTIFY_EPILOG)
def identify_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to identify (or use --input-file)."),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Identify a file's actual type from its content, flagging extension mismatches."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_identify(p: str) -> IdentificationResult:
        start = time.perf_counter()
        try:
            return api.identify(p)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_identify)

    if as_json or jsonl:
        envelopes = [
            _identify_envelope(t, r, e, timings.get(t, 0.0)) for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_identify(result, console=console)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_HASH_EPILOG = """\
[bold]Examples[/bold]

  opskit file hash artifact.tar.gz
  opskit file hash *.tar.gz --algo sha256 --jsonl
"""


def _hash_envelope(
    path: str,
    result: Optional[ChecksumResult],
    error: Optional[OpskitError],
    elapsed_ms: float,
    *,
    algo: str,
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path, "algo": algo}
    if result is not None:
        return build_envelope(
            command="file.hash",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.hash",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.command(name="hash", epilog=_HASH_EPILOG)
def hash_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to checksum (or use --input-file)."),
    ] = None,
    algo: Annotated[
        str,
        typer.Option("--algo", help="Hash algorithm.", rich_help_panel="Query"),
    ] = "sha256",
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Compute a checksum for each file."""
    try:
        targets = collect_target_list(paths, input_file)
        if algo not in {"sha256", "sha1", "md5"}:
            raise UsageError(
                f"unsupported algorithm: {algo}",
                hint="choose one of: md5, sha1, sha256",
            )
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_hash(p: str) -> ChecksumResult:
        start = time.perf_counter()
        try:
            return api.hash_files(p, algo=algo)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_hash)

    if as_json or jsonl:
        envelopes = [
            _hash_envelope(t, r, e, timings.get(t, 0.0), algo=algo)
            for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_checksum(result, console=console)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_ENCODING_EPILOG = """\
[bold]Examples[/bold]

  opskit file encoding legacy.txt
  opskit file encoding *.txt --json
  opskit file encoding -i paths.txt --jsonl
"""


def _encoding_envelope(
    path: str,
    result: Optional[EncodingReport],
    error: Optional[OpskitError],
    elapsed_ms: float,
) -> dict[str, Any]:
    query: dict[str, Any] = {"path": path}
    if result is not None:
        return build_envelope(
            command="file.encoding",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
    return build_envelope(
        command="file.encoding",
        query=query,
        result=None,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.command(name="encoding", epilog=_ENCODING_EPILOG)
def encoding_cmd(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="File path(s) to inspect (or use --input-file)."),
    ] = None,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of paths, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    jsonl: Annotated[
        bool,
        typer.Option(
            "--jsonl",
            help="Emit one JSON envelope per line (NDJSON).",
            rich_help_panel="Output",
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Detect each file's text encoding, BOM presence, and invalid byte sequences."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    timings: dict[str, float] = {}

    def _timed_encoding(p: str) -> EncodingReport:
        start = time.perf_counter()
        try:
            return api.encoding(p)
        finally:
            timings[p] = (time.perf_counter() - start) * 1000.0

    outcomes = collect_outcomes(targets, _timed_encoding)

    if as_json or jsonl:
        envelopes = [
            _encoding_envelope(t, r, e, timings.get(t, 0.0)) for t, r, e in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        for _, result, error in outcomes:
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_encoding(result, console=console)

    codes = [ExitCode.OK if e is None else exit_code_for(e) for _, _, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))


_REENCODE_EPILOG = """\
[bold]Examples[/bold]

  opskit file reencode legacy.txt --from iso-8859-1 --to utf-8 --output legacy.utf8.txt
  opskit file reencode legacy.txt --to utf-8 --in-place --backup
"""


@app.command(name="reencode", epilog=_REENCODE_EPILOG)
def reencode_cmd(
    path: Annotated[str, typer.Argument(help="File to transcode.")],
    to: Annotated[
        Optional[str],
        typer.Option("--to", help="Target encoding name.", rich_help_panel="Query"),
    ] = None,
    from_: Annotated[
        Optional[str],
        typer.Option(
            "--from",
            help="Source encoding, overriding auto-detection.",
            rich_help_panel="Query",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            help="Write result to this new file instead of stdout.",
            rich_help_panel="Write",
        ),
    ] = None,
    in_place: Annotated[
        bool,
        typer.Option(
            "--in-place",
            help="Overwrite the source file atomically.",
            rich_help_panel="Write",
        ),
    ] = False,
    backup: Annotated[
        bool,
        typer.Option(
            "--backup",
            help="With --in-place: write <path>.bak before replacing.",
            rich_help_panel="Write",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="With --in-place --backup: overwrite an existing .bak.",
            rich_help_panel="Write",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
        ),
    ] = False,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Transcode a text file from one encoding to another."""
    try:
        if to is None:
            raise UsageError("--to is required")
        _check_write_flags(output=output, in_place=in_place, backup=backup)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    query: dict[str, Any] = {
        "path": path,
        "to": to,
        "from": from_,
        "in_place": in_place,
        "backup": backup,
    }
    start = time.perf_counter()
    try:
        outcome = api.reencode(
            path,
            to=to,
            from_=from_,
            output=output,
            in_place=in_place,
            backup=backup,
            force=force,
        )
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _report_write_failure(
            error,
            command="file.reencode",
            query=query,
            elapsed_ms=elapsed_ms,
            as_json=as_json,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if as_json:
        envelope = build_envelope(
            command="file.reencode",
            query=query,
            result=outcome.result.to_dict(),
            error=None,
            elapsed_ms=elapsed_ms,
        )
        emit_envelopes([envelope], jsonl=False)
    elif outcome.stdout_content is not None:
        sys.stdout.buffer.write(outcome.stdout_content)
    else:
        render_conversion(outcome.result, console=make_console(no_color=no_color))
    raise typer.Exit(0)
