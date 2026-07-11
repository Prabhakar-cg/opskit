"""Rendering of AD/LDAP results to human-readable (rich) output.

Category-owned so :mod:`opskit.core` stays free of ad models. List-shaped results
(status facts, memberships, group members, check stages) render as rich **tables**
(FR-015), and every directory-derived string (DNs, names, descriptions, hosts) is
escaped as rich markup before printing (CLAUDE.md cross-cutting rule).
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from opskit.ad.models import (
    AccountStatusReport,
    ConnectivityReport,
    MembershipReport,
    MembershipVerdict,
    ObjectSummary,
)

_YES = "[green]yes[/green]"
_NO = "[red]no[/red]"
_UNAVAILABLE = "[dim]not available from this server[/dim]"

_BLOCKER_TEXT = {
    "disabled": "account is disabled — an administrator must re-enable it",
    "locked_out": "account is locked out — unlock it or wait out the lockout window",
    "password_expired": "password has expired — reset it",
    "must_change_password": "password must be changed at next sign-in",
    "account_expired": "account has expired — an administrator must extend it",
}


def _when(value: datetime | None) -> str:
    """Render a timestamp (dimmed placeholder when unknown)."""
    if value is None:
        return "[dim]-[/dim]"
    return escape(value.strftime("%Y-%m-%d %H:%M:%S %Z"))


def _tristate(value: bool | None, *, invert: bool = False) -> str:
    """Render a tri-state fact: yes/no/unavailable (optionally inverted styling)."""
    if value is None:
        return _UNAVAILABLE
    shown = not value if invert else value
    return _YES if shown else _NO


def _plaintext_warning(console: Console) -> None:
    console.print(
        "[bold yellow]warning:[/bold yellow] connection was NOT encrypted (--plaintext)"
    )


def render_status(report: AccountStatusReport, *, console: Console) -> None:
    """Print the account-status verdict and the facts table (US1)."""
    if report.blockers:
        console.print(
            f"[bold red]sign-in blocked[/bold red] for {escape(report.principal)} "
            f"({len(report.blockers)} blocker(s))"
        )
        for blocker in report.blockers:
            console.print(f"  [red]x[/red] {_BLOCKER_TEXT.get(blocker, blocker)}")
    else:
        console.print(
            f"[bold green]no sign-in blockers found[/bold green] "
            f"for {escape(report.principal)}"
        )

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    table.add_row("account", escape(report.sam_account_name or report.principal))
    if report.user_principal_name:
        table.add_row("upn", escape(report.user_principal_name))
    table.add_row("location", escape(report.dn))
    table.add_row("enabled", _tristate(report.enabled))
    locked = _tristate(report.locked)
    if report.locked and report.lockout_stale_possible:
        locked += " [dim](recorded lockout — may have lapsed by policy)[/dim]"
    table.add_row("locked out", locked)
    if report.lockout_time is not None:
        table.add_row("lockout recorded", _when(report.lockout_time))
    table.add_row("password expired", _tristate(report.password_expired))
    if report.password_never_expires:
        table.add_row("password expires", "never")
    else:
        table.add_row("password expires", _when(report.password_expires_at))
    if report.must_change_password:
        table.add_row("must change password", _YES)
    table.add_row("password last set", _when(report.password_last_set))
    if report.account_never_expires:
        table.add_row("account expires", "never")
    else:
        table.add_row("account expires", _when(report.account_expires_at))
    console.print(table)
    if report.facts_unavailable:
        console.print(
            f"[dim]not available from this server: "
            f"{escape(', '.join(report.facts_unavailable))}[/dim]"
        )


def render_membership(report: MembershipReport, *, console: Console) -> None:
    """Print the membership table: group, how acquired, path, location (US2)."""
    kind = "effective" if report.effective else "direct"
    console.print(
        f"[bold]{kind} group membership[/bold] for {escape(report.principal)} "
        f"({len(report.groups)} group(s))"
    )
    if not report.groups:
        console.print("[dim]no group memberships found[/dim]")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("group", style="bold")
    table.add_column("via")
    if report.effective:
        table.add_column("path")
    table.add_column("location", style="dim")
    for entry in report.groups:
        path = " > ".join(escape(part) for part in entry.path)
        row = [escape(entry.name), entry.via]
        if report.effective:
            row.append(path or "[dim]-[/dim]")
        row.append(escape(entry.dn))
        table.add_row(*row)
    console.print(table)


def render_member_verdict(verdict: MembershipVerdict, *, console: Console) -> None:
    """Print the is-P-in-G verdict with the granting chain (US2)."""
    if verdict.member:
        chain = " > ".join(escape(part) for part in (*verdict.path, verdict.group))
        via = f" via {chain}" if verdict.via == "nested" else f" ({verdict.via})"
        console.print(
            f"[bold green]member[/bold green]: {escape(verdict.principal)} is in "
            f"{escape(verdict.group)}{via}"
        )
    else:
        console.print(
            f"[bold red]not a member[/bold red]: {escape(verdict.principal)} is not in "
            f"{escape(verdict.group)} (directly or through nesting)"
        )


def render_check(report: ConnectivityReport, *, console: Console) -> None:
    """Print the staged connectivity report (US3)."""
    source = "discovered" if report.discovered else "given"
    console.print(
        f"[bold]directory check[/bold] against "
        f"{escape(report.server_used)}:{report.port} "
        f"({report.security}, server {source})"
    )
    if not report.encrypted:
        _plaintext_warning(console)
    table = Table(box=None, pad_edge=False)
    table.add_column("stage", style="bold")
    table.add_column("result")
    table.add_column("time", justify="right")
    for stage in report.stages:
        table.add_row(
            stage.name,
            "[green]ok[/green]" if stage.ok else "[red]failed[/red]",
            f"{stage.elapsed_ms:.1f} ms",
        )
    console.print(table)
    bind = escape(report.bind_user) if report.bind_user else "[dim]anonymous[/dim]"
    console.print(f"bind account: {bind}")
    info = report.server_info
    if info.dns_host_name:
        console.print(f"server: {escape(info.dns_host_name)}")
    if info.default_naming_context:
        console.print(f"naming context: {escape(info.default_naming_context)}")
    if info.vendor:
        console.print(f"vendor: {escape(info.vendor)}")
    if report.discovered and len(report.candidates_tried) > 1:
        tried = ", ".join(escape(host) for host in report.candidates_tried)
        console.print(f"[dim]candidates tried: {tried}[/dim]")


def render_object(summary: ObjectSummary, *, console: Console) -> None:
    """Print one object's key-attribute table (group members as a nested table, US5)."""
    console.print(f"[bold]{escape(summary.name)}[/bold] ({summary.object_type})")
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold", no_wrap=True)
    table.add_column()
    table.add_row("location", escape(summary.dn))
    for label, key in (
        ("account name", "sam_account_name"),
        ("upn", "user_principal_name"),
        ("sid", "sid"),
    ):
        value = summary.identifiers.get(key)
        if value:
            table.add_row(label, escape(value))
    table.add_row("created", _when(summary.created))
    table.add_row("changed", _when(summary.changed))
    if summary.description:
        table.add_row("description", escape(summary.description))
    for key, value in summary.type_facts.items():
        if key == "members" or value is None:
            continue
        table.add_row(key.replace("_", " "), escape(str(value)))
    console.print(table)

    raw_members = summary.type_facts.get("members")
    if isinstance(raw_members, list):
        # The api layer builds these entries; the cast just names their shape.
        members = cast("list[dict[str, str]]", raw_members)
        console.print(f"[bold]direct members[/bold] ({len(members)})")
        members_table = Table(box=None, pad_edge=False)
        members_table.add_column("member", style="bold")
        members_table.add_column("location", style="dim")
        for member in members:
            members_table.add_row(
                escape(str(member.get("name", ""))), escape(str(member.get("dn", "")))
            )
        console.print(members_table)
