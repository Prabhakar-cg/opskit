"""Tests for bulk lookups via --input-file and batch output (JSON array / NDJSON)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.dns.errors import NxDomain
from opskit.dns.models import DnsQuery, DnsRecord, LookupResult, RecordType, Resolver

runner = CliRunner()


def _result(target):
    query = DnsQuery(
        target=target, record_types=(RecordType.A,), servers=("127.0.0.1",)
    )
    return LookupResult(
        query=query,
        resolver=Resolver("127.0.0.1"),
        records=(DnsRecord(RecordType.A, "1.2.3.4", 300),),
    )


def test_input_file_batch_json(tmp_path, monkeypatch):
    path = tmp_path / "targets.txt"
    path.write_text("# a comment\nexample.com\n\nexample.org\n")
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name)
    )
    result = runner.invoke(app, ["dns", "lookup", "--input-file", str(path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert [e["query"]["target"] for e in payload] == ["example.com", "example.org"]


def test_input_file_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "t.txt"
    path.write_text("a.com\nb.com\n")
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name)
    )
    result = runner.invoke(app, ["dns", "lookup", "--input-file", str(path), "--jsonl"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line is a standalone JSON object


def test_input_file_human_batch(tmp_path, monkeypatch):
    path = tmp_path / "t.txt"
    path.write_text("a.com\nb.com\n")
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name)
    )
    result = runner.invoke(app, ["dns", "lookup", "--input-file", str(path)])
    assert result.exit_code == 0
    assert ";; a.com" in result.stdout
    assert ";; b.com" in result.stdout


def test_positional_plus_file(tmp_path, monkeypatch):
    path = tmp_path / "t.txt"
    path.write_text("b.com\n")
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name)
    )
    result = runner.invoke(
        app, ["dns", "lookup", "a.com", "--input-file", str(path), "--json"]
    )
    payload = json.loads(result.stdout)
    assert [e["query"]["target"] for e in payload] == ["a.com", "b.com"]


def test_batch_partial_exit_code(tmp_path, monkeypatch):
    path = tmp_path / "t.txt"
    path.write_text("good.com\nbad.com\n")

    def fake(name, *a, **k):
        if name == "bad.com":
            raise NxDomain("bad.com does not exist")
        return _result(name)

    monkeypatch.setattr("opskit.dns.cli.api.lookup", fake)
    result = runner.invoke(app, ["dns", "lookup", "--input-file", str(path)])
    assert result.exit_code == 7  # ExitCode.PARTIAL


def test_reverse_input_file(tmp_path, monkeypatch):
    path = tmp_path / "ips.txt"
    path.write_text("8.8.8.8\n1.1.1.1\n")
    monkeypatch.setattr("opskit.dns.cli.api.reverse", lambda ip, *a, **k: _result(ip))
    result = runner.invoke(app, ["dns", "reverse", "--input-file", str(path), "--json"])
    payload = json.loads(result.stdout)
    assert [e["query"]["target"] for e in payload] == ["8.8.8.8", "1.1.1.1"]


def test_no_target_usage_error():
    result = runner.invoke(app, ["dns", "lookup"])
    assert result.exit_code == 2


def test_missing_input_file():
    result = runner.invoke(
        app, ["dns", "lookup", "--input-file", "/no/such/file-xyz.txt"]
    )
    assert result.exit_code == 2
