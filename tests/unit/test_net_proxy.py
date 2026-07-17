"""Unit tests for the CONNECT tunnel primitive against the scripted stand-in proxy.

US1 scope: happy path + proxy-hop refused/timeout normalization and retry behavior.
US2 extends this file with the full non-2xx classification matrix (research R4).
"""

from __future__ import annotations

import contextlib
import socket

import pytest

from opskit.net.errors import (
    ProxyAuthRequired,
    ProxyConnectRefused,
    ProxyConnectTimeout,
    ProxyError,
    ProxyGatewayError,
    ProxyProtocolError,
    ProxyResolutionError,
    ProxyTunnelDenied,
)
from opskit.net.models import parse_proxy
from opskit.net.proxy import TunnelConnection, connect_via_proxy


def _spec(proxy):
    return parse_proxy(proxy.address)


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


class TestTunnelEstablished:
    def test_returns_socket_and_connection(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        sock, conn = connect_via_proxy(
            _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
        )
        sock.close()
        assert isinstance(conn, TunnelConnection)
        assert conn.proxy_address == "127.0.0.1"
        assert conn.family == "ipv4"
        assert conn.port == 443
        assert conn.tunnel_ms > 0

    def test_sends_connect_request_line_and_host_header(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        sock, _ = connect_via_proxy(
            _spec(proxy), "internal.example", 8443, timeout=2.0, retries=0
        )
        sock.close()
        assert proxy.connect_line() == "CONNECT internal.example:8443 HTTP/1.1"
        assert proxy.header("Host") == "internal.example:8443"
        assert proxy.header("Proxy-Authorization") is None

    def test_ipv6_target_bracketed_in_request(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        sock, _ = connect_via_proxy(
            _spec(proxy), "2001:db8::7", 443, timeout=2.0, retries=0
        )
        sock.close()
        assert proxy.connect_line() == "CONNECT [2001:db8::7]:443 HTTP/1.1"

    def test_nothing_sent_through_tunnel(self, scripted_proxy):
        proxy = scripted_proxy("tunnel")
        sock, _ = connect_via_proxy(
            _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
        )
        sock.close()
        head = proxy.requests[0]
        assert head.startswith("CONNECT ")
        assert len(proxy.requests) == 1  # exactly one request, nothing after 200


class TestProxyHopFailures:
    def test_refused_proxy_raises_proxy_connect_refused(self):
        with _dead_tcp_port() as port:
            spec = parse_proxy(f"127.0.0.1:{port}")
            with pytest.raises((ProxyConnectRefused, ProxyConnectTimeout)) as excinfo:
                # A closed loopback port refuses on Linux/macOS but can time out on
                # Windows — assert the ProxyError class family (CLAUDE.md rule).
                connect_via_proxy(spec, "internal.example", 443, timeout=1.0, retries=0)
        assert isinstance(excinfo.value, ProxyError)
        assert "proxy" in excinfo.value.message

    def test_unresolvable_proxy_raises_proxy_resolution_error(self):
        spec = parse_proxy("no-such-proxy.invalid:3128")
        with pytest.raises(ProxyResolutionError) as excinfo:
            connect_via_proxy(spec, "internal.example", 443, timeout=2.0, retries=0)
        assert "cannot resolve proxy" in excinfo.value.message
        assert excinfo.value.hint is not None

    def test_silent_proxy_times_out_and_is_attributed(self, scripted_proxy):
        proxy = scripted_proxy("silent")
        with pytest.raises(ProxyConnectTimeout) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=0.5, retries=0
            )
        assert "proxy" in excinfo.value.message

    def test_silence_is_retried(self, scripted_proxy):
        proxy = scripted_proxy("silent")
        with pytest.raises(ProxyConnectTimeout):
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=0.4, retries=2
            )
        assert len(proxy.requests) == 3  # initial attempt + 2 retries


# --- US2: full CONNECT status classification (research R4 table) ---


class TestConnectClassification:
    def test_407_raises_auth_required_naming_schemes(self, scripted_proxy):
        proxy = scripted_proxy("auth", schemes=('Basic realm="corp"',))
        with pytest.raises(ProxyAuthRequired) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
            )
        assert "requires authentication" in excinfo.value.message
        assert "user:pass@" in (excinfo.value.hint or "")

    def test_407_with_only_negotiate_names_unsupported_scheme(self, scripted_proxy):
        proxy = scripted_proxy("auth", schemes=("Negotiate", "NTLM"))
        with pytest.raises(ProxyAuthRequired) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
            )
        assert "unsupported authentication method" in excinfo.value.message
        assert "Negotiate" in excinfo.value.message
        assert "Basic" in (excinfo.value.hint or "")

    @pytest.mark.parametrize("behavior", ["deny"])
    def test_4xx_raises_tunnel_denied(self, scripted_proxy, behavior):
        proxy = scripted_proxy(behavior)
        with pytest.raises(ProxyTunnelDenied) as excinfo:
            connect_via_proxy(
                _spec(proxy), "blocked.example", 443, timeout=2.0, retries=0
            )
        assert "denied the tunnel" in excinfo.value.message
        assert "policy" in (excinfo.value.hint or "")

    def test_504_raises_gateway_error_target_silent_flavor(self, scripted_proxy):
        proxy = scripted_proxy("gateway-timeout")
        with pytest.raises(ProxyGatewayError) as excinfo:
            connect_via_proxy(_spec(proxy), "dead.example", 443, timeout=2.0, retries=0)
        assert "did not answer the proxy" in excinfo.value.message
        assert "proxy hop is healthy" in (excinfo.value.hint or "")

    @pytest.mark.parametrize("behavior", ["bad-gateway", "unavailable"])
    def test_other_5xx_raises_gateway_error_unreachable_flavor(
        self, scripted_proxy, behavior
    ):
        proxy = scripted_proxy(behavior)
        with pytest.raises(ProxyGatewayError) as excinfo:
            connect_via_proxy(_spec(proxy), "dead.example", 443, timeout=2.0, retries=0)
        assert "unreachable from proxy" in excinfo.value.message
        assert "proxy hop is healthy" in (excinfo.value.hint or "")

    def test_truncated_success_head_is_protocol_error(self, scripted_proxy):
        # "HTTP/1.1 200 OK" then EOF before the head terminator: the connection is
        # already closed, so reporting OPEN would be a lie — protocol error.
        proxy = scripted_proxy("truncated-ok")
        with pytest.raises(ProxyProtocolError) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
            )
        assert "before completing the tunnel response" in excinfo.value.message

    def test_garbage_response_raises_protocol_error(self, scripted_proxy):
        proxy = scripted_proxy("garbage")
        with pytest.raises(ProxyProtocolError) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
            )
        assert "does not behave like an HTTP proxy" in excinfo.value.message

    def test_close_without_response_raises_protocol_error(self, scripted_proxy):
        proxy = scripted_proxy("close")
        with pytest.raises(ProxyProtocolError) as excinfo:
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=0
            )
        assert "without a response" in excinfo.value.message

    @pytest.mark.parametrize(
        "behavior", ["auth", "deny", "bad-gateway", "gateway-timeout", "garbage"]
    )
    def test_definitive_answers_are_not_retried(self, scripted_proxy, behavior):
        proxy = scripted_proxy(behavior)
        with pytest.raises(ProxyError):
            connect_via_proxy(
                _spec(proxy), "internal.example", 443, timeout=2.0, retries=3
            )
        assert len(proxy.requests) == 1  # one attempt only — the answer is definitive

    def test_407_after_wrong_credentials_says_rejected(self, scripted_proxy):
        proxy = scripted_proxy("tunnel", auth=("svc", "right-password"))
        spec = parse_proxy(f"http://svc:wrong-password@{proxy.address}")
        with pytest.raises(ProxyAuthRequired) as excinfo:
            connect_via_proxy(spec, "internal.example", 443, timeout=2.0, retries=0)
        assert "rejected the supplied credentials" in excinfo.value.message


