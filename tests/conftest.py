"""Shared test fixtures."""

from __future__ import annotations

import datetime
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from opskit.dns.models import DnsRecord, RecordType


class MockResolver:
    """A resolver stub: preset records per type, a global error, or per-type errors."""

    def __init__(self, records=None, error=None, errors=None):
        self._records: dict[RecordType, list[DnsRecord]] = records or {}
        self._error = error
        self._errors = errors or {}

    def query(self, name, rtype, *, server, transport, timeout, retries, port):
        if self._error is not None:
            raise self._error
        if rtype in self._errors:
            raise self._errors[rtype]
        return tuple(self._records.get(rtype, ()))


@pytest.fixture
def make_resolver():
    """Return a factory building a MockResolver from records/error/errors."""

    def _make(records=None, error=None, errors=None):
        return MockResolver(records=records, error=error, errors=errors)

    return _make


@pytest.fixture
def make_cert():
    """Return a factory building a self-signed x509.Certificate for unit tests.

    Generated at runtime (nothing committed); dns_names/ip_sans control the SANs,
    days shifts the validity window (negative -> already expired).
    """

    def _make(
        common_name="unit.test",
        *,
        dns_names=("unit.test",),
        ip_sans=(),
        days=365,
        not_before_days=-1,
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now + datetime.timedelta(days=not_before_days))
            .not_valid_after(now + datetime.timedelta(days=days))
        )
        sans = [x509.DNSName(dns) for dns in dns_names] + [
            x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_sans
        ]
        if sans:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(sans), critical=False
            )
        return builder.sign(key, hashes.SHA256())

    return _make
