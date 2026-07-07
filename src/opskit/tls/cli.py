"""Thin Typer sub-app for TLS verification: parse args, delegate to the API, render.

Holds no business logic — it maps options onto :mod:`opskit.tls.api` and turns typed
results/exceptions into human or JSON output and structured exit codes. Supports bulk
targets via a positional argument and/or ``--input-file`` (one ``host[:port]`` per line).

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer
   reads the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them
   to strings (PEP 563) makes Typer silently drop the metadata on Python 3.9. Keep
   annotations eager here and use ``Optional[...]``.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.markup import escape

from opskit.core.cliutils import (
    ActionResult,
    aggregate_exit,
    collect_outcomes,
    collect_targets,
    emit_envelopes,
    run_or_watch,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import build_envelope
from opskit.tls import api
from opskit.tls.models import TlsCheckResult, TlsOutcome
from opskit.tls.output import render_check

app = typer.Typer(
    name="tls",
    help="TLS verification (certificates, chains, protocol).",
    no_args_is_help=True,
)

_CHECK_EPILOG = """\
[bold]Examples[/bold]

  opskit tls check example.com
  opskit tls check example.com:8443
  opskit tls check 192.0.2.10 -p 8443 --sni internal.example.com
  opskit tls check ldap.corp.example:636 --ca-file corp-root.pem
  opskit tls check example.com --warn-days 14
  opskit tls check -i endpoints.txt --jsonl
  opskit tls check example.com --watch 30s
"""

_OUTCOME_EXIT = {
    TlsOutcome.OK: ExitCode.OK,
    TlsOutcome.EXPIRING_SOON: ExitCode.CERT_EXPIRING,
    TlsOutcome.CERT_INVALID: ExitCode.CERT_INVALID,
}


def _result_exit(result: TlsCheckResult) -> ExitCode:
    return _OUTCOME_EXIT.get(result.outcome, ExitCode.ERROR)


def _envelope(
    target: str,
    result: Optional[TlsCheckResult],
    error: Optional[OpskitError],
    *,
    controls: "dict[str, Any]",
) -> "dict[str, Any]":
    """Build the JSON envelope for one target (success or failure — never dropped)."""
    if result is not None:
        query = dict(result.target.to_dict(), **controls)
        return build_envelope(
            command="tls.check",
            query=query,
            result=result.to_dict(),
            error=None,
            elapsed_ms=result.elapsed_ms,
        )
    return build_envelope(
        command="tls.check",
        query=dict({"target": target}, **controls),
        result=None,
        error=error,
        elapsed_ms=0.0,
    )


def _signature(
    outcomes: "list[tuple[str, Optional[TlsCheckResult], Optional[OpskitError]]]",
) -> str:
    """Change-detection key: outcome class + leaf fingerprint + expiry + protocol (R8)."""
    parts: list[object] = []
    for target, result, error in outcomes:
        if result is not None:
            leaf = result.leaf
            parts.append(
                [
                    target,
                    result.outcome.value,
                    leaf.fingerprint_sha256 if leaf else None,
                    leaf.not_after if leaf else None,
                    result.tls_version,
                ]
            )
        else:
            parts.append([target, error.code if error else "error"])
    return json.dumps(parts, sort_keys=True)


@app.command(epilog=_CHECK_EPILOG)
def check(
    target: Annotated[
        Optional[str],
        typer.Argument(
            help=r"host, host:port, IP, or \[ipv6]:port (or use --input-file)."
        ),
    ] = None,
    port: Annotated[
        Optional[int],
        typer.Option(
            "--port",
            "-p",
            help="Port to check (default 443; must agree with host:port shorthand).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    sni: Annotated[
        Optional[str],
        typer.Option(
            "--sni",
            help="Server name to send (default: the hostname; omitted for IP targets).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    ca_file: Annotated[
        Optional[Path],
        typer.Option(
            "--ca-file",
            help="PEM bundle replacing the platform trust store (private PKI).",
            rich_help_panel="Query controls",
        ),
    ] = None,
    warn_days: Annotated[
        int,
        typer.Option(
            "--warn-days",
            help="Warn when the certificate expires within N days (0 disables).",
            rich_help_panel="Query controls",
        ),
    ] = 30,
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
            "--retries", help="Retries on timeout.", rich_help_panel="Query controls"
        ),
    ] = 2,
    input_file: Annotated[
        Optional[Path],
        typer.Option(
            "--input-file",
            "-i",
            help="File of targets, one host[:port] per line (# comments allowed).",
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
    """Verify the TLS health of one or more endpoints (default port 443)."""
    try:
        targets = collect_targets(target, input_file)
    except UsageError as error:
        typer.echo(f"error: {error.message}", err=True)
        raise typer.Exit(int(ExitCode.USAGE)) from error

    controls: dict[str, Any] = {
        "timeout": timeout,
        "retries": retries,
        "warn_days": warn_days,
    }
    if ca_file is not None:
        controls["ca_file"] = str(ca_file)

    def run_one(raw: str) -> TlsCheckResult:
        return api.check(
            raw,
            port=port,
            server_name=sni,
            ca_file=ca_file,
            warn_days=warn_days,
            timeout=timeout,
            retries=retries,
        )

    def action() -> ActionResult:
        outcomes = collect_outcomes(targets, run_one)
        if as_json or jsonl:
            emit_envelopes(
                [_envelope(t, r, e, controls=controls) for t, r, e in outcomes],
                jsonl=jsonl,
            )
        else:
            console = make_console(no_color=no_color)
            batch = len(targets) > 1
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
        codes: list[ExitCode] = []
        for _, result, error in outcomes:
            if result is not None:
                codes.append(_result_exit(result))
            elif error is not None:
                codes.append(exit_code_for(error))
        return aggregate_exit(codes), _signature(outcomes)

    run_or_watch(action, watch_spec=watch, no_color=no_color)
