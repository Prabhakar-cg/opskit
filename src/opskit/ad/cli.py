"""Thin Typer sub-app for Active Directory / LDAP diagnostics: parse, delegate, render.

Holds no business logic — it maps options onto :mod:`opskit.ad.api` and turns typed
results/exceptions into human or JSON output and structured exit codes. ``user`` and
``show`` are batchable (variadic names, ``--input-file``, stdin via ``-i -``) over one
authenticated session; ``user`` is watchable. The bind password is **never** an option:
it comes from ``OPSKIT_AD_PASSWORD`` or a hidden interactive prompt (Art. III).

.. note::
   This module intentionally does **not** use ``from __future__ import annotations``. Typer
   reads the ``Annotated[...]`` metadata off the concrete annotation objects; deferring them
   to strings (PEP 563) makes Typer silently drop the metadata on Python 3.9. Keep
   annotations eager here and use ``Optional[...]``.
"""

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
from rich.markup import escape

from opskit.ad import api
from opskit.ad.models import AccountStatusReport, DirectoryConfig
from opskit.ad.output import (
    render_check,
    render_member_verdict,
    render_membership,
    render_object,
    render_status,
)
from opskit.core.cliutils import (
    ActionResult,
    aggregate_exit,
    collect_target_list,
    emit_envelopes,
    run_or_watch,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.core.exit_codes import ExitCode, exit_code_for
from opskit.core.output import make_console
from opskit.core.result import build_envelope

PASSWORD_ENV = "OPSKIT_AD_PASSWORD"  # noqa: S105 - env var *name*, not a secret

app = typer.Typer(
    name="ad",
    help="Active Directory / LDAP — account status, group membership, directory checks.",
    no_args_is_help=True,
)

_CHECK_EPILOG = """\
[bold]Examples[/bold]

  opskit ad check dc01.corp.example.com -U ops@corp.example.com
  opskit ad check -d corp.example.com
  opskit ad check dc01:3269 --json
  opskit ad check dc01 --starttls --ca-file corp-root.pem

The bind password comes from OPSKIT_AD_PASSWORD or a hidden prompt — never a flag.
"""

_USER_EPILOG = """\
[bold]Examples[/bold]

  opskit ad user jdoe -d corp.example.com -U ops@corp.example.com
  opskit ad user jdoe asmith svc-backup --jsonl
  opskit ad user -i users.txt --jsonl
  cat users.txt | opskit ad user -i - --jsonl
  opskit ad user jdoe --watch 30s
"""

_GROUPS_EPILOG = """\
[bold]Examples[/bold]

  opskit ad groups jdoe -d corp.example.com
  opskit ad groups jdoe --effective
  opskit ad groups wks-042$ --json
"""

_MEMBER_EPILOG = """\
[bold]Examples[/bold]

  opskit ad member jdoe "VPN Users"
  opskit ad member jdoe "Domain Admins" --json   # exit 0 = member, 17 = not
"""

_SHOW_EPILOG = """\
[bold]Examples[/bold]

  opskit ad show jdoe
  opskit ad show "VPN Users" --type group
  opskit ad show jdoe "VPN Users" wks-042$ --jsonl
  printf 'jdoe\\nVPN Users\\n' | opskit ad show -i - --jsonl
"""

# -- shared connection options (every ad command) ----------------------------------

ServerOption = Annotated[
    Optional[str],
    typer.Option(
        "--server",
        "-s",
        envvar="OPSKIT_AD_SERVER",
        help="Directory server: host or host:port (wins over --domain).",
        rich_help_panel="Connection",
        show_default=False,
    ),
]
DomainOption = Annotated[
    Optional[str],
    typer.Option(
        "--domain",
        "-d",
        envvar="OPSKIT_AD_DOMAIN",
        help="Domain to discover directory servers for (DNS SRV records).",
        rich_help_panel="Connection",
        show_default=False,
    ),
]
BindUserOption = Annotated[
    Optional[str],
    typer.Option(
        "--user",
        "-U",
        envvar="OPSKIT_AD_USER",
        help="Bind account (user@domain, DOMAIN\\name, or DN); omit for anonymous. "
        "Password via OPSKIT_AD_PASSWORD or hidden prompt — never a flag.",
        rich_help_panel="Connection",
        show_default=False,
    ),
]
StartTlsOption = Annotated[
    bool,
    typer.Option(
        "--starttls",
        help="Connect plain (port 389) and upgrade to TLS before any bind.",
        rich_help_panel="Connection",
    ),
]
PlaintextOption = Annotated[
    bool,
    typer.Option(
        "--plaintext",
        help="No TLS at all (lab use). Required to send a password unencrypted; "
        "output is marked as not encrypted.",
        rich_help_panel="Connection",
    ),
]
CaFileOption = Annotated[
    Optional[Path],
    typer.Option(
        "--ca-file",
        help="PEM bundle replacing the platform trust store (private PKI).",
        rich_help_panel="Connection",
        show_default=False,
    ),
]
BaseDnOption = Annotated[
    Optional[str],
    typer.Option(
        "--base-dn",
        help="Search base override (default: the server's defaultNamingContext).",
        rich_help_panel="Connection",
        show_default=False,
    ),
]
TimeoutOption = Annotated[
    float,
    typer.Option(
        "--timeout",
        help="Connect and per-operation timeout, seconds.",
        rich_help_panel="Connection",
    ),
]
InputFileOption = Annotated[
    Optional[Path],
    typer.Option(
        "--input-file",
        "-i",
        help="File of names, one per line (# comments allowed); '-' reads stdin.",
        rich_help_panel="Query",
    ),
]
JsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Emit the versioned JSON envelope.", rich_help_panel="Output"
    ),
]
JsonlOption = Annotated[
    bool,
    typer.Option(
        "--jsonl",
        help="Emit one JSON envelope per line (NDJSON).",
        rich_help_panel="Output",
    ),
]
NoColorOption = Annotated[
    bool,
    typer.Option(
        "--no-color", help="Disable colored output.", rich_help_panel="Output"
    ),
]


