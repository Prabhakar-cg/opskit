"""Tests for the reusable TCP resolve/connect primitive."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from opskit.core.errors import UsageError
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


def _closed_port() -> int:
    """A loopback port that is closed (bound then released, never listened)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_connect_refused_on_closed_port():
    # A closed loopback port refuses on Linux/macOS. Windows refuses a never-listened port
    # (this pattern) but may *time out* on a port that was listen()ed then closed — so accept
    # either "cannot connect" NetError. The precise refused mapping is asserted deterministically
    # in test_refusal_wins_over_timeout_for_dual_stack.
    with pytest.raises((ConnectRefused, ConnectTimeout)) as excinfo:
        connect("127.0.0.1", _closed_port(), timeout=1.0, retries=0)
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
        lambda host, port, timeout=5.0, family=None: [
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


def test_resolve_family_restriction_ipv4():
    candidates = resolve("localhost", 443, family="ipv4")
    assert candidates
    assert all(c.family == "ipv4" for c in candidates)


def test_resolve_family_restriction_ipv6():
    if not socket.has_ipv6:
        pytest.skip("no IPv6 stack")
    try:
        candidates = resolve("localhost", 443, family="ipv6")
    except ResolutionError:
        pytest.skip("localhost has no IPv6 address on this host")
    assert all(c.family == "ipv6" for c in candidates)


def test_resolve_empty_family_raises_resolution_error(monkeypatch):
    # getaddrinfo raising for the restricted family -> ResolutionError naming it (R1).
    def _no_af(*args, **kwargs):
        raise socket.gaierror("Address family for hostname not supported")

    monkeypatch.setattr(socket, "getaddrinfo", _no_af)
    with pytest.raises(ResolutionError) as excinfo:
        resolve("v4only.example", 443, family="ipv6")
    assert "ipv6" in excinfo.value.message
    assert "family" in (excinfo.value.hint or "")


def test_resolve_empty_candidate_list_names_family(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(ResolutionError) as excinfo:
        resolve("empty.example", 443, family="ipv6")
    assert "no ipv6 address" in excinfo.value.message


def test_resolve_unknown_family_is_usage_error():
    with pytest.raises(UsageError):
        resolve("localhost", 443, family="ipv5")


def test_connect_passes_family_through(monkeypatch):
    seen = {}

    def _resolve(host, port, *, timeout=5.0, family=None):
        seen["family"] = family
        raise ResolutionError("stop here")

    monkeypatch.setattr(tcp, "resolve", _resolve)
    with pytest.raises(ResolutionError):
        connect("example.com", 443, family="ipv4")
    assert seen["family"] == "ipv4"
