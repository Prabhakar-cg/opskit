"""Tests for TLS report rendering (escaping, verdicts, chain table)."""

from __future__ import annotations

from opskit.core.output import make_console
from opskit.net import TcpConnection
from opskit.tls.models import (
    CertificateInfo,
    FindingCode,
    TlsCheckResult,
    TlsOutcome,
    ValidationFinding,
    parse_target,
)
from opskit.tls.output import render_check


def _cert(**overrides):
    values = {
        "subject": "CN=unit.test",
        "issuer": "CN=issuer.test",
        "sans": ("dns:unit.test",),
        "not_before": "2026-01-01T00:00:00+00:00",
        "not_after": "2027-01-01T00:00:00+00:00",
        "days_until_expiry": 180,
        "serial": "AB12",
        "signature_algorithm": "ecdsa-with-SHA256",
        "key_type": "EC",
        "key_bits": 256,
        "fingerprint_sha256": "ab" * 32,
        "is_self_signed": False,
    }
    values.update(overrides)
    return CertificateInfo(**values)


def _render(result):
    console = make_console(no_color=True)
    with console.capture() as capture:
        render_check(result, console=console)
    return capture.get()


def _result(**overrides):
    leaf = overrides.pop("leaf", _cert())
    values = {
        "target": parse_target("unit.test"),
        "outcome": TlsOutcome.OK,
        "connection": TcpConnection(
            address="192.0.2.1", family="ipv4", port=443, connect_ms=3.0
        ),
        "tls_version": "TLSv1.3",
        "cipher": "TLS_AES_256_GCM_SHA384",
        "leaf": leaf,
        "chain": (leaf, _cert(subject="CN=issuer.test", issuer="CN=root.test")),
        "findings": (),
    }
    values.update(overrides)
    return TlsCheckResult(**values)


def test_ok_report_contains_all_sections():
    text = _render(_result())
    assert "OK" in text
    assert "CN=unit.test" in text
    assert "dns:unit.test" in text
    assert "TLSv1.3" in text
    assert "Chain" in text
    assert "192.0.2.1" in text


def test_findings_rendered_with_hints():
    text = _render(
        _result(
            outcome=TlsOutcome.CERT_INVALID,
            findings=(
                ValidationFinding(
                    FindingCode.EXPIRED, "certificate expired on X", hint="renew it"
                ),
            ),
        )
    )
    assert "CERTIFICATE INVALID" in text
    assert "expired" in text
    assert "renew it" in text


def test_hostile_values_render_literally():
    hostile = _cert(
        subject="[bold red]CN=evil[/bold red]",
        sans=("dns:[link=http://x]click[/link]",),
    )
    text = _render(_result(leaf=hostile, chain=(hostile,)))
    assert "[bold red]CN=evil[/bold red]" in text
    assert "[link=http://x]click[/link]" in text


def test_ip_target_notes_matching_mode():
    result = _result(target=parse_target("192.0.2.1"))
    text = _render(result)
    assert "IP target" in text


def test_sni_shown_when_overridden():
    result = _result(target=parse_target("unit.test", server_name="other.test"))
    text = _render(result)
    assert "sni: other.test" in text


def test_ip_target_with_sni_override_shows_both():
    result = _result(target=parse_target("192.0.2.1", server_name="vhost.test"))
    text = _render(result)
    assert "192.0.2.1" in text
    assert "sni: vhost.test" in text
    assert "IP target" in text
