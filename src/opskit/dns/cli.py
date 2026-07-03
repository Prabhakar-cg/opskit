"""Thin Typer sub-app for DNS diagnostics: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.dns.api` and turns typed
results/exceptions into human or JSON output and structured exit codes. Supports bulk targets
via a positional argument and/or ``--input-file`` (one target per line, ``#`` comments allowed).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console, render_records
from opskit.core.result import build_envelope, to_json
from opskit.dns import api
from opskit.dns.models import LookupResult

app = typer.Typer(
    name="dns", help="DNS diagnostics (lookup, reverse).", no_args_is_help=True
)

# (target, result-or-None, error-or-None) for one target in a batch.
_Outcome = tuple[str, Optional[LookupResult], Optional[OpskitError]]


def _read_input_file(path: Path) -> list[str]:
    """Read targets from a file, one per line, ignoring blanks and ``#`` comments."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read input file: {path}") from exc
    targets: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            targets.append(stripped)
    return targets


def _collect_targets(positional: str | None, input_file: Path | None) -> list[str]:
    """Combine a positional target with any from ``--input-file`` (positional first)."""
    targets: list[str] = []
    if positional:
        targets.append(positional)
    if input_file is not None:
        targets.extend(_read_input_file(input_file))
    if not targets:
        raise UsageError("provide a target argument or --input-file")
    return targets


def _aggregate_exit(outcomes: Sequence[_Outcome]) -> ExitCode:
    """0 if all succeed; the single code for one target; PARTIAL for a mixed batch."""
    codes = [
        ExitCode.OK if error is None else exit_code_for(error)
        for _, _, error in outcomes
    ]
    if all(code is ExitCode.OK for code in codes):
        return ExitCode.OK
    if len(codes) == 1:
        return codes[0]
    return ExitCode.PARTIAL


