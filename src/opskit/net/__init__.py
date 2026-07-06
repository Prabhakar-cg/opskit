"""Network primitives — resolve/connect shared by socket-using categories.

Library-only for now (no CLI); the future net category registers commands on top of these.
Public surface is SemVer-governed. Failures raise; nothing here prints or exits the process.
"""

from __future__ import annotations

from opskit.net.errors import ConnectRefused, ConnectTimeout, NetError, ResolutionError
from opskit.net.tcp import AddressCandidate, TcpConnection, connect, resolve

__all__ = [
    "AddressCandidate",
    "ConnectRefused",
    "ConnectTimeout",
    "NetError",
    "ResolutionError",
    "TcpConnection",
    "connect",
    "resolve",
]