def _usage_exit(error: OpskitError) -> typer.Exit:
    """Report a pre-flight error to stderr and build its typed exit signal."""
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)
    return typer.Exit(int(exit_code_for(error)))


def _stdin_is_tty() -> bool:
    """Whether an interactive password prompt is possible (seam for tests)."""
    return sys.stdin.isatty()


def _resolve_password(bind_user: Optional[str]) -> Optional[str]:
    """Resolve the bind password: env, else hidden prompt (TTY only) — never a flag.

    Raises:
        UsageError: When a password is needed but stdin is not interactive and the
            environment variable is unset (never hang on a pipe).
    """
    if not bind_user:
        return None
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env is not None:
        return from_env
    if _stdin_is_tty():
        secret: str = typer.prompt(f"Password for {bind_user}", hide_input=True)
        return secret
    raise UsageError(
        "no password available and no interactive prompt (stdin is not a TTY)",
        hint=f"set {PASSWORD_ENV}, or run interactively to be prompted",
    )


def _build_config(
    *,
    server: Optional[str],
    domain: Optional[str],
    bind_user: Optional[str],
    starttls: bool,
    plaintext: bool,
    ca_file: Optional[Path],
    base_dn: Optional[str],
    timeout: float,
) -> DirectoryConfig:
    """Assemble the DirectoryConfig from CLI options (the only env-reading layer)."""
    if starttls and plaintext:
        raise UsageError(
            "--starttls and --plaintext are mutually exclusive",
            hint="pick one connection mode (default is LDAPS on 636)",
        )
    security = "starttls" if starttls else ("plaintext" if plaintext else "ldaps")
    password = _resolve_password(bind_user)
    return DirectoryConfig(
        server=server,
        domain=domain,
        security=security,
        bind_user=bind_user,
        password=password,
        allow_cleartext=plaintext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )


def _connection_echo(
    config: DirectoryConfig, client: Optional[api.AdClient]
) -> "dict[str, Any]":
    """The envelope's connection echo — never contains any password field (FR-004)."""
    echo: dict[str, Any] = {
        "security": config.security,
        "bind_user": config.bind_user,
    }
    connected = client.connected_server if client is not None else None
    if connected is not None:
        echo["server"], echo["port"] = connected
    else:
        echo["server"] = config.server
        echo["port"] = config.effective_port
    if config.domain:
        echo["domain"] = config.domain
    if not config.encrypted:
        echo["encrypted"] = False
    return echo


