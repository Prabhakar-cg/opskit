"""Tests for --watch (re-run loop with change detection), on lookup and reverse."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.core.errors import UsageError
from opskit.dns.cli import _parse_interval
from opskit.dns.models import DnsQuery, DnsRecord, LookupResult, RecordType, Resolver

runner = CliRunner()


def test_parse_interval_units():
    assert _parse_interval("5") == 5.0
    assert _parse_interval("5s") == 5.0
    assert _parse_interval("2m") == 120.0
    assert _parse_interval("250ms") == 0.25


@pytest.mark.parametrize("bad", ["", "abc", "0", "-3", "5x"])
def test_parse_interval_rejects(bad):
    with pytest.raises(UsageError):
        _parse_interval(bad)


def _result(target, value="1.2.3.4"):
    query = DnsQuery(
        target=target, record_types=(RecordType.A,), servers=("127.0.0.1",)
    )
    return LookupResult(
        query=query,
        resolver=Resolver("127.0.0.1"),
        records=(DnsRecord(RecordType.A, value, 300),),
    )


def _stop_after(n):
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= n:
            raise KeyboardInterrupt

    return fake_sleep


def test_watch_loops_until_interrupt(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.time.sleep", _stop_after(2))
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name)
    )
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--watch", "0.01"])
    assert result.exit_code == 0
    assert result.stdout.count("Ctrl-C to stop") >= 2


def test_watch_detects_change(monkeypatch):
    values = iter(["1.1.1.1", "2.2.2.2"])
    monkeypatch.setattr("opskit.dns.cli.time.sleep", _stop_after(2))
    monkeypatch.setattr(
        "opskit.dns.cli.api.lookup", lambda name, *a, **k: _result(name, next(values))
    )
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--watch", "0.01"])
    assert result.exit_code == 0
    assert "changed" in result.stdout


def test_watch_bad_interval():
    result = runner.invoke(app, ["dns", "lookup", "example.com", "--watch", "nope"])
    assert result.exit_code == 2


def test_watch_available_on_reverse(monkeypatch):
    monkeypatch.setattr("opskit.dns.cli.time.sleep", _stop_after(1))
    monkeypatch.setattr(
        "opskit.dns.cli.api.reverse", lambda ip, *a, **k: _result(ip, "host.example.")
    )
    result = runner.invoke(app, ["dns", "reverse", "8.8.8.8", "--watch", "0.01"])
    assert result.exit_code == 0
    assert "Ctrl-C to stop" in result.stdout
