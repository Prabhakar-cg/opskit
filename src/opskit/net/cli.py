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
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, NamedTuple, Optional

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
    ProxySpec,
    Route,
    StopReason,
    Verdict,
    parse_proxy,
    parse_target,
    proxy_exempt,
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
  opskit net check internal.example:443 --proxy proxy.corp.example:3128
  opskit net check api.example.com:443 --direct
"""

_PROBE_EPILOG = """\
[bold]Examples[/bold]

  opskit net probe api.example.com:443 -c 20 --interval 500ms
  opskit net probe dns.example.com:53 --udp -c 10
  opskit net probe internal.example:443 --proxy proxy.corp.example:3128 -c 10
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
    Verdict.AUTH_REQUIRED: ExitCode.AUTH_FAILED,
    Verdict.TUNNEL_DENIED: ExitCode.TUNNEL_DENIED,
    Verdict.GATEWAY_FAILED: ExitCode.PROXY_GATEWAY,
    Verdict.NOT_A_PROXY: ExitCode.NOT_A_PROXY,
}


class ProxyConfig(NamedTuple):
    """The run-level proxy decision: nominated spec, provenance, exemptions."""

    spec: Optional[ProxySpec]
    source: str  # "flag" | "env:<VAR>" | "default"
    exemptions: tuple[str, ...]


# Fixed consultation order regardless of target port (clarification 2026-07-15);
# for each name the uppercase then lowercase form is checked.
_PROXY_ENV_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


def _exemption_list(no_proxy: Optional[str]) -> tuple[str, ...]:
    """The NO_PROXY exemption entries: the flag value replaces the env entirely."""
    raw = no_proxy
    if raw is None:
        raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    return tuple(entry.strip() for entry in raw.split(",") if entry.strip())


def resolve_proxy_config(
    proxy: Optional[str], no_proxy: Optional[str], direct: bool
) -> ProxyConfig:
    """Resolve the effective proxy: flag > env vars > built-in direct (research R3).

    This is the ONLY place opskit reads proxy environment variables — the library
    layer takes the result as explicit arguments (Art. VII). The profile/config
    rungs of the precedence chain slot in here when config support lands.

    Raises:
        UsageError: For ``--proxy`` + ``--direct``, or an invalid proxy spec
            (a bad env value names its variable).
    """
    if direct and proxy is not None:
        raise UsageError(
            "--proxy and --direct are mutually exclusive",
            hint="drop one of them (--direct forces a direct check)",
        )
    exemptions = _exemption_list(no_proxy)
    if direct:
        return ProxyConfig(None, "flag", exemptions)
    if proxy is not None:
        return ProxyConfig(parse_proxy(proxy), "flag", exemptions)
    for name in _PROXY_ENV_VARS:
        for var in (name, name.lower()):
            value = os.environ.get(var)
            if value and value.strip():
                try:
                    return ProxyConfig(parse_proxy(value), f"env:{var}", exemptions)
                except UsageError as exc:
                    raise UsageError(
                        f"{var}: {exc.message}",
                        hint=exc.hint or "fix or unset the variable, or pass --direct",
                    ) from exc
    return ProxyConfig(None, "default", exemptions)


def _route_for(
    cfg: ProxyConfig,
    raw: str,
    *,
    port: Optional[int],
    protocol: Protocol,
    family: Optional[str],
) -> tuple[Optional[ProxySpec], Route]:
    """Decide one target's route: the configured proxy, or direct via exemption."""
    if cfg.spec is None:
        return None, Route.direct(source=cfg.source)
    try:
        host = parse_target(raw, port=port, protocol=protocol, family=family).host
    except UsageError:
        host = raw.strip()  # the target error itself surfaces from api.check
    if proxy_exempt(host, cfg.exemptions):
        return None, Route.direct(source="no-proxy-exemption")
    return cfg.spec, Route.via_proxy(cfg.spec, source=cfg.source)


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
    route: Optional[Route] = None,
) -> None:
    """Report a run-level failure: an envelope in JSON modes, stderr otherwise.

    check/probe failures carry the always-present ``route`` object; listen
    envelopes have no route (the listener makes no outbound connection).
    """
    if as_json or jsonl:
        envelope = build_envelope(
            command=command,
            query=query,
            result=None,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        if route is not None:
            envelope["route"] = route.to_dict()
        emit_envelopes([envelope], jsonl=jsonl)
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
    route: Optional[Route] = None,
) -> "dict[str, Any]":
    """Build the JSON envelope for one target (success or failure — never dropped).

    Every envelope carries an always-present top-level ``route`` object (an explicit
    ``direct`` when no proxy is in play), so a failed target — whose ``result`` is
    ``null`` — still discloses how it was checked.
    """
    if result is not None:
        envelope = build_envelope(
            command="net.check",
            query=dict(result.target.to_dict(), **controls),
            result=result.to_dict(),
            error=None,
            elapsed_ms=result.time_ms,
        )
    else:
        # Failure path: enrich the query with the parsed target when it parses, so a
        # failed target's envelope still identifies the endpoint (not just the raw
        # string).
        try:
            error_query: dict[str, Any] = parse_target(
                raw, port=port, protocol=protocol, family=family
            ).to_dict()
        except UsageError:
            error_query = {"target": raw, "protocol": protocol.value, "family": family}
        envelope = build_envelope(
            command="net.check",
            query=dict(error_query, **controls),
            result=None,
            error=error,
            elapsed_ms=0.0,
        )
    effective_route = route
    if effective_route is None:
        effective_route = result.route if result is not None else Route.direct()
    envelope["route"] = effective_route.to_dict()
    return envelope


