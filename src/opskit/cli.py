"""Root command-line interface for opskit.

Per the constitution the CLI is a thin presentation layer: it parses arguments and
renders results, holding no business logic. Categories (e.g. ``dns``) register
themselves here as Typer sub-apps.
"""

from __future__ import annotations

import typer

from opskit import __version__
from opskit.dns.cli import app as dns_app
from opskit.tls.cli import app as tls_app

app = typer.Typer(
    name="opskit",
    help="Cross-platform diagnostics for engineers — one toolkit, every OS.",
    no_args_is_help=True,
    add_completion=True,
)
app.add_typer(dns_app, name="dns")
app.add_typer(tls_app, name="tls")


def _version_callback(value: bool) -> None:
    """Print the version and exit when ``--version`` was requested."""
    if value:
        typer.echo(f"opskit {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the opskit version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Top-level options shared by every opskit command."""


def main() -> None:  # pragma: no cover
    """Console-script entry point (``opskit``)."""
    app()
