"""Reusable TCP resolve/connect primitive (the seam the future net category builds on).

Pure stdlib sockets: :func:`resolve` orders candidates via ``getaddrinfo`` (dual-stack order is
the platform's), :func:`connect` walks them with timeout/retries and normalizes raw ``OSError``
into the :mod:`opskit.net.errors` hierarchy. Nothing here prints or exits (Art. VII).
"""

from __future__ import annotations

import concurrent.futures
import socket
import time
from dataclasses import dataclass
from typing import Any

from opskit.net.errors import ConnectRefused, ConnectTimeout, ResolutionError


@dataclass(frozen=True)
class AddressCandidate:
    """One resolved address a connection may be attempted against."""

    address: str
    family: str  # "ipv4" | "ipv6"
    sockaddr: tuple[Any, ...]
    af: int  # socket.AF_* constant

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this candidate."""
        return {"address": self.address, "family": self.family}


@dataclass(frozen=True)
class TcpConnection:
    """Facts about an established TCP connection (returned alongside the socket)."""

    address: str
    family: str  # "ipv4" | "ipv6"
    port: int
    connect_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this connection."""
        return {
            "address": self.address,
            "family": self.family,
            "port": self.port,
            "connect_ms": round(self.connect_ms, 3),
        }


def _family_name(af: int) -> str:
    return "ipv6" if af == socket.AF_INET6 else "ipv4"


def resolve(host: str, port: int, *, timeout: float = 5.0) -> list[AddressCandidate]:
    """Resolve ``host`` to ordered address candidates (platform dual-stack order).

    Args:
        host: Hostname or IP literal.
        port: Target port (carried into the socket addresses).
        timeout: Wall-clock budget for the lookup. ``socket.getaddrinfo`` has no per-call
            timeout, so it runs off-thread and is abandoned after ``timeout`` seconds.

    Returns:
        Candidates in the order the platform recommends trying them.

    Raises:
        ResolutionError: If the name does not resolve or resolution exceeds ``timeout``.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    try:
        infos = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise ResolutionError(
            f"resolving {host} timed out after {timeout}s",
            hint="the resolver may be slow or unreachable; diagnose with: opskit dns lookup "
            + host,
        ) from exc
    except OSError as exc:
        raise ResolutionError(
            f"cannot resolve {host}: {exc}",
            hint="check the name for typos, or diagnose with: opskit dns lookup "
            + host,
        ) from exc
    finally:
        # A lookup that timed out keeps running in the worker; don't block on it (the OS
        # resolver abandons it per resolv.conf), just release the executor.
        executor.shutdown(wait=False)
    candidates: list[AddressCandidate] = []
    for af, _, _, _, sockaddr in infos:
        candidates.append(
            AddressCandidate(
                address=str(sockaddr[0]),
                family=_family_name(af),
                sockaddr=tuple(sockaddr),
                af=af,
            )
        )
    if not candidates:
        raise ResolutionError(f"cannot resolve {host}: no addresses returned")
    return candidates


def connect(
    host: str,
    port: int,
    *,
    timeout: float = 5.0,
    retries: int = 2,
) -> tuple[socket.socket, TcpConnection]:
    """Open a TCP connection to ``host:port``; the caller owns (and must close) the socket.

    Candidates are tried in :func:`resolve` order; timeouts are retried up to ``retries``
    times across the candidate list, while a refusal on every candidate fails immediately.

    Returns:
        The connected socket (timeout still set) and a :class:`TcpConnection` report.

    Raises:
        ResolutionError: If the name does not resolve.
        ConnectRefused: If every candidate refused / was unreachable.
        ConnectTimeout: If no candidate answered before the timeout (after retries).
    """
    candidates = resolve(host, port, timeout=timeout)
    start = time.perf_counter()
    saw_timeout = False
    refused_exc: BaseException | None = None
    timeout_exc: BaseException | None = None
    for _ in range(retries + 1):
        for candidate in candidates:
            sock = socket.socket(candidate.af, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(candidate.sockaddr)
            except socket.timeout as exc:
                sock.close()
                saw_timeout = True
                timeout_exc = exc
            except OSError as exc:
                sock.close()
                refused_exc = exc
            else:
                connect_ms = (time.perf_counter() - start) * 1000.0
                return sock, TcpConnection(
                    address=candidate.address,
                    family=candidate.family,
                    port=port,
                    connect_ms=connect_ms,
                )
        # A refusal is definitive (host reachable, port closed) — don't retry or wait for
        # a slow sibling address; only pure-timeout runs are worth retrying.
        if refused_exc is not None or not saw_timeout:
            break
    if refused_exc is not None:
        raise ConnectRefused(
            f"cannot connect to {host}:{port}: {refused_exc}",
            hint="check that the service is listening on this port",
        ) from refused_exc
    raise ConnectTimeout(
        f"no response from {host}:{port} within {timeout}s",
        hint="the port may be filtered; verify reachability and firewall rules",
    ) from timeout_exc
