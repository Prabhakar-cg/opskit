"""Tests for the reusable TCP resolve/connect primitive."""

from __future__ import annotations

import socket
import threading

import pytest

from opskit.net import (
    ConnectRefused,
    ConnectTimeout,
    ResolutionError,
    connect,
    resolve,
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
