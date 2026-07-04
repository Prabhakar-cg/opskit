"""Tests for iterative --trace resolution (lookup and reverse)."""

from __future__ import annotations

import json

import dns.message
import dns.rrset
import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.dns import trace
from opskit.dns.models import DnsRecord, RecordType, TraceStep
from opskit.dns.resolver import trace_resolution

runner = CliRunner()


def _referral(qname, zone, ns_name, glue):
    q = dns.message.make_query(qname, "A")
    r = dns.message.make_response(q)
    r.authority.append(dns.rrset.from_text(zone, 172800, "IN", "NS", ns_name))
    r.additional.append(dns.rrset.from_text(ns_name, 172800, "IN", "A", glue))
    return r


def _answer(qname, ip):
    q = dns.message.make_query(qname, "A")
    r = dns.message.make_response(q)
    r.answer.append(dns.rrset.from_text(qname, 300, "IN", "A", ip))
    return r


def test_trace_walks_delegations():
    responses = {
        "198.41.0.4": _referral("example.com.", "com.", "a.gtld.", "192.5.6.30"),
        "192.5.6.30": _referral(
            "example.com.", "example.com.", "ns.example.", "203.0.113.5"
        ),
        "203.0.113.5": _answer("example.com.", "93.184.216.34"),
    }

    def query_fn(server, request):
        return responses[server]

    steps = trace_resolution("example.com", RecordType.A, query_fn=query_fn)
    assert [s.response for s in steps] == ["referral", "referral", "answer"]
    assert steps[0].server == "198.41.0.4"
    assert steps[1].zone == "com."
    assert steps[-1].records[0].value == "93.184.216.34"


def test_trace_api_validates():
    with pytest.raises(UsageError):
        trace("   ")


def _steps(value="93.184.216.34"):
    return (
        TraceStep("198.41.0.4", ".", "referral", referrals=("a.gtld.",)),
        TraceStep(
            "203.0.113.5",
            "example.com.",
            "answer",
            records=(DnsRecord(RecordType.A, value, 300),),
        ),
    )


def test_cli_trace_human(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.api.trace", lambda name, *a, **k: _steps())
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--trace"])
    assert result.exit_code == 0
    assert "198.41.0.4" in result.stdout
    assert "93.184.216.34" in result.stdout


def test_cli_trace_json(monkeypatch):
    monkeypatch.setattr(
        "opskit.dns.cli.api.trace", lambda name, *a, **k: _steps("1.2.3.4")
    )
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--trace", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "dns.trace"
    assert len(payload["result"]["trace"]) == 2


def test_cli_all_with_trace_rejected():
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--all", "--trace"])
    assert result.exit_code == 2  # --all + --trace is a usage error


def test_cli_trace_batch_partial(monkeypatch, tmp_path):
    def fake(name, *a, **k):
        if name == "bad.com":
            raise UsageError("boom")
        return _steps()

    monkeypatch.setattr("opskit.dns.cli.api.trace", fake)
    hosts = tmp_path / "hosts.txt"
    hosts.write_text("good.com\nbad.com\n")
    result = runner.invoke(app, ["dns", "lookup", "--trace", "-i", str(hosts)])
    # good.com still traced and rendered; bad.com failed → PARTIAL.
    assert result.exit_code == 7
    assert "93.184.216.34" in result.stdout


def test_cli_reverse_trace(monkeypatch):
    steps = (
        TraceStep(
            "203.0.113.5",
            "113.0.203.in-addr.arpa.",
            "answer",
            records=(DnsRecord(RecordType.PTR, "host.example.", 300),),
        ),
    )
    monkeypatch.setattr("opskit.dns.cli.api.reverse_trace", lambda ip, *a, **k: steps)
    result = runner.invoke(app, ["dns", "reverse", "203.0.113.5", "--trace"])
    assert result.exit_code == 0
    assert "host.example." in result.stdout
