"""Thin Typer sub-app for DNS diagnostics: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.dns.api` and turns typed
results/exceptions into human or JSON output and structured exit codes. Supports bulk targets
via a positional argument and/or ``--input-file`` (one target per line, ``#`` comments allowed).

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer reads
   the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them to strings
   (PEP 563) makes Typer silently drop the ``Argument``/``Option`` metadata on Python 3.9, turning
   positional arguments into ``--options``. Keep annotations eager here.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape

from opskit.core.cliutils import (
    ActionResult,
    aggregate_outcome_exit,
    collect_outcomes,
    collect_targets,
    echo_failures,
    emit_envelopes,
    run_or_watch,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode
from opskit.core.output import make_console
from opskit.core.result import build_envelope
from opskit.dns import api
from opskit.dns.models import LookupResult, ResolverComparison, TraceStep
from opskit.dns.output import render_comparison, render_records, render_trace

app = typer.Typer(
    name="dns", help="DNS diagnostics (lookup, reverse).", no_args_is_help=True
)

_LOOKUP_EPILOG = """\
[bold]Examples[/bold]

  opskit dns lookup example.com -t MX -t TXT
  opskit dns lookup example.com --all
  opskit dns lookup example.com --diff -s 1.1.1.1 -s 8.8.8.8
  opskit dns lookup www.wikipedia.org --trace
  opskit dns lookup -i hosts.txt --jsonl
  opskit dns lookup example.com --watch 30s
"""

_REVERSE_EPILOG = """\
[bold]Examples[/bold]

  opskit dns reverse 8.8.8.8
  opskit dns reverse 2001:4860:4860::8888 --json
  opskit dns reverse 8.8.8.8 --trace
  opskit dns reverse -i ips.txt
