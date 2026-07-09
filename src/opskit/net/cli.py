"""Thin Typer sub-app for network diagnostics: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.net.api` /
:class:`opskit.net.listener.Listener` and turns typed results/exceptions into human or
JSON output and structured exit codes. ``check`` is batchable (variadic targets,
``--input-file``, stdin via ``-i -``) and watchable; ``probe`` streams per-attempt
results; ``listen`` streams inbound events.

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer
   reads the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them
   to strings (PEP 563) makes Typer silently drop the metadata on Python 3.9. Keep
   annotations eager here and use ``Optional[...]``.
"""

import json
import time
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape

from opskit.core.cliutils import (
    ActionResult,
    aggregate_exit,
    collect_target_list,
    emit_envelopes,
    parse_interval,
    run_or_watch,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import build_envelope, to_json
from opskit.net import api
from opskit.net.listener import Listener
from opskit.net.models import (
    CheckResult,
    InboundEvent,
    NetTarget,
    ProbeAttempt,
    Protocol,
    StopReason,
    Verdict,
    parse_target,
)
from opskit.net.output import (
    render_check,
    render_listen_banner,
    render_listen_event,
    render_listen_summary,
    render_probe_attempt,
    render_probe_summary,
)

app = typer.Typer(
    name="net",
    help="Network reachability — TCP/UDP port checks, probes, temporary listener.",
    no_args_is_help=True,
)

_CHECK_EPILOG = """\
[bold]Examples[/bold]

  opskit net check db.example.com:5432
  opskit net check 10.0.0.5 -p 22
  opskit net check \\[2001:db8::7]:443 -6
  opskit net check ntp.example.com:123 --udp
  opskit net check web1:443 web2:443 db:5432 --jsonl
  opskit net check -i endpoints.txt --jsonl
  cat endpoints.txt | opskit net check -i - --jsonl
  opskit net check api.example.com:443 --watch 30s
"""

_PROBE_EPILOG = """\
[bold]Examples[/bold]

  opskit net probe api.example.com:443 -c 20 --interval 500ms
  opskit net probe dns.example.com:53 --udp -c 10
"""

_LISTEN_EPILOG = """\
[bold]Examples[/bold]

  opskit net listen 8080
  opskit net listen 514 --udp --max-duration 5m
  opskit net listen 9000 --max-events 1 --json
"""

# Verdict class -> exit class, for probe's per-attempt aggregation (Art. IX).
_VERDICT_EXIT = {
    Verdict.OPEN: ExitCode.OK,
    Verdict.REFUSED: ExitCode.CONNECT_FAILED,
    Verdict.CLOSED: ExitCode.CONNECT_FAILED,
    Verdict.TIMEOUT: ExitCode.TIMEOUT,
    Verdict.INCONCLUSIVE: ExitCode.TIMEOUT,
    Verdict.RESOLVE_FAILED: ExitCode.NXDOMAIN,
}


def _family_flag(ipv4: bool, ipv6: bool) -> Optional[str]:
    """Map the -4/-6 flags onto the API's family value (mutually exclusive)."""
    if ipv4 and ipv6:
        raise UsageError(
            "--ipv4 and --ipv6 are mutually exclusive",
            hint="pick one family, or neither to try both",
        )
    if ipv4:
        return "ipv4"
    if ipv6:
        return "ipv6"
    return None


def _usage_exit(error: UsageError) -> typer.Exit:
    """Report a pre-flight usage error to stderr and build the exit-2 signal."""
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)
    return typer.Exit(int(ExitCode.USAGE))


def _report_failure(
    error: OpskitError,
    *,
    command: str,
    query: "dict[str, Any]",
    elapsed_ms: float,
    as_json: bool,
    jsonl: bool,
    prefix: str = "",
) -> None:
    """Report a run-level failure: an envelope in JSON modes, stderr otherwise."""
    if as_json or jsonl:
        emit_envelopes(
            [
                build_envelope(
                    command=command,
                    query=query,
                    result=None,
                    error=error,
                    elapsed_ms=elapsed_ms,
                )
            ],
            jsonl=jsonl,
        )
        return
    message = f"error: {prefix}{error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)


