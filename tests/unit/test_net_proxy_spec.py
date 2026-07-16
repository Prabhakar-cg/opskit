"""Unit tests for the proxy models: parse_proxy, ProxySpec redaction, proxy_exempt, Route."""

from __future__ import annotations

import base64

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.core.errors import UsageError
from opskit.net.models import ProxySpec, Route, parse_proxy, proxy_exempt

PASSWORD = "hunter2-s3cret"


class TestParseProxy:
    def test_bare_host_port(self):
        spec = parse_proxy("proxy.corp.example:3128")
        assert spec.host == "proxy.corp.example"
        assert spec.port == 3128
        assert spec.username is None
        assert spec.password is None

    def test_http_scheme(self):
        spec = parse_proxy("http://proxy.corp.example:8080")
        assert (spec.host, spec.port) == ("proxy.corp.example", 8080)

    def test_scheme_case_insensitive_and_trailing_slash(self):
        spec = parse_proxy("HTTP://proxy.corp.example:8080/")
        assert (spec.host, spec.port) == ("proxy.corp.example", 8080)

    def test_credentials(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp.example:3128")
        assert spec.username == "svc"
        assert spec.password == PASSWORD

    def test_credentials_percent_decoded(self):
        spec = parse_proxy("http://svc%40corp:p%40ss%3Aword@proxy.corp.example:3128")
        assert spec.username == "svc@corp"
        assert spec.password == "p@ss:word"

    def test_username_only(self):
        spec = parse_proxy("http://svc@proxy.corp.example:3128")
        assert spec.username == "svc"
        assert spec.password is None

    def test_bracketed_ipv6_host(self):
        spec = parse_proxy("[2001:db8::9]:3128")
        assert spec.host == "2001:db8::9"
        assert spec.port == 3128

    @pytest.mark.parametrize(
        "raw",
        [
            "socks5://proxy.corp:1080",
            "https://proxy.corp:3128",
            "ftp://proxy.corp:21",
        ],
    )
    def test_unsupported_scheme_names_it(self, raw):
        with pytest.raises(UsageError) as excinfo:
            parse_proxy(raw)
        assert raw.split(":")[0] in excinfo.value.message

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "proxy.corp",  # no port
            "http://proxy.corp",  # no port
            "proxy.corp:0",
            "proxy.corp:65536",
            "proxy.corp:https",
            "http://:3128",  # empty host
            "http://proxy corp:3128",  # whitespace
            "http://proxy.corp:3128/path",  # path
        ],
    )
    def test_invalid_specs_rejected(self, raw):
        with pytest.raises(UsageError):
            parse_proxy(raw)

    @given(port=st.integers(min_value=1, max_value=65535))
    def test_any_valid_port_roundtrips(self, port):
        assert parse_proxy(f"proxy.example:{port}").port == port

    @given(
        text=st.text(min_size=1, max_size=40).filter(
            lambda t: not any(c.isspace() for c in t)
        )
    )
    def test_never_raises_anything_but_usage_error(self, text):
        try:
            spec = parse_proxy(text)
        except UsageError:
            return
        assert 1 <= spec.port <= 65535
        assert spec.host


class TestRedactionByConstruction:
    def test_display_masks_password(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp.example:3128")
        assert spec.display == "svc:***@proxy.corp.example:3128"
        assert PASSWORD not in spec.display

    def test_str_is_display(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp:3128")
        assert str(spec) == spec.display

    def test_repr_never_contains_password(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp:3128")
        assert PASSWORD not in repr(spec)

    def test_display_without_credentials(self):
        assert parse_proxy("proxy.corp:3128").display == "proxy.corp:3128"

    def test_display_brackets_ipv6(self):
        assert parse_proxy("[2001:db8::9]:3128").display == "[2001:db8::9]:3128"

    def test_to_dict_is_redacted(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp:3128")
        assert PASSWORD not in str(spec.to_dict())

    def test_authorization_builds_basic_header(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp:3128")
        token = base64.b64encode(f"svc:{PASSWORD}".encode()).decode("ascii")
        assert spec.authorization == f"Basic {token}"

    def test_authorization_none_without_credentials(self):
        assert parse_proxy("proxy.corp:3128").authorization is None

    @given(password=st.text(min_size=1, max_size=30))
    def test_rendering_is_independent_of_the_password(self, password):
        # The strongest redaction property: display/str/repr are byte-identical for
        # ANY password value, so the secret cannot influence (or leak into) output.
        spec = ProxySpec(host="p.example", port=3128, username="u", password=password)
        other = ProxySpec(host="p.example", port=3128, username="u", password="x")
        assert spec.display == other.display
        assert str(spec) == str(other)
        assert repr(spec) == repr(other)


class TestProxyExempt:
    @pytest.mark.parametrize(
        ("host", "entry"),
        [
            ("internal.corp.example", "internal.corp.example"),  # exact
            ("INTERNAL.corp.example", "internal.CORP.example"),  # case-insensitive
            ("db.internal.corp.example", "corp.example"),  # suffix
            ("db.internal.corp.example", ".corp.example"),  # leading dot
            ("anything.example", "*"),  # wildcard
            ("internal.corp.example", "internal.corp.example:443"),  # port ignored
            ("internal.corp.example.", "internal.corp.example"),  # trailing dot host
        ],
    )
    def test_matches(self, host, entry):
        assert proxy_exempt(host, [entry]) is True

    @pytest.mark.parametrize(
        ("host", "entry"),
        [
            ("corp.example.evil.com", "corp.example"),  # suffix must be label-aligned
            ("notcorp.example", "corp.example"),  # no substring matching
            ("corp.example", "internal.corp.example"),  # entry more specific
            ("other.example", ""),  # empty entry ignored
        ],
    )
    def test_non_matches(self, host, entry):
        assert proxy_exempt(host, [entry]) is False

    def test_list_any_match_wins(self):
        assert proxy_exempt("a.example", ["b.example", "a.example"]) is True

    def test_empty_list(self):
        assert proxy_exempt("a.example", []) is False

    @given(host=st.from_regex(r"[a-z]{1,10}(\.[a-z]{1,10}){1,3}", fullmatch=True))
    def test_wildcard_always_exempts(self, host):
        assert proxy_exempt(host, ["*"]) is True


class TestRoute:
    def test_direct_default(self):
        route = Route.direct()
        assert route.to_dict() == {"via": "direct", "proxy": None, "source": "default"}

    def test_direct_with_exemption_source(self):
        route = Route.direct(source="no-proxy-exemption")
        assert route.to_dict()["source"] == "no-proxy-exemption"

    def test_via_proxy_stores_redacted_display(self):
        spec = parse_proxy("http://svc:" + PASSWORD + "@proxy.corp:3128")
        route = Route.via_proxy(spec, source="env:HTTPS_PROXY")
        assert route.to_dict() == {
            "via": "http-proxy",
            "proxy": "svc:***@proxy.corp:3128",
            "source": "env:HTTPS_PROXY",
        }
        assert PASSWORD not in str(route.to_dict())
