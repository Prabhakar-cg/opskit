"""Network-layer exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

These cover the resolve/connect layers shared by every category that opens a socket
(tls today; the future net category). Each type owns its exit code (constitution Art. VII).
"""

from __future__ import annotations

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class NetError(OpskitError):
    """Base class for name-resolution and connection failures."""

    code = "net_error"


class ResolutionError(NetError):
    """The host name could not be resolved to any address."""

    code = "resolve_failed"
    exit_code = ExitCode.NXDOMAIN  # same outcome class as DNS "name does not exist"


class ConnectRefused(NetError):
    """The target actively refused the connection (or was unreachable)."""

    code = "connect_refused"
    exit_code = ExitCode.CONNECT_FAILED


class ConnectTimeout(NetError):
    """No connection could be established before the timeout (possibly filtered)."""

    code = "connect_timeout"
    exit_code = ExitCode.TIMEOUT
