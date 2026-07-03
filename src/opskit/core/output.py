"""Rendering of DNS results to human-readable (rich) and JSON forms.

``rich.Console`` auto-detects a TTY (plain when piped) and honors ``NO_COLOR``; ``no_color``
forces plain output regardless.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from opskit.dns.models import DnsRecord


def make_console(*, no_color: bool = False) -> Console:
    """Return a console configured for the current output context."""
    return Console(no_color=no_color, highlight=False)


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
        table.add_row(record.type.value, record.value, str(record.ttl))
    console.print(table)
