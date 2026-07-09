"""Tests for the net CLI: envelopes, exit codes, batch contract, stdin, watch, streams."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app
from opskit.net.cli import _check_signature
from opskit.net.errors import (
    ConnectRefused,
    ConnectTimeout,
    PortInUse,
    ResolutionError,
    UdpInconclusive,
)
from opskit.net.models import (
    CheckResult,
    InboundEvent,
    ListenerSession,
    ProbeAttempt,
    ProbeResult,
    Protocol,
    StopReason,
    Verdict,
    parse_target,
)

runner = CliRunner()


def _open_result(raw, *, port=None, protocol=Protocol.TCP, time_ms=12.4):
    target = parse_target(raw, port=port, protocol=protocol)
    return CheckResult(
        target=target,
        verdict=Verdict.OPEN,
        address="192.0.2.7",
        family="ipv4",
        port=target.port,
        time_ms=time_ms,
    )


def _patch_check(monkeypatch, fn):
    monkeypatch.setattr("opskit.net.cli.api.check", fn)


# --- net check ---


def test_check_json_envelope_shape(monkeypatch):
    _patch_check(monkeypatch, lambda raw, **kw: _open_result(raw))
    result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "net.check"
    assert payload["query"]["host"] == "db.example.com"
    assert payload["query"]["port"] == 5432
    assert payload["query"]["protocol"] == "tcp"
    assert payload["query"]["family"] is None
    assert payload["query"]["timeout"] == 5.0
    assert payload["query"]["retries"] == 2
    assert payload["result"]["verdict"] == "open"
    assert payload["result"]["address"] == "192.0.2.7"
    assert payload["result"]["family"] == "ipv4"
    assert payload["result"]["time_ms"] == 12.4
    assert payload["error"] is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectRefused("refused", hint="h"), 8),
        (ConnectTimeout("timed out", hint="h"), 6),
        (ResolutionError("no such host", hint="h"), 3),
        (UdpInconclusive("open or filtered", hint="h"), 6),
    ],
)
def test_check_failure_exit_codes(monkeypatch, error, expected):
    def _fail(raw, **kw):
        raise error

    _patch_check(monkeypatch, _fail)
    result = runner.invoke(app, ["net", "check", "svc.example:7000"])
    assert result.exit_code == expected


def test_check_failure_envelope_never_dropped(monkeypatch):
    def _fail(raw, **kw):
        raise UdpInconclusive(
            "no response from vpn.example.com:500 — open or filtered (inconclusive)",
            hint="silence does not mean closed",
        )

    _patch_check(monkeypatch, _fail)
    result = runner.invoke(
        app, ["net", "check", "vpn.example.com:500", "--udp", "--jsonl"]
    )
    assert result.exit_code == 6
    payload = json.loads(result.stdout.strip())
    assert payload["result"] is None
    assert payload["error"]["code"] == "udp_inconclusive"
    assert "open or filtered (inconclusive)" in payload["error"]["message"]
    assert payload["query"]["protocol"] == "udp"


def test_check_missing_port_is_usage_error():
    result = runner.invoke(app, ["net", "check", "db.example.com"])
    assert result.exit_code == 2


def test_check_port_conflict_is_usage_error():
    result = runner.invoke(app, ["net", "check", "db.example.com:5432", "-p", "5433"])
    assert result.exit_code == 2


def test_check_ipv4_and_ipv6_together_is_usage_error():
    result = runner.invoke(app, ["net", "check", "db.example.com:5432", "-4", "-6"])
    assert result.exit_code == 2


def test_check_no_targets_is_usage_error():
    result = runner.invoke(app, ["net", "check"])
    assert result.exit_code == 2


def test_check_human_output(monkeypatch):
    _patch_check(monkeypatch, lambda raw, **kw: _open_result(raw))
    result = runner.invoke(app, ["net", "check", "db.example.com:5432", "--no-color"])
    assert result.exit_code == 0
    assert "open" in result.stdout
    assert "192.0.2.7" in result.stdout


def test_check_udp_and_family_flags_reach_api(monkeypatch):
    seen = {}

    def _capture(raw, **kw):
        seen.update(kw)
        return _open_result(raw, protocol=kw["protocol"])

    _patch_check(monkeypatch, _capture)
    result = runner.invoke(
        app, ["net", "check", "ntp.example.com:123", "--udp", "-4", "--json"]
    )
    assert result.exit_code == 0
    assert seen["protocol"] is Protocol.UDP
    assert seen["family"] == "ipv4"
    assert json.loads(result.stdout)["query"]["protocol"] == "udp"


# --- batch contract (Art. IX) ---


def test_batch_mixed_50_targets_processes_all_and_exits_partial(monkeypatch):
    def _mixed(raw, **kw):
        index = int(raw.rsplit(":", 1)[0].removeprefix("host"))
        if index % 2:
            raise ConnectRefused(f"refused {index}", hint="h")
        return _open_result(raw)

    _patch_check(monkeypatch, _mixed)
    targets = [f"host{i}:443" for i in range(50)]
    result = runner.invoke(app, ["net", "check", *targets, "--jsonl"])
    assert result.exit_code == 7  # PARTIAL
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert len(lines) == 50  # an envelope for every target, failures included
    failures = [line for line in lines if line["result"] is None]
    assert len(failures) == 25
    assert all(line["error"]["code"] == "connect_refused" for line in failures)
    successes = [line for line in lines if line["result"] is not None]
    assert all(line["result"]["address"] == "192.0.2.7" for line in successes)


def test_batch_uniform_failure_exits_that_class(monkeypatch):
    def _refuse(raw, **kw):
        raise ConnectRefused("refused", hint="h")

    _patch_check(monkeypatch, _refuse)
    result = runner.invoke(app, ["net", "check", "a:1", "b:2", "--jsonl"])
    assert result.exit_code == 8


def test_batch_all_pass_exits_zero(monkeypatch):
    _patch_check(monkeypatch, lambda raw, **kw: _open_result(raw))
    result = runner.invoke(app, ["net", "check", "a:1", "b:2"])
    assert result.exit_code == 0


def test_batch_stdin_with_comments_and_blanks(monkeypatch):
    processed = []

    def _record(raw, **kw):
        processed.append(raw)
        return _open_result(raw)

    _patch_check(monkeypatch, _record)
    result = runner.invoke(
        app,
        ["net", "check", "-i", "-", "--jsonl"],
        input="# fleet\n\nweb1:443\ndb:5432\n",
    )
    assert result.exit_code == 0
    assert processed == ["web1:443", "db:5432"]
    assert len(result.stdout.strip().splitlines()) == 2


def test_batch_human_failures_go_to_stderr(monkeypatch):
    def _mixed(raw, **kw):
        if raw.startswith("bad"):
            raise ConnectRefused("refused here", hint="check the service")
        return _open_result(raw)

    _patch_check(monkeypatch, _mixed)
    result = runner.invoke(app, ["net", "check", "good:1", "bad:2", "--no-color"])
    assert result.exit_code == 7
    assert "192.0.2.7" in result.stdout
    try:
        stderr_text = result.stderr
    except ValueError:  # click < 8.2 mixes the streams into output
        stderr_text = result.output
    assert "refused here" in stderr_text
    assert "check the service" in stderr_text


def test_batch_port_option_applies_to_portless_lines(monkeypatch):
    seen = []

    def _record(raw, **kw):
        seen.append((raw, kw["port"]))
        return _open_result(raw, port=kw["port"])

    _patch_check(monkeypatch, _record)
    result = runner.invoke(
        app, ["net", "check", "-p", "22", "-i", "-"], input="10.0.0.5\n10.0.0.6\n"
    )
    assert result.exit_code == 0
    assert seen == [("10.0.0.5", 22), ("10.0.0.6", 22)]


# --- watch (R8) ---


def test_watch_runs_and_stops_on_interrupt(monkeypatch):
    _patch_check(monkeypatch, lambda raw, **kw: _open_result(raw))
    monkeypatch.setattr(
        "opskit.core.cliutils.time.sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    result = runner.invoke(
        app, ["net", "check", "db.example.com:5432", "--watch", "1s", "--no-color"]
    )
    assert result.exit_code == 0
    assert "initial" in result.stdout


def test_watch_signature_ignores_timing_jitter():
    fast = [("t:1", _open_result("t:1", time_ms=5.0), None)]
    slow = [("t:1", _open_result("t:1", time_ms=250.0), None)]
    assert _check_signature(fast) == _check_signature(slow)


def test_watch_signature_flags_verdict_change():
    ok = [("t:1", _open_result("t:1"), None)]
    bad = [("t:1", None, ConnectRefused("refused"))]
    assert _check_signature(ok) != _check_signature(bad)


# --- net probe ---


def _probe_result(attempts, *, raw="api.example.com:443", protocol=Protocol.TCP):
    answered = [a.time_ms for a in attempts if a.time_ms is not None]
    successes = sum(1 for a in attempts if a.verdict is Verdict.OPEN)
    return ProbeResult(
        target=parse_target(raw, protocol=protocol),
        attempts=tuple(attempts),
        requested=len(attempts),
        completed=len(attempts),
        successes=successes,
        failures=len(attempts) - successes,
        replies=0,
        closed_signals=0,
        silent=0,
        min_ms=min(answered) if answered else None,
        avg_ms=sum(answered) / len(answered) if answered else None,
        max_ms=max(answered) if answered else None,
        elapsed_ms=3095.2,
    )


def _patch_probe(monkeypatch, attempts, **result_kwargs):
    outcome = _probe_result(attempts, **result_kwargs)

    def _fake(target, *, on_attempt=None, **kw):
        if on_attempt is not None:
            for attempt in attempts:
                on_attempt(attempt)
        return outcome

    monkeypatch.setattr("opskit.net.cli.api.probe", _fake)
    return outcome


_OPEN_ATTEMPT = ProbeAttempt(
    index=1, verdict=Verdict.OPEN, address="203.0.113.7", family="ipv4", time_ms=18.1
)
_TIMEOUT_ATTEMPT = ProbeAttempt(index=2, verdict=Verdict.TIMEOUT, error="timed out")


def test_probe_jsonl_streams_attempts_then_summary(monkeypatch):
    _patch_probe(monkeypatch, [_OPEN_ATTEMPT, _TIMEOUT_ATTEMPT])
    result = runner.invoke(
        app, ["net", "probe", "api.example.com:443", "-c", "2", "--jsonl"]
    )
    assert result.exit_code == 7  # mixed attempts -> PARTIAL
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert len(lines) == 3
    assert [line["result"]["kind"] for line in lines] == [
        "attempt",
        "attempt",
        "summary",
    ]
    assert lines[0]["command"] == "net.probe"
    assert lines[0]["result"]["verdict"] == "open"
    assert lines[0]["result"]["address"] == "203.0.113.7"
    assert lines[1]["result"]["error"] == "timed out"
    summary = lines[2]["result"]
    assert summary["completed"] == 2
    assert summary["successes"] == 1
    assert summary["min_ms"] == 18.1


def test_probe_json_single_envelope(monkeypatch):
    _patch_probe(monkeypatch, [_OPEN_ATTEMPT])
    result = runner.invoke(app, ["net", "probe", "api.example.com:443", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "net.probe"
    assert payload["query"]["host"] == "api.example.com"
    assert payload["query"]["count"] == 4
    assert len(payload["result"]["attempts"]) == 1
    assert payload["result"]["successes"] == 1


def test_probe_uniform_failure_exit(monkeypatch):
    _patch_probe(monkeypatch, [_TIMEOUT_ATTEMPT, _TIMEOUT_ATTEMPT])
    result = runner.invoke(app, ["net", "probe", "api.example.com:443"])
    assert result.exit_code == 6


def test_probe_human_output(monkeypatch):
    _patch_probe(monkeypatch, [_OPEN_ATTEMPT, _TIMEOUT_ATTEMPT])
    result = runner.invoke(app, ["net", "probe", "api.example.com:443", "--no-color"])
    assert "18.1 ms" in result.stdout
    assert "probe statistics" in result.stdout
    assert "2 attempts, 1 succeeded, 1 failed" in result.stdout


def test_probe_usage_errors():
    assert runner.invoke(app, ["net", "probe", "hostonly"]).exit_code == 2
    assert runner.invoke(app, ["net", "probe", "h:1", "-4", "-6"]).exit_code == 2
    assert (
        runner.invoke(app, ["net", "probe", "h:1", "--interval", "nonsense"]).exit_code
        == 2
    )


def test_probe_preflight_resolution_failure_envelope(monkeypatch):
    def _fail(target, **kw):
        raise ResolutionError("cannot resolve gone.example", hint="opskit dns lookup")

    monkeypatch.setattr("opskit.net.cli.api.probe", _fail)
    result = runner.invoke(app, ["net", "probe", "gone.example:443", "--jsonl"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout.strip())
    assert payload["result"] is None
    assert payload["error"]["code"] == "resolve_failed"


# --- net listen ---


def _session(**overrides):
    defaults = {
        "protocol": Protocol.TCP,
        "port": 8080,
        "bound_addresses": ("127.0.0.1", "::1"),
        "started_at": "2026-07-09T10:00:00.000Z",
        "stopped_at": "2026-07-09T10:05:00.000Z",
        "stop_reason": StopReason.INTERRUPT,
        "events_received": 1,
        "max_duration_s": None,
        "max_events": None,
    }
    defaults.update(overrides)
    return ListenerSession(**defaults)


_EVENT = InboundEvent(
    index=1,
    peer_address="198.51.100.23",
    peer_port=52114,
    family="ipv4",
    timestamp="2026-07-09T10:15:02.114Z",
)


def _patch_listener(monkeypatch, *, events=(), session=None, enter_error=None):
    final_session = session or _session()

    class _FakeListener:
        def __init__(
            self, port, *, protocol=Protocol.TCP, max_duration=None, max_events=None
        ):
            self.port = port

        def __enter__(self):
            if enter_error is not None:
                raise enter_error
            return self

        def __exit__(self, *exc):
            return None

        def events(self):
            yield from events

        @property
        def session(self):
            return final_session

    monkeypatch.setattr("opskit.net.cli.Listener", _FakeListener)


def test_listen_jsonl_streams_events_then_session(monkeypatch):
    _patch_listener(monkeypatch, events=[_EVENT])
    result = runner.invoke(app, ["net", "listen", "8080", "--jsonl"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [line["result"]["kind"] for line in lines] == ["event", "session"]
    assert lines[0]["command"] == "net.listen"
    assert lines[0]["result"]["peer_address"] == "198.51.100.23"
    assert lines[1]["result"]["stop_reason"] == "interrupt"
    assert lines[1]["result"]["events_received"] == 1


def test_listen_json_single_envelope_with_events(monkeypatch):
    _patch_listener(monkeypatch, events=[_EVENT])
    result = runner.invoke(app, ["net", "listen", "8080", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result"]["events"][0]["peer_port"] == 52114
    assert payload["result"]["bound_addresses"] == ["127.0.0.1", "::1"]


def test_listen_zero_event_duration_expiry_exits_timeout_class(monkeypatch):
    _patch_listener(
        monkeypatch,
        session=_session(stop_reason=StopReason.MAX_DURATION, events_received=0),
    )
    result = runner.invoke(app, ["net", "listen", "8080", "--max-duration", "1s"])
    assert result.exit_code == 6


def test_listen_duration_expiry_with_events_exits_zero(monkeypatch):
    _patch_listener(
        monkeypatch,
        events=[_EVENT],
        session=_session(stop_reason=StopReason.MAX_DURATION, events_received=1),
    )
    result = runner.invoke(app, ["net", "listen", "8080", "--max-duration", "1s"])
    assert result.exit_code == 0


def test_listen_port_in_use_exits_12(monkeypatch):
    _patch_listener(
        monkeypatch,
        enter_error=PortInUse("port 8080 is already in use", hint="pick another"),
    )
    result = runner.invoke(app, ["net", "listen", "8080", "--json"])
    assert result.exit_code == 12
    payload = json.loads(result.stdout)
    assert payload["result"] is None
    assert payload["error"]["code"] == "port_in_use"


def test_listen_human_output(monkeypatch):
    _patch_listener(monkeypatch, events=[_EVENT])
    result = runner.invoke(app, ["net", "listen", "8080", "--no-color"])
    assert result.exit_code == 0
    assert "listening on port 8080" in result.stdout
    assert "198.51.100.23" in result.stdout
    assert "listener summary" in result.stdout


def test_listen_bad_duration_is_usage_error():
    result = runner.invoke(app, ["net", "listen", "8080", "--max-duration", "bogus"])
    assert result.exit_code == 2
