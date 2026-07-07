"""Tests for opskit.tls.api.check() with injected connect/handshake fakes."""

from __future__ import annotations

import socket

import pytest

from opskit.core.errors import UsageError
from opskit.core.exit_codes import ExitCode
from opskit.net import TcpConnection
from opskit.net.errors import ConnectRefused, ConnectTimeout, ResolutionError
from opskit.tls import (
    CertificateExpiring,
    CertificateInvalid,
    check,
)
from opskit.tls.handshake import HandshakeOutcome
from opskit.tls.inspect import X509_V_ERR_CERT_HAS_EXPIRED
from opskit.tls.models import FindingCode, TlsOutcome


@pytest.fixture
def fake_stack(monkeypatch, make_cert):
    """Patch net_connect + perform_handshake; returns a config the fakes read."""
    config = {
        "cert": make_cert("unit.test", dns_names=("unit.test",), days=200),
        "verify_errors": (),
        "tls_version": "TLSv1.3",
        "cipher": "TLS_AES_256_GCM_SHA384",
        "sni_seen": [],
    }

    def fake_connect(host, port, *, timeout, retries):
        left, right = socket.socketpair()
        right.close()
        return left, TcpConnection(
            address="192.0.2.1", family="ipv4", port=port, connect_ms=1.0
        )

    def fake_handshake(sock, *, server_name, timeout, ca_file=None):
        config["sni_seen"].append(server_name)
        return HandshakeOutcome(
            chain=(config["cert"],),
            tls_version=config["tls_version"],
            cipher=config["cipher"],
            verify_errors=config["verify_errors"],
        )

    monkeypatch.setattr("opskit.tls.api.net_connect", fake_connect)
    monkeypatch.setattr("opskit.tls.api.perform_handshake", fake_handshake)
    return config


def test_ok_result_populates_everything(fake_stack):
    result = check("unit.test")
    assert result.outcome is TlsOutcome.OK
    assert result.ok and bool(result)
    assert result.tls_version == "TLSv1.3"
    assert result.cipher
    assert result.leaf is not None and result.leaf.days_until_expiry > 100
    assert result.connection.address == "192.0.2.1"
    assert result.elapsed_ms >= 0
    assert fake_stack["sni_seen"] == ["unit.test"]


def test_verify_error_yields_cert_invalid(fake_stack):
    fake_stack["verify_errors"] = ((X509_V_ERR_CERT_HAS_EXPIRED, 0),)
    result = check("unit.test")
    assert result.outcome is TlsOutcome.CERT_INVALID
    assert result.leaf is not None  # details preserved (FR-006)
    assert {f.code for f in result.findings} == {FindingCode.EXPIRED}


def test_name_mismatch_detected(fake_stack, make_cert):
    fake_stack["cert"] = make_cert("other.test", dns_names=("other.test",))
    result = check("unit.test")
    assert result.outcome is TlsOutcome.CERT_INVALID
    assert FindingCode.NAME_MISMATCH in {f.code for f in result.findings}


def test_ip_target_omits_sni_and_matches_ip_san(fake_stack, make_cert):
    fake_stack["cert"] = make_cert("ip.test", dns_names=(), ip_sans=("192.0.2.1",))
    result = check("192.0.2.1")
    assert fake_stack["sni_seen"] == [None]
    assert result.outcome is TlsOutcome.OK
    assert result.target.is_ip


def test_sni_override_forwarded_and_validated(fake_stack, make_cert):
    # With --sni, validation targets the SNI name (the vhost identity being checked),
    # so connecting by one name/IP while verifying another works as intended.
    fake_stack["cert"] = make_cert("split.test", dns_names=("split.test",))
    result = check("unit.test", server_name="split.test")
    assert fake_stack["sni_seen"] == ["split.test"]
    assert result.outcome is TlsOutcome.OK


def test_expiring_soon_outcome_and_raise(fake_stack, make_cert):
    fake_stack["cert"] = make_cert("unit.test", dns_names=("unit.test",), days=5)
    result = check("unit.test")
    assert result.outcome is TlsOutcome.EXPIRING_SOON
    with pytest.raises(CertificateExpiring) as excinfo:
        check("unit.test", raise_on_invalid=True)
    assert excinfo.value.days_remaining <= 5
    assert check("unit.test", warn_days=0).outcome is TlsOutcome.OK


def test_raise_on_invalid_carries_findings(fake_stack):
    fake_stack["verify_errors"] = ((X509_V_ERR_CERT_HAS_EXPIRED, 0),)
    with pytest.raises(CertificateInvalid) as excinfo:
        check("unit.test", raise_on_invalid=True)
    assert excinfo.value.findings
    assert excinfo.value.exit_code is ExitCode.CERT_INVALID


def test_empty_chain_raises(fake_stack, monkeypatch):
    def empty_handshake(sock, *, server_name, timeout, ca_file=None):
        return HandshakeOutcome(
            chain=(), tls_version="TLSv1.3", cipher=None, verify_errors=()
        )

    monkeypatch.setattr("opskit.tls.api.perform_handshake", empty_handshake)
    with pytest.raises(CertificateInvalid):
        check("unit.test")


@pytest.mark.parametrize(
    "kwargs",
    [{"timeout": 0}, {"retries": -1}, {"warn_days": -1}],
)
def test_bad_controls_rejected_before_network(kwargs, monkeypatch):
    def boom(*args, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("network reached with invalid controls")

    monkeypatch.setattr("opskit.tls.api.net_connect", boom)
    with pytest.raises(UsageError):
        check("unit.test", **kwargs)


def test_connect_layer_errors_propagate(monkeypatch):
    for error in (
        ResolutionError("nope"),
        ConnectRefused("refused"),
        ConnectTimeout("slow"),
    ):

        def fail_connect(host, port, *, timeout, retries, _error=error):
            raise _error

        monkeypatch.setattr("opskit.tls.api.net_connect", fail_connect)
        with pytest.raises(type(error)):
            check("unit.test")


def test_socket_closed_even_when_handshake_raises(monkeypatch, make_cert):
    closed = []

    class TrackingSocket:
        def close(self):
            closed.append(True)

    def fake_connect(host, port, *, timeout, retries):
        return TrackingSocket(), TcpConnection(
            address="192.0.2.1", family="ipv4", port=port, connect_ms=1.0
        )

    def fail_handshake(sock, *, server_name, timeout, ca_file=None):
        raise ConnectTimeout("handshake timed out")

    monkeypatch.setattr("opskit.tls.api.net_connect", fake_connect)
    monkeypatch.setattr("opskit.tls.api.perform_handshake", fail_handshake)
    with pytest.raises(ConnectTimeout):
        check("unit.test")
    assert closed == [True]


def test_contract_example_shape(fake_stack):
    """The python-api.md example fields all exist and behave as documented."""
    result = check("unit.test:443", warn_days=14)
    assert result.outcome.value == "ok"
    assert result.tls_version and result.cipher
    assert result.leaf.subject and result.leaf.days_until_expiry
    for cert in result.chain:
        assert cert.subject and cert.issuer
    assert result.findings == ()
    assert result.to_dict()["outcome"] == "ok"
