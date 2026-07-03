"""Public DNS diagnostics API — the CLI is a thin client over this module.

Functions return typed results on success and raise :class:`opskit.dns.errors.DnsError`
subclasses (or :class:`opskit.core.errors.UsageError`) on failure. Nothing here prints or
calls ``sys.exit``.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import dns.exception
import dns.reversename

from opskit.core.errors import UsageError
from opskit.dns.errors import DnsError, NxDomain
from opskit.dns.models import (
    DnsQuery,
    DnsRecord,
    LookupResult,
    Outcome,
    RecordType,
    Resolver,
    ResolverAnswer,
    ResolverComparison,
    TraceStep,
    Transport,
)
from opskit.dns.resolver import DnspythonResolver, system_nameserver, trace_resolution
from opskit.dns.resolver import Resolver as ResolverEngine

_MAX_PORT = 65535

# The common forward record types queried by lookup_all() / `--all`. DNS `ANY` is deprecated
# (RFC 8482), so a one-stop lookup fans out across these individually.
ALL_RECORD_TYPES: tuple[RecordType, ...] = (
    RecordType.A,
    RecordType.AAAA,
    RecordType.CNAME,
    RecordType.MX,
    RecordType.NS,
    RecordType.SOA,
    RecordType.TXT,
    RecordType.SRV,
    RecordType.CAA,
)


def _coerce_types(types: Sequence[RecordType | str]) -> tuple[RecordType, ...]:
    coerced: list[RecordType] = []
    for value in types:
        if isinstance(value, RecordType):
            coerced.append(value)
            continue
        try:
            coerced.append(RecordType(str(value).upper()))
        except ValueError as exc:
            raise UsageError(f"unknown record type: {value}") from exc
    if not coerced:
        raise UsageError("at least one record type is required")
    return tuple(coerced)


def _coerce_servers(server: str | Sequence[str] | None) -> tuple[str, ...]:
    if server is None:
        return ()
    if isinstance(server, str):
        return (server,)
    return tuple(server)


def _coerce_transport(transport: Transport | str) -> Transport:
    if isinstance(transport, Transport):
        return transport
    try:
        return Transport(str(transport).lower())
    except ValueError as exc:
        raise UsageError(f"unknown transport: {transport}") from exc


def _validate(timeout: float, retries: int, port: int) -> None:
    if timeout <= 0:
        raise UsageError("timeout must be positive")
    if retries < 0:
        raise UsageError("retries must be >= 0")
    if not 1 <= port <= _MAX_PORT:
        raise UsageError(f"port must be between 1 and {_MAX_PORT}")


def lookup(
    target: str,
    types: Sequence[RecordType | str] = ("A",),
    *,
    server: str | Sequence[str] | None = None,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
    resolver: ResolverEngine | None = None,
) -> LookupResult:
    """Resolve ``target`` for one or more record types against a single resolver.

    Args:
        target: The hostname to resolve.
        types: Record types to query (defaults to ``A``).
        server: Explicit resolver(s); the first is used, else the system resolver.
        transport: ``auto`` (UDP→TCP on truncation), ``udp``, or ``tcp``.
        timeout: Per-attempt timeout in seconds.
        retries: Number of retries on timeout.
        port: Resolver port.
        resolver: Optional resolver engine (injected in tests).

    Returns:
        A :class:`LookupResult`; an empty ``records`` tuple means the name exists but has no
        record of the requested type.

    Raises:
        UsageError: For invalid input (before any network I/O).
        DnsError: For resolution failures (NXDOMAIN, SERVFAIL, REFUSED, timeout, …).
    """
    if not target or not target.strip():
        raise UsageError("a target name is required")
    record_types = _coerce_types(types)
    transport_enum = _coerce_transport(transport)
    servers = _coerce_servers(server)
    _validate(timeout, retries, port)

    server_addr = servers[0] if servers else system_nameserver()
    engine: ResolverEngine = resolver if resolver is not None else DnspythonResolver()
    query = DnsQuery(
        target=target,
        record_types=record_types,
        servers=servers,
        transport=transport_enum,
        timeout_s=timeout,
        retries=retries,
        port=port,
    )
    start = time.perf_counter()
    records: list[DnsRecord] = []
    for rtype in record_types:
        records.extend(
            engine.query(
                target,
                rtype,
                server=server_addr,
                transport=transport_enum,
                timeout=timeout,
                retries=retries,
                port=port,
            )
        )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return LookupResult(
        query=query,
        resolver=Resolver(address=server_addr),
        records=tuple(records),
        elapsed_ms=elapsed_ms,
    )


def reverse(
    ip: str,
    *,
    server: str | Sequence[str] | None = None,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
    resolver: ResolverEngine | None = None,
) -> LookupResult:
    """Reverse (PTR) lookup for an IPv4 or IPv6 address.

    Returns a :class:`LookupResult` whose records are the PTR hostname(s); an empty tuple means
    the address has no PTR record.

    Raises:
        UsageError: For an invalid IP or bad controls (before any network I/O).
        DnsError: For resolution failures.
    """
    if not ip or not ip.strip():
        raise UsageError("an IP address is required")
    try:
        ptr_name = dns.reversename.from_address(ip.strip())
    except (ValueError, dns.exception.SyntaxError) as exc:
        raise UsageError(f"invalid IP address: {ip}") from exc
    transport_enum = _coerce_transport(transport)
    servers = _coerce_servers(server)
    _validate(timeout, retries, port)

    server_addr = servers[0] if servers else system_nameserver()
    engine: ResolverEngine = resolver if resolver is not None else DnspythonResolver()
    query = DnsQuery(
        target=ip,
        record_types=(RecordType.PTR,),
        servers=servers,
        transport=transport_enum,
        timeout_s=timeout,
        retries=retries,
        port=port,
    )
    start = time.perf_counter()
    records = engine.query(
        str(ptr_name),
        RecordType.PTR,
        server=server_addr,
        transport=transport_enum,
        timeout=timeout,
        retries=retries,
        port=port,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return LookupResult(
        query=query,
        resolver=Resolver(address=server_addr),
        records=tuple(records),
        elapsed_ms=elapsed_ms,
    )


def lookup_all(
    target: str,
    *,
    server: str | Sequence[str] | None = None,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
    resolver: ResolverEngine | None = None,
) -> LookupResult:
    """Query every common forward record type at once — a one-stop lookup.

    Records from the types that answer are aggregated. Per-type failures are tolerated (some
    resolvers refuse specific types), so nothing that exists is missed.

    Raises:
        UsageError: For invalid input (before any network I/O).
        NxDomain: If the name does not exist.
        DnsError: If every type fails and no records are collected (the first error).
    """
    if not target or not target.strip():
        raise UsageError("a target name is required")
    transport_enum = _coerce_transport(transport)
    servers = _coerce_servers(server)
    _validate(timeout, retries, port)

    server_addr = servers[0] if servers else system_nameserver()
    engine: ResolverEngine = resolver if resolver is not None else DnspythonResolver()
    query = DnsQuery(
        target=target,
        record_types=ALL_RECORD_TYPES,
        servers=servers,
        transport=transport_enum,
        timeout_s=timeout,
        retries=retries,
        port=port,
    )
    start = time.perf_counter()
    records: list[DnsRecord] = []
    first_error: DnsError | None = None
    for rtype in ALL_RECORD_TYPES:
        try:
            records.extend(
                engine.query(
                    target,
                    rtype,
                    server=server_addr,
                    transport=transport_enum,
                    timeout=timeout,
                    retries=retries,
                    port=port,
                )
            )
        except NxDomain:
            raise  # NXDOMAIN is name-level: the name does not exist.
        except DnsError as exc:
            if first_error is None:
                first_error = exc
    if not records and first_error is not None:
        raise first_error
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return LookupResult(
        query=query,
        resolver=Resolver(address=server_addr),
        records=tuple(records),
        elapsed_ms=elapsed_ms,
    )


_MIN_COMPARE_SERVERS = 2


def _outcome_for(error: DnsError) -> Outcome:
    """Map a DNS error to its outcome class (defaults to SERVFAIL for unmapped errors)."""
    try:
        return Outcome(error.code)
    except ValueError:
        return Outcome.SERVFAIL


def _comparison_consistent(answers: Sequence[ResolverAnswer]) -> bool:
    """True when every resolver returned the same outcome and record set (TTL ignored)."""
    signatures = {
        (answer.outcome, frozenset((r.type, r.value) for r in answer.records))
        for answer in answers
    }
    return len(signatures) == 1


def compare(
    target: str,
    servers: Sequence[str],
    types: Sequence[RecordType | str] = ("A",),
    *,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
    resolver: ResolverEngine | None = None,
) -> ResolverComparison:
    """Query the same name across several resolvers and compare their answers.

    Returns a :class:`ResolverComparison` with one :class:`ResolverAnswer` per resolver (records
    or a failure), and a ``consistent`` flag that is True only when every resolver agrees. A
    resolver that fails does not abort the comparison — its failure is recorded.

    Raises:
        UsageError: For invalid input or fewer than two resolvers.
    """
    server_list = list(_coerce_servers(servers))
    if len(server_list) < _MIN_COMPARE_SERVERS:
        raise UsageError("comparing resolvers needs at least two --server values")
    record_types = _coerce_types(types)
    answers: list[ResolverAnswer] = []
    for srv in server_list:
        try:
            result = lookup(
                target,
                record_types,
                server=srv,
                transport=transport,
                timeout=timeout,
                retries=retries,
                port=port,
                resolver=resolver,
            )
            answers.append(
                ResolverAnswer(
                    server=srv,
                    outcome=Outcome.OK,
                    records=result.records,
                    elapsed_ms=result.elapsed_ms,
                )
            )
        except DnsError as exc:
            answers.append(
                ResolverAnswer(server=srv, outcome=_outcome_for(exc), error=exc.message)
            )
    return ResolverComparison(
        target=target,
        record_types=record_types,
        answers=tuple(answers),
        consistent=_comparison_consistent(answers),
    )


def trace(
    name: str,
    rtype: RecordType | str = "A",
    *,
    timeout: float = 5.0,
    port: int = 53,
) -> tuple[TraceStep, ...]:
    """Trace the iterative resolution path of ``name`` from the root down to the answer.

    Raises:
        UsageError: For invalid input.
    """
    if not name or not name.strip():
        raise UsageError("a target name is required")
    record_type = _coerce_types([rtype])[0]
    _validate(timeout, 0, port)
    return trace_resolution(name, record_type, timeout=timeout, port=port)


def reverse_trace(
    ip: str,
    *,
    timeout: float = 5.0,
    port: int = 53,
) -> tuple[TraceStep, ...]:
    """Trace the iterative resolution path of an IP's PTR name.

    Raises:
        UsageError: For an invalid IP.
    """
    if not ip or not ip.strip():
        raise UsageError("an IP address is required")
    try:
        ptr_name = dns.reversename.from_address(ip.strip())
    except (ValueError, dns.exception.SyntaxError) as exc:
        raise UsageError(f"invalid IP address: {ip}") from exc
    _validate(timeout, 0, port)
    return trace_resolution(str(ptr_name), RecordType.PTR, timeout=timeout, port=port)
