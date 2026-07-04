"""Tests for lookup_all() and the --all flag (one-stop lookup)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.dns import lookup_all
from opskit.dns.api import ALL_RECORD_TYPES
from opskit.dns.errors import DnsTimeout, NxDomain, ServerFailure
from opskit.dns.models import DnsQuery, DnsRecord, LookupResult, RecordType, Resolver

runner = CliRunner()


def test_all_types_set_is_forward_only():
    assert RecordType.CAA in ALL_RECORD_TYPES
    assert RecordType.PTR not in ALL_RECORD_TYPES


def test_lookup_all_aggregates(make_resolver):
    resolver = make_resolver(
        {
            RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)],
            RecordType.MX: [DnsRecord(RecordType.MX, "10 mail.example.com.", 300)],
            RecordType.TXT: [DnsRecord(RecordType.TXT, '"v=spf1 -all"', 300)],
        }
    )
    result = lookup_all("example.com", server="127.0.0.1", resolver=resolver)
    types = {r.type for r in result.records}
    assert {RecordType.A, RecordType.MX, RecordType.TXT} <= types


def test_lookup_all_nxdomain(make_resolver):
    with pytest.raises(NxDomain):
        lookup_all(
            "nope.invalid",
            server="127.0.0.1",
            resolver=make_resolver(error=NxDomain("no")),
        )


def test_lookup_all_tolerates_per_type_errors(make_resolver):
    resolver = make_resolver(
        records={RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)]},
        errors={
            RecordType.SOA: ServerFailure("nope"),
            RecordType.CAA: DnsTimeout("slow"),
        },
    )
    result = lookup_all("example.com", server="127.0.0.1", resolver=resolver)
    assert result.records[0].value == "1.2.3.4"


def test_lookup_all_total_failure_raises(make_resolver):
    errors = {t: ServerFailure("down") for t in ALL_RECORD_TYPES}
    with pytest.raises(ServerFailure):
        lookup_all(
            "example.com", server="127.0.0.1", resolver=make_resolver(errors=errors)
        )


def _all_result(target="example.com"):
    query = DnsQuery(
        target=target, record_types=ALL_RECORD_TYPES, servers=("127.0.0.1",)
    )
    return LookupResult(
        query=query,
        resolver=Resolver("127.0.0.1"),
        records=(
            DnsRecord(RecordType.A, "1.2.3.4", 300),
            DnsRecord(RecordType.MX, "10 mx.example.com.", 300),
        ),
    )


def test_cli_all_flag_json(monkeypatch):
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup_all", lambda name, *a, **k: _all_result(name)
    )
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "dns.lookup"
    values = {r["type"] for r in payload["result"]["records"]}
    assert {"A", "MX"} <= values
