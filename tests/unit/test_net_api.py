"""Tests for check()/probe() orchestration: raise/return split, stats, streaming."""

from __future__ import annotations

import socket
import threading
import types

import pytest

from opskit.core.errors import UsageError
from opskit.net import Listener, api, check, probe
from opskit.net.errors import (
    ConnectRefused,
    ConnectTimeout,
    NetError,
    ResolutionError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.models import CheckResult, Protocol, Verdict, parse_target
from opskit.net.tcp import TcpConnection
from opskit.net.udp import UdpReply


class _FakeSock:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _fake_tcp(monkeypatch, connect=None, resolve=None):
    """Replace api's tcp seam with an injected namespace."""
    namespace = types.SimpleNamespace(
        connect=connect or (lambda *a, **k: pytest.fail("connect not expected")),
        resolve=resolve or (lambda *a, **k: ["candidate"]),
    )
    monkeypatch.setattr(api, "tcp", namespace)
    return namespace


def test_check_open_returns_result_and_closes_socket(monkeypatch):
    sock = _FakeSock()
    seen = {}

    def _connect(host, port, *, timeout, retries, family):
        seen.update(host=host, port=port, family=family)
        return sock, TcpConnection(
            address="192.0.2.7", family="ipv4", port=port, connect_ms=12.5
        )

    _fake_tcp(monkeypatch, connect=_connect)
    result = check("db.example.com:5432", family="ipv4")
    assert result.verdict is Verdict.OPEN
    assert result.address == "192.0.2.7"
    assert result.family == "ipv4"
    assert result.port == 5432
    assert result.time_ms == 12.5
    assert sock.closed  # verdict only — no application data (FR-006)
    assert seen == {"host": "db.example.com", "port": 5432, "family": "ipv4"}


@pytest.mark.parametrize(
    "error",
    [
        ConnectRefused("refused", hint="h"),
        ConnectTimeout("timed out", hint="h"),
        ResolutionError("no such host", hint="h"),
    ],
)
def test_check_non_open_outcomes_raise(monkeypatch, error):
    def _connect(*a, **k):
        raise error

    _fake_tcp(monkeypatch, connect=_connect)
    with pytest.raises(type(error)):
        check("db.example.com:5432")


def test_check_missing_port_is_usage_error_before_any_io(monkeypatch):
    _fake_tcp(monkeypatch)  # connect would fail the test if reached
    with pytest.raises(UsageError):
        check("db.example.com")


def test_check_udp_dispatch(monkeypatch):
    def _udp_probe(host, port, *, timeout, retries, family):
        return UdpReply(address="192.0.2.9", family="ipv4", port=port, time_ms=3.3)

    monkeypatch.setattr(api, "udp_probe", _udp_probe)
    result = check("ntp.example.com:123", protocol=Protocol.UDP)
    assert result.verdict is Verdict.OPEN
    assert result.address == "192.0.2.9"
    assert result.target.protocol is Protocol.UDP


def test_check_udp_closed_propagates(monkeypatch):
    def _udp_probe(*a, **k):
        raise UdpClosed("closed", hint="h")

    monkeypatch.setattr(api, "udp_probe", _udp_probe)
    with pytest.raises(UdpClosed):
        check("ntp.example.com:123", protocol=Protocol.UDP)


def _probe_with_outcomes(monkeypatch, outcomes, **kwargs):
    """Run probe() with _check_parsed yielding the given results/exceptions in order."""
    _fake_tcp(monkeypatch)  # pre-flight resolve succeeds
    calls = iter(outcomes)

    def _check_parsed(parsed, *, timeout, retries):
        outcome = next(calls)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(api, "_check_parsed", _check_parsed)
    return probe("svc.example:7000", interval=0.0, **kwargs)


def _open_result(time_ms, port=7000, protocol=Protocol.TCP):
    return CheckResult(
        target=parse_target(f"svc.example:{port}", protocol=protocol),
        verdict=Verdict.OPEN,
        address="192.0.2.7",
        family="ipv4",
        port=port,
        time_ms=time_ms,
    )


def test_probe_statistics_over_answered_attempts(monkeypatch):
    result = _probe_with_outcomes(
        monkeypatch,
        [_open_result(10.0), _open_result(20.0), ConnectRefused("refused")],
        count=3,
    )
    assert result.requested == 3
    assert result.completed == 3
    assert result.successes == 2
    assert result.failures == 1
    assert (result.min_ms, result.avg_ms, result.max_ms) == (10.0, 15.0, 20.0)
    assert (result.replies, result.closed_signals, result.silent) == (0, 0, 0)
    assert result.attempts[2].verdict is Verdict.REFUSED
    assert result.attempts[2].error == "refused"
    assert result.elapsed_ms >= 0


def test_probe_stats_none_when_nothing_answered(monkeypatch):
    result = _probe_with_outcomes(
        monkeypatch,
        [ConnectTimeout("t"), ConnectTimeout("t")],
        count=2,
    )
    assert result.successes == 0
    assert result.min_ms is None and result.avg_ms is None and result.max_ms is None


def test_probe_udp_breakdown(monkeypatch):
    result = _probe_with_outcomes(
        monkeypatch,
        [
            _open_result(5.0, port=123, protocol=Protocol.UDP),
            UdpClosed("closed"),
            UdpInconclusive("silent"),
        ],
        count=3,
        protocol=Protocol.UDP,
    )
    assert (result.replies, result.closed_signals, result.silent) == (1, 1, 1)


def test_probe_interrupt_finalizes_completed_attempts(monkeypatch):
    result = _probe_with_outcomes(
        monkeypatch,
        [_open_result(10.0), _open_result(12.0), KeyboardInterrupt()],
        count=5,
    )
    assert result.requested == 5
    assert result.completed == 2
    assert result.successes == 2


def test_probe_streams_attempts_via_hook(monkeypatch):
    streamed = []
    result = _probe_with_outcomes(
        monkeypatch,
        [_open_result(10.0), ConnectRefused("refused")],
        count=2,
        on_attempt=streamed.append,
    )
    assert [a.index for a in streamed] == [1, 2]
    assert streamed == list(result.attempts)


def test_probe_interrupt_from_hook_finalizes(monkeypatch):
    def _interrupt(attempt):
        raise KeyboardInterrupt

    result = _probe_with_outcomes(
        monkeypatch,
        [_open_result(10.0), _open_result(11.0)],
        count=2,
        on_attempt=_interrupt,
    )
    assert result.completed == 1


def test_probe_preflight_validation(monkeypatch):
    _fake_tcp(monkeypatch)
    with pytest.raises(UsageError):
        probe("svc.example:7000", count=0)
    with pytest.raises(UsageError):
        probe("svc.example:7000", interval=-1.0)
    with pytest.raises(UsageError):
        probe("svc.example")  # no port


def test_probe_preflight_resolution_failure(monkeypatch):
    def _resolve(*a, **k):
        raise ResolutionError("no such host")

    _fake_tcp(monkeypatch, resolve=_resolve)
    with pytest.raises(ResolutionError):
        probe("gone.example:7000")


def test_verdict_for_generic_net_error_defaults_to_timeout():
    assert api.verdict_for(NetError("odd")) is Verdict.TIMEOUT


def test_python_api_usage_example_runs_silently(capsys):
    """The contracts/python-api.md flow works against loopback; the library never prints."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]

    def _accept_loop():
        for _ in range(4):
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                return

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        result = check(f"127.0.0.1:{port}")
        assert result.verdict.value == "open"

        stats = probe(f"127.0.0.1:{port}", count=2, interval=0.0)
        assert stats.successes == 2
        assert stats.min_ms is not None
    finally:
        server.close()
        thread.join(timeout=2)

    with Listener(_free_port(), protocol=Protocol.TCP, max_events=1) as listener:
        peer_port = listener.session.port

        def _poke():
            client = socket.create_connection(("127.0.0.1", peer_port), timeout=2)
            client.close()

        poker = threading.Thread(target=_poke, daemon=True)
        poker.start()
        events = list(listener.events())
        poker.join(timeout=2)
    assert len(events) == 1
    assert listener.session.stop_reason.value == "max_events"
    assert listener.session.events_received == 1

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""  # Art. VII: API never prints


def _free_port() -> int:
    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_sock.bind(("127.0.0.1", 0))
    port = int(probe_sock.getsockname()[1])
    probe_sock.close()
    return port
