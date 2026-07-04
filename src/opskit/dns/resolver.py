"""Resolver abstraction over dnspython, injectable so tests can supply a fake.

The concrete :class:`DnspythonResolver` sends a query to one server, applies UDP→TCP fallback
on truncation, and normalizes rcodes/OS errors into the DNS exception hierarchy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol, cast

import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdata
import dns.rdatatype
import dns.resolver
import dns.rrset

from opskit.dns.errors import DnsError, DnsRefused, DnsTimeout, NxDomain, ServerFailure
from opskit.dns.models import DnsRecord, RecordType, TraceStep, Transport

# Root server IPs (a/b/c-root) used as the entry point for iterative --trace.
_ROOT_SERVERS = ("198.41.0.4", "199.9.14.201", "192.33.4.12")
_MAX_TRACE_DEPTH = 15


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
            except (dns.exception.Timeout, OSError) as exc:
                # Timeouts are retried; raw socket errors (refused/unreachable) also land here.
                last_exc = exc
        if last_exc is None or isinstance(last_exc, dns.exception.Timeout):
            raise DnsTimeout(
                f"no response from {server} within {timeout}s",
                hint="the resolver may be filtered; try --transport tcp or a different --server",
            ) from last_exc
        raise ServerFailure(
            f"cannot reach {server} on port {port}: {last_exc}",
            hint="check the --server address and --port, or that the resolver is reachable",
        ) from last_exc

    def _extract(self, response: dns.message.Message) -> tuple[DnsRecord, ...]:
        return _records_from(response.answer)


def _records_from(rrsets: list[dns.rrset.RRset]) -> tuple[DnsRecord, ...]:
    """Extract our supported records from a list of rrsets (skips unsupported types)."""
    records: list[DnsRecord] = []
    for rrset in rrsets:
        type_text = dns.rdatatype.to_text(rrset.rdtype)
        try:
            rtype = RecordType(type_text)
        except ValueError:
            continue  # skip records outside our supported set (e.g. RRSIG)
        ttl = int(rrset.ttl)
        for item in cast("Iterable[dns.rdata.Rdata]", rrset):
            records.append(DnsRecord(type=rtype, value=str(item.to_text()), ttl=ttl))
    return tuple(records)


def _referral(response: dns.message.Message) -> tuple[list[str], list[str], str]:
    """Parse a delegation response into (NS names, glue IPs, delegated zone)."""
    ns_names: list[str] = []
    zone = ""
    for rrset in response.authority:
        if rrset.rdtype == dns.rdatatype.NS:
            zone = str(rrset.name)
            for item in cast("Iterable[dns.rdata.Rdata]", rrset):
                ns_names.append(str(item.to_text()))
    glue: list[str] = []
    for rrset in response.additional:
        if rrset.rdtype in (dns.rdatatype.A, dns.rdatatype.AAAA):
            for item in cast("Iterable[dns.rdata.Rdata]", rrset):
                glue.append(str(item.to_text()))
    return ns_names, glue, zone


def _resolve_glue(ns_name: str, timeout: float) -> str | None:
    """Resolve an NS hostname to an address (used when a referral lacks glue).

    Tries A then AAAA so IPv6-only or dual-stack NS hosts are not missed.
    """
    for family in ("A", "AAAA"):
        try:
            answer = dns.resolver.resolve(ns_name, family, lifetime=timeout)
        except dns.exception.DNSException:
            continue
        for item in cast("Iterable[dns.rdata.Rdata]", answer):
            return str(item.to_text())
    return None


def _next_hop_servers(
    glue: list[str], ns_names: list[str], timeout: float
) -> list[str]:
    """Servers to query for the next trace hop: prefer glue, else resolve an NS name."""
    if glue:
        return glue
    if ns_names:
        resolved = _resolve_glue(ns_names[0], timeout)
        return [resolved] if resolved else []
    return []


QueryFn = Callable[[str, dns.message.Message], dns.message.Message]


def trace_resolution(
    name: str,
    rtype: RecordType,
    *,
    timeout: float = 5.0,
    port: int = 53,
    query_fn: QueryFn | None = None,
) -> tuple[TraceStep, ...]:
    """Iteratively resolve ``name`` from the root, recording each delegation hop.

    ``query_fn`` (server, request) -> response is injectable for tests; by default it sends a
    non-recursive UDP query to each server in turn.
    """

    def default_send(server: str, request: dns.message.Message) -> dns.message.Message:
        # Fall back to TCP when the UDP answer is truncated so large referrals aren't lost.
        response, _ = dns.query.udp_with_fallback(
            request, server, timeout=timeout, port=port
        )
        return response

    send = query_fn or default_send
    qname = name if name.endswith(".") else name + "."
    servers: list[str] = list(_ROOT_SERVERS)
    zone = "."
    steps: list[TraceStep] = []
    for _ in range(_MAX_TRACE_DEPTH):
        if not servers:
            break
        server = servers[0]
        request = dns.message.make_query(qname, rtype.value)
        request.flags &= ~dns.flags.RD
        try:
            response = send(server, request)
        except (dns.exception.Timeout, OSError):
            # A timeout or raw socket error (refused/unreachable) ends the trace at this hop
            # rather than escaping as an unhandled exception.
            steps.append(TraceStep(server=server, zone=zone, response="error"))
            break
        if response.answer:
            steps.append(
                TraceStep(
                    server=server,
                    zone=zone,
                    response="answer",
                    records=_records_from(response.answer),
                )
            )
            break
        ns_names, glue, delegated = _referral(response)
        steps.append(
            TraceStep(
                server=server, zone=zone, response="referral", referrals=tuple(ns_names)
            )
        )
        if delegated:
            zone = delegated
        servers = _next_hop_servers(glue, ns_names, timeout)
    return tuple(steps)