def _emit_failure(
    error: OpskitError,
    *,
    command: str,
    query: "dict[str, Any]",
    as_json: bool,
    jsonl: bool,
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
                    elapsed_ms=0.0,
                )
            ],
            jsonl=jsonl,
        )
        return
    message = f"error: {error.message}"
    if error.hint:
        message += f"\nhint: {error.hint}"
    typer.echo(message, err=True)


def _run_batch(
    names: "list[str]",
    config: DirectoryConfig,
    run_one: "Callable[[api.AdClient, str], Any]",
    client: api.AdClient,
) -> "list[tuple[str, Any, Optional[OpskitError]]]":
    """Run every name over one session; a connect failure applies to every name."""
    outcomes: list[tuple[str, Any, Optional[OpskitError]]] = []
    try:
        client.connect()
    except OpskitError as exc:
        return [(name, None, exc) for name in names]
    for name in names:  # every name runs — never abort on first failure (Art. IX)
        try:
            outcomes.append((name, run_one(client, name), None))
        except OpskitError as exc:
            outcomes.append((name, None, exc))
    return outcomes


def _emit_batch(
    outcomes: "list[tuple[str, Any, Optional[OpskitError]]]",
    *,
    command: str,
    name_key: str,
    config: DirectoryConfig,
    client: api.AdClient,
    as_json: bool,
    jsonl: bool,
    no_color: bool,
    render: "Callable[[Any, Any], None]",
) -> ExitCode:
    """Render/emit a batch's outcomes and return the aggregate exit code (Art. IX)."""
    echo = _connection_echo(config, client)
    if as_json or jsonl:
        envelopes = [
            build_envelope(
                command=command,
                query=dict(echo, **{name_key: name}),
                result=result.to_dict() if result is not None else None,
                error=error,
                elapsed_ms=0.0,
            )
            for name, result, error in outcomes
        ]
        emit_envelopes(envelopes, jsonl=jsonl)
    else:
        console = make_console(no_color=no_color)
        batch = len(outcomes) > 1
        for name, result, error in outcomes:
            if batch:
                console.print(f"[bold];; {escape(name)}[/bold]")
            if result is not None:
                render(result, console)
            elif error is not None:
                message = f"error: {name}: {error.message}"
                if error.hint:
                    message += f"\nhint: {error.hint}"
                typer.echo(message, err=True)
    return aggregate_exit(
        [
            ExitCode.OK if error is None else exit_code_for(error)
            for _, _, error in outcomes
        ]
    )


@app.command(epilog=_CHECK_EPILOG)
def check(
    server_arg: Annotated[
        Optional[str],
        typer.Argument(
            help="Directory server: host or host:port (or use --domain).",
            metavar="[SERVER]",
            show_default=False,
        ),
    ] = None,
    server: ServerOption = None,
    domain: DomainOption = None,
    bind_user: BindUserOption = None,
    starttls: StartTlsOption = False,
    plaintext: PlaintextOption = False,
    ca_file: CaFileOption = None,
    base_dn: BaseDnOption = None,
    timeout: TimeoutOption = 5.0,
    as_json: JsonOption = False,
    no_color: NoColorOption = False,
) -> None:
    """Verify directory connectivity and credentials (reached / secured / authenticated)."""
    try:
        if server_arg is not None and server is not None and server_arg != server:
            raise UsageError(
                f"conflicting servers: '{server_arg}' vs --server {server}",
                hint="give the server once",
            )
        effective_server = server_arg if server_arg is not None else server
        config = _build_config(
            server=effective_server,
            domain=domain if effective_server is None else None,
            bind_user=bind_user,
            starttls=starttls,
            plaintext=plaintext,
            ca_file=ca_file,
            base_dn=base_dn,
            timeout=timeout,
        )
    except OpskitError as error:
        raise _usage_exit(error) from error

    client = api.AdClient(config)
    with client:
        try:
            report = client.check()
        except OpskitError as error:
            _emit_failure(
                error,
                command="ad.check",
                query=_connection_echo(config, client),
                as_json=as_json,
                jsonl=False,
            )
            raise typer.Exit(int(exit_code_for(error))) from error
        if as_json:
            emit_envelopes(
                [
                    build_envelope(
                        command="ad.check",
                        query=_connection_echo(config, client),
                        result=report.to_dict(),
                        error=None,
                        elapsed_ms=sum(s.elapsed_ms for s in report.stages),
                    )
                ],
                jsonl=False,
            )
        else:
            render_check(report, console=make_console(no_color=no_color))
    raise typer.Exit(int(ExitCode.OK))


