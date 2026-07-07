"""Certificate parsing, RFC 6125 name matching, and validation-finding assembly.

Pure functions over :mod:`cryptography` certificate objects — no sockets, no printing —
so every rule here is unit-testable against generated certificates.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa

from opskit.tls.models import CertificateInfo, FindingCode, TlsTarget, ValidationFinding

# OpenSSL X509 verify-callback error codes we classify (see `man verify(1)`).
X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT = 2
X509_V_ERR_CERT_NOT_YET_VALID = 9
X509_V_ERR_CERT_HAS_EXPIRED = 10
X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT = 18
X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN = 19
X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY = 20
X509_V_ERR_UNABLE_TO_VERIFY_LEAF_SIGNATURE = 21

_LEGACY_PROTOCOLS = {"SSLv3", "TLSv1", "TLSv1.1"}


def parse_certificate(cert: x509.Certificate) -> CertificateInfo:
    """Extract the reportable attributes (FR-011) from one certificate."""
    dns_sans: list[str] = []
    ip_sans: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_sans = list(san_ext.value.get_values_for_type(x509.DNSName))
        ip_sans = [str(ip) for ip in san_ext.value.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass

    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    now = datetime.now(timezone.utc)
    days = (not_after - now).days

    key = cert.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        key_type, key_bits = "RSA", key.key_size
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key_type, key_bits = "EC", key.curve.key_size
    elif isinstance(key, ed25519.Ed25519PublicKey):
        key_type, key_bits = "Ed25519", 256
    elif isinstance(key, ed448.Ed448PublicKey):
        key_type, key_bits = "Ed448", 456
    elif isinstance(key, dsa.DSAPublicKey):
        key_type, key_bits = "DSA", key.key_size
    else:  # pragma: no cover - exotic key types
        key_type, key_bits = type(key).__name__, 0

    oid = cert.signature_algorithm_oid
    signature_algorithm = getattr(oid, "_name", None) or oid.dotted_string

    return CertificateInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        sans=tuple(
            [f"dns:{name}" for name in dns_sans] + [f"ip:{addr}" for addr in ip_sans]
        ),
        not_before=not_before.isoformat(),
        not_after=not_after.isoformat(),
        days_until_expiry=days,
        serial=format(cert.serial_number, "x").upper(),
        signature_algorithm=signature_algorithm,
        key_type=key_type,
        key_bits=key_bits,
        fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
        is_self_signed=cert.subject == cert.issuer,
    )


def match_hostname(target: TlsTarget, cert: x509.Certificate) -> bool:
    """RFC 6125 name matching: exact DNS-SAN, single left-most wildcard label, IP SANs.

    No CN fallback — a certificate without a matching SAN does not cover the target.
    """
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return False
    if target.is_ip:
        wanted = ipaddress.ip_address(target.host)
        return any(
            ip == wanted for ip in san_ext.value.get_values_for_type(x509.IPAddress)
        )
    host = target.host.lower()
    return any(
        _dns_name_matches(pattern.lower(), host)
        for pattern in san_ext.value.get_values_for_type(x509.DNSName)
    )


def _dns_name_matches(pattern: str, host: str) -> bool:
    pattern = pattern.rstrip(".")
    host = host.rstrip(".")
    if pattern == host:
        return True
    if not pattern.startswith("*."):
        return False
    pattern_labels = pattern.split(".")
    host_labels = host.split(".")
    # The wildcard covers exactly one left-most label; never the bare domain.
    if len(pattern_labels) != len(host_labels):
        return False
    return pattern_labels[1:] == host_labels[1:]


def _cert_sans_summary(leaf: CertificateInfo) -> str:
    return ", ".join(leaf.sans) if leaf.sans else "(none)"


def _classify_verify_error(
    errno: int, depth: int, leaf: CertificateInfo, chain_length: int
) -> tuple[FindingCode, str, str | None]:
    """Map one OpenSSL verify error (errno, depth) to a finding triple."""
    where = "certificate" if depth == 0 else f"chain certificate (depth {depth})"
    if errno == X509_V_ERR_CERT_HAS_EXPIRED:
        message = (
            f"{where} expired on {leaf.not_after}"
            if depth == 0
            else f"{where} has expired"
        )
        return FindingCode.EXPIRED, message, "renew the certificate"
    if errno == X509_V_ERR_CERT_NOT_YET_VALID:
        message = (
            f"{where} is not valid before {leaf.not_before}"
            if depth == 0
            else f"{where} is not yet valid"
        )
        return (
            FindingCode.NOT_YET_VALID,
            message,
            "check the server clock and the certificate's validity window",
        )
    if errno == X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT:
        return (
            FindingCode.SELF_SIGNED,
            "certificate is self-signed",
            "add it to a private CA bundle and pass --ca-file to trust it",
        )
    if errno == X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN:
        return (
            FindingCode.UNTRUSTED_CHAIN,
            "chain ends in a root that is not in the trust store",
            "pass the issuing root via --ca-file if this is a private PKI",
        )
    if errno in (
        X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT,
        X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY,
        X509_V_ERR_UNABLE_TO_VERIFY_LEAF_SIGNATURE,
    ):
        if chain_length <= 1 and not leaf.is_self_signed:
            return (
                FindingCode.INCOMPLETE_CHAIN,
                "server did not present the intermediate certificate(s)",
                "configure the server to serve the full chain",
            )
        return (
            FindingCode.UNTRUSTED_CHAIN,
            "certificate does not chain to a trusted authority",
            "pass the issuing root via --ca-file if this is a private PKI",
        )
    return (
        FindingCode.UNTRUSTED_CHAIN,
        f"chain validation failed (OpenSSL verify error {errno} at depth {depth})",
        None,
    )


def build_findings(
    target: TlsTarget,
    leaf: CertificateInfo,
    *,
    name_matched: bool,
    verify_errors: Sequence[tuple[int, int]],
    chain_length: int,
    tls_version: str | None,
    warn_days: int,
) -> tuple[ValidationFinding, ...]:
    """Turn the verify-callback errors + parsed leaf + match result into findings.

    Args:
        target: What was checked (drives the name-mismatch message).
        leaf: Parsed leaf certificate.
        name_matched: Result of :func:`match_hostname`.
        verify_errors: (errno, depth) pairs recorded during the handshake.
        chain_length: Number of certificates the server presented.
        tls_version: Negotiated protocol name.
        warn_days: Expiring-soon threshold (0 disables).
    """
    findings: list[ValidationFinding] = []
    seen: set[FindingCode] = set()

    def add(code: FindingCode, message: str, hint: str | None = None) -> None:
        if code not in seen:
            seen.add(code)
            findings.append(ValidationFinding(code=code, message=message, hint=hint))

    for errno, depth in verify_errors:
        add(*_classify_verify_error(errno, depth, leaf, chain_length))

    if not name_matched:
        if not leaf.sans:
            add(
                FindingCode.NO_SANS,
                "certificate has no subject alternative names "
                "(legacy CN-only certificates fail modern validation)",
                hint="reissue the certificate with SANs",
            )
        mode = "IP address" if target.is_ip else "hostname"
        add(
            FindingCode.NAME_MISMATCH,
            f"requested {mode} {target.host!r} is not covered; "
            f"certificate covers: {_cert_sans_summary(leaf)}",
            hint="check you are hitting the intended vhost (see --sni)",
        )

    if tls_version in _LEGACY_PROTOCOLS:
        add(
            FindingCode.LEGACY_PROTOCOL,
            f"negotiated {tls_version} is below TLS 1.2",
            hint="enable TLS 1.2+ on the server",
        )

    invalid = {
        FindingCode.EXPIRED,
        FindingCode.NOT_YET_VALID,
        FindingCode.NAME_MISMATCH,
        FindingCode.SELF_SIGNED,
        FindingCode.UNTRUSTED_CHAIN,
        FindingCode.INCOMPLETE_CHAIN,
        FindingCode.NO_SANS,
    }
    if (
        warn_days > 0
        and 0 <= leaf.days_until_expiry <= warn_days
        and not (seen & invalid)
    ):
        add(
            FindingCode.EXPIRING_SOON,
            f"certificate expires in {leaf.days_until_expiry} day(s) "
            f"(threshold {warn_days})",
            hint="schedule renewal",
        )

    return tuple(findings)