def _envelope(
    command: str,
    target: str,
    result: LookupResult | None,
    error: OpskitError | None,
    error_query: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    if result is not None:
        return build_envelope(
            command=command,
            query=result.query.to_dict(),
            result=result.to_dict(),
            error=None,
            elapsed_ms=result.elapsed_ms,
        )
    return build_envelope(
        command=command,
        query=error_query(target),
        result=None,
        error=error,
        elapsed_ms=0.0,
    )


def _render_json(
    command: str,
    outcomes: Sequence[_Outcome],
    error_query: Callable[[str], dict[str, Any]],
    *,
    jsonl: bool,
) -> None:
    envelopes = [_envelope(command, t, r, e, error_query) for t, r, e in outcomes]
    if jsonl:
        for envelope in envelopes:
            typer.echo(to_json(envelope, indent=None))
    elif len(envelopes) == 1:
        typer.echo(to_json(envelopes[0]))
    else:
        typer.echo(json.dumps(envelopes, indent=2))


def _render_human(outcomes: Sequence[_Outcome], *, batch: bool, no_color: bool) -> None:
    console = make_console(no_color=no_color)
    for target, result, error in outcomes:
        if batch:
            console.print(f"[bold];; {target}[/bold]")
        if error is not None:
            message = f"error: {error.message}"
            if error.hint:
                message += f"\nhint: {error.hint}"
            typer.echo(message, err=True)
        elif result is not None:
            render_records(result.records, console=console)


def _run(
    command: str,
    targets: Sequence[str],
    run_one: Callable[[str], LookupResult],
    error_query: Callable[[str], dict[str, Any]],
    *,
    as_json: bool,
    jsonl: bool,
    no_color: bool,
) -> ExitCode:
    """Execute the query for each target and render, returning the aggregate exit code."""
    outcomes: list[_Outcome] = []
    for target in targets:
        try:
            outcomes.append((target, run_one(target), None))
        except OpskitError as error:
            outcomes.append((target, None, error))
    if as_json or jsonl:
        _render_json(command, outcomes, error_query, jsonl=jsonl)
    else:
        _render_human(outcomes, batch=len(targets) > 1, no_color=no_color)
    return _aggregate_exit(outcomes)


@app.command()
def lookup(
    target: Annotated[
        Optional[str], typer.Argument(help="Hostname to resolve (or use --input-file).")
    ] = None,
    types: Annotated[
        Optional[list[str]],
        typer.Option("--type", "-t", help="Record type(s) to query."),
    ] = None,
    all_types: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Query all common record types (A/AAAA/CNAME/MX/NS/SOA/TXT/SRV/CAA).",
        ),
    ] = False,
    server: Annotated[
        Optional[list[str]],
        typer.Option("--server", "-s", help="Resolver(s) to query."),
    ] = None,
    transport: Annotated[
        str, typer.Option("--transport", help="udp | tcp | auto.")
    ] = "auto",
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-attempt timeout (s).")
    ] = 5.0,
    retries: Annotated[int, typer.Option("--retries", help="Retry count.")] = 2,
    port: Annotated[int, typer.Option("--port", help="Resolver port.")] = 53,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file", "-i", help="File of targets, one per line (# comments ok)."
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the versioned JSON envelope.")
    ] = False,
    jsonl: Annotated[
        bool, typer.Option("--jsonl", help="Emit one JSON envelope per line (NDJSON).")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output.")
    ] = False,
) -> None:
    """Forward DNS lookup for one or more hostnames."""
    requested = types if types else ["A"]
    try:
        targets = _collect_targets(target, input_file)
    except UsageError as error:
        typer.echo(f"error: {error.message}", err=True)
        raise typer.Exit(int(ExitCode.USAGE)) from error

    def run_one(name: str) -> LookupResult:
        if all_types:
            return api.lookup_all(
                name,
                server=server,
                transport=transport,
                timeout=timeout,
                retries=retries,
                port=port,
            )
        return api.lookup(
            name,
            requested,
            server=server,
            transport=transport,
            timeout=timeout,
            retries=retries,
            port=port,
        )

    def error_query(name: str) -> dict[str, Any]:
        if all_types:
            return {
                "target": name,
                "record_types": [t.value for t in api.ALL_RECORD_TYPES],
            }
        return {"target": name, "record_types": [t.upper() for t in requested]}

    code = _run(
        "dns.lookup",
        targets,
        run_one,
        error_query,
        as_json=as_json,
        jsonl=jsonl,
        no_color=no_color,
    )
    raise typer.Exit(int(code))


@app.command()
def reverse(
    target: Annotated[
        Optional[str],
        typer.Argument(help="IP address to reverse-resolve (or use --input-file)."),
    ] = None,
    server: Annotated[
        Optional[list[str]],
        typer.Option("--server", "-s", help="Resolver(s) to query."),
    ] = None,
    transport: Annotated[
        str, typer.Option("--transport", help="udp | tcp | auto.")
    ] = "auto",
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-attempt timeout (s).")
    ] = 5.0,
    retries: Annotated[int, typer.Option("--retries", help="Retry count.")] = 2,
    port: Annotated[int, typer.Option("--port", help="Resolver port.")] = 53,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file", "-i", help="File of IPs, one per line (# comments ok)."
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the versioned JSON envelope.")
    ] = False,
    jsonl: Annotated[
        bool, typer.Option("--jsonl", help="Emit one JSON envelope per line (NDJSON).")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output.")
    ] = False,
) -> None:
    """Reverse (PTR) lookup for one or more IP addresses."""
    try:
        targets = _collect_targets(target, input_file)
    except UsageError as error:
        typer.echo(f"error: {error.message}", err=True)
        raise typer.Exit(int(ExitCode.USAGE)) from error

    def run_one(ip: str) -> LookupResult:
        return api.reverse(
            ip,
            server=server,
            transport=transport,
            timeout=timeout,
            retries=retries,
            port=port,
        )

    def error_query(ip: str) -> dict[str, Any]:
        return {"target": ip}

    code = _run(
        "dns.reverse",
        targets,
        run_one,
        error_query,
        as_json=as_json,
        jsonl=jsonl,
        no_color=no_color,
    )
    raise typer.Exit(int(code))
