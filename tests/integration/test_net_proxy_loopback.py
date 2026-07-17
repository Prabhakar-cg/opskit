"""End-to-end proxied checks against the in-process stand-in proxy (real sockets).

Covers every FR-009 outcome through the actual CLI (no API monkeypatching), the
mixed-batch contract (SC-005 shape), and proxied probes. Proxy-hop refused-vs-timeout
is asserted as the ProxyError class family / exit-code set, tolerant of the
platform variance CLAUDE.md documents (a closed loopback port refuses on Linux but
can time out on Windows).
"""

from __future__ import annotations

import contextlib
import json
import socket

import pytest
from typer.testing import CliRunner

from opskit.cli import app

runner = CliRunner()

PROXY_ENV_VARS = [
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
]


@pytest.fixture(autouse=True)
def clean_proxy_env(monkeypatch):
    for var in PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@contextlib.contextmanager
def _dead_tcp_port():
    """A loopback port with no TCP listener, reserved (via a UDP bind) for the
    duration so the number can't be recycled by a concurrent test's bind(0)."""
    guard = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    guard.bind(("127.0.0.1", 0))
    try:
        yield int(guard.getsockname()[1])
    finally:
        guard.close()


def _check(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["net", "check", *args], env={"NO_COLOR": "1"})
    return result.exit_code, result.output


class TestOutcomeMatrix:
    def test_open_via_proxy(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        code, output = _check(
            "internal.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 0
        assert "open" in output
        assert f"via {proxy.address} (flag)" in output
        assert "tunnel" in output

    def test_auth_required(self, scripted_proxy):
        proxy = scripted_proxy("auth")
        code, output = _check(
            "internal.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 14
        assert "requires authentication" in output

    def test_tunnel_denied(self, scripted_proxy):
        proxy = scripted_proxy("deny")
        code, output = _check(
            "blocked.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 18
        assert "denied the tunnel" in output

    def test_gateway_failure_502(self, scripted_proxy):
        proxy = scripted_proxy("bad-gateway")
        code, output = _check(
            "dead.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 19
        assert "unreachable from proxy" in output
        assert "proxy hop is healthy" in output

    def test_gateway_failure_504_target_silent(self, scripted_proxy):
        proxy = scripted_proxy("gateway-timeout")
        code, output = _check(
            "dead.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 19
        assert "did not answer the proxy" in output

    def test_not_a_proxy(self, scripted_proxy):
        proxy = scripted_proxy("garbage")
        code, output = _check(
            "internal.example:443", "--proxy", proxy.address, "--timeout", "2"
        )
        assert code == 20
        assert "does not behave like an HTTP proxy" in output

    def test_proxy_unreachable_class_family(self):
        # Nothing listens here: refused on Linux/macOS, may time out on Windows —
        # either way the failure is attributed to the proxy hop (exit 8 or 6).
        with _dead_tcp_port() as port:
            code, output = _check(
                "internal.example:443",
                "--proxy",
                f"127.0.0.1:{port}",
                "--timeout",
                "1",
                "--retries",
                "0",
            )
        assert code in (8, 6)
        assert "proxy" in output

    def test_proxy_unresolvable(self):
        code, output = _check(
            "internal.example:443",
            "--proxy",
            "no-such-proxy.invalid:3128",
            "--timeout",
            "2",
            "--retries",
            "0",
        )
        assert code == 3
        assert "opskit dns" in output


class TestBatchAndProbe:
    def test_mixed_batch_reports_every_target_with_route(
        self, scripted_proxy, monkeypatch
    ):
        proxy = scripted_proxy("tunnel")
        monkeypatch.setenv("HTTPS_PROXY", f"http://{proxy.address}")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        # Target 1+2 tunnel via env proxy; target 3 is exempt and checked directly
        # (it happens to be the stand-in proxy's own listening socket -> open).
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "a.internal.example:443",
                "b.internal.example:8443",
                f"127.0.0.1:{proxy.port}",
                "--timeout",
                "2",
                "--jsonl",
            ],
        )
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert len(lines) == 3
        assert [line["route"]["via"] for line in lines] == [
            "http-proxy",
            "http-proxy",
            "direct",
        ]
        assert lines[0]["route"]["source"] == "env:HTTPS_PROXY"
        assert lines[2]["route"]["source"] == "no-proxy-exemption"
        assert all(line["result"]["verdict"] == "open" for line in lines)
        assert result.exit_code == 0

    def test_mixed_failure_batch_partial_exit(self, scripted_proxy):
        deny = scripted_proxy("deny")
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "a.example:443",
                f"127.0.0.1:{deny.port}",
                "--proxy",
                deny.address,
                "--no-proxy",
                "127.0.0.1",
                "--timeout",
                "2",
                "--jsonl",
            ],
        )
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        # Target 1 denied via proxy (18); target 2 exempt-direct hits the stand-in
        # proxy socket and is open (0) -> aggregate is PARTIAL (7).
        assert result.exit_code == 7
        assert len(lines) == 2
        assert lines[0]["error"]["code"] == "proxy_tunnel_denied"
        assert lines[0]["route"]["via"] == "http-proxy"
        assert lines[1]["result"]["verdict"] == "open"
        assert lines[1]["route"]["source"] == "no-proxy-exemption"

    def test_uniform_denial_batch_exits_with_class(self, scripted_proxy):
        deny = scripted_proxy("deny")
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "a.example:443",
                "b.example:443",
                "--proxy",
                deny.address,
                "--timeout",
                "2",
                "--jsonl",
            ],
        )
        assert result.exit_code == 18
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert len(lines) == 2
        assert all(line["error"]["code"] == "proxy_tunnel_denied" for line in lines)

    def test_probe_via_proxy_fresh_tunnel_per_attempt(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        result = runner.invoke(
            app,
            [
                "net",
                "probe",
                "internal.example:443",
                "--proxy",
                proxy.address,
                "-c",
                "3",
                "--interval",
                "10ms",
                "--timeout",
                "2",
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["route"]["via"] == "http-proxy"
        assert payload["result"]["successes"] == 3
        assert len(proxy.requests) == 3  # one CONNECT per attempt (FR-012)
