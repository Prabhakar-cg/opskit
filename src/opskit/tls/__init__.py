"""TLS verification diagnostics — importable API and CLI sub-app.

Public surface (SemVer-governed): :func:`check`, the typed models, and the TLS exception
hierarchy. Failures raise; nothing here prints or exits the process.
"""

from __future__ import annotations

from opskit.tls.api import check
from opskit.tls.errors import (
    CertificateExpiring,
    CertificateInvalid,
    HandshakeError,
    TlsError,
)
from opskit.tls.models import (
    CertificateInfo,
    FindingCode,
    TlsCheckResult,
    TlsOutcome,
    TlsTarget,
    ValidationFinding,
    parse_target,
)

__all__ = [
    "CertificateExpiring",
    "CertificateInfo",
    "CertificateInvalid",
    "FindingCode",
    "HandshakeError",
    "TlsCheckResult",
    "TlsError",
    "TlsOutcome",
    "TlsTarget",
    "ValidationFinding",
    "check",
    "parse_target",
]
