"""Tests for the temporary listener: bind failures, stop conditions, metadata-only events."""

from __future__ import annotations

import errno
import selectors
import socket
import threading
import time

import pytest

from opskit.core.errors import UsageError
from opskit.net.errors import BindPermissionDenied, PortInUse
from opskit.net.listener import Listener
from opskit.net.models import Protocol, StopReason


def _free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_busy_port_raises_port_in_use():
    # Loopback-bound is enough: the Listener's wildcard IPv4 bind on the same
    # port still collides (and CodeQL flags wildcard binds in tests).
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = int(holder.getsockname()[1])
    try:
        with pytest.raises(PortInUse) as excinfo, Listener(port):
            pass
    finally:
        holder.close()
    assert excinfo.value.exit_code == 12
    assert excinfo.value.hint


@pytest.mark.parametrize("code", [errno.EACCES, 10013])
def test_bind_permission_denied(monkeypatch, code):
    port = _free_port()  # before the patch: the helper binds too

    def _deny(self, addr):
        raise OSError(code, "permission denied")

    monkeypatch.setattr(socket.socket, "bind", _deny)
    with pytest.raises(BindPermissionDenied) as excinfo, Listener(port):
        pass
    assert excinfo.value.exit_code == 13
    assert "unprivileged" in (excinfo.value.hint or "")


def test_winsock_eaddrinuse_code_maps_to_port_in_use(monkeypatch):
    port = _free_port()  # before the patch: the helper binds too

    def _busy(self, addr):
        raise OSError(10048, "address already in use")

    monkeypatch.setattr(socket.socket, "bind", _busy)
    with pytest.raises(PortInUse), Listener(port):
        pass


def test_invalid_configuration_is_usage_error():
    with pytest.raises(UsageError):
        Listener(0)
    with pytest.raises(UsageError):
        Listener(70000)
    with pytest.raises(UsageError):
        Listener(8080, max_duration=0)
    with pytest.raises(UsageError):
        Listener(8080, max_events=0)


def test_max_events_stop_and_event_metadata():
    port = _free_port()
    with Listener(port, max_events=1) as listener:
        assert listener.session.bound_addresses  # bound immediately after entry

        def _poke():
            client = socket.create_connection(("127.0.0.1", port), timeout=2)
            client.sendall(b"super-secret-payload")
            client.close()

        poker = threading.Thread(target=_poke, daemon=True)
        poker.start()
        events = list(listener.events())
        poker.join(timeout=2)
    assert len(events) == 1
    event = events[0]
    assert event.index == 1
    assert event.peer_address == "127.0.0.1"
    assert event.peer_port > 0
    assert event.family == "ipv4"
    assert event.timestamp.endswith("Z")
    session = listener.session
    assert session.stop_reason is StopReason.MAX_EVENTS
    assert session.events_received == 1
    assert session.stopped_at is not None
    # FR-010: payload bytes never surface anywhere in the model.
    assert "super-secret-payload" not in repr(events) + repr(session)


def test_max_duration_expiry_with_zero_events():
    with Listener(_free_port(), max_duration=0.3) as listener:
        start = time.monotonic()
        events = list(listener.events())
        elapsed = time.monotonic() - start
    assert events == []
    assert 0.2 <= elapsed < 5.0
    assert listener.session.stop_reason is StopReason.MAX_DURATION
    assert listener.session.events_received == 0


def test_udp_datagram_event_metadata_only():
    port = _free_port()
    with Listener(port, protocol=Protocol.UDP, max_events=1) as listener:

        def _send():
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.sendto(b"top-secret-datagram", ("127.0.0.1", port))
            client.close()

        sender = threading.Thread(target=_send, daemon=True)
        sender.start()
        events = list(listener.events())
        sender.join(timeout=2)
    assert len(events) == 1
    assert events[0].peer_address == "127.0.0.1"
    assert "top-secret-datagram" not in repr(events)
    assert listener.session.protocol is Protocol.UDP


def test_keyboard_interrupt_finalizes_then_propagates(monkeypatch):
    def _interrupt(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(selectors.DefaultSelector, "select", _interrupt)
    with Listener(_free_port()) as listener:
        with pytest.raises(KeyboardInterrupt):
            list(listener.events())
        assert listener.session.stop_reason is StopReason.INTERRUPT
        assert listener.session.stopped_at is not None


def test_early_exit_without_events_finalizes_as_interrupt():
    with Listener(_free_port()) as listener:
        pass  # never iterated events()
    assert listener.session.stop_reason is StopReason.INTERRUPT


def test_session_to_dict_shape():
    with Listener(_free_port(), max_events=5, max_duration=60.0) as listener:
        payload = listener.session.to_dict()
    assert payload["max_events"] == 5
    assert payload["max_duration_s"] == 60.0
    assert payload["events_received"] == 0
    assert isinstance(payload["bound_addresses"], list)
