"""Tests for the honest UDP probe with injected/mocked sockets (every outcome class)."""

from __future__ import annotations

import errno
import socket

import pytest

from opskit.net import udp
from opskit.net.errors import (
    ConnectRefused,
    NetError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.tcp import AddressCandidate

_CANDIDATE = AddressCandidate("127.0.0.1", "ipv4", ("127.0.0.1", 5300), socket.AF_INET)


class _FakeUdpSocket:
    """A connected-UDP socket double whose recv behavior is injected."""

    def __init__(self, recv_behavior, sent: list) -> None:
        self._recv = recv_behavior
        self._sent = sent
        self.closed = False

    def settimeout(self, value) -> None:
        self.timeout = value

    def connect(self, addr) -> None:
        self.addr = addr

    def send(self, data) -> int:
        self._sent.append(data)
        return len(data)

    def recv(self, size):
        return self._recv()

    def close(self) -> None:
        self.closed = True


def _patch(monkeypatch, recv_behavior) -> list:
    """Route udp_probe's resolution and socket creation through fakes."""
    sent: list = []
    monkeypatch.setattr(udp, "resolve", lambda *a, **k: [_CANDIDATE])
    monkeypatch.setattr(
        udp.socket, "socket", lambda af, kind: _FakeUdpSocket(recv_behavior, sent)
    )
    return sent


def test_reply_means_open_and_probe_is_zero_bytes(monkeypatch):
    sent = _patch(monkeypatch, lambda: b"pong")
    reply = udp.udp_probe("ntp.example", 5300, timeout=0.5, retries=2)
    assert reply.address == "127.0.0.1"
    assert reply.family == "ipv4"
    assert reply.port == 5300
    assert reply.time_ms >= 0
    assert sent == [b""]  # exactly one zero-byte probe datagram (FR-018)
    assert reply.to_dict()["address"] == "127.0.0.1"


def test_econnrefused_is_udp_closed(monkeypatch):
    def _refused():
        raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")

    _patch(monkeypatch, _refused)
    with pytest.raises(UdpClosed) as excinfo:
        udp.udp_probe("ntp.example", 5300, timeout=0.5)
    assert "port unreachable" in excinfo.value.message
    assert excinfo.value.exit_code == 8


def test_windows_style_reset_on_recv_is_udp_closed(monkeypatch):
    def _reset():
        raise ConnectionResetError(10054, "WSAECONNRESET")

    _patch(monkeypatch, _reset)
    with pytest.raises(UdpClosed):
        udp.udp_probe("ntp.example", 5300, timeout=0.5)


def test_silence_is_inconclusive_after_exactly_retries_plus_one_sends(monkeypatch):
    def _silent():
        raise socket.timeout("timed out")

    sent = _patch(monkeypatch, _silent)
    with pytest.raises(UdpInconclusive) as excinfo:
        udp.udp_probe("vpn.example", 5300, timeout=0.01, retries=2)
    assert len(sent) == 3  # initial + 2 re-sends; every probe zero bytes
    assert all(datagram == b"" for datagram in sent)
    assert "open or filtered (inconclusive)" in excinfo.value.message
    assert "net listen" in (excinfo.value.hint or "")
    assert excinfo.value.exit_code == 6


def test_unreachable_errno_normalizes_to_connect_refused(monkeypatch):
    def _unreachable():
        raise OSError(errno.ENETUNREACH, "network unreachable")

    _patch(monkeypatch, _unreachable)
    with pytest.raises(ConnectRefused):
        udp.udp_probe("far.example", 5300, timeout=0.5)


def test_other_oserror_normalizes_to_net_error(monkeypatch):
    def _invalid():
        raise OSError(errno.EINVAL, "invalid argument")

    _patch(monkeypatch, _invalid)
    with pytest.raises(NetError) as excinfo:
        udp.udp_probe("odd.example", 5300, timeout=0.5)
    assert not isinstance(excinfo.value, (UdpClosed, UdpInconclusive))
    assert excinfo.value.hint  # raw OSError never escapes (Art. VI)
