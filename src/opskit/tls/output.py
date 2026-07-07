"""Rendering of TLS check results to human-readable (rich) tables.

Category-owned so :mod:`opskit.core` stays free of TLS models. Certificate- and
server-derived strings are escaped as rich markup before printing.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from opskit.tls.models import TlsCheckResult, TlsOutcome

_VERDICT_STYLE = {
    TlsOutcome.OK: "[green]OK[/green]",
    TlsOutcome.EXPIRING_SOON: "[yellow]EXPIRING SOON[/yellow]",
    TlsOutcome.CERT_INVALID: "[red]CERTIFICATE INVALID[/red]",
}


def render_check(result: TlsCheckResult, *, console: Console) -> None:
    """Print the full layered report: verdict, leaf, chain, protocol, findings."""
    verdict = _VERDICT_STYLE.get(result.outcome, result.outcome.value)
    target = result.target
    console.print(
        f"{verdict}  {escape(target.host)}:{target.port}"
        + (
            f"  (sni: {escape(target.server_name)})"
            if target.server_name and target.server_name != target.host
            else ""
        )
    )
    if result.connection is not None:
        console.print(
            f"[dim]connected to {escape(result.connection.address)} "
            f"({result.connection.family}) in {result.connection.connect_ms:.0f} ms[/dim]"
        )
    if target.is_ip:
        console.print("[dim]IP target: no SNI sent; matched against IP SANs[/dim]")

    leaf = result.leaf
    if leaf is not None:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("field", style="bold", no_wrap=True)
        table.add_column("value")
        table.add_row("subject", escape(leaf.subject))
        table.add_row("issuer", escape(leaf.issuer))
        table.add_row("SANs", escape(", ".join(leaf.sans) or "(none)"))
        table.add_row(
            "validity",
            escape(f"{leaf.not_before} -> {leaf.not_after}")
            + f"  ({leaf.days_until_expiry} days remaining)",
        )
        table.add_row("serial", escape(leaf.serial))
        table.add_row("signature", escape(leaf.signature_algorithm))
        table.add_row("public key", f"{escape(leaf.key_type)} {leaf.key_bits} bits")
        table.add_row("sha256", escape(leaf.fingerprint_sha256))
        console.print(table)

    if len(result.chain) > 1:
        chain_table = Table(show_header=True, header_style="bold", title="Chain")
        chain_table.add_column("#", justify="right")
        chain_table.add_column("SUBJECT")
        chain_table.add_column("ISSUER")
        chain_table.add_column("EXPIRES")
        for index, cert in enumerate(result.chain):
            chain_table.add_row(
                str(index),
                escape(cert.subject),
                escape(cert.issuer),
                escape(cert.not_after),
            )
        console.print(chain_table)

    if result.tls_version or result.cipher:
        console.print(
            f"protocol: [bold]{escape(result.tls_version or '?')}[/bold]"
            f"  cipher: {escape(result.cipher or '?')}"
        )

    for finding in result.findings:
        console.print(f"[red]![/red] {finding.code.value}: {escape(finding.message)}")
        if finding.hint:
            console.print(f"  [dim]hint: {escape(finding.hint)}[/dim]")
