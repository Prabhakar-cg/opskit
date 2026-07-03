"""Resolver abstraction over dnspython, injectable so tests can supply a fake.

The concrete :class:`DnspythonResolver` sends a query to one server, applies UDP→TCP fallback
on truncation, and normalizes rcodes/OS errors into the DNS exception hierarchy.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdata
import dns.rdatatype
import dns.resolver

from opskit.dns.errors import DnsError, DnsRefused, DnsTimeout, NxDomain, ServerFailure
from opskit.dns.models import DnsRecord, RecordType, Transport


class Resolver(Protocol):
    """Answers a single ``(name, type)`` query against one server."""

    def query(
        self,
        name: str,
        rtype: RecordType,
        *,
        server: str,
        transport: Transport,
        timeout: float,
        retries: int,
        port: int,
    ) -> tuple[DnsRecord, ...]:
        """Return records (possibly empty) or raise a :class:`DnsError` subclass."""
        ...


def system_nameserver() -> str:
    """Return the first system-configured resolver address (cross-platform via dnspython)."""
    try:
        servers = dns.resolver.get_default_resolver().nameservers
    except Exception as exc:  # pragma: no cover - platform dependent
        raise DnsError("could not determine the system resolver") from exc
    if not servers:  # pragma: no cover - unusual
        raise DnsError("no system resolver configured", hint="pass --server explicitly")
    return str(servers[0])


class DnspythonResolver:
    """Concrete resolver using dnspython's low-level query API."""

    def query(
        self,
        name: str,
        rtype: RecordType,
        *,
        server: str,
        transport: Transport,
        timeout: float,
        retries: int,
        port: int,
    ) -> tuple[DnsRecord, ...]:
        """Send one query, applying rcode→exception mapping and TCP fallback."""
        request = dns.message.make_query(name, rtype.value)
        response = self._send(request, server, transport, timeout, retries, port)
        rcode = response.rcode()
        if rcode == dns.rcode.NXDOMAIN:
            raise NxDomain(
                f"{name} does not exist (NXDOMAIN)", hint="check the name for typos"
            )
        if rcode == dns.rcode.SERVFAIL:
            raise ServerFailure(f"{server} returned SERVFAIL for {name}")
        if rcode == dns.rcode.REFUSED:
            raise DnsRefused(
                f"{server} refused the query for {name}",
                hint="the resolver may not serve this client; try a different --server",
            )
        if rcode != dns.rcode.NOERROR:
            raise ServerFailure(
                f"{server} returned {dns.rcode.to_text(rcode)} for {name}"
            )
        return self._extract(response)

    def _send(
        self,
        request: dns.message.Message,
        server: str,
        transport: Transport,
        timeout: float,
        retries: int,
        port: int,
    ) -> dns.message.Message:
        last_exc: BaseException | None = None
        for _ in range(retries + 1):
            try:
                if transport is Transport.TCP:
                    return dns.query.tcp(request, server, timeout=timeout, port=port)
                response = dns.query.udp(request, server, timeout=timeout, port=port)
                if transport is Transport.AUTO and (response.flags & dns.flags.TC):
                    return dns.query.tcp(request, server, timeout=timeout, port=port)
                return response
            except dns.exception.Timeout as exc:
                last_exc = exc
        raise DnsTimeout(
            f"no response from {server} within {timeout}s",
            hint="the resolver may be filtered; try --transport tcp or a different --server",
        ) from last_exc

    def _extract(self, response: dns.message.Message) -> tuple[DnsRecord, ...]:
        records: list[DnsRecord] = []
        for rrset in response.answer:
            type_text = dns.rdatatype.to_text(rrset.rdtype)
            try:
                rtype = RecordType(type_text)
            except ValueError:
                continue  # skip records outside our supported set (e.g. RRSIG)
            ttl = int(rrset.ttl)
            for item in cast("Iterable[dns.rdata.Rdata]", rrset):
                records.append(
                    DnsRecord(type=rtype, value=str(item.to_text()), ttl=ttl)
                )
        return tuple(records)