"""

# (target, result-or-None, error-or-None) for one target in a batch.
_Outcome = tuple[str, Optional[LookupResult], Optional[OpskitError]]


def _envelope(
    command: str,
    target: str,
    result: Optional[LookupResult],
    error: Optional[OpskitError],
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


def _compare_envelope(
    target: str,
    comparison: Optional[ResolverComparison],
    error: Optional[OpskitError],
    types: Sequence[str],
) -> dict[str, Any]:
    """Build the JSON envelope for one compared target (success or failure)."""
    if comparison is not None:
        return build_envelope(
            command="dns.compare",
            query={
                "target": comparison.target,
                "record_types": [t.value for t in comparison.record_types],
            },
            result=comparison.to_dict(),
            error=None,
            elapsed_ms=0.0,
        )
    return build_envelope(
        command="dns.compare",
        query={"target": target, "record_types": [t.upper() for t in types]},
        result=None,
        error=error,
        elapsed_ms=0.0,
    )


def _trace_envelope(
    command: str,
    target: str,
    steps: Optional[tuple[TraceStep, ...]],
    error: Optional[OpskitError],
) -> dict[str, Any]:
    """Build the JSON envelope for one traced target (success or failure)."""
    result = (
        {"trace": [step.to_dict() for step in steps]} if steps is not None else None
    )
    return build_envelope(
        command=command,
        query={"target": target},
        result=result,
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
    emit_envelopes(envelopes, jsonl=jsonl)


def _render_human(outcomes: Sequence[_Outcome], *, batch: bool, no_color: bool) -> None:
    console = make_console(no_color=no_color)
    for target, result, error in outcomes:
        if batch:
            console.print(f"[bold];; {escape(target)}[/bold]")
        if error is not None:
            message = f"error: {error.message}"
            if error.hint:
                message += f"\nhint: {error.hint}"
            typer.echo(message, err=True)
        elif result is not None:
            render_records(result.records, console=console)


def _render_comparisons_human(
    outcomes: Sequence[tuple[str, Optional[ResolverComparison], Optional[OpskitError]]],
    *,
    no_color: bool,
) -> None:
    """Render successful comparisons; echo failed targets to stderr."""
    console = make_console(no_color=no_color)
    for target, comparison, error in outcomes:
        if comparison is not None:
            render_comparison(comparison, console=console)
        elif error is not None:
            typer.echo(f"error: {target}: {error.message}", err=True)


def _render_traces_human(
    outcomes: Sequence[
        tuple[str, Optional[tuple[TraceStep, ...]], Optional[OpskitError]]
    ],
    *,
    batch: bool,
    no_color: bool,
) -> None:
    """Render successful traces; echo failed targets to stderr."""
    console = make_console(no_color=no_color)
    for target, steps, error in outcomes:
        if steps is not None:
            if batch:
                console.print(f"[bold];; trace {escape(target)}[/bold]")
            render_trace(steps, console=console)
        elif error is not None:
            typer.echo(f"error: {target}: {error.message}", err=True)


def _signature(outcomes: Sequence[_Outcome]) -> str:
    """A change-detection key over targets and their (type, value) records (TTL ignored)."""
    parts: list[object] = []
    for target, result, error in outcomes:
        if result is not None:
            recs = sorted([r.type.value, r.value] for r in result.records)
            parts.append([target, "ok", recs])
        else:
            parts.append([target, error.code if error else "error"])
    return json.dumps(parts, sort_keys=True)


def _run(
    command: str,
    targets: Sequence[str],
    run_one: Callable[[str], LookupResult],
    error_query: Callable[[str], dict[str, Any]],
    *,
    as_json: bool,
    jsonl: bool,
    no_color: bool,
) -> tuple[ExitCode, str]:
    """Execute the query for each target and render; return (exit code, change signature)."""
    outcomes = collect_outcomes(targets, run_one)
    if as_json or jsonl:
        _render_json(command, outcomes, error_query, jsonl=jsonl)
    else:
        _render_human(outcomes, batch=len(targets) > 1, no_color=no_color)
    return aggregate_outcome_exit(outcomes), _signature(outcomes)


def _run_compare(
    targets: Sequence[str],
    servers: Sequence[str],
    types: Sequence[str],
    *,
    transport: str,
    timeout: float,
    retries: int,
    port: int,
    as_json: bool,
    jsonl: bool,
    no_color: bool,
) -> tuple[ExitCode, str]:
    """Compare each target across the given resolvers; render; return (code, signature).

    Per-target failures don't abort the batch: successful comparisons are still rendered, and the
    exit code degrades to PARTIAL when any target failed (USAGE only when none succeeded).
    """
    outcomes = collect_outcomes(
        targets,
        lambda target: api.compare(
            target,
            list(servers),
            types,
            transport=transport,
            timeout=timeout,
            retries=retries,
            port=port,
        ),
    )
    successes = [c for _, c, _ in outcomes if c is not None]
    had_error = any(error is not None for _, _, error in outcomes)
    if not successes:
        echo_failures(outcomes)
        return ExitCode.USAGE, ""

    if as_json or jsonl:
        emit_envelopes(
            [_compare_envelope(t, c, e, types) for t, c, e in outcomes], jsonl=jsonl
        )
    else:
        _render_comparisons_human(outcomes, no_color=no_color)
    signature = json.dumps(
        [
            [
                c.target,
                [
                    [
                        a.server,
                        a.outcome.value,
                        sorted([r.type.value, r.value] for r in a.records),
                    ]
                    for a in c.answers
                ],
            ]
            for c in successes
        ]
    )
    consistent = all(c.consistent for c in successes)
    code = ExitCode.OK if consistent and not had_error else ExitCode.PARTIAL
    return code, signature


def _run_trace(
    targets: Sequence[str],
    trace_fn: Callable[[str], tuple[TraceStep, ...]],
    *,
    command: str,
    as_json: bool,
    jsonl: bool,
    no_color: bool,
) -> tuple[ExitCode, str]:
    """Trace each target's resolution path; render; return (code, signature).

    Per-target failures don't abort the batch: successful traces are still rendered, and the exit
    code degrades to PARTIAL when any target failed (USAGE only when none succeeded).
    """
    outcomes = collect_outcomes(targets, trace_fn)
    successes = [(t, s) for t, s, _ in outcomes if s is not None]
    had_error = any(error is not None for _, _, error in outcomes)
    if not successes:
        echo_failures(outcomes)
        return ExitCode.USAGE, ""
    if as_json or jsonl:
        emit_envelopes(
            [_trace_envelope(command, t, s, e) for t, s, e in outcomes], jsonl=jsonl
        )
    else:
        _render_traces_human(outcomes, batch=len(targets) > 1, no_color=no_color)
    signature = json.dumps([[t, [s.response for s in steps]] for t, steps in successes])
    resolved = all(steps and steps[-1].response == "answer" for _, steps in successes)
    code = ExitCode.OK if resolved and not had_error else ExitCode.PARTIAL
    return code, signature


@app.command(epilog=_LOOKUP_EPILOG)
def lookup(
    target: Annotated[
        Optional[str], typer.Argument(help="Hostname to resolve (or use --input-file).")
    ] = None,
    types: Annotated[
        Optional[list[str]],
        typer.Option(
            "--type", "-t", help="Record type(s) to query.", rich_help_panel="Query"
        ),
    ] = None,
    all_types: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Query all common record types (A/AAAA/CNAME/MX/NS/SOA/TXT/SRV/CAA).",
            rich_help_panel="Modes",
        ),
    ] = False,
    diff: Annotated[
        bool,
        typer.Option(
            "--diff",
            help="Query every --server resolver and compare/diff their answers.",
            rich_help_panel="Modes",
        ),
    ] = False,
    server: Annotated[
        Optional[list[str]],
        typer.Option(
            "--server",
            "-s",
            help="Resolver(s) to query.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    transport: Annotated[
        str,
        typer.Option(
            "--transport", help="udp | tcp | auto.", rich_help_panel="Query controls"
        ),
    ] = "auto",
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Per-attempt timeout (s).",
            rich_help_panel="Query controls",
        ),
    ] = 5.0,
    retries: Annotated[
        int,
        typer.Option(
            "--retries", help="Retry count.", rich_help_panel="Query controls"
        ),
    ] = 2,
    port: Annotated[
        int,
        typer.Option("--port", help="Resolver port.", rich_help_panel="Query controls"),
    ] = 53,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file", "-i", help="File of targets, one per line (# comments ok)."
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
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Show the iterative resolution path (root -> authoritative).",
            rich_help_panel="Modes",
        ),
    ] = False,
    watch: Annotated[
        Optional[str],
        typer.Option(
            "--watch",
            help="Re-run every interval (e.g. 5s, 2m) until Ctrl-C.",
            rich_help_panel="Modes",
        ),
    ] = None,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Forward DNS lookup for one or more hostnames."""
    requested = types if types else ["A"]
    try:
        targets = collect_targets(target, input_file)
    except UsageError as error:
        typer.echo(f"error: {error.message}", err=True)
        raise typer.Exit(int(ExitCode.USAGE)) from error

    if all_types and trace:
        typer.echo(
            "error: --all cannot be combined with --trace (a trace follows one record type)",
            err=True,
        )
        raise typer.Exit(int(ExitCode.USAGE))
    # --all fans out over every common type; --diff honors it, --trace is rejected above.
    compare_types = [t.value for t in api.ALL_RECORD_TYPES] if all_types else requested

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

    def action() -> ActionResult:
        if trace:
            return _run_trace(
                targets,
                lambda name: api.trace(name, requested[0], timeout=timeout, port=port),
                command="dns.trace",
                as_json=as_json,
                jsonl=jsonl,
                no_color=no_color,
            )
        if diff:
            return _run_compare(
                targets,
                server or [],
                compare_types,
                transport=transport,
                timeout=timeout,
                retries=retries,
                port=port,
                as_json=as_json,
                jsonl=jsonl,
                no_color=no_color,
            )
        return _run(
            "dns.lookup",
            targets,
            run_one,
            error_query,
            as_json=as_json,
            jsonl=jsonl,
            no_color=no_color,
        )

    run_or_watch(action, watch_spec=watch, no_color=no_color)


