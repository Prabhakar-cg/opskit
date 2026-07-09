"""Tests for net target parsing: host:port/[v6]:port grammar and the port-required rule."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.core.errors import UsageError
from opskit.net.models import NetTarget, Protocol, parse_target


def test_shorthand_port():
    target = parse_target("db.example.com:5432")
    assert target == NetTarget(host="db.example.com", port=5432)
    assert target.protocol is Protocol.TCP
    assert target.family is None


def test_bare_host_with_port_option():
    target = parse_target("10.0.0.5", port=22)
    assert target.host == "10.0.0.5"
    assert target.port == 22


def test_missing_port_is_usage_error_before_any_io():
    with pytest.raises(UsageError) as excinfo:
        parse_target("db.example.com")
    assert "port" in excinfo.value.message
    assert excinfo.value.hint


def test_shorthand_and_option_conflict():
    with pytest.raises(UsageError) as excinfo:
        parse_target("db.example.com:5432", port=5433)
    assert "conflicting" in excinfo.value.message


def test_shorthand_and_option_agreement_is_fine():
    assert parse_target("db.example.com:5432", port=5432).port == 5432


def test_bracketed_ipv6_with_port():
    target = parse_target("[2001:db8::7]:443")
    assert target.host == "2001:db8::7"
    assert target.port == 443


def test_bracketed_ipv6_without_port_needs_port_option():
    with pytest.raises(UsageError):
        parse_target("[2001:db8::7]")
    assert parse_target("[2001:db8::7]", port=443).host == "2001:db8::7"


def test_bare_ipv6_literal_with_port_option():
    target = parse_target("2001:db8::7", port=443)
    assert target.host == "2001:db8::7"


def test_ambiguous_multi_colon_non_ipv6_is_usage_error():
    with pytest.raises(UsageError) as excinfo:
        parse_target("host:443:extra")
    assert "[ipv6]:port" in (excinfo.value.hint or "")


def test_unclosed_bracket():
    with pytest.raises(UsageError):
        parse_target("[2001:db8::7:443")


def test_junk_after_bracket():
    with pytest.raises(UsageError):
        parse_target("[2001:db8::7]x", port=443)


def test_trailing_dot_hostname_normalized():
    assert parse_target("example.com.:443").host == "example.com"


def test_empty_target_and_empty_host():
    with pytest.raises(UsageError):
        parse_target("")
    with pytest.raises(UsageError):
        parse_target(":443")


@pytest.mark.parametrize("bad", ["0", "-1", "70000", "http", ""])
def test_bad_shorthand_port(bad):
    with pytest.raises(UsageError):
        parse_target(f"example.com:{bad}")


@pytest.mark.parametrize("bad", [0, -1, 70000])
def test_bad_port_option(bad):
    with pytest.raises(UsageError):
        parse_target("example.com", port=bad)


def test_unknown_family_rejected():
    with pytest.raises(UsageError):
        parse_target("example.com:443", family="ipv5")


def test_protocol_and_family_carried():
    target = parse_target("ntp.example.com:123", protocol=Protocol.UDP, family="ipv6")
    assert target.protocol is Protocol.UDP
    assert target.family == "ipv6"
    assert target.to_dict() == {
        "host": "ntp.example.com",
        "port": 123,
        "protocol": "udp",
        "family": "ipv6",
    }


@given(
    host=st.from_regex(
        r"[a-z][a-z0-9\-]{0,20}(\.[a-z][a-z0-9\-]{0,10}){0,3}", fullmatch=True
    ),
    port=st.integers(min_value=1, max_value=65535),
)
def test_roundtrip_hostname_port(host, port):
    target = parse_target(f"{host}:{port}")
    assert target.host == host
    assert target.port == port


@given(port=st.integers(min_value=1, max_value=65535))
def test_roundtrip_bracketed_ipv6(port):
    target = parse_target(f"[2001:db8::7]:{port}")
    assert target.host == "2001:db8::7"
    assert target.port == port


@given(port=st.integers().filter(lambda p: not 1 <= p <= 65535))
def test_out_of_range_ports_always_rejected(port):
    with pytest.raises(UsageError):
        parse_target(f"example.com:{port}")
