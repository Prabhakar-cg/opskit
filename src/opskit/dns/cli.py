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
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape

from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import (
    make_console,
    render_comparison,
    render_records,
    render_trace,
)
from opskit.core.result import build_envelope, to_json
from opskit.dns import api
from opskit.dns.models import LookupResult, ResolverComparison, TraceStep

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


def _collect_targets(
    positional: Optional[str], input_file: Optional[Path]
) -> list[str]:
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
            console.print(f"[bold];; {escape(target)}[/bold]")
        if error is not None:
            message = f"error: {error.message}"
            if error.hint:
                message += f"\nhint: {error.hint}"
            typer.echo(message, err=True)
        elif result is not None:
            render_records(result.records, console=console)


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
    return _aggregate_exit(outcomes), _signature(outcomes)


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
    comparisons: list[ResolverComparison] = []
    had_error = False
    for target in targets:
        try:
            comparisons.append(
                api.compare(
                    target,
                    list(servers),
                    types,
                    transport=transport,
                    timeout=timeout,
                    retries=retries,
                    port=port,
                )
            )
        except OpskitError as error:
            had_error = True
            typer.echo(f"error: {target}: {error.message}", err=True)
    if not comparisons:
        return ExitCode.USAGE, ""

    if as_json or jsonl:
        envelopes = [
            build_envelope(
                command="dns.compare",
                query={
                    "target": c.target,
                    "record_types": [t.value for t in c.record_types],
                },
                result=c.to_dict(),
                error=None,
                elapsed_ms=0.0,
            )
            for c in comparisons
        ]
        if jsonl:
            for envelope in envelopes:
                typer.echo(to_json(envelope, indent=None))
        elif len(envelopes) == 1:
            typer.echo(to_json(envelopes[0]))
        else:
            typer.echo(json.dumps(envelopes, indent=2))
    else:
        console = make_console(no_color=no_color)
        for comparison in comparisons:
            render_comparison(comparison, console=console)
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
            for c in comparisons
        ]
    )
    consistent = all(c.consistent for c in comparisons)
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
    per_target: list[tuple[str, tuple[TraceStep, ...]]] = []
    had_error = False
    for target in targets:
        try:
            per_target.append((target, trace_fn(target)))
        except OpskitError as error:
            had_error = True
            typer.echo(f"error: {target}: {error.message}", err=True)
    if not per_target:
        return ExitCode.USAGE, ""
    if as_json or jsonl:
        envelopes = [
            build_envelope(
                command=command,
                query={"target": target},
                result={"trace": [step.to_dict() for step in steps]},
                error=None,
                elapsed_ms=0.0,
            )
            for target, steps in per_target
        ]
        if jsonl:
            for envelope in envelopes:
                typer.echo(to_json(envelope, indent=None))
        elif len(envelopes) == 1:
            typer.echo(to_json(envelopes[0]))
        else:
            typer.echo(json.dumps(envelopes, indent=2))
    else:
        console = make_console(no_color=no_color)
        for target, steps in per_target:
            if len(targets) > 1:
                console.print(f"[bold];; trace {escape(target)}[/bold]")
            render_trace(steps, console=console)
    signature = json.dumps(
        [[t, [s.response for s in steps]] for t, steps in per_target]
    )
    resolved = all(steps and steps[-1].response == "answer" for _, steps in per_target)
    code = ExitCode.OK if resolved and not had_error else ExitCode.PARTIAL
    return code, signature


_ActionResult = tuple[ExitCode, str]


def _parse_interval(text: str) -> float:
    """Parse a --watch interval like '5', '5s', '250ms', or '2m' into seconds."""
    value = text.strip().lower()
    try:
        if value.endswith("ms"):
            seconds = float(value[:-2]) / 1000.0
        elif value.endswith("s"):
            seconds = float(value[:-1])
        elif value.endswith("m"):
            seconds = float(value[:-1]) * 60.0
        else:
            seconds = float(value)
    except ValueError as exc:
        raise UsageError(f"invalid --watch interval: {text}") from exc
    if seconds <= 0:
        raise UsageError("--watch interval must be positive")
    return seconds


def _watch(
    action: Callable[[], _ActionResult],
    *,
    interval: float,
    no_color: bool,
) -> ExitCode:
    """Re-run ``action`` every ``interval`` seconds until interrupted, flagging changes."""
    console = make_console(no_color=no_color)
    previous: Optional[str] = None
    code = ExitCode.OK
    try:
        while True:
            code, signature = action()
            if previous is None:
                status = "initial"
            elif signature != previous:
                status = "changed"
            else:
                status = "no change"
            stamp = datetime.now().strftime("%H:%M:%S")
            console.print(
                f"[dim]-- {stamp} - {status} - next in {interval:g}s (Ctrl-C to stop) --[/dim]"
            )
            previous = signature
            time.sleep(interval)  # looked up at call time so tests can patch it
    except KeyboardInterrupt:
        return code


def _run_or_watch(
    action: Callable[[], _ActionResult], *, watch: Optional[str], no_color: bool
) -> None:
    """Run ``action`` once, or repeatedly under --watch; then exit with its code."""
    if watch is not None:
        try:
            interval = _parse_interval(watch)
        except UsageError as error:
            typer.echo(f"error: {error.message}", err=True)
            raise typer.Exit(int(ExitCode.USAGE)) from error
        raise typer.Exit(int(_watch(action, interval=interval, no_color=no_color)))
    code, _ = action()
    raise typer.Exit(int(code))


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
        targets = _collect_targets(target, input_file)
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

    def action() -> _ActionResult:
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

    _run_or_watch(action, watch=watch, no_color=no_color)


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

    def action() -> _ActionResult:
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

    _run_or_watch(action, watch=watch, no_color=no_color)