# --- US3: authentication passthrough (Basic) ---


class TestProxyAuthentication:
    def test_correct_basic_header_sent_and_tunnel_established(self, scripted_proxy):
        proxy = scripted_proxy("tunnel", auth=("svc", "s3cret-pw"))
        spec = parse_proxy(f"http://svc:s3cret-pw@{proxy.address}")
        sock, _ = connect_via_proxy(
            spec, "internal.example", 443, timeout=2.0, retries=0
        )
        sock.close()
        import base64

        expected = base64.b64encode(b"svc:s3cret-pw").decode("ascii")
        assert proxy.header("Proxy-Authorization") == f"Basic {expected}"

    def test_percent_encoded_utf8_credentials_decoded_before_encoding(
        self, scripted_proxy
    ):
        # user "svc@corp", password "pä:ss" — percent-encoded in the spec, sent
        # as UTF-8 in the Basic token.
        proxy = scripted_proxy("tunnel", auth=("svc@corp", "pä:ss"))
        spec = parse_proxy(f"http://svc%40corp:p%C3%A4%3Ass@{proxy.address}")
        sock, _ = connect_via_proxy(
            spec, "internal.example", 443, timeout=2.0, retries=0
        )
        sock.close()
        import base64

        expected = base64.b64encode("svc@corp:pä:ss".encode()).decode("ascii")
        assert proxy.header("Proxy-Authorization") == f"Basic {expected}"

    def test_messages_never_leak_credentials(self, scripted_proxy):
        secret = "sup3r-secret-pw"
        proxy = scripted_proxy("deny")
        from opskit.net.models import ProxySpec

        spec = ProxySpec(
            host=proxy.host, port=proxy.port, username="svc", password=secret
        )
        with pytest.raises(ProxyTunnelDenied) as excinfo:
            connect_via_proxy(spec, "blocked.example", 443, timeout=2.0, retries=0)
        rendered = excinfo.value.message + (excinfo.value.hint or "")
        assert secret not in rendered
        assert "svc:***@" in excinfo.value.message
