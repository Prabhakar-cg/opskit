"""End-to-end TLS checks against in-process loopback servers (research R6).

Every SC-002 failure class is exercised deterministically: valid, expired, not-yet-valid,
wrong-name, self-signed, untrusted, incomplete chain, non-TLS service, refused port.
"""

from __future__ import annotations

import json
import socket

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.net.errors import ConnectRefused
from opskit.tls import CertificateInvalid, HandshakeError, check
from opskit.tls.models import FindingCode, TlsOutcome

runner = CliRunner()


@pytest.fixture
def root_ca(cert_factory):
    """Path to the root that issued the generated identities (the --ca-file)."""
    return cert_factory.valid.root_file


@pytest.fixture
def check_server(root_ca):
    """Run a check against a loopback server, trusting the generated root."""

    def _run(server, *, host="127.0.0.1", **kwargs):
        kwargs.setdefault("ca_file", root_ca)
        return check(f"{host}:{server.port}", timeout=5.0, **kwargs)

    return _run


def test_valid_chain_passes(tls_server, check_server):
    server = tls_server("valid")
    result = check_server(server)
    assert result.outcome is TlsOutcome.OK
    assert result.ok
    assert result.tls_version and result.tls_version.startswith("TLS")
    assert result.cipher
    assert result.leaf is not None
    assert any(san.startswith("ip:127.0.0.1") for san in result.leaf.sans)
    assert len(result.chain) == 2  # leaf + intermediate
    assert result.connection.address == "127.0.0.1"


def test_expired_cert_reports_and_keeps_details(tls_server, check_server):
    server = tls_server("expired")
    result = check_server(server)
    assert result.outcome is TlsOutcome.CERT_INVALID
    codes = {finding.code for finding in result.findings}
    assert FindingCode.EXPIRED in codes
    assert result.leaf is not None  # details shown despite failure (FR-006)
    assert result.leaf.days_until_expiry < 0


def test_not_yet_valid_distinct(tls_server, check_server):
    server = tls_server("not_yet_valid")
    result = check_server(server)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.NOT_YET_VALID in codes
    assert FindingCode.EXPIRED not in codes


def test_wrong_name_reports_requested_vs_covered(tls_server, check_server):
    server = tls_server("wrong_name")
    result = check_server(server)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.NAME_MISMATCH in codes
    mismatch = next(f for f in result.findings if f.code is FindingCode.NAME_MISMATCH)
    assert "127.0.0.1" in mismatch.message
    assert "otherhost.test" in mismatch.message


def test_no_san_cert_flagged(tls_server, check_server):
    server = tls_server("no_san")
    result = check_server(server)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.NO_SANS in codes
    assert FindingCode.NAME_MISMATCH in codes


def test_self_signed_distinct_from_untrusted(tls_server, check_server):
    server = tls_server("self_signed")
    result = check_server(server)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.SELF_SIGNED in codes
    assert FindingCode.UNTRUSTED_CHAIN not in codes


def test_untrusted_chain_when_root_not_trusted(tls_server, cert_factory, check_server):
    server = tls_server("valid")
    result = check_server(server, ca_file=cert_factory.other_root_file)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.UNTRUSTED_CHAIN in codes or FindingCode.INCOMPLETE_CHAIN in codes
    assert result.outcome is TlsOutcome.CERT_INVALID


def test_missing_intermediate_reported_incomplete(tls_server, check_server):
    server = tls_server("missing_intermediate")
    result = check_server(server)
    codes = {finding.code for finding in result.findings}
    assert FindingCode.INCOMPLETE_CHAIN in codes
    assert len(result.chain) == 1


def test_short_lived_cert_warns_expiring_soon(tls_server, check_server):
    server = tls_server("short_lived")
    result = check_server(server)  # default warn_days=30, cert expires in ~5 days
    assert result.outcome is TlsOutcome.EXPIRING_SOON
    warning = next(f for f in result.findings if f.code is FindingCode.EXPIRING_SOON)
    assert "day" in warning.message


def test_warn_days_zero_disables_warning(tls_server, check_server):
    server = tls_server("short_lived")
    result = check_server(server, warn_days=0)
    assert result.outcome is TlsOutcome.OK


def test_non_tls_service_hint(plain_tcp_server):
    host, port = plain_tcp_server
    with pytest.raises(HandshakeError) as excinfo:
        check(f"{host}:{port}", timeout=5.0)
    assert "TLS" in (excinfo.value.hint or "")


def test_refused_port(closed_port):
    with pytest.raises(ConnectRefused):
        check(f"127.0.0.1:{closed_port}", timeout=5.0)


def test_raise_on_invalid(tls_server, check_server):
    server = tls_server("expired")
    with pytest.raises(CertificateInvalid) as excinfo:
        check_server(server, raise_on_invalid=True)
    assert excinfo.value.findings


def test_ipv6_loopback_target(tls_server, check_server):
    try:
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        probe.bind(("::1", 0))
        probe.close()
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    server = tls_server("valid", host="::1")
    result = check_server(server, host="[::1]")
    assert result.outcome is TlsOutcome.OK
    assert result.connection.family == "ipv6"


def test_cli_end_to_end_exit_codes(tls_server, cert_factory):
    valid = tls_server("valid")
    expired = tls_server("expired")
    ca = str(cert_factory.valid.root_file)
    ok = runner.invoke(
        app, ["tls", "check", f"127.0.0.1:{valid.port}", "--ca-file", ca]
    )
    assert ok.exit_code == 0
    bad = runner.invoke(
        app, ["tls", "check", f"127.0.0.1:{expired.port}", "--ca-file", ca]
    )
    assert bad.exit_code == 10


def test_cli_json_batch_includes_failures(
    tls_server, cert_factory, tmp_path, closed_port
):
    valid = tls_server("valid")
    targets = tmp_path / "targets.txt"
    targets.write_text(f"127.0.0.1:{valid.port}\n127.0.0.1:{closed_port}\n")
    result = runner.invoke(
        app,
        [
            "tls",
            "check",
            "-i",
            str(targets),
            "--ca-file",
            str(cert_factory.valid.root_file),
            "--json",
        ],
    )
    assert result.exit_code == 7  # mixed outcomes -> PARTIAL
    payload = json.loads(result.stdout)
    assert len(payload) == 2
    by_state = {bool(e["result"]): e for e in payload}
    assert by_state[True]["result"]["outcome"] == "ok"
    assert by_state[False]["error"] is not None  # failed target present (Art. IX)
