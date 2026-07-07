"""Tests for TLS target parsing (host[:port], IP literals, [v6]:port)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.core.errors import UsageError
from opskit.tls.models import parse_target


def test_bare_hostname_defaults_443():
    target = parse_target("example.com")
    assert (target.host, target.port) == ("example.com", 443)
    assert target.server_name == "example.com"
    assert not target.is_ip


def test_host_port_shorthand():
    target = parse_target("example.com:8443")
    assert (target.host, target.port) == ("example.com", 8443)


def test_port_option():
    target = parse_target("example.com", port=993)
    assert target.port == 993


def test_shorthand_and_option_must_agree():
    assert parse_target("example.com:8443", port=8443).port == 8443
    with pytest.raises(UsageError):
        parse_target("example.com:8443", port=443)


def test_ipv4_target_has_no_sni():
    target = parse_target("192.0.2.10")
    assert target.is_ip
    assert target.server_name is None


def test_bare_ipv6_literal():
    target = parse_target("2001:db8::1")
    assert target.is_ip
    assert target.port == 443


def test_bracketed_ipv6_with_port():
    target = parse_target("[2001:db8::1]:8443")
    assert (target.host, target.port) == ("2001:db8::1", 8443)
    assert target.is_ip


def test_sni_override_applies_even_for_ip():
    target = parse_target("192.0.2.10", server_name="internal.example.com")
    assert target.server_name == "internal.example.com"


def test_trailing_dot_normalized():
    assert parse_target("example.com.").host == "example.com"


@pytest.mark.parametrize(
    "raw", ["", "   ", ":443", "[2001:db8::1", "host:notaport", "host:0", "host:70000"]
)
def test_invalid_targets_rejected(raw):
    with pytest.raises(UsageError):
        parse_target(raw)


@given(
    host=st.from_regex(
        r"[a-z]([a-z0-9-]{0,20}[a-z0-9])?(\.[a-z]{2,6}){1,3}", fullmatch=True
    ),
    port=st.integers(min_value=1, max_value=65535),
)
def test_roundtrip_host_port(host, port):
    target = parse_target(f"{host}:{port}")
    assert target.host == host
    assert target.port == port
    assert target.server_name == host


@given(port=st.integers(min_value=1, max_value=65535))
def test_roundtrip_bracketed_v6(port):
    target = parse_target(f"[2001:db8::2]:{port}")
    assert target.host == "2001:db8::2"
    assert target.port == port
    assert target.is_ip
