"""SRV-based domain-controller discovery via the in-tree dns category (R4).

Queries ``_ldap._tcp.dc._msdcs.<domain>`` (AD's DC-specific record) first, falling back
to ``_ldap._tcp.<domain>`` (generic LDAP). Candidates are ordered by SRV priority
(ascending) then weight (descending) — deterministic, no weighted randomness — and the
SRV-advertised port is deliberately ignored in favor of the security mode's port.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable

from opskit.ad.errors import DiscoveryError
from opskit.dns import api as dns_api
from opskit.dns.errors import DnsError

# Injectable lookup signature: (record_name, types, timeout=...) -> LookupResult-like
# object exposing .records with (value: str) items. Defaults to opskit.dns.api.lookup.
LookupFn = Callable[..., object]


def _parse_srv(value: str) -> tuple[int, int, str] | None:
    """Parse an SRV record's rendered value into (priority, weight, host)."""
    parts = value.split()
    if len(parts) != 4:  # noqa: PLR2004 - SRV rdata is exactly 4 fields
        return None
    try:
        priority, weight = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    host = parts[3].strip().rstrip(".")
    if not host:
        return None
    return priority, weight, host


def discover_dcs(
    domain: str,
    *,
    timeout: float = 5.0,
    lookup: LookupFn | None = None,
) -> list[str]:
    """Discover the domain's directory servers from its published SRV records.

    Args:
        domain: The domain name to discover directory servers for.
        timeout: DNS query timeout, seconds.
        lookup: Optional lookup function (injected in tests); defaults to
            :func:`opskit.dns.api.lookup` with the system resolver.

    Returns:
        Candidate hostnames ordered by SRV priority (asc) then weight (desc),
        de-duplicated preserving order.

    Raises:
        DiscoveryError: When neither SRV name yields any usable record.
    """
    lookup_fn: LookupFn = lookup if lookup is not None else dns_api.lookup
    names = (f"_ldap._tcp.dc._msdcs.{domain}", f"_ldap._tcp.{domain}")
    for name in names:
        try:
            result = lookup_fn(name, ("SRV",), timeout=timeout)
        except DnsError:
            continue
        records: Sequence[object] = getattr(result, "records", ())
        parsed = [
            srv
            for srv in (_parse_srv(getattr(record, "value", "")) for record in records)
            if srv is not None
        ]
        parsed.sort(key=lambda srv: (srv[0], -srv[1]))
        hosts: list[str] = []
        for _, _, host in parsed:
            if host not in hosts:
                hosts.append(host)
        if hosts:
            return hosts
    raise DiscoveryError(
        f"no directory servers found for domain: {domain}",
        hint=(
            "check the domain name and DNS (try: opskit dns lookup "
            f"_ldap._tcp.dc._msdcs.{domain} -t SRV), or pass --server explicitly"
        ),
    )
