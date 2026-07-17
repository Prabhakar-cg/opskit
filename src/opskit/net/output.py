"""Rendering of net results to human-readable (rich) output.

Category-owned so :mod:`opskit.core` stays free of net models. Every network- or
user-derived string (hostnames, addresses, peer strings) is escaped as rich markup
before printing (CLAUDE.md cross-cutting rule).
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from opskit.net.models import (
    CheckResult,
    InboundEvent,
    ListenerSession,
    ProbeAttempt,
    ProbeResult,
    Protocol,
    Verdict,
)

_VERDICT_STYLE = {
    Verdict.OPEN: "[green]open[/green]",
    Verdict.REFUSED: "[red]refused[/red]",
    Verdict.TIMEOUT: "[yellow]timeout[/yellow]",
    Verdict.CLOSED: "[red]closed[/red]",
    Verdict.INCONCLUSIVE: "[yellow]inconclusive[/yellow]",
    Verdict.RESOLVE_FAILED: "[red]resolve failed[/red]",
    Verdict.AUTH_REQUIRED: "[red]proxy auth required[/red]",
    Verdict.TUNNEL_DENIED: "[red]tunnel denied[/red]",
    Verdict.GATEWAY_FAILED: "[red]unreachable via proxy[/red]",
    Verdict.NOT_A_PROXY: "[red]not an HTTP proxy[/red]",
}


def _target_label(host: str, port: int, protocol: Protocol) -> str:
    suffix = "/udp" if protocol is Protocol.UDP else ""
    return f"{escape(host)}:{port}{suffix}"


def render_check(result: CheckResult, *, console: Console) -> None:
    """Print the open verdict line: address, family, and timing detail.

    A proxied check discloses its route (redacted proxy + provenance) and labels
    the timing as tunnel establishment time; direct output is unchanged.
    """
    target = result.target
    console.print(
        f"{_VERDICT_STYLE[result.verdict]}  "
        f"{_target_label(target.host, target.port, target.protocol)}"
    )
    if result.route.via == "http-proxy":
        console.print(
            f"[dim]via {escape(result.route.proxy or '')} "
            f"({escape(result.route.source)}) — tunnel established through "
            f"{escape(result.address)} ({result.family}) "
            f"in {result.time_ms:.1f} ms[/dim]"
        )
        return
    detail = "reply from" if target.protocol is Protocol.UDP else "connected to"
    console.print(
        f"[dim]{detail} {escape(result.address)} ({result.family}) "
        f"in {result.time_ms:.1f} ms[/dim]"
    )


def render_probe_attempt(
    attempt: ProbeAttempt, target_host: str, *, console: Console
) -> None:
    """Print one per-attempt line as it completes."""
    verdict = _VERDICT_STYLE[attempt.verdict]
    if attempt.time_ms is not None and attempt.address is not None:
        console.print(
            f"[dim]{attempt.index:>3}[/dim]  {verdict}  "
            f"{escape(attempt.address)} ({attempt.family})  "
            f"{attempt.time_ms:.1f} ms"
        )
    else:
        detail = f"  {escape(attempt.error)}" if attempt.error else ""
        console.print(f"[dim]{attempt.index:>3}[/dim]  {verdict}{detail}")


def render_probe_summary(result: ProbeResult, *, console: Console) -> None:
    """Print the probe summary block (counts and min/avg/max statistics).

    A proxied run discloses its route; timings are tunnel establishment times.
    """
    target = result.target
    console.print(
        f"\n[bold]--- {_target_label(target.host, target.port, target.protocol)} "
        f"probe statistics ---[/bold]"
    )
    if result.route.via == "http-proxy":
        console.print(
            f"[dim]via {escape(result.route.proxy or '')} "
            f"({escape(result.route.source)}) — timings are tunnel "
            f"establishment times[/dim]"
        )
    line = (
        f"{result.completed} attempts, {result.successes} succeeded, "
        f"{result.failures} failed"
    )
    if result.completed < result.requested:
        line += f" (interrupted; {result.requested} requested)"
    console.print(line)
    if target.protocol is Protocol.UDP:
        console.print(
            f"replies {result.replies}, closed signals {result.closed_signals}, "
            f"silent {result.silent}"
        )
    if result.min_ms is not None and result.avg_ms is not None:
        console.print(
            f"min/avg/max = {result.min_ms:.1f}/{result.avg_ms:.1f}/"
            f"{result.max_ms:.1f} ms"
        )


def render_listen_banner(session: ListenerSession, *, console: Console) -> None:
    """Print the listening banner with every bound wildcard address."""
    addresses = ", ".join(escape(address) for address in session.bound_addresses)
    console.print(
        f"listening on port [bold]{session.port}[/bold] "
        f"({session.protocol.value}) — bound {addresses}  (Ctrl-C to stop)"
    )


def render_listen_event(event: InboundEvent, *, console: Console) -> None:
    """Print one inbound connection/datagram as peer metadata only (FR-010)."""
    console.print(
        f"[dim]{escape(event.timestamp)}[/dim]  "
        f"[green]#{event.index}[/green]  "
        f"from {escape(event.peer_address)}:{event.peer_port} ({event.family})"
    )


def render_listen_summary(session: ListenerSession, *, console: Console) -> None:
    """Print the end-of-session summary (stop reason and event count)."""
    reason = session.stop_reason.value if session.stop_reason else "stopped"
    console.print(
        f"\n[bold]--- listener summary ---[/bold]\n"
        f"{session.events_received} events received; stopped: {escape(reason)}"
    )
