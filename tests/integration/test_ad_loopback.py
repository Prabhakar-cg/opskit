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
