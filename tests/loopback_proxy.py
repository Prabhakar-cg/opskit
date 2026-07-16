"""In-process, scriptable stand-in HTTP CONNECT proxy for tests (research R9).

A threaded loopback server whose behavior per connection is scripted at construction,
so every FR-009 outcome is inducible deterministically on every platform:

- ``tunnel``      → ``200 Connection established`` (checking credentials when ``auth`` set)
- ``auth``        → always ``407`` advertising the configured ``schemes``
- ``deny``        → ``403 Forbidden``
- ``bad-gateway`` → ``502``; ``unavailable`` → ``503``; ``gateway-timeout`` → ``504``
- ``garbage``     → a non-HTTP banner
- ``silent``      → reads the request, never answers (client must time out)
- ``close``       → closes without sending a byte

It records every request head it receives (CONNECT line + headers) so tests can assert
what was sent — including the exact ``Proxy-Authorization`` header — and how many
attempts (retries) reached the proxy.
"""

from __future__ import annotations

import base64
import contextlib
import socket
import threading


class ScriptedProxy:
    """A loopback CONNECT responder with one scripted behavior."""

    def __init__(
        self,
        behavior: str = "tunnel",
        *,
        auth: tuple[str, str] | None = None,
        schemes: tuple[str, ...] = ("Basic",),
    ) -> None:
        self.behavior = behavior
        self.auth = auth  # (user, password) required for a 200 in "tunnel" mode
        self.schemes = schemes
        self.requests: list[str] = []  # raw request heads, in arrival order
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.host = "127.0.0.1"
        self.port = int(self._sock.getsockname()[1])
        self._accepter = threading.Thread(target=self._accept_loop, daemon=True)

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> ScriptedProxy:
        self._accepter.start()
        return self

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()

    def __enter__(self) -> ScriptedProxy:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- assertions helpers ----------------------------------------------------------

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def connect_line(self, index: int = 0) -> str:
        """The request line (``CONNECT host:port HTTP/1.1``) of request ``index``."""
        return self.requests[index].splitlines()[0]

    def header(self, name: str, index: int = 0) -> str | None:
        """A header value from request ``index`` (case-insensitive), or None."""
        prefix = name.lower() + ":"
        for line in self.requests[index].splitlines()[1:]:
            if line.lower().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return None

    # -- server internals --------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _read_head(self, conn: socket.socket) -> str:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data = data + chunk
        return data.split(b"\r\n\r\n", 1)[0].decode("latin-1")

    def _expected_authorization(self) -> str:
        assert self.auth is not None
        token = base64.b64encode(f"{self.auth[0]}:{self.auth[1]}".encode()).decode(
            "ascii"
        )
        return f"Basic {token}"

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(10.0)
        try:
            head = self._read_head(conn)
            if not head:
                # The peer connected and closed without sending anything (e.g. a
                # plain TCP reachability check against this port) — not a request.
                return
            with self._lock:
                self.requests.append(head)
                index = len(self.requests) - 1

            if self.behavior == "close":
                return
            if self.behavior == "silent":
                self._stop.wait(10.0)  # hold the connection; the client times out
                return
            if self.behavior == "garbage":
                conn.sendall(b"SSH-2.0-NotAProxy_1.0\r\n")
                return

            status = {
                "auth": (407, "Proxy Authentication Required"),
                "deny": (403, "Forbidden"),
                "bad-gateway": (502, "Bad Gateway"),
                "unavailable": (503, "Service Unavailable"),
                "gateway-timeout": (504, "Gateway Timeout"),
            }.get(self.behavior)
            if self.behavior == "tunnel":
                if self.auth is not None and (
                    self.header("Proxy-Authorization", index)
                    != self._expected_authorization()
                ):
                    status = (407, "Proxy Authentication Required")
                else:
                    status = (200, "Connection established")
            assert status is not None, f"unknown behavior: {self.behavior}"

            code, reason = status
            lines = [f"HTTP/1.1 {code} {reason}"]
            if code == 407:
                lines.extend(f"Proxy-Authenticate: {s}" for s in self.schemes)
            lines.extend(["Content-Length: 0", "", ""])
            conn.sendall("\r\n".join(lines).encode("latin-1"))
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()
