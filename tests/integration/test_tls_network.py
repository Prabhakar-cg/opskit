"""Real-endpoint smoke tests (opt-in; never gate CI).

Run with: uv run pytest -m network
"""

from __future__ import annotations

import pytest

from opskit.net.errors import ResolutionError
from opskit.tls import check
from opskit.tls.models import FindingCode, TlsOutcome

pytestmark = pytest.mark.network


def test_healthy_public_endpoint():
    result = check("example.com", timeout=10)
    assert result.outcome in (TlsOutcome.OK, TlsOutcome.EXPIRING_SOON)
    assert result.leaf is not None
    assert result.tls_version.startswith("TLS")


def test_expired_badssl():
    result = check("expired.badssl.com", timeout=10)
    assert result.outcome is TlsOutcome.CERT_INVALID
    assert FindingCode.EXPIRED in {f.code for f in result.findings}


def test_wrong_host_badssl():
    result = check("wrong.host.badssl.com", timeout=10)
    assert FindingCode.NAME_MISMATCH in {f.code for f in result.findings}


def test_self_signed_badssl():
    result = check("self-signed.badssl.com", timeout=10)
    assert FindingCode.SELF_SIGNED in {f.code for f in result.findings}


def test_unresolvable():
    with pytest.raises(ResolutionError):
        check("no-such-host.invalid", timeout=5)
