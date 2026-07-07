"""Tests for the in-tree RFC 6125 hostname/SAN matcher."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.tls.inspect import _dns_name_matches, match_hostname
from opskit.tls.models import parse_target

_LABEL = st.from_regex(r"[a-z]([a-z0-9-]{0,10}[a-z0-9])?", fullmatch=True)


def _target(host):
    return parse_target(host)


@pytest.mark.parametrize(
    ("host", "sans", "expected"),
    [
        ("example.com", ("example.com",), True),
        ("EXAMPLE.com", ("example.COM",), True),  # case-insensitive
        ("a.example.com", ("*.example.com",), True),
        ("example.com", ("*.example.com",), False),  # wildcard never covers bare domain
        ("a.b.example.com", ("*.example.com",), False),  # exactly one label
        ("a.example.com", ("a.example.org",), False),
        ("example.com.", ("example.com",), True),  # trailing dot normalized
    ],
)
def test_dns_matching_rules(host, sans, expected, make_cert):
    cert = make_cert(dns_names=sans)
    assert match_hostname(_target(host), cert) is expected


def test_ip_target_matches_ip_san_only(make_cert):
    cert = make_cert(dns_names=("host.example",), ip_sans=("127.0.0.1",))
    assert match_hostname(_target("127.0.0.1"), cert) is True
    cert_no_ip = make_cert(dns_names=("host.test",))
    assert match_hostname(_target("127.0.0.1"), cert_no_ip) is False


def test_ipv6_san_match(make_cert):
    cert = make_cert(dns_names=("v6.test",), ip_sans=("2001:db8::1",))
    assert match_hostname(_target("[2001:db8::1]:443"), cert) is True


def test_cert_without_sans_never_matches(make_cert):
    cert = make_cert(dns_names=())
    assert match_hostname(_target("unit.test"), cert) is False


# Property tests target the pure string matcher directly (no certificates needed).


@given(sub=_LABEL, domain=_LABEL, tld=st.sampled_from(["com", "net", "test"]))
def test_wildcard_covers_exactly_one_label(sub, domain, tld):
    pattern = f"*.{domain}.{tld}"
    assert _dns_name_matches(pattern, f"{sub}.{domain}.{tld}") is True
    assert _dns_name_matches(pattern, f"{domain}.{tld}") is False
    assert _dns_name_matches(pattern, f"x.{sub}.{domain}.{tld}") is False


@given(host=st.from_regex(r"[a-z][a-z0-9.-]{0,30}[a-z0-9]", fullmatch=True))
def test_exact_match_is_reflexive(host):
    assert _dns_name_matches(host, host) is True


@given(
    pattern=st.from_regex(r"[a-z][a-z0-9.-]{0,20}[a-z0-9]", fullmatch=True),
    host=st.from_regex(r"[a-z][a-z0-9.-]{0,20}[a-z0-9]", fullmatch=True),
)
def test_non_wildcard_requires_exact_equality(pattern, host):
    if not pattern.startswith("*."):
        assert _dns_name_matches(pattern, host) is (
            pattern.rstrip(".") == host.rstrip(".")
        )
