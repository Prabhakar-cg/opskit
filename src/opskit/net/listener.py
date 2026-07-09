"""Temporary diagnostic listener (research R4) — metadata-only inbound reporting.

A single-threaded poll loop over non-blocking wildcard sockets (one per available
family), multiplexed with :mod:`selectors` at a short timeout so ``Ctrl-C`` interrupts
promptly on every platform (a blocking ``accept()`` cannot be interrupted on Windows).
TCP connections are closed unread; UDP datagram bytes are discarded at receive time —
payloads are never read into the model, rendered, or stored (FR-010). The listener
never sends anything. Nothing here prints or exits (Art. VII).
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import selectors
import socket
import time
from collections.abc import Iterator
from typing import cast

from opskit.core.errors import UsageError
from opskit.net.errors import BindPermissionDenied, NetError, PortInUse
from opskit.net.models import InboundEvent, ListenerSession, Protocol, StopReason

_POLL_TIMEOUT_S = 0.25
_MAX_PORT = 65535

# POSIX errno values plus the raw winsock codes — Windows socket OSErrors carry the
# WSA number (WSAEADDRINUSE 10048, WSAEACCES 10013), not the MSVC errno mapping.
_IN_USE_ERRNOS = frozenset({errno.EADDRINUSE, 10048})
_PERMISSION_ERRNOS = frozenset({errno.EACCES, errno.EPERM, 10013})


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _family_name(af: int) -> str:
    return "ipv6" if af == socket.AF_INET6 else "ipv4"


class Listener:
    """A temporary wildcard listener reporting inbound activity as metadata only.

    Use as a context manager: ``__enter__`` binds (raising :class:`PortInUse` or
    :class:`BindPermissionDenied` on failure), :meth:`events` yields
    :class:`InboundEvent` items until a stop condition fires, and :attr:`session`
    holds the summary (bound addresses immediately after entry; stop reason and
    counters final once :meth:`events` ends).

    Example:
        >>> with Listener(8080, max_events=1) as listener:      # doctest: +SKIP
        ...     for event in listener.events():
        ...         print(event.peer_address, event.peer_port)
    """

    def __init__(
        self,
        port: int,
        *,
        protocol: Protocol = Protocol.TCP,
        max_duration: float | None = None,
        max_events: int | None = None,
    ) -> None:
        """Validate the configuration; sockets are not bound until ``__enter__``.

        Args:
            port: The port to bind on the wildcard address of every available family.
            protocol: Accept TCP connections (default) or receive UDP datagrams.
            max_duration: Stop after this many seconds, if set.
            max_events: Stop after this many connections/datagrams, if set.

        Raises:
            UsageError: For an out-of-range port or non-positive stop condition.
        """
        if not 1 <= port <= _MAX_PORT:
            raise UsageError(f"port must be between 1 and {_MAX_PORT}: {port}")
        if max_duration is not None and max_duration <= 0:
            raise UsageError("--max-duration must be positive")
        if max_events is not None and max_events < 1:
            raise UsageError("--max-events must be at least 1")
        self._port = port
        self._protocol = protocol
        self._max_duration = max_duration
        self._max_events = max_events
        self._sockets: list[socket.socket] = []
        self._bound_addresses: tuple[str, ...] = ()
        self._started_at: str | None = None
        self._started_monotonic = 0.0
        self._stopped_at: str | None = None
        self._stop_reason: StopReason | None = None
        self._events_received = 0

    def __enter__(self) -> Listener:
        """Bind the wildcard address on every available family and start the session.

        Raises:
            PortInUse: When the port is already bound by another process.
            BindPermissionDenied: When the OS denies the bind (privileged port).
            NetError: For any other bind failure (normalized, Art. VI).
        """
        socktype = (
            socket.SOCK_STREAM if self._protocol is Protocol.TCP else socket.SOCK_DGRAM
        )
        bound: list[str] = []
        bind_errors: list[OSError] = []
        for af, wildcard in ((socket.AF_INET, ""), (socket.AF_INET6, "::")):
            if af == socket.AF_INET6 and not socket.has_ipv6:
                continue
            try:
                sock = socket.socket(af, socktype)
            except OSError:
                continue  # family not available on this host — tolerated
            try:
                if af == socket.AF_INET6:
                    # Both wildcard sockets must coexist: with V6ONLY off (the Linux
                    # default) the v6 bind also claims v4 and EADDRINUSEs against the
                    # v4 socket. v4 traffic has its own socket, so scope this one to v6.
                    with contextlib.suppress(OSError, AttributeError):
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((wildcard, self._port))
                if socktype == socket.SOCK_STREAM:
                    sock.listen(16)
                sock.setblocking(False)
            except OSError as exc:
                sock.close()
                bind_errors.append(exc)
                # A busy port or permission denial is the user's answer regardless of
                # what the other family would do; only an absent/unusable stack is a
                # tolerable single-family failure (research R4).
                if exc.errno in _IN_USE_ERRNOS or exc.errno in _PERMISSION_ERRNOS:
                    break
                continue
            self._sockets.append(sock)
            bound.append(sock.getsockname()[0])
        if (
            any(
                exc.errno in _IN_USE_ERRNOS or exc.errno in _PERMISSION_ERRNOS
                for exc in bind_errors
            )
            or not self._sockets
        ):
            for sock in self._sockets:
                with contextlib.suppress(OSError):
                    sock.close()
            self._sockets = []
            self._raise_bind_error(bind_errors)
        self._bound_addresses = tuple(bound)
        self._started_at = _utc_now_iso()
        self._started_monotonic = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        """Close every socket and finalize the session summary."""
        for sock in self._sockets:
            with contextlib.suppress(OSError):
                sock.close()
        self._sockets = []
        if self._stop_reason is None:
            # events() never ran to a stop condition (early break / error path).
            self._finalize(
                StopReason.ERROR if exc and exc[0] is not None else StopReason.INTERRUPT
            )

    def events(self) -> Iterator[InboundEvent]:
        """Yield inbound events as they arrive, until a stop condition fires.

        Returns when ``max_duration``/``max_events`` is reached. A
        ``KeyboardInterrupt`` propagates *after* the session is finalized, so the
        interrupted session summary is still complete (FR-011).
        """
        deadline = (
            self._started_monotonic + self._max_duration
            if self._max_duration is not None
            else None
        )
        selector = selectors.DefaultSelector()
        for sock in self._sockets:
            selector.register(sock, selectors.EVENT_READ)
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    self._finalize(StopReason.MAX_DURATION)
                    return
                for key, _ in selector.select(timeout=_POLL_TIMEOUT_S):
                    sock = cast(socket.socket, key.fileobj)
                    event = self._receive_one(sock)
                    if event is None:
                        continue
                    yield event
                    if (
                        self._max_events is not None
                        and self._events_received >= self._max_events
                    ):
                        self._finalize(StopReason.MAX_EVENTS)
                        return
        except KeyboardInterrupt:
            self._finalize(StopReason.INTERRUPT)
            raise
        finally:
            selector.close()

    def _receive_one(self, sock: socket.socket) -> InboundEvent | None:
        """Accept/receive one inbound item and record its metadata (never its bytes)."""
        try:
            if self._protocol is Protocol.TCP:
                conn, peer = sock.accept()
                conn.close()  # closed unread: payload is never read (FR-010)
            else:
                _, peer = sock.recvfrom(65535)  # datagram bytes discarded immediately
        except OSError:
            return None  # readiness raced away (spurious wakeup / peer reset)
        self._events_received += 1
        return InboundEvent(
            index=self._events_received,
            peer_address=str(peer[0]),
            peer_port=int(peer[1]),
            family=_family_name(sock.family),
            timestamp=_utc_now_iso(),
        )

    @property
    def session(self) -> ListenerSession:
        """The session summary (final once :meth:`events` has returned)."""
        return ListenerSession(
            protocol=self._protocol,
            port=self._port,
            bound_addresses=self._bound_addresses,
            started_at=self._started_at or "",
            stopped_at=self._stopped_at,
            stop_reason=self._stop_reason,
            events_received=self._events_received,
            max_duration_s=self._max_duration,
            max_events=self._max_events,
        )

    def _finalize(self, reason: StopReason) -> None:
        if self._stop_reason is None:
            self._stop_reason = reason
            self._stopped_at = _utc_now_iso()

    def _raise_bind_error(self, bind_errors: list[OSError]) -> None:
        """Normalize the bind failure into the typed hierarchy (FR-012, Art. VI)."""
        for exc in bind_errors:
            if exc.errno in _IN_USE_ERRNOS:
                raise PortInUse(
                    f"port {self._port} is already in use",
                    hint="something is already listening here; pick another port or "
                    "find the process using it",
                ) from exc
        for exc in bind_errors:
            if exc.errno in _PERMISSION_ERRNOS:
                raise BindPermissionDenied(
                    f"permission denied binding port {self._port}",
                    hint="ports below 1024 need elevation — choose an unprivileged "
                    "port (1024-65535)",
                ) from exc
        detail = bind_errors[0] if bind_errors else "no usable address family"
        raise NetError(
            f"cannot bind port {self._port}: {detail}",
            hint="check local network configuration and try again",
        ) from (bind_errors[0] if bind_errors else None)
