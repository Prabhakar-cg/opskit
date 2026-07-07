"""pyOpenSSL handshake with a recording verify callback.

The callback never aborts the handshake — it records every OpenSSL verify error so one
connection yields both the full presented chain *and* the precise validation findings
(FR-006). The trust store is sourced from the stdlib's platform defaults, or replaced by a
user CA bundle (research R2).
"""

from __future__ import annotations

import contextlib
import select
import socket
import ssl as stdlib_ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from cryptography import x509
from OpenSSL import SSL
from OpenSSL.crypto import X509

from opskit.net.errors import ConnectTimeout
from opskit.tls.errors import HandshakeError

_NON_TLS_MARKERS = (
    "wrong version number",
    "unknown protocol",
    "packet length too long",
    "record layer failure",
    "unexpected message",
)


@dataclass(frozen=True)
class HandshakeOutcome:
    """Everything one completed handshake yields."""

    chain: tuple[x509.Certificate, ...]  # as presented, leaf first
    tls_version: str | None
    cipher: str | None
    verify_errors: tuple[tuple[int, int], ...]  # (errno, depth) pairs


def _load_platform_store(context: SSL.Context) -> None:
    """Fill the pyOpenSSL store with the platform's default CAs.

    Two complementary sources (research R2): OpenSSL's own default paths (Linux/macOS
    cafile/capath — covers distros where the stdlib loads a lazy capath that
    ``get_ca_certs`` cannot enumerate) plus the stdlib's enumeration (which pulls from
    the Windows system certificate stores).
    """
    with contextlib.suppress(SSL.Error):
        context.set_default_verify_paths()
    stdlib_context = stdlib_ssl.SSLContext(stdlib_ssl.PROTOCOL_TLS_CLIENT)
    stdlib_context.load_default_certs(stdlib_ssl.Purpose.SERVER_AUTH)
    store = context.get_cert_store()
    if store is None:  # pragma: no cover - pyOpenSSL always returns a store
        return
    for der in stdlib_context.get_ca_certs(binary_form=True):
        try:
            store.add_cert(X509.from_cryptography(x509.load_der_x509_certificate(der)))
        except Exception:  # noqa: S112 - skip malformed platform entries, keep the rest
            continue


def build_context(ca_file: str | Path | None = None) -> SSL.Context:
    """Return a TLS client context whose trust store is the platform's (or ``ca_file``)."""
    context = SSL.Context(SSL.TLS_CLIENT_METHOD)
    if ca_file is not None:
        context.load_verify_locations(str(ca_file))
    else:
        _load_platform_store(context)
    return context


def perform_handshake(
    sock: socket.socket,
    *,
    server_name: str | None,
    timeout: float,
    ca_file: str | Path | None = None,
) -> HandshakeOutcome:
    """Run the TLS handshake over an already-connected socket; never sends app data.

    Args:
        sock: Connected TCP socket (consumed; caller still closes it).
        server_name: SNI to send, or ``None`` to omit (IP targets).
        timeout: Handshake timeout in seconds.
        ca_file: Optional PEM bundle replacing the platform trust store.

    Raises:
        ConnectTimeout: If the peer stops responding mid-handshake.
        HandshakeError: If the handshake fails (e.g. the service does not speak TLS).
    """
    verify_errors: list[tuple[int, int]] = []

    def _record(
        _conn: SSL.Connection,
        _cert: object,
        errno: int,
        depth: int,
        ok: int,
    ) -> bool:
        if not ok:
            verify_errors.append((errno, depth))
        return True  # never abort: we want the chain plus the recorded findings

    context = build_context(ca_file)
    context.set_verify(SSL.VERIFY_PEER, _record)
    connection = SSL.Connection(context, sock)
    if server_name:
        try:
            encoded = server_name.encode("idna")
        except UnicodeError:
            encoded = server_name.encode("ascii", errors="ignore")
        connection.set_tlsext_host_name(encoded)
    sock.settimeout(timeout)
    connection.set_connect_state()
    try:
        _handshake_with_timeout(connection, sock, timeout)
    except socket.timeout as exc:
        raise ConnectTimeout(
            f"TLS handshake timed out after {timeout}s",
            hint="the service may be hanging mid-handshake; try a longer --timeout",
        ) from exc
    except SSL.SysCallError as exc:
        raise HandshakeError(
            f"connection closed during TLS handshake: {exc}",
            hint="the service may not speak TLS on this port",
        ) from exc
    except SSL.Error as exc:
        raise HandshakeError(
            f"TLS handshake failed: {_ssl_error_text(exc)}",
            hint=(
                "the service may not speak TLS on this port "
                "(STARTTLS-style upgrades are not supported)"
            ),
        ) from exc

    chain = tuple(connection.get_peer_cert_chain(as_cryptography=True) or [])
    tls_version: str | None = connection.get_protocol_version_name()
    cipher: str | None = connection.get_cipher_name()
    # Best-effort close; the data is already extracted.
    with contextlib.suppress(SSL.Error, OSError):
        connection.shutdown()
    return HandshakeOutcome(
        chain=chain,
        tls_version=tls_version,
        cipher=cipher,
        verify_errors=tuple(verify_errors),
    )


def _handshake_with_timeout(
    connection: SSL.Connection, sock: socket.socket, timeout: float
) -> None:
    """Drive ``do_handshake`` to completion under a wall-clock deadline.

    A Python socket with a timeout is internally non-blocking, so pyOpenSSL surfaces
    ``WantRead/WantWrite`` — wait with ``select`` and retry until done or out of time.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection.do_handshake()
            return
        except (SSL.WantReadError, SSL.WantWriteError) as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("handshake deadline exceeded") from exc
            wants_read = isinstance(exc, SSL.WantReadError)
            readable, writable, _ = select.select(
                [sock] if wants_read else [],
                [] if wants_read else [sock],
                [],
                remaining,
            )
            if not readable and not writable:
                raise socket.timeout("handshake deadline exceeded") from exc


def _ssl_error_text(exc: SSL.Error) -> str:
    """Flatten pyOpenSSL's ``[(lib, func, reason), ...]`` error args into one line."""
    parts: list[str] = []
    for entry in cast("tuple[object, ...]", exc.args):
        if isinstance(entry, (list, tuple)):
            for item in cast("Sequence[object]", entry):
                if isinstance(item, (list, tuple)):
                    joined = ":".join(
                        str(piece) for piece in cast("Sequence[object]", item) if piece
                    )
                    if joined:
                        parts.append(joined)
                elif str(item):
                    parts.append(str(item))
        elif str(entry):
            parts.append(str(entry))
    text = "; ".join(parts) or exc.__class__.__name__
    lowered = text.lower()
    if any(marker in lowered for marker in _NON_TLS_MARKERS):
        text += " (the response does not look like TLS)"
    return text