@app.command(epilog=_REVERSE_EPILOG)
def reverse(
    target: Annotated[
        Optional[str],
        typer.Argument(help="IP address to reverse-resolve (or use --input-file)."),
    ] = None,
    server: Annotated[
        Optional[list[str]],
        typer.Option(
            "--server",
            "-s",
            help="Resolver(s) to query.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    transport: Annotated[
        str,
        typer.Option(
            "--transport", help="udp | tcp | auto.", rich_help_panel="Query controls"
        ),
    ] = "auto",
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Per-attempt timeout (s).",
            rich_help_panel="Query controls",
        ),
    ] = 5.0,
    retries: Annotated[
        int,
        typer.Option(
            "--retries", help="Retry count.", rich_help_panel="Query controls"
        ),
    ] = 2,
    port: Annotated[
        int,
        typer.Option("--port", help="Resolver port.", rich_help_panel="Query controls"),
    ] = 53,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file", "-i", help="File of IPs, one per line (# comments ok)."
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
    trace: Annotated[
        bool,
        typer.Option(
            "--trace",
            help="Show the iterative resolution path (root -> authoritative).",
            rich_help_panel="Modes",
        ),
    ] = False,
    watch: Annotated[
        Optional[str],
        typer.Option(
            "--watch",
            help="Re-run every interval (e.g. 5s, 2m) until Ctrl-C.",
            rich_help_panel="Modes",
        ),
    ] = None,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color", help="Disable colored output.", rich_help_panel="Output"
        ),
    ] = False,
) -> None:
    """Reverse (PTR) lookup for one or more IP addresses."""
    try:
        targets = collect_targets(target, input_file)
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

    def action() -> ActionResult:
        if trace:
            return _run_trace(
                targets,
                lambda ip: api.reverse_trace(ip, timeout=timeout, port=port),
                command="dns.trace",
                as_json=as_json,
                jsonl=jsonl,
                no_color=no_color,
            )
        return _run(
            "dns.reverse",
            targets,
            run_one,
            error_query,
            as_json=as_json,
            jsonl=jsonl,
            no_color=no_color,
        )

    run_or_watch(action, watch_spec=watch, no_color=no_color)
