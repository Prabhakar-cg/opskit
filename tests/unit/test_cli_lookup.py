"""CLI tests for `opskit dns lookup` (api stubbed so no network is used)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.dns.errors import NxDomain
from opskit.dns.models import DnsQuery, DnsRecord, LookupResult, RecordType, Resolver

runner = CliRunner()


def _fake_result(target="example.com"):
    query = DnsQuery(
        target=target, record_types=(RecordType.A,), servers=("127.0.0.1",)
    )
    return LookupResult(
        query=query,
        resolver=Resolver("127.0.0.1"),
        records=(DnsRecord(RecordType.A, "93.184.216.34", 300),),
        elapsed_ms=1.5,
    )


def test_lookup_help():
    result = runner.invoke(app, ["dns", "lookup", "--help"])
    assert result.exit_code == 0
    assert "lookup" in result.stdout.lower()


def test_lookup_human(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.lookup", lambda *a, **k: _fake_result())
    result = runner.invoke(app, ["dns", "lookup", "example.com"])
    assert result.exit_code == 0
    assert "93.184.216.34" in result.stdout


def test_lookup_json_envelope(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.lookup", lambda *a, **k: _fake_result())
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "dns.lookup"
    assert payload["result"]["records"][0]["value"] == "93.184.216.34"
    assert payload["error"] is None


def test_lookup_usage_error_exit_code(monkeypatch):
    def _raise(*a, **k):
        raise UsageError("unknown record type: ZZZ", hint="use A/AAAA/MX/…")

    monkeypatch.setattr("opskit.dns.cli.api.lookup", _raise)
    result = runner.invoke(app, ["dns", "lookup", "example.com", "-t", "ZZZ"])
    assert result.exit_code == 2  # ExitCode.USAGE


def test_lookup_nxdomain_exit_code_json(monkeypatch):
    def _raise(*a, **k):
        raise NxDomain("no.invalid does not exist")

    monkeypatch.setattr("opskit.dns.cli.api.lookup", _raise)
    result = runner.invoke(app, ["dns", "lookup", "no.invalid", "--json"])
    assert result.exit_code == 3  # ExitCode.NXDOMAIN
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "nxdomain"
    assert payload["result"] is None