def _check_envelope(
    raw: str,
    result: Optional[CheckResult],
    error: Optional[OpskitError],
    *,
    port: Optional[int],
    protocol: Protocol,
    family: Optional[str],
    controls: "dict[str, Any]",
) -> "dict[str, Any]":
    """Build the JSON envelope for one target (success or failure — never dropped)."""
    if result is not None:
        return build_envelope(
            command="net.check",
            query=dict(result.target.to_dict(), **controls),
            result=result.to_dict(),
            error=None,
            elapsed_ms=result.time_ms,
        )
    # Failure path: enrich the query with the parsed target when it parses, so a failed
    # target's envelope still identifies the endpoint (not just the raw string).
    try:
        error_query: dict[str, Any] = parse_target(
            raw, port=port, protocol=protocol, family=family
        ).to_dict()
    except UsageError:
        error_query = {"target": raw, "protocol": protocol.value, "family": family}
    return build_envelope(
        command="net.check",
        query=dict(error_query, **controls),
        result=None,
        error=error,
        elapsed_ms=0.0,
    )


def _check_signature(
    outcomes: "list[tuple[str, Optional[CheckResult], Optional[OpskitError]]]",
) -> str:
    """Change-detection key: verdict class + connected address + family (R8).

    Timing is deliberately excluded so latency jitter never flags a change.
    """
    parts: list[object] = []
    for target, result, error in outcomes:
        if result is not None:
            parts.append([target, result.verdict.value, result.address, result.family])
        else:
            parts.append([target, error.code if error else "error"])
    return json.dumps(parts, sort_keys=True)