def _check_signature(
    outcomes: "list[tuple[str, Optional[CheckResult], Optional[OpskitError], Route]]",
) -> str:
    """Change-detection key: verdict class + address + family + route (R8, FR-019).

    Timing is deliberately excluded so latency jitter never flags a change; the
    route (via + proxy) is included so a route flip flags like a verdict flip.
    """
    parts: list[object] = []
    for target, result, error, route in outcomes:
        route_key = [route.via, route.proxy]
        if result is not None:
            parts.append(
                [
                    target,
                    result.verdict.value,
                    result.address,
                    result.family,
                    *route_key,
                ]
            )
        else:
            parts.append([target, error.code if error else "error", *route_key])
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
    proxy: Annotated[
        Optional[str],
        typer.Option(
            "--proxy",
            help="HTTP proxy to tunnel through (host:port or "
            "http://user:pass@host:port); falls back to HTTPS_PROXY/HTTP_PROXY/"
            "ALL_PROXY. Worst case per target is about 2 x timeout x (retries+1).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    no_proxy: Annotated[
        Optional[str],
        typer.Option(
            "--no-proxy",
            help="Comma-separated proxy exemptions (host or domain suffix); "
            "replaces the NO_PROXY variable when given.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    direct: Annotated[
        bool,
        typer.Option(
            "--direct",
            help="Force a direct check even when the environment nominates a proxy.",
            rich_help_panel="Query controls",
        ),
    ] = False,
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
        proxy_cfg = resolve_proxy_config(proxy, no_proxy, direct)
        # No run-level UDP guard: NO_PROXY-exempt targets are checked directly, so
        # the api layer rejects UDP+proxy per target (only where the proxy is in
        # force, T028) and exempt targets keep working.
    except UsageError as error:
        raise _usage_exit(error) from error

    controls: dict[str, Any] = {"timeout": timeout, "retries": retries}
    if proxy_cfg.spec is not None:
        controls["proxy"] = proxy_cfg.spec.display  # redacted echo (FR-014)

    def run_one(raw: str, spec: Optional[ProxySpec]) -> CheckResult:
        return api.check(
            raw,
            port=port,
            protocol=protocol,
            family=family,
            timeout=timeout,
            retries=retries,
            proxy=spec,
        )

    def action() -> ActionResult:
        outcomes: list[
            tuple[str, Optional[CheckResult], Optional[OpskitError], Route]
        ] = []
        for raw in target_list:  # every target runs — never abort on first failure
            spec, route = _route_for(
                proxy_cfg, raw, port=port, protocol=protocol, family=family
            )
            try:
                checked = replace(run_one(raw, spec), route=route)
                outcomes.append((raw, checked, None, route))
            except OpskitError as exc:
                outcomes.append((raw, None, exc, route))
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
                        route=route,
                    )
                    for raw, result, error, route in outcomes
                ],
                jsonl=jsonl,
            )
        else:
            console = make_console(no_color=no_color)
            batch = len(target_list) > 1
            for raw, result, error, _route in outcomes:
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
            for _, _, error, _ in outcomes
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
    proxy: Annotated[
        Optional[str],
        typer.Option(
            "--proxy",
            help="HTTP proxy to tunnel through (host:port or "
            "http://user:pass@host:port); falls back to HTTPS_PROXY/HTTP_PROXY/"
            "ALL_PROXY. Timings become tunnel establishment times.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    no_proxy: Annotated[
        Optional[str],
        typer.Option(
            "--no-proxy",
            help="Comma-separated proxy exemptions (host or domain suffix); "
            "replaces the NO_PROXY variable when given.",
            rich_help_panel="Query controls",
        ),
    ] = None,
    direct: Annotated[
        bool,
        typer.Option(
            "--direct",
            help="Force a direct probe even when the environment nominates a proxy.",
            rich_help_panel="Query controls",
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
        proxy_cfg = resolve_proxy_config(proxy, no_proxy, direct)
        spec, route = _route_for(
            proxy_cfg, target, port=port, protocol=protocol, family=family
        )
        if spec is not None and protocol is Protocol.UDP:
            # Guard only when the proxy is in force for THIS target (T028): a
            # NO_PROXY-exempt target probes directly and stays valid with --udp.
            raise UsageError(
                "cannot probe a UDP port through an HTTP proxy",
                hint="HTTP CONNECT tunnels are TCP-only; drop --udp or pass "
                "--direct to bypass the environment's proxy",
            )
    except UsageError as error:
        raise _usage_exit(error) from error
    controls: dict[str, Any] = {
        "count": count,
        "interval_s": interval_s,
        "timeout": timeout,
        "retries": retries,
    }
    if proxy_cfg.spec is not None:
        controls["proxy"] = proxy_cfg.spec.display  # redacted echo (FR-014)
    query = dict(parsed.to_dict(), **controls)
    console = make_console(no_color=no_color)

    def _envelope(result_obj: "dict[str, Any]", elapsed_ms: float) -> "dict[str, Any]":
        envelope = build_envelope(
            command="net.probe",
            query=query,
            result=result_obj,
            error=None,
            elapsed_ms=elapsed_ms,
        )
        envelope["route"] = route.to_dict()
        return envelope

    def on_attempt(attempt: ProbeAttempt) -> None:
        if jsonl:
            envelope = _envelope(
                dict({"kind": "attempt"}, **attempt.to_dict()),
                attempt.time_ms or 0.0,
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
            proxy=spec,
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
            route=route,
        )
        raise typer.Exit(int(exit_code_for(error))) from error

    result = replace(result, route=route)
    if jsonl:
        summary = _envelope(
            dict({"kind": "summary"}, **result.summary_dict()), result.elapsed_ms
        )
        typer.echo(to_json(summary, indent=None))
    elif as_json:
        emit_envelopes([_envelope(result.to_dict(), result.elapsed_ms)], jsonl=False)
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