@app.command(epilog=_USER_EPILOG)
def user(
    principals: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="Principals: account name, user@domain, or DN.",
            show_default=False,
        ),
    ] = None,
    input_file: InputFileOption = None,
    watch: Annotated[
        Optional[str],
        typer.Option(
            "--watch",
            help="Re-run every interval (e.g. 30s, 2m) until Ctrl-C.",
            rich_help_panel="Modes",
        ),
    ] = None,
    server: ServerOption = None,
    domain: DomainOption = None,
    bind_user: BindUserOption = None,
    starttls: StartTlsOption = False,
    plaintext: PlaintextOption = False,
    ca_file: CaFileOption = None,
    base_dn: BaseDnOption = None,
    timeout: TimeoutOption = 5.0,
    as_json: JsonOption = False,
    jsonl: JsonlOption = False,
    no_color: NoColorOption = False,
) -> None:
    """Diagnose why an account can't sign in (enabled, lockout, password/account expiry)."""
    try:
        names = collect_target_list(principals, input_file)
        config = _build_config(
            server=server,
            domain=domain,
            bind_user=bind_user,
            starttls=starttls,
            plaintext=plaintext,
            ca_file=ca_file,
            base_dn=base_dn,
            timeout=timeout,
        )
    except OpskitError as error:
        raise _usage_exit(error) from error

    client = api.AdClient(config)

    def action() -> ActionResult:
        outcomes = _run_batch(
            names, config, lambda c, name: c.user_status(name), client
        )
        code = _emit_batch(
            outcomes,
            command="ad.user",
            name_key="principal",
            config=config,
            client=client,
            as_json=as_json,
            jsonl=jsonl,
            no_color=no_color,
            render=lambda result, console: render_status(result, console=console),
        )
        return code, _status_signature(outcomes)

    with client:
        run_or_watch(action, watch_spec=watch, no_color=no_color)


def _status_signature(
    outcomes: "list[tuple[str, Optional[AccountStatusReport], Optional[OpskitError]]]",
) -> str:
    """Watch change-detection key: blockers + core facts, never timings (R10)."""
    parts: list[object] = []
    for name, result, error in outcomes:
        if result is not None:
            parts.append(
                [
                    name,
                    sorted(result.blockers),
                    result.enabled,
                    result.locked,
                    result.password_expired,
                    result.account_expired,
                ]
            )
        else:
            parts.append([name, error.code if error else "error"])
    return json.dumps(parts, sort_keys=True)


@app.command(epilog=_GROUPS_EPILOG)
def groups(
    principal: Annotated[
        str,
        typer.Argument(help="Principal: account name, user@domain, or DN."),
    ],
    effective: Annotated[
        bool,
        typer.Option(
            "--effective",
            "-e",
            help="Resolve nested membership (cycle-safe) with acquisition paths.",
            rich_help_panel="Query",
        ),
    ] = False,
    server: ServerOption = None,
    domain: DomainOption = None,
    bind_user: BindUserOption = None,
    starttls: StartTlsOption = False,
    plaintext: PlaintextOption = False,
    ca_file: CaFileOption = None,
    base_dn: BaseDnOption = None,
    timeout: TimeoutOption = 5.0,
    as_json: JsonOption = False,
    no_color: NoColorOption = False,
) -> None:
    """List a principal's group memberships (direct, or effective with nesting)."""
    try:
        config = _build_config(
            server=server,
            domain=domain,
            bind_user=bind_user,
            starttls=starttls,
            plaintext=plaintext,
            ca_file=ca_file,
            base_dn=base_dn,
            timeout=timeout,
        )
    except OpskitError as error:
        raise _usage_exit(error) from error

    client = api.AdClient(config)
    with client:
        try:
            report = client.membership(principal, effective=effective)
        except OpskitError as error:
            _emit_failure(
                error,
                command="ad.groups",
                query=dict(_connection_echo(config, client), principal=principal),
                as_json=as_json,
                jsonl=False,
            )
            raise typer.Exit(int(exit_code_for(error))) from error
        if as_json:
            emit_envelopes(
                [
                    build_envelope(
                        command="ad.groups",
                        query=dict(
                            _connection_echo(config, client),
                            principal=principal,
                            effective=effective,
                        ),
                        result=report.to_dict(),
                        error=None,
                        elapsed_ms=0.0,
                    )
                ],
                jsonl=False,
            )
        else:
            render_membership(report, console=make_console(no_color=no_color))
    raise typer.Exit(int(ExitCode.OK))