@app.command(epilog=_CHECK_EPILOG)
def check(
    targets: Annotated[
        Optional[list[str]],
        typer.Argument(
            help=r"Targets: host:port, \[ipv6]:port, or bare host/IP with --port.",
            show_default=False,
        ),
    ] = None,
    port: Annotated[
        Optional[int],
        typer.Option(
            "--port",
            "-p",
            help="Port for targets given without :port (must agree with shorthand).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    udp: Annotated[
        bool,
        typer.Option(
            "--udp",
            "-u",
            help="UDP mode: honest open / closed / inconclusive verdicts.",
            rich_help_panel="Query controls",
        ),
    ] = False,
    ipv4: Annotated[
        bool,
        typer.Option(
            "--ipv4",
            "-4",
            help="Restrict to IPv4 addresses (mutually exclusive with --ipv6).",
            rich_help_panel="Query controls",
        ),
    ] = False,
    ipv6: Annotated[
        bool,
        typer.Option(
            "--ipv6",
            "-6",
            help="Restrict to IPv6 addresses (mutually exclusive with --ipv4).",
            rich_help_panel="Query controls",
        ),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Per-attempt timeout, seconds.",
            rich_help_panel="Query controls",
        ),
    ] = 5.0,
    retries: Annotated[
        int,
        typer.Option(
            "--retries",
            help="Retries on timeout/silence (a refusal/unreachable is definitive).",
            rich_help_panel="Query controls",
        ),
    ] = 2,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of targets, one per line (# comments allowed); '-' reads stdin.",
            rich_help_panel="Query",
        ),
    ] = None,
    watch: Annotated[
        Optional[str],
        typer.Option(
            "--watch",
            help="Re-run every interval (e.g. 30s, 2m) until Ctrl-C.",
            rich_help_panel="Modes",
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
    """Check whether TCP/UDP ports are reachable (open / refused / timeout / …)."""
    protocol = Protocol.UDP if udp else Protocol.TCP
    try:
        family = _family_flag(ipv4, ipv6)
        target_list = collect_target_list(targets, input_file)
    except UsageError as error:
        raise _usage_exit(error) from error

    controls: dict[str, Any] = {"timeout": timeout, "retries": retries}

    def run_one(raw: str) -> CheckResult:
        return api.check(
            raw,
            port=port,
            protocol=protocol,
            family=family,
            timeout=timeout,
            retries=retries,
        )

    def action() -> ActionResult:
        outcomes: list[tuple[str, Optional[CheckResult], Optional[OpskitError]]] = []
        for raw in target_list:  # every target runs — never abort on first failure
            try:
                outcomes.append((raw, run_one(raw), None))
            except OpskitError as exc:
                outcomes.append((raw, None, exc))
        if as_json or jsonl:
            emit_envelopes(
                [
                    _check_envelope(
                        raw,
                        result,
                        error,
                        port=port,
                        protocol=protocol,
                        family=family,
                        controls=controls,
                    )
                    for raw, result, error in outcomes
                ],
                jsonl=jsonl,
            )
        else:
            console = make_console(no_color=no_color)
            batch = len(target_list) > 1
            for raw, result, error in outcomes:
                if batch:
                    console.print(f"[bold];; {escape(raw)}[/bold]")
                if result is not None:
                    render_check(result, console=console)
                elif error is not None:
                    message = f"error: {raw}: {error.message}"
                    if error.hint:
                        message += f"\nhint: {error.hint}"
                    typer.echo(message, err=True)
        codes = [
            ExitCode.OK if error is None else exit_code_for(error)
            for _, _, error in outcomes
        ]
        return aggregate_exit(codes), _check_signature(outcomes)

    run_or_watch(action, watch_spec=watch, no_color=no_color)


@app.command(epilog=_PROBE_EPILOG)
def probe(
    target: Annotated[
        str,
        typer.Argument(
            help=r"Target: host:port, \[ipv6]:port, or bare host/IP with --port."
        ),
    ],
    count: Annotated[
        int,
        typer.Option(
            "--count",
            "-c",
            help="Number of attempts (ping-like).",
            rich_help_panel="Query controls",
        ),
    ] = 4,
    interval: Annotated[
        str,
        typer.Option(
            "--interval",
            help="Delay between attempt starts (e.g. 500ms, 2s, 1m).",
            rich_help_panel="Query controls",
        ),
    ] = "1s",
    port: Annotated[
        Optional[int],
        typer.Option(
            "--port",
            "-p",
            help="Port when the target has no :port (must agree with shorthand).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    udp: Annotated[
        bool,
        typer.Option(
            "--udp",
            "-u",
            help="UDP mode: honest open / closed / inconclusive verdicts.",
            rich_help_panel="Query controls",
        ),
    ] = False,
    ipv4: Annotated[
        bool,
        typer.Option(
            "--ipv4",
            "-4",
            help="Restrict to IPv4 addresses (mutually exclusive with --ipv6).",
            rich_help_panel="Query controls",
        ),
    ] = False,
    ipv6: Annotated[
        bool,
        typer.Option(
            "--ipv6",
            "-6",
            help="Restrict to IPv6 addresses (mutually exclusive with --ipv4).",
            rich_help_panel="Query controls",
        ),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            help="Per-attempt timeout, seconds.",
            rich_help_panel="Query controls",
        ),
    ] = 5.0,
    retries: Annotated[
        int,
        typer.Option(
            "--retries",
            help="Retries within one attempt (the count is the retry story).",
            rich_help_panel="Query controls",
        ),
    ] = 0,
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
            help="Stream one envelope per attempt plus a summary envelope (NDJSON).",
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
    """Measure latency/stability with repeated probes (per-attempt timings + stats)."""
    protocol = Protocol.UDP if udp else Protocol.TCP
    try:
        family = _family_flag(ipv4, ipv6)
        interval_s = parse_interval(interval)
        parsed: NetTarget = parse_target(
            target, port=port, protocol=protocol, family=family
        )
    except UsageError as error:
        raise _usage_exit(error) from error

    controls: dict[str, Any] = {
        "count": count,
        "interval_s": interval_s,
        "timeout": timeout,
        "retries": retries,
    }
    query = dict(parsed.to_dict(), **controls)
    console = make_console(no_color=no_color)

    def on_attempt(attempt: ProbeAttempt) -> None:
        if jsonl:
            envelope = build_envelope(
                command="net.probe",
                query=query,
                result=dict({"kind": "attempt"}, **attempt.to_dict()),
                error=None,
                elapsed_ms=attempt.time_ms or 0.0,
            )
            typer.echo(to_json(envelope, indent=None))
        elif not as_json:
            render_probe_attempt(attempt, parsed.host, console=console)

    try:
        result = api.probe(
            target,
            port=port,
            protocol=protocol,
            family=family,
            count=count,
            interval=interval_s,
            timeout=timeout,
            retries=retries,
            on_attempt=on_attempt,
        )
    except OpskitError as error:  # pre-flight only (usage / resolution)
        _report_failure(
            error,
            command="net.probe",
            query=query,
            elapsed_ms=0.0,
            as_json=as_json,
            jsonl=jsonl,
            prefix=f"{target}: ",
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    if jsonl:
        summary = build_envelope(
            command="net.probe",
            query=query,
            result=dict({"kind": "summary"}, **result.summary_dict()),
            error=None,
            elapsed_ms=result.elapsed_ms,
        )
        typer.echo(to_json(summary, indent=None))
    elif as_json:
        emit_envelopes(
            [
                build_envelope(
                    command="net.probe",
                    query=query,
                    result=result.to_dict(),
                    error=None,
                    elapsed_ms=result.elapsed_ms,
                )
            ],
            jsonl=False,
        )
    else:
        render_probe_summary(result, console=console)

    codes = [_VERDICT_EXIT.get(a.verdict, ExitCode.ERROR) for a in result.attempts]
    raise typer.Exit(int(aggregate_exit(codes)))


@app.command(epilog=_LISTEN_EPILOG)
def listen(
    port: Annotated[
        int,
        typer.Argument(
            help="Port to bind on the wildcard address (both available families)."
        ),
    ],
    udp: Annotated[
        bool,
        typer.Option(
            "--udp",
            "-u",
            help="Receive UDP datagrams instead of accepting TCP connections.",
            rich_help_panel="Query controls",
        ),
    ] = False,
    max_duration: Annotated[
        Optional[str],
        typer.Option(
            "--max-duration",
            help="Stop after this long (e.g. 30s, 5m).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    max_events: Annotated[
        Optional[int],
        typer.Option(
            "--max-events",
            help="Stop after N connections/datagrams.",
            rich_help_panel="Query controls",
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
            help="Stream one envelope per event plus a session envelope (NDJSON).",
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
    """Listen temporarily and report inbound connections/datagrams (metadata only)."""
    protocol = Protocol.UDP if udp else Protocol.TCP
    try:
        duration_s = parse_interval(max_duration) if max_duration is not None else None
    except UsageError as error:
        raise _usage_exit(error) from error

    query: dict[str, Any] = {"port": port, "protocol": protocol.value}
    console = make_console(no_color=no_color)
    start = time.perf_counter()
    collected: list[InboundEvent] = []

    def _elapsed_ms() -> float:
        return (time.perf_counter() - start) * 1000.0

    try:
        listener = Listener(
            port, protocol=protocol, max_duration=duration_s, max_events=max_events
        )
        with listener:
            if not (as_json or jsonl):
                render_listen_banner(listener.session, console=console)
            try:
                for event in listener.events():
                    if jsonl:
                        envelope = build_envelope(
                            command="net.listen",
                            query=query,
                            result=dict({"kind": "event"}, **event.to_dict()),
                            error=None,
                            elapsed_ms=_elapsed_ms(),
                        )
                        typer.echo(to_json(envelope, indent=None))
                    elif as_json:
                        collected.append(event)
                    else:
                        render_listen_event(event, console=console)
            except KeyboardInterrupt:
                pass  # session already finalized by events(); summarize below
    except OpskitError as error:  # bad port/controls, or bind failure (12/13)
        _report_failure(
            error,
            command="net.listen",
            query=query,
            elapsed_ms=_elapsed_ms(),
            as_json=as_json,
            jsonl=jsonl,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    session = listener.session
    if jsonl:
        envelope = build_envelope(
            command="net.listen",
            query=query,
            result=dict({"kind": "session"}, **session.to_dict()),
            error=None,
            elapsed_ms=_elapsed_ms(),
        )
        typer.echo(to_json(envelope, indent=None))
    elif as_json:
        emit_envelopes(
            [
                build_envelope(
                    command="net.listen",
                    query=query,
                    result=dict(
                        session.to_dict(),
                        events=[event.to_dict() for event in collected],
                    ),
                    error=None,
                    elapsed_ms=_elapsed_ms(),
                )
            ],
            jsonl=False,
        )
    else:
        render_listen_summary(session, console=console)

    # "Nothing reached me" is the branchable answer: duration expiry with zero events
    # exits with the no-response class; every other clean stop is success (R4).
    zero_event_expiry = (
        session.stop_reason is StopReason.MAX_DURATION and session.events_received == 0
    )
    raise typer.Exit(int(ExitCode.TIMEOUT if zero_event_expiry else ExitCode.OK))
