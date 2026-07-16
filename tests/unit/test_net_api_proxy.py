"""Unit tests for check()/probe() with an explicit proxy (API layer, Art. VII)."""

from __future__ import annotations

import pytest

from opskit.core.errors import UsageError
from opskit.net import api
from opskit.net.models import Protocol, Verdict, parse_proxy


class TestCheckViaProxy:
    def test_open_via_proxy_stamps_route_and_hop_details(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        result = api.check(
            "internal.example:443", proxy=proxy.address, timeout=2.0, retries=0
        )
        assert result.verdict is Verdict.OPEN
        assert result.route.via == "http-proxy"
        assert result.route.proxy == proxy.address
        assert result.address == "127.0.0.1"  # the proxy hop the tool connected to
        assert result.family == "ipv4"
        assert result.port == 443
        assert result.time_ms > 0

    def test_accepts_prebuilt_proxyspec(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        spec = parse_proxy(proxy.address)
        result = api.check("internal.example:443", proxy=spec, timeout=2.0, retries=0)
        assert result.route.via == "http-proxy"

    def test_direct_default_route(self, scripted_proxy, monkeypatch):
        # A running proxy + proxy env vars must NOT affect an explicit-args library
        # call: the library never reads the environment (FR-005/FR-020).
        proxy = scripted_proxy("tunnel")
        monkeypatch.setenv("HTTPS_PROXY", f"http://{proxy.address}")
        monkeypatch.setenv("HTTP_PROXY", f"http://{proxy.address}")

        # Spy on the direct-connect seam to prove check() takes the direct path.
        from opskit.net import tcp

        calls = []
        real_connect = tcp.connect

        def spying_connect(host, port, **kwargs):
            calls.append((host, port))
            return real_connect(host, port, **kwargs)

        monkeypatch.setattr("opskit.net.api.tcp.connect", spying_connect)
        result = api.check(proxy.address, timeout=2.0, retries=0)  # proxy IS the target
        assert result.route.to_dict() == {
            "via": "direct",
            "proxy": None,
            "source": "default",
        }
        assert calls == [("127.0.0.1", proxy.port)]
        assert proxy.requests == []  # no CONNECT was ever sent

    def test_udp_with_proxy_is_usage_error_before_io(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        with pytest.raises(UsageError) as excinfo:
            api.check(
                "ntp.example:123",
                protocol=Protocol.UDP,
                proxy=proxy.address,
                timeout=1.0,
            )
        assert "UDP" in excinfo.value.message or "udp" in excinfo.value.message
        assert proxy.requests == []  # rejected pre-flight, no network I/O

    def test_invalid_proxy_string_is_usage_error(self):
        with pytest.raises(UsageError):
            api.check("host.example:443", proxy="socks5://p.example:1080")

    def test_udp_with_env_vars_only_is_not_affected(self, scripted_proxy, monkeypatch):
        # Env vars alone never route the library: UDP without an explicit proxy
        # keeps working even when HTTPS_PROXY is set (Art. VII).
        proxy = scripted_proxy("tunnel")
        monkeypatch.setenv("HTTPS_PROXY", f"http://{proxy.address}")
        from opskit.net.errors import NetError

        with pytest.raises(NetError):
            # Loopback UDP to a fresh port: closed or inconclusive — but never a
            # UsageError, proving the env proxy was not consulted.
            api.check(
                "127.0.0.1:9",
                protocol=Protocol.UDP,
                timeout=0.3,
                retries=0,
            )
        assert proxy.requests == []

    def test_family_restriction_applies_to_proxy_hop(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        # The stand-in proxy is IPv4-only loopback. Restricting the check to ipv6
        # must constrain the PROXY hop: either resolution fails (no ipv6 route to
        # the proxy) or the platform offers a v4-mapped ipv6 path — in which case
        # the connection itself must have been made in the ipv6 family.
        from opskit.net.errors import ProxyResolutionError

        try:
            result = api.check(
                "internal.example:443",
                proxy=proxy.address,
                family="ipv6",
                timeout=2.0,
                retries=0,
            )
        except ProxyResolutionError:
            assert proxy.requests == []  # never reached the proxy, let alone the target
        else:
            assert result.family == "ipv6"  # v4-mapped: the restriction still applied
            assert len(proxy.requests) == 1
            assert proxy.connect_line().startswith("CONNECT internal.example:443")


class TestProbeViaProxy:
    def test_fresh_tunnel_per_attempt_with_tunnel_timings(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        result = api.probe(
            "internal.example:443",
            proxy=proxy.address,
            count=3,
            interval=0.0,
            timeout=2.0,
        )
        assert result.route.via == "http-proxy"
        assert result.completed == 3
        assert result.successes == 3
        assert len(proxy.requests) == 3  # one CONNECT per attempt (FR-012)
        assert all(a.time_ms is not None and a.time_ms > 0 for a in result.attempts)
        assert result.min_ms is not None

    def test_unresolvable_proxy_fails_preflight_before_first_attempt(self):
        from opskit.net.errors import ProxyResolutionError

        with pytest.raises(ProxyResolutionError):
            api.probe(
                "internal.example:443",
                proxy="no-such-proxy.invalid:3128",
                count=3,
                interval=0.0,
                timeout=2.0,
            )

    def test_denied_attempts_are_data_with_new_verdict(self, scripted_proxy):
        proxy = scripted_proxy("deny")
        result = api.probe(
            "blocked.example:443",
            proxy=proxy.address,
            count=2,
            interval=0.0,
            timeout=2.0,
        )
        assert result.completed == 2  # failures never abort the run
        assert result.failures == 2
        assert all(a.verdict is Verdict.TUNNEL_DENIED for a in result.attempts)
        assert all(a.error is not None for a in result.attempts)

    def test_udp_probe_with_proxy_is_usage_error(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        with pytest.raises(UsageError):
            api.probe(
                "ntp.example:123",
                protocol=Protocol.UDP,
                proxy=proxy.address,
                count=1,
            )
        assert proxy.requests == []


# --- US5: public API surface (contracts/python-api.md) ---


class TestPublicSurface:
    def test_all_new_names_importable_from_opskit_net(self):
        from opskit.net import (  # noqa: F401
            ProxyAuthRequired,
            ProxyConnectRefused,
            ProxyConnectTimeout,
            ProxyError,
            ProxyGatewayError,
            ProxyProtocolError,
            ProxyResolutionError,
            ProxySpec,
            ProxyTunnelDenied,
            Route,
            TunnelConnection,
            connect_via_proxy,
            parse_proxy,
            proxy_exempt,
        )

    def test_proxy_error_catches_every_hop_failure(self, scripted_proxy):
        from opskit import net

        proxy = scripted_proxy("deny")
        try:
            net.check(
                "blocked.example:443", proxy=proxy.address, timeout=2.0, retries=0
            )
        except net.ProxyError as exc:
            assert isinstance(exc, net.ProxyTunnelDenied)
        else:
            pytest.fail("expected a ProxyError")

    def test_gateway_error_separable_as_target_side(self, scripted_proxy):
        from opskit import net

        proxy = scripted_proxy("bad-gateway")
        with pytest.raises(net.ProxyGatewayError) as excinfo:
            net.check("dead.example:443", proxy=proxy.address, timeout=2.0, retries=0)
        assert excinfo.value.exit_code == 19

    def test_existing_positional_call_shapes_unaffected(self, scripted_proxy):
        # proxy is keyword-only with a None default: pre-feature call shapes work.
        proxy = scripted_proxy("tunnel")
        result = api.check(f"127.0.0.1:{proxy.port}", timeout=2.0, retries=0)
        assert result.verdict is Verdict.OPEN
        assert result.route.via == "direct"

    def test_library_never_prints(self, scripted_proxy, capsys):
        proxy = scripted_proxy("deny")
        with pytest.raises(Exception):  # noqa: B017 - any typed error; output is the point
            api.check(
                "blocked.example:443", proxy=proxy.address, timeout=2.0, retries=0
            )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
