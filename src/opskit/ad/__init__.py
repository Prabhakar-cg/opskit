"""Active Directory / LDAP diagnostics: account status, membership, directory checks.

Public API (contracts/python-api.md): convenience functions plus the reusable
:class:`AdClient` session. Importing this package works without the ``opskit[ad]``
extra — the optional ldap3 dependency is loaded lazily by the adapter and surfaces as
:class:`~opskit.ad.errors.DependencyMissing` with an install hint.
"""

from __future__ import annotations

from opskit.ad.api import (
    AdClient,
    check,
    is_member,
    membership,
    show,
    user_status,
)
from opskit.ad.errors import (
    AdError,
    AmbiguousPrincipal,
    AuthenticationFailed,
    CleartextRefused,
    DependencyMissing,
    DiscoveryError,
    PermissionDenied,
    PrincipalNotFound,
)
from opskit.ad.models import (
    AccountStatusReport,
    ConnectivityReport,
    DirectoryConfig,
    MembershipEntry,
    MembershipReport,
    MembershipVerdict,
    ObjectSummary,
    ServerInfo,
    Stage,
)

__all__ = [
    "AccountStatusReport",
    "AdClient",
    "AdError",
    "AmbiguousPrincipal",
    "AuthenticationFailed",
    "CleartextRefused",
    "ConnectivityReport",
    "DependencyMissing",
    "DirectoryConfig",
    "DiscoveryError",
    "MembershipEntry",
    "MembershipReport",
    "MembershipVerdict",
    "ObjectSummary",
    "PermissionDenied",
    "PrincipalNotFound",
    "ServerInfo",
    "Stage",
    "check",
    "is_member",
    "membership",
    "show",
    "user_status",
]
