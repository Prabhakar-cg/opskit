"""Tests for the tls CLI: envelope, exit codes, port precedence, batch, watch."""

from __future__ import annotations

import dataclasses
import json

from typer.testing import CliRunner

from opskit.cli import app
from opskit.net import TcpConnection
from opskit.net.errors import ConnectRefused
from opskit.tls.models import (
    CertificateInfo,
    FindingCode,
    TlsCheckResult,
    TlsOutcome,
    ValidationFinding,
    parse_target,
)

runner = CliRunner()


def _leaf(days=200, fingerprint="ab" * 32):
    return CertificateInfo(
        subject="CN=unit.test",
        issuer="CN=opskit test intermediate",
        sans=("dns:unit.test",),
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2027-01-01T00:00:00+00:00",
        days_until_expiry=days,
        serial="AB12",
        signature_algorithm="ecdsa-with-SHA256",
        key_type="EC",
        key_bits=256,
        fingerprint_sha256=fingerprint,
        is_self_signed=False,
    )


def _result(raw="unit.test", outcome=TlsOutcome.OK, findings=(), **kwargs):
    leaf = kwargs.pop("leaf", _leaf())
    return TlsCheckResult(
        target=parse_target(raw),
        outcome=outcome,
        connection=TcpConnection(
            address="192.0.2.1", family="ipv4", port=443, connect_ms=3.2
        ),
        tls_version="TLSv1.3",
        cipher="TLS_AES_256_GCM_SHA384",
        leaf=leaf,
        chain=(leaf,),
        findings=tuple(findings),
        elapsed_ms=42.0,
        **kwargs,
    )


def test_json_envelope_shape(monkeypatch):
    monkeypatch.setattr("opskit.tls.cli.api.check", lambda raw, **kw: _result(raw))
    result = runner.invoke(app, ["tls", "check", "unit.test", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "tls.check"
    assert payload["query"]["host"] == "unit.test"
    assert payload["query"]["port"] == 443
    assert payload["query"]["warn_days"] == 30
    assert payload["result"]["outcome"] == "ok"
    assert payload["result"]["tls_version"] == "TLSv1.3"
    assert payload["error"] is None


def test_chain_json_contract(monkeypatch):
    """Every FR-011 leaf field is present; chain entries carry the core fields."""
    monkeypatch.setattr("opskit.tls.cli.api.check", lambda raw, **kw: _result(raw))
    result = runner.invoke(app, ["tls", "check", "unit.test", "--json"])
    leaf = json.loads(result.stdout)["result"]["leaf"]
    for field in (
        "subject",
        "issuer",
        "sans",
        "not_before",
        "not_after",
        "days_until_expiry",
        "serial",
        "signature_algorithm",
        "key_type",
        "key_bits",
        "fingerprint_sha256",
        "is_self_signed",
    ):
        assert field in leaf
    chain = json.loads(result.stdout)["result"]["chain"]
    assert chain and all(
        "subject" in c and "issuer" in c and "not_after" in c for c in chain
    )


def test_cert_invalid_exit_code(monkeypatch):
    invalid = _result(
        outcome=TlsOutcome.CERT_INVALID,
        findings=[ValidationFinding(FindingCode.EXPIRED, "certificate expired on X")],
    )
    monkeypatch.setattr("opskit.tls.cli.api.check", lambda raw, **kw: invalid)
    result = runner.invoke(app, ["tls", "check", "unit.test", "--no-color"])
    assert result.exit_code == 10
    assert "expired" in result.stdout


def test_expiring_soon_exit_code(monkeypatch):
    warning = _result(
        outcome=TlsOutcome.EXPIRING_SOON,
        findings=[ValidationFinding(FindingCode.EXPIRING_SOON, "expires in 5 day(s)")],
        leaf=_leaf(days=5),
    )
    monkeypatch.setattr("opskit.tls.cli.api.check", lambda raw, **kw: warning)
    result = runner.invoke(app, ["tls", "check", "unit.test"])
    assert result.exit_code == 11


def test_port_option_forwarded(monkeypatch):
    seen = {}

    def fake(raw, **kw):
        seen.update(kw, raw=raw)
        return _result(raw)

    monkeypatch.setattr("opskit.tls.cli.api.check", fake)
    result = runner.invoke(app, ["tls", "check", "unit.test", "-p", "8443"])
    assert result.exit_code == 0
    assert seen["port"] == 8443
    assert seen["raw"] == "unit.test"


def test_port_conflict_is_usage_error():
    result = runner.invoke(app, ["tls", "check", "unit.test:8443", "-p", "443"])
    assert result.exit_code == 2


def test_no_target_is_usage_error():
    result = runner.invoke(app, ["tls", "check"])
    assert result.exit_code == 2


def test_batch_uniform_failure_class(monkeypatch, tmp_path):
    def fake(raw, **kw):
        raise ConnectRefused(f"{raw} refused")

    monkeypatch.setattr("opskit.tls.cli.api.check", fake)
    targets = tmp_path / "t.txt"
    targets.write_text("a.test\nb.test\n")
    result = runner.invoke(app, ["tls", "check", "-i", str(targets)])
    assert result.exit_code == 8  # uniform class, not PARTIAL


def test_batch_mixed_partial_and_failures_in_jsonl(monkeypatch, tmp_path):
    def fake(raw, **kw):
        if raw == "bad.test":
            raise ConnectRefused("refused")
        return _result(raw)

    monkeypatch.setattr("opskit.tls.cli.api.check", fake)
    targets = tmp_path / "t.txt"
    targets.write_text("good.test\nbad.test\n# comment\n")
    result = runner.invoke(app, ["tls", "check", "-i", str(targets), "--jsonl"])
    assert result.exit_code == 7
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["result"]["outcome"] == "ok"
    assert lines[1]["result"] is None
    assert lines[1]["error"]["code"] == "connect_refused"
    assert lines[1]["query"]["target"] == "bad.test"


def test_watch_flags_certificate_rotation(monkeypatch):
    results = iter(
        [
            _result(leaf=_leaf(fingerprint="aa" * 32)),
            _result(leaf=_leaf(fingerprint="bb" * 32)),  # rotated cert
        ]
    )
    monkeypatch.setattr("opskit.tls.cli.api.check", lambda raw, **kw: next(results))
    calls = {"n": 0}

    def stop_after(n):
        def _sleep(_interval):
            calls["n"] += 1
            if calls["n"] >= n:
                raise KeyboardInterrupt

        return _sleep

    monkeypatch.setattr("opskit.core.cliutils.time.sleep", stop_after(2))
    result = runner.invoke(
        app, ["tls", "check", "unit.test", "--watch", "1s", "--no-color"]
    )
    assert result.exit_code == 0
    assert "changed" in result.stdout


def test_human_output_escapes_markup(monkeypatch):
    hostile = dataclasses.replace(_leaf(), subject="[red]CN=evil[/red]")
    monkeypatch.setattr(
        "opskit.tls.cli.api.check",
        lambda raw, **kw: _result(leaf=hostile),
    )
    result = runner.invoke(app, ["tls", "check", "unit.test", "--no-color"])
    assert result.exit_code == 0
    assert "[red]CN=evil[/red]" in result.stdout  # literal, not styled/stripped
