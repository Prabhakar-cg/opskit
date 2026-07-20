"""Thin Typer sub-app for storage diagnostics: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.storage.api` and turns typed
results/exceptions into human or JSON output and structured exit codes.

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer reads
   the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them to strings
   (PEP 563) makes Typer silently drop the ``Argument``/``Option`` metadata on Python 3.9, turning
   positional arguments into ``--options``. Keep annotations eager here.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape

from opskit.core.cliutils import (
    aggregate_exit,
    collect_outcomes,
    collect_target_list,
    emit_envelopes,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import build_envelope, to_json
from opskit.storage import api
from opskit.storage.models import DirSizeResult
from opskit.storage.output import render_dir_size, render_disks, render_volumes

app = typer.Typer(
    name="storage",
    help="Storage diagnostics (volumes, disks, directory size).",
    no_args_is_help=True,
)


def _error_exit(error: OpskitError) -> typer.Exit:
    """Report a failure to stderr and build its typed exit signal."""
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)
    return typer.Exit(int(exit_code_for(error)))


_VOLUMES_EPILOG = """\
[bold]Examples[/bold]

  opskit storage volumes
  opskit storage volumes --json
  opskit storage volumes --jsonl
"""


@app.command(epilog=_VOLUMES_EPILOG)
def volumes(
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
            help="Emit one JSON object per volume (NDJSON).",
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
    """List every mounted, non-pseudo volume with utilization and filesystem type."""
    try:
        result = api.list_volumes()
    except OpskitError as error:
        raise _error_exit(error) from error

    if jsonl:
        for volume in result:
            typer.echo(json.dumps(volume.to_dict()))
    elif as_json:
        envelope = build_envelope(
            command="storage.volumes",
            query={},
            result={"volumes": [v.to_dict() for v in result]},
            error=None,
            elapsed_ms=0.0,
        )
        typer.echo(to_json(envelope))
    else:
        console = make_console(no_color=no_color)
        render_volumes(result, console=console)
    raise typer.Exit(int(ExitCode.OK))


_DISKS_EPILOG = """\
[bold]Examples[/bold]

  opskit storage disks
  opskit storage disks --json
"""


@app.command(epilog=_DISKS_EPILOG)
def disks(
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
            help="Emit one JSON object per disk (NDJSON).",
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
    """List physical/logical disks, each with its nested partitions."""
    try:
        result = api.list_disks()
    except OpskitError as error:
        raise _error_exit(error) from error

    if jsonl:
        for disk in result:
            typer.echo(json.dumps(disk.to_dict()))
    elif as_json:
        envelope = build_envelope(
            command="storage.disks",
            query={},
            result={"disks": [d.to_dict() for d in result]},
            error=None,
            elapsed_ms=0.0,
        )
        typer.echo(to_json(envelope))
    else:
        console = make_console(no_color=no_color)
        render_disks(result, console=console)
    raise typer.Exit(int(ExitCode.OK))


_SIZE_EPILOG = """\
[bold]Examples[/bold]

  opskit storage size /var/log
  opskit storage size /var/log --depth 2
  opskit storage size /data /var/log /tmp --jsonl
  opskit storage size -i paths.txt --depth 1
  opskit storage size /var/log --include-hidden
"""


def _size_envelope(
    path: str, result: Optional[DirSizeResult], error: Optional[OpskitError]
) -> dict[str, Any]:
    if result is not None:
        return build_envelope(
            command="storage.size",
            query={
                "path": path,
                "depth": result.depth_requested,
                "include_hidden": result.include_hidden,
            },
            result=result.to_dict(),
            error=None,
            elapsed_ms=0.0,
        )
    return build_envelope(
        command="storage.size",
        query={"path": path},
        result=None,
        error=error,
        elapsed_ms=0.0,
    )


def _size_exit_code(
    result: Optional[DirSizeResult], error: Optional[OpskitError]
) -> ExitCode:
    """A path's outcome class: raised error > returned-but-incomplete > OK.

    `--depth`/other option failures surface here too (uniform across every target when
    they're the cause), reusing existing exit classes rather than a special upfront check
    (research R6) — matching how `dns`/`tls` validate shared controls per-target.
    """
    if error is not None:
        return exit_code_for(error)
    if result is not None and result.incomplete:
        return ExitCode.PARTIAL
    return ExitCode.OK


@app.command(epilog=_SIZE_EPILOG)
def size(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="Directory path(s) to measure (or use --input-file)."),
    ] = None,
    depth: Annotated[
        int,
        typer.Option(
            "--depth",
            help="Child-directory breakdown levels below each path.",
            rich_help_panel="Query controls",
        ),
    ] = 0,
    include_hidden: Annotated[
        bool,
        typer.Option(
            "--include-hidden",
            help="Include hidden files/directories in the size calculation.",
            rich_help_panel="Query controls",
        ),
    ] = False,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of directory paths, one per line (# comments allowed); "
            "'-' reads stdin.",
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
    """Recursive size of one or more directories, with an optional depth breakdown."""
    try:
        targets = collect_target_list(paths, input_file)
    except UsageError as usage_error:
        raise _error_exit(usage_error) from usage_error

    outcomes = collect_outcomes(
        targets, lambda p: api.dir_size(p, depth=depth, include_hidden=include_hidden)
    )

    if as_json or jsonl:
        envelopes = [_size_envelope(t, r, e) for t, r, e in outcomes]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        batch = len(targets) > 1
        for target, result, error in outcomes:
            if batch:
                console.print(f"[bold];; {escape(target)}[/bold]")
            if error is not None:
                message = f"error: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
            elif result is not None:
                render_dir_size(result, console=console)

    codes = [_size_exit_code(r, e) for _, r, e in outcomes]
    raise typer.Exit(int(aggregate_exit(codes)))
