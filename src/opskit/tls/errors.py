"""TLS-specific exception hierarchy (subclasses of :class:`opskit.core.errors.OpskitError`).

Connection-layer failures come from :mod:`opskit.net.errors`; these cover the handshake and
certificate-validation layers. Each type owns its exit code (constitution Art. VII).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opskit.core.errors import OpskitError
from opskit.core.exit_codes import ExitCode

if TYPE_CHECKING:
    from opskit.tls.models import ValidationFinding


class TlsError(OpskitError):
    """Base class for TLS handshake/validation failures."""

    code = "tls_error"
    exit_code = ExitCode.HANDSHAKE_FAILED


class HandshakeError(TlsError):
    """The TLS handshake failed (the service may not speak TLS on this port)."""

    code = "handshake_failed"
    exit_code = ExitCode.HANDSHAKE_FAILED


class CertificateInvalid(TlsError):
    """The certificate failed validation (expired, name mismatch, untrusted, ...)."""

    code = "cert_invalid"
    exit_code = ExitCode.CERT_INVALID

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        findings: tuple[ValidationFinding, ...] = (),
    ) -> None:
        """Initialize with the failing findings attached for programmatic access."""
        super().__init__(message, hint=hint)
        self.findings = findings


class CertificateExpiring(TlsError):
    """The certificate is valid but expires within the warning threshold."""

    code = "cert_expiring"
    exit_code = ExitCode.CERT_EXPIRING

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        days_remaining: int = 0,
    ) -> None:
        """Initialize with the days remaining until expiry."""
        super().__init__(message, hint=hint)
        self.days_remaining = days_remaining