@app.command(epilog=_MEMBER_EPILOG)
def member(
    principal: Annotated[
        str,
        typer.Argument(help="Principal: account name, user@domain, or DN."),
    ],
    group: Annotated[
        str,
        typer.Argument(help="Group: name, or DN."),
    ],
    server: ServerOption = None,
    domain: DomainOption = None,
    bind_user: BindUserOption = None,
    starttls: StartTlsOption = False,
    plaintext: PlaintextOption = False,
    ca_file: CaFileOption = None,
    base_dn: BaseDnOption = None,
    timeout: TimeoutOption = 5.0,
    as_json: JsonOption = False,
    no_color: NoColorOption = False,
) -> None:
    """Test whether a principal is in a group (exit 0 = member, 17 = not a member)."""
    try:
        config = _build_config(
            server=server,
            domain=domain,
            bind_user=bind_user,
            starttls=starttls,
            plaintext=plaintext,
            ca_file=ca_file,
            base_dn=base_dn,
            timeout=timeout,
        )
    except OpskitError as error:
        raise _usage_exit(error) from error

    client = api.AdClient(config)
    with client:
        try:
            verdict = client.is_member(principal, group)
        except OpskitError as error:
            _emit_failure(
                error,
                command="ad.member",
                query=dict(
                    _connection_echo(config, client),
                    principal=principal,
                    group=group,
                ),
                as_json=as_json,
                jsonl=False,
            )
            raise typer.Exit(int(exit_code_for(error))) from error
        if as_json:
            emit_envelopes(
                [
                    build_envelope(
                        command="ad.member",
                        query=dict(
                            _connection_echo(config, client),
                            principal=principal,
                            group=group,
                        ),
                        result=verdict.to_dict(),
                        error=None,
                        elapsed_ms=0.0,
                    )
                ],
                jsonl=False,
            )
        else:
            render_member_verdict(verdict, console=make_console(no_color=no_color))
    raise typer.Exit(int(ExitCode.OK if verdict.member else ExitCode.NOT_MEMBER))


@app.command(epilog=_SHOW_EPILOG)
def show(
    names: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="Object names: account name, user@domain, group name, or DN.",
            show_default=False,
        ),
    ] = None,
    object_type: Annotated[
        str,
        typer.Option(
            "--type",
            help="Object type to match: auto, user, group, or computer.",
            rich_help_panel="Query",
        ),
    ] = "auto",
    input_file: InputFileOption = None,
    server: ServerOption = None,
    domain: DomainOption = None,
    bind_user: BindUserOption = None,
    starttls: StartTlsOption = False,
    plaintext: PlaintextOption = False,
    ca_file: CaFileOption = None,
    base_dn: BaseDnOption = None,
    timeout: TimeoutOption = 5.0,
    as_json: JsonOption = False,
    jsonl: JsonlOption = False,
    no_color: NoColorOption = False,
) -> None:
    """Show key attributes of named objects (users, groups, computers) — batchable."""
    try:
        if object_type not in api.OBJECT_TYPES:
            raise UsageError(
                f"unknown object type: {object_type}",
                hint="use one of: " + ", ".join(api.OBJECT_TYPES),
            )
        batch_names = collect_target_list(names, input_file)
        config = _build_config(
            server=server,
            domain=domain,
            bind_user=bind_user,
            starttls=starttls,
            plaintext=plaintext,
            ca_file=ca_file,
            base_dn=base_dn,
            timeout=timeout,
        )
    except OpskitError as error:
        raise _usage_exit(error) from error

    client = api.AdClient(config)
    with client:
        outcomes = _run_batch(
            batch_names,
            config,
            lambda c, name: c.show(name, object_type=object_type),
            client,
        )
        code = _emit_batch(
            outcomes,
            command="ad.show",
            name_key="name",
            config=config,
            client=client,
            as_json=as_json,
            jsonl=jsonl,
            no_color=no_color,
            render=lambda result, console: render_object(result, console=console),
        )
    raise typer.Exit(int(code))
