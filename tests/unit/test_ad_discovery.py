"""Unit tests for SRV-based DC discovery (injected dns lookup — no network)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from opskit.ad.discovery import discover_dcs
from opskit.ad.errors import DiscoveryError
from opskit.dns.errors import NxDomain


@dataclass
class _Record:
    value: str


@dataclass
class _Result:
    records: tuple


def _lookup_returning(answers: dict):
    """Build a lookup fake keyed by query name; values: list[str] or Exception."""
    calls: list[str] = []

    def lookup(name, types, *, timeout):
        calls.append(name)
        answer = answers.get(name)
        if answer is None:
            raise NxDomain(f"{name} does not exist")
        if isinstance(answer, Exception):
            raise answer
        return _Result(records=tuple(_Record(value) for value in answer))

    lookup.calls = calls
    return lookup


class TestDiscoverDcs:
    def test_prefers_dc_msdcs_record(self):
        lookup = _lookup_returning(
            {
                "_ldap._tcp.dc._msdcs.corp.example.com": [
                    "0 100 389 dc01.corp.example.com.",
                    "0 50 389 dc02.corp.example.com.",
                ],
            }
        )
        hosts = discover_dcs("corp.example.com", lookup=lookup)
        assert hosts == ["dc01.corp.example.com", "dc02.corp.example.com"]
        assert lookup.calls == ["_ldap._tcp.dc._msdcs.corp.example.com"]

    def test_priority_then_weight_ordering(self):
        lookup = _lookup_returning(
            {
                "_ldap._tcp.dc._msdcs.corp.example.com": [
                    "10 100 389 low-priority.corp.example.com.",
                    "0 10 389 light.corp.example.com.",
                    "0 90 389 heavy.corp.example.com.",
                ],
            }
        )
        hosts = discover_dcs("corp.example.com", lookup=lookup)
        assert hosts == [
            "heavy.corp.example.com",
            "light.corp.example.com",
            "low-priority.corp.example.com",
        ]

    def test_falls_back_to_generic_ldap_record(self):
        lookup = _lookup_returning(
            {"_ldap._tcp.corp.example.com": ["0 0 389 generic.corp.example.com."]}
        )
        hosts = discover_dcs("corp.example.com", lookup=lookup)
        assert hosts == ["generic.corp.example.com"]
        assert len(lookup.calls) == 2

    def test_duplicates_removed_and_garbage_skipped(self):
        lookup = _lookup_returning(
            {
                "_ldap._tcp.dc._msdcs.corp.example.com": [
                    "0 100 389 dc01.corp.example.com.",
                    "0 90 389 dc01.corp.example.com.",
                    "not an srv record",
                    "x y 389 bad-numbers.example.com.",
                ],
            }
        )
        assert discover_dcs("corp.example.com", lookup=lookup) == [
            "dc01.corp.example.com"
        ]

    def test_no_records_anywhere_raises_discovery_error(self):
        lookup = _lookup_returning({})
        with pytest.raises(DiscoveryError) as excinfo:
            discover_dcs("nosuch.example.com", lookup=lookup)
        assert "nosuch.example.com" in excinfo.value.message
        assert excinfo.value.hint is not None
        assert "--server" in excinfo.value.hint

    def test_empty_answer_falls_through(self):
        lookup = _lookup_returning(
            {
                "_ldap._tcp.dc._msdcs.corp.example.com": [],
                "_ldap._tcp.corp.example.com": ["0 0 389 dc.corp.example.com."],
            }
        )
        assert discover_dcs("corp.example.com", lookup=lookup) == [
            "dc.corp.example.com"
        ]
