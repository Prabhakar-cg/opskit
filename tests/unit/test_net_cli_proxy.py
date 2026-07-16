"""CLI tests for proxy resolution: flags, env fallback order, exemptions, route envelope."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.net import errors
from opskit.net.models import (
    CheckResult,
    ProxySpec,
    Route,
    Verdict,
    parse_proxy,
    parse_target,
)

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
    """Strip ambient proxy variables so tests control the environment fully."""
    for var in PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _open_result(raw, *, proxy=None, **kw):
    target = parse_target(raw, port=kw.get("port"))
    route = (
        Route.via_proxy(proxy, source="explicit")
        if isinstance(proxy, ProxySpec)
        else Route.direct()
    )
    return CheckResult(
        target=target,
        verdict=Verdict.OPEN,
        address="192.0.2.7",
        family="ipv4",
        port=target.port,
        time_ms=12.4,
        route=route,
    )


@pytest.fixture
def capture_check(monkeypatch):
    """Patch api.check in the CLI; record the proxy kwarg passed per call."""
    calls: list = []

    def fake(raw, **kw):
        calls.append(kw.get("proxy"))
        return _open_result(raw, proxy=kw.get("proxy"), port=kw.get("port"))

    monkeypatch.setattr("opskit.net.cli.api.check", fake)
    return calls


def _route(output: str) -> dict:
    return json.loads(output)["route"]


class TestProxyPrecedence:
    def test_flag_wins_over_env(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "db.example.com:5432",
                "--proxy",
                "flag.corp:3128",
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert capture_check[0] == parse_proxy("flag.corp:3128")
        assert _route(result.stdout) == {
            "via": "http-proxy",
            "proxy": "flag.corp:3128",
            "source": "flag",
        }

    @pytest.mark.parametrize(
        ("preset", "winner_var", "winner"),
        [
            (
                {"HTTPS_PROXY": "https-p.corp:1", "HTTP_PROXY": "http-p.corp:2"},
                "HTTPS_PROXY",
                "https-p.corp:1",
            ),
            (
                {"https_proxy": "lc-https.corp:1", "HTTP_PROXY": "http-p.corp:2"},
                "https_proxy",
                "lc-https.corp:1",
            ),
            (
                {"HTTP_PROXY": "http-p.corp:2", "ALL_PROXY": "all-p.corp:3"},
                "HTTP_PROXY",
                "http-p.corp:2",
            ),
            (
                {"http_proxy": "lc-http.corp:2", "all_proxy": "all-p.corp:3"},
                "http_proxy",
                "lc-http.corp:2",
            ),
            ({"ALL_PROXY": "all-p.corp:3"}, "ALL_PROXY", "all-p.corp:3"),
            ({"all_proxy": "lc-all.corp:3"}, "all_proxy", "lc-all.corp:3"),
        ],
    )
    def test_env_fallback_order(
        self, capture_check, monkeypatch, preset, winner_var, winner
    ):
        for var, value in preset.items():
            monkeypatch.setenv(var, value)
        result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--json"])
        assert result.exit_code == 0
        assert capture_check[0] == parse_proxy(winner)
        assert _route(result.stdout) == {
            "via": "http-proxy",
            "proxy": winner,
            "source": f"env:{winner_var}",
        }

    def test_no_proxy_anywhere_is_direct_default(self, capture_check):
        result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--json"])
        assert result.exit_code == 0
        assert capture_check[0] is None
        assert _route(result.stdout) == {
            "via": "direct",
            "proxy": None,
            "source": "default",
        }

    def test_direct_flag_overrides_env(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(
            app, ["net", "check", "db.example.com:5432", "--direct", "--json"]
        )
        assert result.exit_code == 0
        assert capture_check[0] is None
        assert _route(result.stdout)["via"] == "direct"
        assert _route(result.stdout)["source"] == "flag"

    def test_proxy_plus_direct_is_usage_error(self, capture_check):
        result = runner.invoke(
            app,
            ["net", "check", "db.example.com:5432", "--proxy", "p.corp:1", "--direct"],
        )
        assert result.exit_code == 2
        assert capture_check == []

    def test_invalid_env_proxy_names_the_variable(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "socks5://p.corp:1080")
        result = runner.invoke(app, ["net", "check", "db.example.com:5432"])
        assert result.exit_code == 2
        assert "HTTPS_PROXY" in result.output


class TestExemptions:
    def test_no_proxy_env_exempts_target(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        monkeypatch.setenv("NO_PROXY", "other.example,db.example.com")
        result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--json"])
        assert result.exit_code == 0
        assert capture_check[0] is None
        assert _route(result.stdout) == {
            "via": "direct",
            "proxy": None,
            "source": "no-proxy-exemption",
        }

    def test_no_proxy_flag_replaces_env_value(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        monkeypatch.setenv("NO_PROXY", "db.example.com")  # would exempt
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "db.example.com:5432",
                "--no-proxy",
                "unrelated.example",
                "--json",
            ],
        )
        assert result.exit_code == 0
        # The flag replaced the env list, so the target is NOT exempt anymore.
        assert capture_check[0] == parse_proxy("http://envproxy.corp:8080")
        assert _route(result.stdout)["via"] == "http-proxy"

    def test_suffix_exemption(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "db.internal.example.com:5432",
                "--no-proxy",
                ".example.com",
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert capture_check[0] is None
        assert _route(result.stdout)["source"] == "no-proxy-exemption"


class TestRouteEnvelope:
    def test_route_present_in_batch_envelopes_including_mixed(
        self, capture_check, monkeypatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        monkeypatch.setenv("NO_PROXY", "exempt.example")
        result = runner.invoke(
            app,
            ["net", "check", "a.example:443", "exempt.example:443", "--jsonl"],
        )
        assert result.exit_code == 0
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert len(lines) == 2
        assert lines[0]["route"]["via"] == "http-proxy"
        assert lines[0]["route"]["source"] == "env:HTTPS_PROXY"
        assert lines[1]["route"] == {
            "via": "direct",
            "proxy": None,
            "source": "no-proxy-exemption",
        }

    def test_direct_envelope_shape_unchanged_except_route(self, capture_check):
        result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--json"])
        payload = json.loads(result.stdout)
        assert set(payload.keys()) == {
            "schema_version",
            "command",
            "query",
            "result",
            "error",
            "elapsed_ms",
            "route",
        }
        assert payload["query"] == {
            "host": "db.example.com",
            "port": 5432,
            "protocol": "tcp",
            "family": None,
            "timeout": 5.0,
            "retries": 2,
        }

    def test_human_output_shows_via_line_when_proxied(self, capture_check):
        result = runner.invoke(
            app,
            ["net", "check", "db.example.com:5432", "--proxy", "p.corp:3128"],
            env={"NO_COLOR": "1"},
        )
        assert result.exit_code == 0
        assert "via p.corp:3128 (flag)" in result.stdout
        assert "tunnel" in result.stdout

    def test_human_output_direct_has_no_via_line(self, capture_check):
        result = runner.invoke(
            app, ["net", "check", "db.example.com:5432"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0
        assert "via " not in result.stdout
        assert "connected to 192.0.2.7" in result.stdout


# --- US2: exit codes and hop attribution per contract (contracts/cli.md) ---

PROXY_DISPLAY = "p.corp:3128"


def _raising_check(monkeypatch, error):
    def fake(raw, **kw):
        raise error

    monkeypatch.setattr("opskit.net.cli.api.check", fake)


class TestProxiedExitCodes:
    @pytest.mark.parametrize(
        ("error", "expected_exit"),
        [
            (
                errors.ProxyResolutionError(
                    f"cannot resolve proxy {PROXY_DISPLAY}", hint="h"
                ),
                3,
            ),
            (
                errors.ProxyConnectRefused(
                    f"cannot connect to proxy {PROXY_DISPLAY}", hint="h"
                ),
                8,
            ),
            (
                errors.ProxyConnectTimeout(
                    f"no response from proxy {PROXY_DISPLAY}", hint="h"
                ),
                6,
            ),
            (
                errors.ProxyAuthRequired(
                    f"proxy {PROXY_DISPLAY} requires authentication", hint="h"
                ),
                14,
            ),
            (
                errors.ProxyTunnelDenied(
                    f"proxy {PROXY_DISPLAY} denied the tunnel", hint="h"
                ),
                18,
            ),
            (
                errors.ProxyGatewayError(
                    f"target x:443 is unreachable from proxy {PROXY_DISPLAY}",
                    hint="the proxy hop is healthy",
                ),
                19,
            ),
            (
                errors.ProxyProtocolError(
                    f"{PROXY_DISPLAY} does not behave like an HTTP proxy", hint="h"
                ),
                20,
            ),
        ],
    )
    def test_each_outcome_exits_with_contract_code(
        self, monkeypatch, error, expected_exit
    ):
        _raising_check(monkeypatch, error)
        result = runner.invoke(
            app,
            ["net", "check", "internal.example:443", "--proxy", PROXY_DISPLAY],
        )
        assert result.exit_code == expected_exit
        assert error.message in result.output

    def test_proxy_hop_wording_names_the_proxy(self, monkeypatch):
        _raising_check(
            monkeypatch,
            errors.ProxyConnectRefused(
                f"cannot connect to proxy {PROXY_DISPLAY}: connection refused",
                hint="check the proxy address and port",
            ),
        )
        result = runner.invoke(
            app,
            ["net", "check", "internal.example:443", "--proxy", PROXY_DISPLAY],
        )
        assert "proxy" in result.output

    def test_gateway_wording_states_proxy_hop_healthy(self, monkeypatch):
        _raising_check(
            monkeypatch,
            errors.ProxyGatewayError(
                f"target internal.example:443 is unreachable from proxy "
                f"{PROXY_DISPLAY}: 502 Bad Gateway",
                hint="the proxy hop is healthy; the target may be down",
            ),
        )
        result = runner.invoke(
            app,
            ["net", "check", "internal.example:443", "--proxy", PROXY_DISPLAY],
        )
        assert result.exit_code == 19
        assert "proxy hop is healthy" in result.output

    def test_json_envelope_carries_error_code_and_route(self, monkeypatch):
        _raising_check(
            monkeypatch,
            errors.ProxyTunnelDenied(
                f"proxy {PROXY_DISPLAY} denied the tunnel to internal.example:443",
                hint="policy",
            ),
        )
        result = runner.invoke(
            app,
            [
                "net",
                "check",
                "internal.example:443",
                "--proxy",
                PROXY_DISPLAY,
                "--json",
            ],
        )
        assert result.exit_code == 18
        payload = json.loads(result.stdout)
        assert payload["result"] is None
        assert payload["error"]["code"] == "proxy_tunnel_denied"
        assert payload["route"] == {
            "via": "http-proxy",
            "proxy": PROXY_DISPLAY,
            "source": "flag",
        }
        assert payload["query"]["proxy"] == PROXY_DISPLAY


# --- US4: batch/probe/watch/UDP-guard contract compliance ---


class TestUdpGuard:
    def test_udp_plus_proxy_flag_is_usage_error(self, capture_check):
        result = runner.invoke(
            app,
            ["net", "check", "ntp.example:123", "--udp", "--proxy", "p.corp:3128"],
        )
        assert result.exit_code == 2
        assert "UDP" in result.output or "udp" in result.output.lower()
        assert capture_check == []  # rejected before any target ran

    def test_udp_plus_env_proxy_is_usage_error_with_direct_hint(
        self, capture_check, monkeypatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(app, ["net", "check", "ntp.example:123", "--udp"])
        assert result.exit_code == 2
        assert "--direct" in result.output
        assert capture_check == []

    def test_udp_plus_env_proxy_with_direct_works(self, capture_check, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(
            app, ["net", "check", "ntp.example:123", "--udp", "--direct", "--json"]
        )
        assert result.exit_code == 0
        assert capture_check[0] is None


class TestWatchSignatureRoute:
    def test_route_flip_flags_a_change(self):
        from opskit.net.cli import _check_signature

        result = _open_result("t:1")
        direct = [("t:1", result, None, Route.direct())]
        proxied = [
            (
                "t:1",
                result,
                None,
                Route.via_proxy(parse_proxy("p.corp:3128"), source="env:HTTPS_PROXY"),
            )
        ]
        assert _check_signature(direct) != _check_signature(proxied)


class TestProbeProxyCli:
    @pytest.fixture
    def capture_probe(self, monkeypatch):
        from opskit.net.models import ProbeAttempt, ProbeResult

        calls: list = []

        def fake(raw, **kw):
            calls.append(kw.get("proxy"))
            target = parse_target(raw, port=kw.get("port"))
            spec = kw.get("proxy")
            route = (
                Route.via_proxy(spec, source="explicit")
                if isinstance(spec, ProxySpec)
                else Route.direct()
            )
            attempts = tuple(
                ProbeAttempt(
                    index=i + 1,
                    verdict=Verdict.OPEN,
                    address="192.0.2.7",
                    family="ipv4",
                    time_ms=10.0,
                )
                for i in range(kw.get("count", 4))
            )
            on_attempt = kw.get("on_attempt")
            if on_attempt is not None:
                for attempt in attempts:
                    on_attempt(attempt)
            return ProbeResult(
                target=target,
                attempts=attempts,
                requested=len(attempts),
                completed=len(attempts),
                successes=len(attempts),
                failures=0,
                replies=0,
                closed_signals=0,
                silent=0,
                min_ms=10.0,
                avg_ms=10.0,
                max_ms=10.0,
                elapsed_ms=42.0,
                route=route,
            )

        monkeypatch.setattr("opskit.net.cli.api.probe", fake)
        return calls

    def test_probe_passes_proxy_and_envelope_has_route(self, capture_probe):
        result = runner.invoke(
            app,
            [
                "net",
                "probe",
                "api.example.com:443",
                "--proxy",
                "p.corp:3128",
                "-c",
                "2",
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert capture_probe[0] == parse_proxy("p.corp:3128")
        payload = json.loads(result.stdout)
        assert payload["route"] == {
            "via": "http-proxy",
            "proxy": "p.corp:3128",
            "source": "flag",
        }
        assert payload["query"]["proxy"] == "p.corp:3128"

    def test_probe_jsonl_attempt_and_summary_envelopes_carry_route(
        self, capture_probe, monkeypatch
    ):
        monkeypatch.setenv("HTTPS_PROXY", "http://envproxy.corp:8080")
        result = runner.invoke(
            app,
            ["net", "probe", "api.example.com:443", "-c", "2", "--jsonl"],
        )
        assert result.exit_code == 0
        lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
        assert len(lines) == 3  # 2 attempts + summary
        assert all(line["route"]["source"] == "env:HTTPS_PROXY" for line in lines)

    def test_probe_direct_default_unchanged(self, capture_probe):
        result = runner.invoke(
            app, ["net", "probe", "api.example.com:443", "-c", "1", "--json"]
        )
        assert result.exit_code == 0
        assert capture_probe[0] is None
        payload = json.loads(result.stdout)
        assert payload["route"] == {
            "via": "direct",
            "proxy": None,
            "source": "default",
        }

    def test_probe_udp_plus_proxy_usage_error(self, capture_probe):
        result = runner.invoke(
            app,
            [
                "net",
                "probe",
                "dns.example.com:53",
                "--udp",
                "--proxy",
                "p.corp:3128",
            ],
        )
        assert result.exit_code == 2
        assert capture_probe == []
