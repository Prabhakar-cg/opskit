"""AD/LDAP-specific exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

Connection-layer failures reuse :mod:`opskit.net.errors` (refused/timeout) and
:mod:`opskit.tls.errors` (handshake/certificate) so scripts branch on the same exit
classes across categories; these cover the directory-specific outcomes. Each type owns
its exit code (constitution Art. VII). This module never imports ldap3.
"""

from __future__ import annotations

import re

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode


class AdError(OpskitError):
    """Base class for directory diagnostics failures (unclassified directory error)."""

    code = "ad_error"
    exit_code = ExitCode.ERROR


class DependencyMissing(AdError):
    """The optional ldap3 dependency is not installed (the ``opskit[ad]`` extra)."""

    code = "dependency_missing"
    exit_code = ExitCode.USAGE


class CleartextRefused(AdError):
    """A password would be sent over an unencrypted connection without explicit opt-in."""

    code = "cleartext_refused"
    exit_code = ExitCode.USAGE


class AmbiguousPrincipal(AdError):
    """An identifier matched more than one directory object; refusing to guess."""

    code = "ambiguous_principal"
    exit_code = ExitCode.USAGE


class DiscoveryError(AdError):
    """No directory servers could be discovered for the domain (SRV lookup failed)."""

    code = "discovery_failed"
    exit_code = ExitCode.NXDOMAIN  # same outcome class as "name does not exist"


class AuthenticationFailed(AdError):
    """The directory rejected the bind credentials."""

    code = "auth_failed"
    exit_code = ExitCode.AUTH_FAILED


class PermissionDenied(AdError):
    """Bound successfully, but the account is not authorized for the query."""

    code = "permission_denied"
    exit_code = ExitCode.PERMISSION_DENIED


class PrincipalNotFound(AdError):
    """The named principal/group/object does not exist in the directory."""

    code = "principal_not_found"
    exit_code = ExitCode.NOT_FOUND


# Active Directory encodes the *reason* a bind failed as a hex sub-code in the
# invalidCredentials diagnostic message ("... data 52e, ..."). Decoding it turns a bare
# credential rejection into a sign-in diagnosis (research R3).
_BIND_DATA_REASONS: dict[str, str] = {
    "525": "user not found",
    "52e": "invalid credentials (bad password)",
    "530": "not permitted to log on at this time",
    "531": "not permitted to log on at this workstation",
    "532": "password expired",
    "533": "account disabled",
    "534": "not granted the requested logon type at this machine",
    "701": "account expired",
    "773": "user must reset password",
    "775": "account locked out",
}

_BIND_DATA_RE = re.compile(r"\bdata\s+([0-9a-fA-F]+)\b")


def decode_bind_data(message: str) -> str | None:
    """Decode an AD bind diagnostic ``data`` sub-code into a human reason.

    Args:
        message: The server's bind diagnostic message (may be empty).

    Returns:
        The decoded reason (e.g. ``"account locked out"``), or ``None`` when the
        message carries no recognizable AD sub-code.
    """
    match = _BIND_DATA_RE.search(message or "")
    if match is None:
        return None
    return _BIND_DATA_REASONS.get(match.group(1).lower())
