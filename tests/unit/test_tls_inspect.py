"""Tests for certificate parsing and validation-finding assembly."""

from __future__ import annotations

import dataclasses

import pytest

from opskit.tls.inspect import (
    X509_V_ERR_CERT_HAS_EXPIRED,
    X509_V_ERR_CERT_NOT_YET_VALID,
    X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT,
    X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN,
    X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY,
    build_findings,
    parse_certificate,
)
from opskit.tls.models import FindingCode, parse_target


@pytest.fixture
def leaf(make_cert):
    return parse_certificate(
        make_cert("unit.test", dns_names=("unit.test", "alt.unit.test"), days=100)
    )


def test_parse_certificate_fields(leaf):
    assert "CN=unit.test" in leaf.subject
    assert leaf.subject == leaf.issuer  # self-signed builder
    assert leaf.is_self_signed
    assert "dns:unit.test" in leaf.sans
    assert 98 <= leaf.days_until_expiry <= 100
    assert leaf.key_type == "EC"
    assert leaf.key_bits == 256
    assert "ecdsa" in leaf.signature_algorithm.lower()
    assert len(leaf.fingerprint_sha256) == 64
    assert leaf.serial
    assert leaf.not_before < leaf.not_after


def test_expired_days_negative(make_cert):
    info = parse_certificate(make_cert(days=-2, not_before_days=-30))
    assert info.days_until_expiry < 0


def _findings(leaf, **kwargs):
    defaults = {
        "name_matched": True,
        "verify_errors": (),
        "chain_length": 2,
        "tls_version": "TLSv1.3",
        "warn_days": 30,
    }
    defaults.update(kwargs)
    return build_findings(parse_target("unit.test"), leaf, **defaults)


def test_clean_pass_produces_no_findings(leaf):
    assert _findings(leaf, warn_days=30) == ()


def test_expired_finding_carries_date(leaf):
    findings = _findings(leaf, verify_errors=((X509_V_ERR_CERT_HAS_EXPIRED, 0),))
    assert findings[0].code is FindingCode.EXPIRED
    assert leaf.not_after in findings[0].message


def test_not_yet_valid_distinct(leaf):
    findings = _findings(leaf, verify_errors=((X509_V_ERR_CERT_NOT_YET_VALID, 0),))
    assert findings[0].code is FindingCode.NOT_YET_VALID


def test_self_signed_vs_chain_untrusted(leaf):
    self_signed = _findings(
        leaf, verify_errors=((X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT, 0),)
    )
    untrusted = _findings(
        leaf, verify_errors=((X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN, 1),)
    )
    assert self_signed[0].code is FindingCode.SELF_SIGNED
    assert untrusted[0].code is FindingCode.UNTRUSTED_CHAIN


def test_missing_issuer_maps_by_chain_length(make_cert):
    # Chain of 1 (and not self-signed) -> the server forgot the intermediate.
    ca_issued = dataclasses.replace(
        parse_certificate(make_cert()), is_self_signed=False
    )
    short = build_findings(
        parse_target("unit.test"),
        ca_issued,
        name_matched=True,
        verify_errors=((X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY, 0),),
        chain_length=1,
        tls_version="TLSv1.3",
        warn_days=0,
    )
    full = build_findings(
        parse_target("unit.test"),
        ca_issued,
        name_matched=True,
        verify_errors=((X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY, 1),),
        chain_length=2,
        tls_version="TLSv1.3",
        warn_days=0,
    )
    assert short[0].code is FindingCode.INCOMPLETE_CHAIN
    assert full[0].code is FindingCode.UNTRUSTED_CHAIN


def test_unknown_verify_error_falls_back_to_untrusted(leaf):
    findings = _findings(leaf, verify_errors=((99, 0),))
    assert findings[0].code is FindingCode.UNTRUSTED_CHAIN
    assert "99" in findings[0].message


def test_name_mismatch_lists_requested_and_covered(leaf):
    findings = _findings(leaf, name_matched=False)
    mismatch = next(f for f in findings if f.code is FindingCode.NAME_MISMATCH)
    assert "unit.test" in mismatch.message
    assert "dns:unit.test" in mismatch.message


def test_no_sans_flagged_alongside_mismatch(make_cert):
    bare = parse_certificate(make_cert(dns_names=()))
    findings = build_findings(
        parse_target("unit.test"),
        bare,
        name_matched=False,
        verify_errors=(),
        chain_length=1,
        tls_version="TLSv1.3",
        warn_days=0,
    )
    codes = [f.code for f in findings]
    assert FindingCode.NO_SANS in codes
    assert FindingCode.NAME_MISMATCH in codes


def test_legacy_protocol_warning(leaf):
    findings = _findings(leaf, tls_version="TLSv1.1")
    assert findings[0].code is FindingCode.LEGACY_PROTOCOL


def test_expiring_soon_threshold(make_cert):
    soon = parse_certificate(make_cert(days=5))
    inside = build_findings(
        parse_target("unit.test"),
        soon,
        name_matched=True,
        verify_errors=(),
        chain_length=1,
        tls_version="TLSv1.3",
        warn_days=30,
    )
    disabled = build_findings(
        parse_target("unit.test"),
        soon,
        name_matched=True,
        verify_errors=(),
        chain_length=1,
        tls_version="TLSv1.3",
        warn_days=0,
    )
    assert inside[0].code is FindingCode.EXPIRING_SOON
    assert disabled == ()


def test_expiring_soon_suppressed_when_invalid(make_cert):
    soon = parse_certificate(make_cert(days=5))
    findings = build_findings(
        parse_target("unit.test"),
        soon,
        name_matched=False,
        verify_errors=(),
        chain_length=1,
        tls_version="TLSv1.3",
        warn_days=30,
    )
    codes = [f.code for f in findings]
    assert FindingCode.EXPIRING_SOON not in codes


def test_duplicate_errors_deduplicated(leaf):
    findings = _findings(
        leaf,
        verify_errors=(
            (X509_V_ERR_CERT_HAS_EXPIRED, 0),
            (X509_V_ERR_CERT_HAS_EXPIRED, 1),
        ),
    )
    assert len([f for f in findings if f.code is FindingCode.EXPIRED]) == 1
