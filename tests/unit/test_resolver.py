"""Deterministic tests for DnspythonResolver (dns.query stubbed via monkeypatch)."""

from __future__ import annotations

import dns.exception
import dns.flags
import dns.message
import dns.rcode
import dns.rrset
import pytest

from opskit.dns.errors import DnsRefused, DnsTimeout, NxDomain, ServerFailure
from opskit.dns.models import RecordType, Transport
from opskit.dns.resolver import DnspythonResolver


def _reply(rcode=0, answer=None, tc=False):
    query = dns.message.make_query("example.test.", "A")
    resp = dns.message.make_response(query)
    resp.set_rcode(rcode)
    if tc:
        resp.flags |= dns.flags.TC
    if answer is not None:
        resp.answer.append(dns.rrset.from_text("example.test.", 300, "IN", "A", answer))
    return resp


def _run(resolver, transport=Transport.AUTO):
    return resolver.query(
        "example.test.",
        RecordType.A,
        server="127.0.0.1",
        transport=transport,
        timeout=1.0,
        retries=1,
        port=53,
    )


def test_success(monkeypatch):
    monkeypatch.setattr("dns.query.udp", lambda *a, **k: _reply(answer="1.2.3.4"))
    records = _run(DnspythonResolver())
    assert records[0].value == "1.2.3.4"
    assert records[0].type is RecordType.A


def test_nxdomain(monkeypatch):
    monkeypatch.setattr(
        "dns.query.udp", lambda *a, **k: _reply(rcode=dns.rcode.NXDOMAIN)
    )
    with pytest.raises(NxDomain):
        _run(DnspythonResolver())


def test_servfail(monkeypatch):
    monkeypatch.setattr(
        "dns.query.udp", lambda *a, **k: _reply(rcode=dns.rcode.SERVFAIL)
    )
    with pytest.raises(ServerFailure):
        _run(DnspythonResolver())


def test_refused(monkeypatch):
    monkeypatch.setattr(
        "dns.query.udp", lambda *a, **k: _reply(rcode=dns.rcode.REFUSED)
    )
    with pytest.raises(DnsRefused):
        _run(DnspythonResolver())


def test_other_rcode_is_servfail(monkeypatch):
    monkeypatch.setattr(
        "dns.query.udp", lambda *a, **k: _reply(rcode=dns.rcode.FORMERR)
    )
    with pytest.raises(ServerFailure):
        _run(DnspythonResolver())


def test_tcp_fallback_on_truncation(monkeypatch):
    monkeypatch.setattr("dns.query.udp", lambda *a, **k: _reply(tc=True))
    monkeypatch.setattr("dns.query.tcp", lambda *a, **k: _reply(answer="5.6.7.8"))
    records = _run(DnspythonResolver())
    assert records[0].value == "5.6.7.8"


def test_explicit_tcp_transport(monkeypatch):
    monkeypatch.setattr("dns.query.tcp", lambda *a, **k: _reply(answer="9.9.9.9"))
    records = _run(DnspythonResolver(), transport=Transport.TCP)
    assert records[0].value == "9.9.9.9"


def test_timeout_after_retries(monkeypatch):
    def _raise(*a, **k):
        raise dns.exception.Timeout

    monkeypatch.setattr("dns.query.udp", _raise)
    with pytest.raises(DnsTimeout):
        _run(DnspythonResolver())


def test_os_error_becomes_server_failure(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr("dns.query.udp", _raise)
    with pytest.raises(ServerFailure) as excinfo:
        _run(DnspythonResolver())
    assert excinfo.value.hint  # actionable guidance is surfaced
