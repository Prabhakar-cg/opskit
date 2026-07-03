"""Thin Typer sub-app for DNS diagnostics: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.dns.api` and turns typed
results/exceptions into human or JSON output and structured exit codes.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Optional

import typer

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console, render_records
from opskit.core.result import build_envelope, to_json
from opskit.dns import api
from opskit.dns.models import LookupResult

app = typer.Typer(
    name="dns", help="DNS diagnostics (lookup, reverse).", no_args_is_help=True
)


def _emit_error(
    error: OpskitError,
    *,
    command: str,
    query: dict[str, Any],
    as_json: bool,
    elapsed_ms: float,
) -> None:
    """Render an error as a JSON envelope (stdout) or an actionable message (stderr)."""
    if as_json:
        envelope = build_envelope(
            command=command,
            query=query,
            result=None,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        typer.echo(to_json(envelope))
    else:
        message = f"error: {error.message}"
        if error.hint:
            message += f"\nhint: {error.hint}"
        typer.echo(message, err=True)


def _render_result(
    result: LookupResult, *, command: str, as_json: bool, no_color: bool
) -> None:
    """Render a successful result as a JSON envelope (stdout) or a human table."""
    if as_json:
        envelope = build_envelope(
            command=command,
            query=result.query.to_dict(),
            result=result.to_dict(),
            error=None,
            elapsed_ms=result.elapsed_ms,
        )
        typer.echo(to_json(envelope))
    else:
        render_records(result.records, console=make_console(no_color=no_color))


@app.command()
def lookup(
    target: Annotated[str, typer.Argument(help="Hostname to resolve.")],
    types: Annotated[
        Optional[list[str]],
        typer.Option("--type", "-t", help="Record type(s) to query."),
    ] = None,
    server: Annotated[
        Optional[list[str]],
        typer.Option("--server", "-s", help="Resolver(s) to query."),
    ] = None,
    transport: Annotated[
        str, typer.Option("--transport", help="udp | tcp | auto.")
    ] = "auto",
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-attempt timeout (seconds).")
    ] = 5.0,
    retries: Annotated[int, typer.Option("--retries", help="Retry count.")] = 2,
    port: Annotated[int, typer.Option("--port", help="Resolver port.")] = 53,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the versioned JSON envelope.")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output.")
    ] = False,
) -> None:
    """Forward DNS lookup for a hostname."""
    requested = types if types else ["A"]
    start = time.perf_counter()
    try:
        result = api.lookup(
            target,
            requested,
            server=server,
            transport=transport,
            timeout=timeout,
            retries=retries,
            port=port,
        )
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        query: dict[str, Any] = {
            "target": target,
            "record_types": [t.upper() for t in requested],
        }
        _emit_error(
            error,
            command="dns.lookup",
            query=query,
            as_json=as_json,
            elapsed_ms=elapsed_ms,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    _render_result(result, command="dns.lookup", as_json=as_json, no_color=no_color)
    raise typer.Exit(int(ExitCode.OK))


@app.command()
def reverse(
    target: Annotated[str, typer.Argument(help="IP address to reverse-resolve.")],
    server: Annotated[
        Optional[list[str]],
        typer.Option("--server", "-s", help="Resolver(s) to query."),
    ] = None,
    transport: Annotated[
        str, typer.Option("--transport", help="udp | tcp | auto.")
    ] = "auto",
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-attempt timeout (seconds).")
    ] = 5.0,
    retries: Annotated[int, typer.Option("--retries", help="Retry count.")] = 2,
    port: Annotated[int, typer.Option("--port", help="Resolver port.")] = 53,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the versioned JSON envelope.")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output.")
    ] = False,
) -> None:
    """Reverse (PTR) lookup for an IP address."""
    start = time.perf_counter()
    try:
        result = api.reverse(
            target,
            server=server,
            transport=transport,
            timeout=timeout,
            retries=retries,
            port=port,
        )
    except OpskitError as error:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        query: dict[str, Any] = {"target": target}
        _emit_error(
            error,
            command="dns.reverse",
            query=query,
            as_json=as_json,
            elapsed_ms=elapsed_ms,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    _render_result(result, command="dns.reverse", as_json=as_json, no_color=no_color)
    raise typer.Exit(int(ExitCode.OK))
