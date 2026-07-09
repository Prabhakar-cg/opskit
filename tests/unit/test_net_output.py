"""Tests for net rendering, including rich-markup escaping of external strings."""

from __future__ import annotations

import io

from rich.console import Console

from opskit.net.models import (
    CheckResult,
    InboundEvent,
    ListenerSession,
    NetTarget,
    ProbeAttempt,
    ProbeResult,
    Protocol,
    StopReason,
    Verdict,
)
from opskit.net.output import (
    render_check,
    render_listen_banner,
    render_listen_event,
    render_listen_summary,
    render_probe_attempt,
    render_probe_summary,
)


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, no_color=True, width=200), buffer


def _result(host="db.example.com", protocol=Protocol.TCP, address="192.0.2.7"):
    return CheckResult(
        target=NetTarget(host=host, port=5432, protocol=protocol),
        verdict=Verdict.OPEN,
        address=address,
        family="ipv4",
        port=5432,
        time_ms=12.4,
    )


def test_render_check_open_line():
    console, buffer = _console()
    render_check(_result(), console=console)
    output = buffer.getvalue()
    assert "open" in output
    assert "192.0.2.7" in output
    assert "12.4 ms" in output


def test_render_check_udp_wording():
    console, buffer = _console()
    render_check(_result(protocol=Protocol.UDP), console=console)
    output = buffer.getvalue()
    assert "/udp" in output
    assert "reply from" in output


def test_markup_injection_in_hostname_is_escaped():
    console, buffer = _console()
    render_check(_result(host="[bold]evil[/bold]"), console=console)
    output = buffer.getvalue()
    assert "[bold]evil[/bold]" in output  # literal text, not interpreted markup


def test_markup_injection_in_address_is_escaped():
    console, buffer = _console()
    render_check(_result(address="[red]203.0.113.9[/red]"), console=console)
    assert "[red]203.0.113.9[/red]" in buffer.getvalue()


def test_render_probe_attempt_lines():
    console, buffer = _console()
    render_probe_attempt(
        ProbeAttempt(
            index=1,
            verdict=Verdict.OPEN,
            address="192.0.2.7",
            family="ipv4",
            time_ms=18.1,
        ),
        "api.example.com",
        console=console,
    )
    render_probe_attempt(
        ProbeAttempt(index=2, verdict=Verdict.TIMEOUT, error="[bold]slow[/bold]"),
        "api.example.com",
        console=console,
    )
    output = buffer.getvalue()
    assert "18.1 ms" in output
    assert "timeout" in output
    assert "[bold]slow[/bold]" in output  # error detail escaped


def _probe_result(**overrides):
    defaults = {
        "target": NetTarget(host="api.example.com", port=443, protocol=Protocol.TCP),
        "attempts": (),
        "requested": 4,
        "completed": 4,
        "successes": 3,
        "failures": 1,
        "replies": 0,
        "closed_signals": 0,
        "silent": 0,
        "min_ms": 17.2,
        "avg_ms": 19.0,
        "max_ms": 21.3,
        "elapsed_ms": 3095.2,
    }
    defaults.update(overrides)
    return ProbeResult(**defaults)


def test_render_probe_summary():
    console, buffer = _console()
    render_probe_summary(_probe_result(), console=console)
    output = buffer.getvalue()
    assert "4 attempts, 3 succeeded, 1 failed" in output
    assert "min/avg/max = 17.2/19.0/21.3 ms" in output


def test_render_probe_summary_interrupted_and_udp():
    console, buffer = _console()
    render_probe_summary(
        _probe_result(
            target=NetTarget(host="dns.example.com", port=53, protocol=Protocol.UDP),
            completed=2,
            successes=1,
            failures=1,
            replies=1,
            closed_signals=0,
            silent=1,
            min_ms=None,
            avg_ms=None,
            max_ms=None,
        ),
        console=console,
    )
    output = buffer.getvalue()
    assert "interrupted; 4 requested" in output
    assert "replies 1, closed signals 0, silent 1" in output
    assert "min/avg/max" not in output  # nothing answered


def _session(**overrides):
    defaults = {
        "protocol": Protocol.TCP,
        "port": 8080,
        "bound_addresses": ("127.0.0.1", "::1"),
        "started_at": "2026-07-09T10:00:00.000Z",
        "stopped_at": "2026-07-09T10:05:00.000Z",
        "stop_reason": StopReason.INTERRUPT,
        "events_received": 2,
        "max_duration_s": None,
        "max_events": None,
    }
    defaults.update(overrides)
    return ListenerSession(**defaults)


def test_render_listen_banner_and_summary():
    console, buffer = _console()
    render_listen_banner(_session(), console=console)
    render_listen_summary(_session(), console=console)
    output = buffer.getvalue()
    assert "listening on port 8080" in output
    assert "127.0.0.1" in output
    assert "2 events received; stopped: interrupt" in output


def test_render_listen_event_escapes_peer():
    console, buffer = _console()
    render_listen_event(
        InboundEvent(
            index=1,
            peer_address="[green]198.51.100.23[/green]",
            peer_port=52114,
            family="ipv4",
            timestamp="2026-07-09T10:15:02.114Z",
        ),
        console=console,
    )
    output = buffer.getvalue()
    assert "[green]198.51.100.23[/green]" in output
    assert ":52114" in output
