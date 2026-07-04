"""Tests for multi-resolver compare() and the --diff flag."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.dns import compare
from opskit.dns.errors import ServerFailure
from opskit.dns.models import (
    DnsRecord,
    Outcome,
    RecordType,
    ResolverAnswer,
    ResolverComparison,
)

runner = CliRunner()


class PerServerResolver:
    """Resolver stub returning different records (or errors) per server."""

    def __init__(self, mapping, errors=None):
        self.mapping = mapping  # server -> {rtype: [records]}
        self.errors = errors or {}  # server -> exception

    def query(self, name, rtype, *, server, transport, timeout, retries, port):
        if server in self.errors:
            raise self.errors[server]
        return tuple(self.mapping.get(server, {}).get(rtype, ()))


def test_compare_consistent_ignores_ttl():
    resolver = PerServerResolver(
        {
            "1.1.1.1": {RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)]},
            "8.8.8.8": {RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 60)]},
        }
    )
    result = compare("example.com", ["1.1.1.1", "8.8.8.8"], ["A"], resolver=resolver)
    assert result.consistent
    assert len(result.answers) == 2


def test_compare_differs_shows_each_server():
    resolver = PerServerResolver(
        {
            "1.1.1.1": {RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)]},
            "8.8.8.8": {RecordType.A: [DnsRecord(RecordType.A, "5.6.7.8", 300)]},
        }
    )
    result = compare("example.com", ["1.1.1.1", "8.8.8.8"], ["A"], resolver=resolver)
    assert not result.consistent
    by_server = {a.server: a.records[0].value for a in result.answers}
    assert by_server == {"1.1.1.1": "1.2.3.4", "8.8.8.8": "5.6.7.8"}


def test_compare_records_per_resolver_failure():
    resolver = PerServerResolver(
        {"1.1.1.1": {RecordType.A: [DnsRecord(RecordType.A, "1.2.3.4", 300)]}},
        errors={"8.8.8.8": ServerFailure("down")},
    )
    result = compare("example.com", ["1.1.1.1", "8.8.8.8"], ["A"], resolver=resolver)
    assert not result.consistent
    failed = [a for a in result.answers if a.outcome is Outcome.SERVFAIL]
    assert failed and failed[0].server == "8.8.8.8"


def test_compare_needs_two_servers():
    with pytest.raises(UsageError):
        compare("example.com", ["1.1.1.1"], ["A"])


def _fake_diff(target, servers, types, **kw):
    answers = (
        ResolverAnswer(
            "1.1.1.1", Outcome.OK, (DnsRecord(RecordType.A, "1.2.3.4", 300),)
        ),
        ResolverAnswer(
            "8.8.8.8", Outcome.OK, (DnsRecord(RecordType.A, "5.6.7.8", 300),)
        ),
    )
    return ResolverComparison(target, (RecordType.A,), answers, consistent=False)


def test_cli_diff_json(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.compare", _fake_diff)
    result = runner.invoke(
        app,
        [
            "dns",
            "lookup",
            "example.com",
            "--diff",
            "-s",
            "1.1.1.1",
            "-s",
            "8.8.8.8",
            "--json",
        ],
    )
    assert result.exit_code == 7  # DIFFERS → PARTIAL
    payload = json.loads(result.stdout)
    assert payload["command"] == "dns.compare"
    assert payload["result"]["consistent"] is False
    assert {a["server"] for a in payload["result"]["answers"]} == {"1.1.1.1", "8.8.8.8"}


def test_cli_diff_human_highlights_server(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.compare", _fake_diff)
    result = runner.invoke(
        app,
        [
            "dns",
            "lookup",
            "example.com",
            "--diff",
            "-s",
            "1.1.1.1",
            "-s",
            "8.8.8.8",
            "--no-color",
        ],
    )
    assert result.exit_code == 7
    assert "1.1.1.1" in result.stdout
    assert "8.8.8.8" in result.stdout
    assert "differs" in result.stdout.lower()


def test_cli_diff_needs_two_servers():
    result = runner.invoke(
        app, ["dns", "lookup", "example.com", "--diff", "-s", "1.1.1.1"]
    )
    assert result.exit_code == 2  # UsageError → USAGE


def test_cli_diff_batch_partial(monkeypatch, tmp_path):
    def fake(target, servers, types, **kw):
        if target == "bad.com":
            raise UsageError("boom")
        answers = (
            ResolverAnswer(
                "1.1.1.1", Outcome.OK, (DnsRecord(RecordType.A, "1.2.3.4", 300),)
            ),
            ResolverAnswer(
                "8.8.8.8", Outcome.OK, (DnsRecord(RecordType.A, "1.2.3.4", 300),)
            ),
        )
        return ResolverComparison(target, (RecordType.A,), answers, consistent=True)

    monkeypatch.setattr("opskit.dns.cli.api.compare", fake)
    hosts = tmp_path / "hosts.txt"
    hosts.write_text("good.com\nbad.com\n")
    result = runner.invoke(
        app,
        ["dns", "lookup", "--diff", "-s", "1.1.1.1", "-s", "8.8.8.8", "-i", str(hosts)],
    )
    # One target failed but the other still rendered → PARTIAL, not an aborted USAGE.
    assert result.exit_code == 7
    assert "good.com" in result.stdout
