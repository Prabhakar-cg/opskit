"""Loopback integration for the net category — real sockets, no external network.

Cross-OS variance is tolerated by design (research R6, CLAUDE.md): a closed loopback
TCP port refuses on Linux/macOS but may time out on Windows, so those asserts accept
the NetError class family; UDP closed-vs-inconclusive depends on ICMP delivery, so
{UdpClosed, UdpInconclusive} are both accepted where noted.
"""

from __future__ import annotations

import json
import selectors
import socket
import threading
import time

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.net import check, probe
from opskit.net.errors import (
    ConnectRefused,
    ConnectTimeout,
    NetError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.models import Protocol, StopReason, Verdict

runner = CliRunner()


# --- US1: TCP check ---


def test_tcp_open_verdict_with_plausible_timing(tcp_listener):
    result = check(f"127.0.0.1:{tcp_listener.port}", timeout=2.0)
    assert result.verdict is Verdict.OPEN
    assert result.address == "127.0.0.1"
    assert result.family == "ipv4"
    assert 0 <= result.time_ms < 5000


def test_tcp_open_via_cli_exit_zero(tcp_listener):
    result = runner.invoke(
        app, ["net", "check", f"127.0.0.1:{tcp_listener.port}", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["verdict"] == "open"
    assert payload["result"]["address"] == "127.0.0.1"


def test_tcp_closed_port_is_net_error_class_family(closed_port):
    # Refused on Linux/macOS; may be timeout on Windows — assert the family (R6).
    with pytest.raises((ConnectRefused, ConnectTimeout)) as excinfo:
        check(f"127.0.0.1:{closed_port}", timeout=1.0, retries=0)
    assert isinstance(excinfo.value, NetError)
    assert excinfo.value.hint


def test_tcp_closed_port_via_cli_exit_class(closed_port):
    result = runner.invoke(
        app,
        [
            "net",
            "check",
            f"127.0.0.1:{closed_port}",
            "--timeout",
            "1",
            "--retries",
            "0",
        ],
    )
    assert result.exit_code in (6, 8)  # timeout class or refused class (platform)


def test_trailing_dot_hostname_accepted(tcp_listener):
    result = check(f"localhost.:{tcp_listener.port}", timeout=2.0)
    assert result.verdict is Verdict.OPEN


def test_accept_then_immediate_close_still_open(tcp_listener):
    # The fixture's accept loop closes every connection instantly; still "open".
    result = check(f"127.0.0.1:{tcp_listener.port}", timeout=2.0)
    assert result.verdict is Verdict.OPEN


# --- US2: UDP check ---


def test_udp_echo_reply_is_open_with_timing(udp_echo):
    result = check(f"127.0.0.1:{udp_echo.port}", protocol=Protocol.UDP, timeout=2.0)
    assert result.verdict is Verdict.OPEN
    assert result.address == "127.0.0.1"
    assert 0 <= result.time_ms < 5000


def test_udp_open_via_cli_exit_zero(udp_echo):
    result = runner.invoke(
        app, ["net", "check", f"127.0.0.1:{udp_echo.port}", "--udp", "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["result"]["verdict"] == "open"


def test_udp_closed_port_is_closed_or_inconclusive(udp_closed_port):
    # ICMP port-unreachable delivery is platform-dependent (R6): accept both.
    with pytest.raises((UdpClosed, UdpInconclusive)):
        check(
            f"127.0.0.1:{udp_closed_port}",
            protocol=Protocol.UDP,
            timeout=0.5,
            retries=0,
        )


def test_udp_cli_envelope_never_claims_open_on_silence(udp_closed_port):
    result = runner.invoke(
        app,
        [
            "net",
            "check",
            f"127.0.0.1:{udp_closed_port}",
            "--udp",
            "--timeout",
            "0.5",
            "--retries",
            "0",
            "--json",
        ],
    )
    assert result.exit_code in (6, 8)  # inconclusive or closed — never 0
    payload = json.loads(result.stdout)
    assert payload["result"] is None
    assert payload["error"]["code"] in ("udp_inconclusive", "udp_closed")
    if payload["error"]["code"] == "udp_inconclusive":
        assert "open or filtered (inconclusive)" in payload["error"]["message"]


# --- US3: probe ---


def test_probe_ten_attempts_against_live_listener(tcp_listener):
    result = probe(
        f"127.0.0.1:{tcp_listener.port}", count=10, interval=0.0, timeout=2.0
    )
    assert result.requested == 10
    assert result.completed == 10
    assert result.successes == 10
    assert result.min_ms is not None and result.max_ms is not None
    assert result.min_ms <= result.avg_ms <= result.max_ms


def test_probe_survives_listener_stopping_mid_run(tcp_listener):
    stopped = threading.Event()

    def _stop_after_third(attempt):
        if attempt.index == 3 and not stopped.is_set():
            tcp_listener.close()
            stopped.set()

    result = probe(
        f"127.0.0.1:{tcp_listener.port}",
        count=6,
        interval=0.0,
        timeout=0.5,
        on_attempt=_stop_after_third,
    )
    assert result.completed == 6  # the run completes despite mid-run failures
    assert result.successes >= 3
    assert result.failures >= 1  # post-stop attempts fail but are counted


# --- US5: listener <-> check pairing ---


def test_listener_check_pairing_tcp(entered_listener):
    outcomes = {}

    with entered_listener(protocol=Protocol.TCP, max_events=1) as listener:
        port = listener.session.port

        def _probe_side():
            outcomes["result"] = check(f"127.0.0.1:{port}", timeout=2.0)

        prober = threading.Thread(target=_probe_side, daemon=True)
        prober.start()
        events = list(listener.events())
        prober.join(timeout=3)

    assert outcomes["result"].verdict is Verdict.OPEN
    assert len(events) == 1
    assert events[0].peer_address == "127.0.0.1"
    assert events[0].peer_port > 0
    assert events[0].family == "ipv4"
    session = listener.session
    assert session.stop_reason is StopReason.MAX_EVENTS
    assert session.events_received == 1


def test_listener_check_pairing_udp(entered_listener):
    outcomes = {}

    with entered_listener(protocol=Protocol.UDP, max_events=1) as listener:
        port = listener.session.port

        def _probe_side():
            # The listener never replies, so the honest verdict is inconclusive —
            # but the datagram must still arrive and be reported as metadata.
            try:
                check(
                    f"127.0.0.1:{port}",
                    protocol=Protocol.UDP,
                    timeout=0.5,
                    retries=0,
                )
            except (UdpClosed, UdpInconclusive) as exc:
                outcomes["error"] = exc

        prober = threading.Thread(target=_probe_side, daemon=True)
        prober.start()
        events = list(listener.events())
        prober.join(timeout=3)

    assert len(events) == 1
    assert events[0].peer_address == "127.0.0.1"
    assert isinstance(outcomes["error"], UdpInconclusive)  # silence, never "open"
    assert listener.session.events_received == 1


def _alloc_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def _invoke_listen(extra_args, *, before=None, attempts=5):
    """Invoke `net listen` on a fresh port, retrying past Windows excluded
    port ranges (a wildcard bind there exits 12/13 through no fault of ours)."""
    for _ in range(attempts):
        port = _alloc_port()
        thread = before(port) if before is not None else None
        result = runner.invoke(app, ["net", "listen", str(port), *extra_args])
        if thread is not None:
            thread.join(timeout=3)
        if result.exit_code not in (12, 13):
            return result
    pytest.skip("no bindable listener port on this runner")


def test_listener_zero_event_expiry_exits_6():
    result = _invoke_listen(["--max-duration", "300ms"])
    assert result.exit_code == 6


def test_listener_max_events_via_cli_exits_0():
    def _start_poker(port):
        def _poke():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=0.3)
                    client.close()
                    return
                except OSError:
                    time.sleep(0.05)  # listener may not have bound yet

        poker = threading.Thread(target=_poke, daemon=True)
        poker.start()
        return poker

    result = _invoke_listen(["--max-events", "1", "--jsonl"], before=_start_poker)
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert lines[-1]["result"]["kind"] == "session"
    assert lines[-1]["result"]["stop_reason"] == "max_events"
    assert lines[-1]["result"]["events_received"] == 1


def test_listener_interrupt_stops_cleanly_with_summary(monkeypatch):
    original_select = selectors.DefaultSelector.select
    calls = {"n": 0}

    def _interrupt_second(self, timeout=None):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
        return original_select(self, timeout)

    monkeypatch.setattr(selectors.DefaultSelector, "select", _interrupt_second)
    result = _invoke_listen(["--no-color"])
    assert result.exit_code == 0  # Ctrl-C is a clean stop (R4)
    assert "listener summary" in result.stdout
    assert "stopped: interrupt" in result.stdout
