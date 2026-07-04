"""Tests for reverse (PTR) lookups — API and CLI."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.dns import reverse
from opskit.dns.models import DnsQuery, DnsRecord, LookupResult, RecordType, Resolver

runner = CliRunner()


def test_reverse_returns_ptr(make_resolver):
    resolver = make_resolver(
        {RecordType.PTR: [DnsRecord(RecordType.PTR, "dns.google.", 300)]}
    )
    result = reverse("8.8.8.8", server="127.0.0.1", resolver=resolver)
    assert result.ok
    assert result.records[0].value == "dns.google."


def test_reverse_ipv6(make_resolver):
    resolver = make_resolver(
        {RecordType.PTR: [DnsRecord(RecordType.PTR, "host.example.", 60)]}
    )
    result = reverse("2001:4860:4860::8888", server="127.0.0.1", resolver=resolver)
    assert result.records[0].value == "host.example."


def test_reverse_no_record_is_ok(make_resolver):
    result = reverse("8.8.8.8", server="127.0.0.1", resolver=make_resolver())
    assert result.ok
    assert result.records == ()


def test_reverse_rejects_empty(make_resolver):
    with pytest.raises(UsageError):
        reverse("   ", resolver=make_resolver())


def test_reverse_rejects_invalid_ip(make_resolver):
    with pytest.raises(UsageError):
        reverse("not-an-ip", resolver=make_resolver())


def _fake(target="8.8.8.8"):
    query = DnsQuery(
        target=target, record_types=(RecordType.PTR,), servers=("127.0.0.1",)
    )
    return LookupResult(
        query=query,
        resolver=Resolver("127.0.0.1"),
        records=(DnsRecord(RecordType.PTR, "dns.google.", 300),),
    )


def test_cli_reverse_human(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.reverse", lambda *a, **k: _fake())
    result = runner.invoke(app, ["dns", "reverse", "8.8.8.8"])
    assert result.exit_code == 0
    assert "dns.google." in result.stdout


def test_cli_reverse_json(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.reverse", lambda *a, **k: _fake())
    result = runner.invoke(app, ["dns", "reverse", "8.8.8.8", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "dns.reverse"
    assert payload["result"]["records"][0]["value"] == "dns.google."


def test_cli_reverse_invalid_ip_exit_code():
    result = runner.invoke(app, ["dns", "reverse", "not-an-ip"])
    assert result.exit_code == 2  # UsageError → ExitCode.USAGE
