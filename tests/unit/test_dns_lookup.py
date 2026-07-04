"""Unit tests for the DNS lookup API (network stubbed via the mock resolver)."""

from __future__ import annotations

import pytest

from opskit.core.errors import UsageError
from opskit.dns import lookup
from opskit.dns.errors import DnsRefused, DnsTimeout, NxDomain, ServerFailure
from opskit.dns.models import DnsRecord, RecordType


def test_lookup_returns_records(make_resolver):
    resolver = make_resolver(
        {RecordType.A: [DnsRecord(RecordType.A, "93.184.216.34", 300)]}
    )
    result = lookup("example.com", ["A"], server="127.0.0.1", resolver=resolver)
    assert result.ok
    assert bool(result) is True
    assert result.records[0].value == "93.184.216.34"
    assert [r.type for r in result] == [RecordType.A]


def test_lookup_multiple_types(make_resolver):
    resolver = make_resolver(
        {
            RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 60)],
            RecordType.MX: [DnsRecord(RecordType.MX, "10 mail.example.com.", 60)],
        }
    )
    result = lookup("example.com", ["A", "MX"], server="127.0.0.1", resolver=resolver)
    assert {r.type for r in result.records} == {RecordType.A, RecordType.MX}


def test_lookup_empty_answer_is_ok(make_resolver):
    result = lookup(
        "example.com", ["AAAA"], server="127.0.0.1", resolver=make_resolver()
    )
    assert result.ok
    assert result.records == ()


def test_lookup_uses_system_resolver_when_none(make_resolver):
    resolver = make_resolver({RecordType.A: [DnsRecord(RecordType.A, "1.1.1.1", 30)]})
    result = lookup("example.com", ["A"], resolver=resolver)
    assert result.resolver.address


@pytest.mark.parametrize(
    "error",
    [NxDomain("nope"), ServerFailure("fail"), DnsRefused("no"), DnsTimeout("slow")],
)
def test_lookup_propagates_dns_errors(make_resolver, error):
    resolver = make_resolver(error=error)
    with pytest.raises(type(error)):
        lookup("example.com", ["A"], server="127.0.0.1", resolver=resolver)


def test_lookup_multitype_keeps_records_when_one_type_fails(make_resolver):
    resolver = make_resolver(
        records={RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)]},
        errors={RecordType.MX: ServerFailure("mx failed")},
    )
    result = lookup("example.com", ["A", "MX"], server="127.0.0.1", resolver=resolver)
    assert [r.value for r in result.records] == ["1.2.3.4"]


def test_lookup_multitype_raises_when_all_types_fail(make_resolver):
    resolver = make_resolver(
        errors={
            RecordType.A: ServerFailure("a failed"),
            RecordType.MX: ServerFailure("mx failed"),
        }
    )
    with pytest.raises(ServerFailure):
        lookup("example.com", ["A", "MX"], server="127.0.0.1", resolver=resolver)


def test_lookup_multitype_nxdomain_still_raises(make_resolver):
    resolver = make_resolver(errors={RecordType.A: NxDomain("nope")})
    with pytest.raises(NxDomain):
        lookup("example.com", ["A", "MX"], server="127.0.0.1", resolver=resolver)


def test_lookup_rejects_unknown_type(make_resolver):
    with pytest.raises(UsageError):
        lookup("example.com", ["ZZZ"], resolver=make_resolver())


def test_lookup_rejects_empty_target(make_resolver):
    with pytest.raises(UsageError):
        lookup("   ", ["A"], resolver=make_resolver())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"retries": -1},
        {"port": 0},
        {"port": 70000},
        {"transport": "carrier-pigeon"},
    ],
)
def test_lookup_rejects_bad_controls(make_resolver, kwargs):
    with pytest.raises(UsageError):
        lookup("example.com", ["A"], resolver=make_resolver(), **kwargs)


def test_lookup_query_echoes_parameters(make_resolver):
    result = lookup(
        "example.com",
        ["A", "MX"],
        server="9.9.9.9",
        timeout=3,
        resolver=make_resolver(),
    )
    payload = result.query.to_dict()
    assert payload["target"] == "example.com"
    assert payload["record_types"] == ["A", "MX"]
    assert payload["servers"] == ["9.9.9.9"]
