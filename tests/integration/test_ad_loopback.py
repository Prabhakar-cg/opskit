"""Loopback socket/TLS stage classification for `ad check` (real sockets, no network).

Cross-OS rule (CLAUDE.md): a closed loopback port *refuses* on Linux/macOS but can
*time out* on Windows — assert the NetError **class family**, never one subclass.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.ad import api
from opskit.cli import app
from opskit.net.errors import ConnectRefused, ConnectTimeout, NetError
from opskit.tls.errors import CertificateInvalid

runner = CliRunner()


class TestReachStage:
    def test_closed_port_is_connect_class_family(self, closed_port):
        with pytest.raises((ConnectRefused, ConnectTimeout)) as excinfo:
            api.check(server=f"127.0.0.1:{closed_port}", timeout=2.0)
        assert isinstance(excinfo.value, NetError)
        assert excinfo.value.hint is not None

    def test_cli_exit_code_matches_class(self, closed_port):
        result = runner.invoke(
            app, ["ad", "check", f"127.0.0.1:{closed_port}", "--timeout", "2"]
        )
        assert result.exit_code in (6, 8)  # timeout vs refused: platform-dependent
        assert "credential" not in result.output.lower()  # never blames credentials


class TestSecureStage:
    def test_self_signed_certificate_is_cert_invalid(self, tls_server):
        server = tls_server("self_signed")
        with pytest.raises(CertificateInvalid) as excinfo:
            api.check(server=f"{server.host}:{server.port}", timeout=3.0)
        assert "opskit tls check" in str(excinfo.value.hint)

    def test_cli_exit_ten_with_tls_hint(self, tls_server):
        server = tls_server("self_signed")
        result = runner.invoke(
            app,
            [
                "ad",
                "check",
                f"{server.host}:{server.port}",
                "--timeout",
                "3",
                "--json",
            ],
        )
        assert result.exit_code == 10
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "cert_invalid"
        assert "opskit tls" in payload["error"]["hint"]


class TestServerUrlScheme:
    """`--server ldap(s)://host:port` must reach the real network, not just parse."""

    def test_ldaps_scheme_reaches_real_tls_stage(self, tls_server):
        """A scheme-prefixed --server still gets far enough to hit the cert check."""
        server = tls_server("self_signed")
        with pytest.raises(CertificateInvalid):
            api.check(server=f"ldaps://{server.host}:{server.port}", timeout=3.0)

    def test_ldap_scheme_with_closed_port_is_connect_class_family(self, closed_port):
        """A scheme-prefixed --server on a dead port still classifies as reach-stage."""
        with pytest.raises((ConnectRefused, ConnectTimeout)):
            api.check(server=f"ldap://127.0.0.1:{closed_port}", timeout=2.0)

    def test_scheme_via_cli(self, closed_port):
        result = runner.invoke(
            app,
            ["ad", "check", f"ldap://127.0.0.1:{closed_port}", "--timeout", "2"],
        )
        assert result.exit_code in (6, 8)  # timeout vs refused: platform-dependent


class TestDiscoveryHintOverRealSocket:
    """The retry hint from a discovery-picked DC, exercised over a real TLS socket."""

    def test_discovered_self_signed_cert_gets_ca_file_hint(
        self, tls_server, monkeypatch
    ):
        server = tls_server("self_signed")
        monkeypatch.setattr(
            "opskit.ad.api.discovery.discover_dcs",
            lambda domain, timeout: [server.host],
        )
        with pytest.raises(CertificateInvalid) as excinfo:
            api.check(domain="corp.example.com", port=server.port, timeout=3.0)
        # both the original tls-check pointer and the new discovery-retry guidance
        assert "opskit tls check" in excinfo.value.hint
        assert "retry with --server" in excinfo.value.hint

    def test_explicit_server_gets_no_discovery_hint_over_real_socket(self, tls_server):
        """Same failing DC, but named explicitly: no discovery-retry text appended."""
        server = tls_server("self_signed")
        with pytest.raises(CertificateInvalid) as excinfo:
            api.check(server=f"{server.host}:{server.port}", timeout=3.0)
        assert "retry with --server" not in excinfo.value.hint
