"""Honest UDP reachability probe (research R2).

Connected-UDP sockets are the only pure-stdlib, unprivileged way to observe ICMP *port
unreachable* on every platform: the OS delivers it back to the connected socket as a
socket error. One **zero-byte** datagram is sent per attempt — a valid, deliverable UDP
packet carrying no application payload (FR-008/FR-018). Nothing here prints or exits.
"""

from __future__ import annotations

import errno
import socket
import time
from dataclasses import dataclass
from typing import Any

from opskit.net.errors import (
    ConnectRefused,
    NetError,
    UdpClosed,
    UdpInconclusive,
)
from opskit.net.tcp import resolve

# POSIX errno values plus the raw winsock codes (WSAENETUNREACH/WSAEHOSTUNREACH) —
# Windows socket OSErrors carry the WSA number, not the MSVC errno mapping.
_UNREACHABLE_ERRNOS = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065})


@dataclass(frozen=True)
class UdpReply:
    """Facts about a received UDP reply (the only way a UDP port is called open)."""

    address: str
    family: str  # "ipv4" | "ipv6"
    port: int
    time_ms: float  # send-to-reply round trip

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this reply."""
        return {
            "address": self.address,
            "family": self.family,
            "port": self.port,
            "time_ms": round(self.time_ms, 3),
        }


def udp_probe(
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    family: str | None = None,
) -> UdpReply:
    """Probe ``host:port`` over UDP with the honest tri-state contract (SC-007).

    A port is reported open **only** when a reply datagram is received. An ICMP
    port-unreachable signal is definitive (like a TCP refusal) and not retried;
    retries apply only to silence (the probe is re-sent).

    Args:
        host: Hostname or IP literal.
        port: Target UDP port.
        timeout: Per-attempt reply timeout, seconds.
        retries: Probe re-sends after silence (an unreachable signal is definitive).
        family: Restrict candidates to one family (``"ipv4"``/``"ipv6"``); ``None`` = both.

    Returns:
        The :class:`UdpReply` when a reply datagram arrived.

    Raises:
        ResolutionError: If the name does not resolve (or not in the requested family).
        UdpClosed: If the host signaled port unreachable (the port is closed).
        UdpInconclusive: If nothing answered — the port is open or filtered; silence
            is never reported as open or closed.
        NetError: For any other normalized socket-level failure.
    """
    candidates = resolve(
        host, port, timeout=timeout, family=family, socktype=socket.SOCK_DGRAM
    )
    for _ in range(retries + 1):
        for candidate in candidates:
            sock = socket.socket(candidate.af, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            try:
                sock.connect(candidate.sockaddr)
                start = time.perf_counter()
                sock.send(b"")  # zero-byte probe: deliverable, no payload (FR-018)
                sock.recv(65535)
                time_ms = (time.perf_counter() - start) * 1000.0
                return UdpReply(
                    address=candidate.address,
                    family=candidate.family,
                    port=port,
                    time_ms=time_ms,
                )
            except socket.timeout:
                continue  # silence: try the next candidate / re-send
            except (ConnectionRefusedError, ConnectionResetError) as exc:
                # Linux/macOS surface the ICMP as ECONNREFUSED; Windows as
                # WSAECONNRESET on the recv following the ICMP. Both mean closed.
                raise UdpClosed(
                    f"{host}:{port}/udp is closed: the host signaled port unreachable",
                    hint="nothing is listening on this UDP port at the target",
                ) from exc
            except OSError as exc:
                _raise_normalized(host, port, exc)
            finally:
                sock.close()
    raise UdpInconclusive(
        f"no response from {host}:{port}/udp — open or filtered (inconclusive)",
        hint="silence does not mean closed: check from the service side with "
        f"'opskit net listen {port} --udp', or use protocol-aware tooling "
        "(e.g. 'opskit dns' for DNS ports)",
    )


def _raise_normalized(host: str, port: int, exc: OSError) -> None:
    """Re-raise an unexpected socket ``OSError`` as a typed NetError (Art. VI)."""
    if exc.errno in _UNREACHABLE_ERRNOS:
        raise ConnectRefused(
            f"cannot reach {host}:{port}/udp: {exc}",
            hint="the network or host is unreachable; check routing and connectivity",
        ) from exc
    raise NetError(
        f"UDP probe of {host}:{port} failed: {exc}",
        hint="check local network configuration and try again",
    ) from exc
