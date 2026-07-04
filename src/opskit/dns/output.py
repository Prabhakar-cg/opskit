"""Rendering of DNS results to human-readable (rich) tables.

Category-owned so :mod:`opskit.core` stays free of DNS models. Resolver-derived and
user-supplied strings are escaped as rich markup before printing to avoid markup injection.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from opskit.dns.models import DnsRecord, Outcome, ResolverComparison, TraceStep


def render_records(records: Sequence[DnsRecord], *, console: Console) -> None:
    """Print records as a table (or a plain notice when there are none)."""
    if not records:
        console.print("No records found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("TYPE")
    table.add_column("VALUE")
    table.add_column("TTL", justify="right")
    for record in records:
        table.add_row(record.type.value, escape(record.value), str(record.ttl))
    console.print(table)


def render_comparison(comparison: ResolverComparison, *, console: Console) -> None:
    """Print a per-resolver comparison table, highlighting resolvers that disagree."""
    status = "consistent" if comparison.consistent else "DIFFERS"
    types = "/".join(t.value for t in comparison.record_types)
    table = Table(
        title=f"{escape(comparison.target)}  [{types}]  — {status}",
        show_header=True,
        header_style="bold",
    )
    table.add_column("RESOLVER")
    table.add_column("OUTCOME")
    table.add_column("RECORDS / ERROR")
    signatures = [a.signature() for a in comparison.answers]
    majority = Counter(signatures).most_common(1)[0][0] if signatures else None
    for answer, signature in zip(comparison.answers, signatures):
        lines = [
            f"{r.type.value}  {escape(r.value)}  (ttl {r.ttl})" for r in answer.records
        ]
        cell = "\n".join(lines) if lines else escape(answer.error or "—")
        differs = not comparison.consistent and signature != majority
        server = escape(answer.server)
        resolver = f"[yellow]{server}  ⚠ differs[/yellow]" if differs else server
        outcome = (
            answer.outcome.value
            if answer.outcome is Outcome.OK
            else f"[red]{answer.outcome.value}[/red]"
        )
        table.add_row(resolver, outcome, cell)
    console.print(table)


def render_trace(steps: Sequence[TraceStep], *, console: Console) -> None:
    """Print an iterative resolution trace (root -> authoritative), one row per hop."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("SERVER")
    table.add_column("ZONE")
    table.add_column("RESULT")
    for index, step in enumerate(steps, start=1):
        if step.response == "answer":
            records = "\n".join(
                f"{r.type.value}  {escape(r.value)}" for r in step.records
            )
            detail = records or "(answer)"
        elif step.response == "referral":
            referrals = ", ".join(escape(r) for r in step.referrals)
            detail = "-> " + referrals if referrals else "-> (referral)"
        else:
            detail = "(no response)"
        table.add_row(str(index), escape(step.server), escape(step.zone), detail)
    console.print(table)
