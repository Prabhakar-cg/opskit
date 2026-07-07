"""Tests for the reusable TCP resolve/connect primitive."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from opskit.net import (
    ConnectRefused,
    ConnectTimeout,
    ResolutionError,
    connect,
    resolve,
    tcp,
)


def _loopback_listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server, server.getsockname()[1]


def test_connect_success_reports_address_and_timing():
    server, port = _loopback_listener()
    accepted = []

    def _accept():
        conn, _ = server.accept()
        accepted.append(conn)

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    sock, info = connect("127.0.0.1", port, timeout=2.0)
    thread.join(timeout=2)
    assert info.address == "127.0.0.1"
    assert info.family == "ipv4"
    assert info.port == port
    assert info.connect_ms >= 0
    sock.close()
    for conn in accepted:
        conn.close()
    server.close()


def test_connect_refused_on_closed_port():
    server, port = _loopback_listener()
    server.close()  # port now closed -> RST on connect
    with pytest.raises(ConnectRefused) as excinfo:
        connect("127.0.0.1", port, timeout=2.0)
    assert excinfo.value.hint


def test_connect_timeout_after_retries(monkeypatch):
    attempts = []

    def _timeout_connect(self, addr):
        attempts.append(addr)
        raise socket.timeout("timed out")

    monkeypatch.setattr(socket.socket, "connect", _timeout_connect)
    with pytest.raises(ConnectTimeout):
        connect("127.0.0.1", 65000, timeout=0.01, retries=2)
    assert len(attempts) >= 3  # initial + 2 retries (per candidate)


def test_resolution_error():
    with pytest.raises(ResolutionError) as excinfo:
        resolve("no-such-host.invalid", 443)
    assert "opskit dns lookup" in (excinfo.value.hint or "")


def test_resolve_orders_candidates():
    candidates = resolve("localhost", 443)
    assert candidates, "localhost must resolve"
    assert {c.family for c in candidates} <= {"ipv4", "ipv6"}
    assert all(c.sockaddr[1] == 443 for c in candidates)


def test_connect_unresolvable_raises_resolution_error():
    with pytest.raises(ResolutionError):
        connect("no-such-host.invalid", 443, timeout=0.5)


def test_resolve_enforces_timeout(monkeypatch):
    def _slow(*args, **kwargs):
        time.sleep(2)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", _slow)
    with pytest.raises(ResolutionError) as excinfo:
        resolve("slow.example", 443, timeout=0.1)
    assert "timed out" in str(excinfo.value.message)


def test_refusal_wins_over_timeout_for_dual_stack(monkeypatch):
    # First candidate refuses (definitive), second would time out: report refused.
    monkeypatch.setattr(
        tcp,
        "resolve",
        lambda host, port, timeout=5.0: [
            tcp.AddressCandidate(
                "192.0.2.1", "ipv4", ("192.0.2.1", port), socket.AF_INET
            ),
            tcp.AddressCandidate("::1", "ipv6", ("::1", port, 0, 0), socket.AF_INET6),
        ],
    )
    calls = []

    def _connect(self, addr):
        calls.append(addr)
        if addr[0] == "192.0.2.1":
            raise ConnectionRefusedError(111, "refused")
        raise socket.timeout("slow")

    monkeypatch.setattr(socket.socket, "connect", _connect)
    with pytest.raises(ConnectRefused):
        connect("dual.example", 443, timeout=0.05, retries=1)
