"""Typed data model for network connectivity diagnostics.

Frozen stdlib dataclasses (no Pydantic) with ``to_dict()`` for the JSON envelope, plus the
shared bracket-aware ``host:port`` splitter every socket category parses targets with (it
moved here from ``tls/models.py``; ``tls`` delegates to it). See
specs/003-net-diagnostics/data-model.md.
"""

from __future__ import annotations

import base64
import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import unquote

from opskit.core.errors import UsageError

_MAX_PORT = 65535

_FAMILIES = ("ipv4", "ipv6")


class Protocol(str, Enum):
    """Transport protocol of a check/probe/listen (FR-004)."""

    TCP = "tcp"
    UDP = "udp"


class Verdict(str, Enum):
    """Per-attempt outcome classification shared by check and probe.

    TCP uses OPEN/REFUSED/TIMEOUT/RESOLVE_FAILED; UDP uses
    OPEN/CLOSED/INCONCLUSIVE/RESOLVE_FAILED (FR-005/FR-008).
    """

    OPEN = "open"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    CLOSED = "closed"
    INCONCLUSIVE = "inconclusive"
    RESOLVE_FAILED = "resolve_failed"
    # Proxied-check outcomes (005-net-proxy-checks FR-009). Proxy-hop refusal/timeout/
    # resolution reuse REFUSED/TIMEOUT/RESOLVE_FAILED; route + wording carry attribution.
    AUTH_REQUIRED = "auth_required"
    TUNNEL_DENIED = "tunnel_denied"
    GATEWAY_FAILED = "gateway_failed"
    NOT_A_PROXY = "not_a_proxy"


class StopReason(str, Enum):
    """Why a listener session ended (FR-011)."""

    INTERRUPT = "interrupt"
    MAX_DURATION = "max_duration"
    MAX_EVENTS = "max_events"
    ERROR = "error"


# --- shared host:port splitting core (used by net and tls target parsing) ---


def split_host_port(text: str, raw: str) -> tuple[str, int | None]:
    """Split a target into (host, shorthand-port); handles ``[v6]:port`` and bare IPv6.

    Args:
        text: The stripped target string to split.
        raw: The original user input, for error messages.

    Raises:
        UsageError: For unclosed brackets, trailing junk, or an ambiguous bare-IPv6
            literal followed by what might be a port.
    """
    if text.startswith("["):  # [v6]:port or [v6]
        closing = text.find("]")
        if closing < 0:
            raise UsageError(f"invalid target (unclosed '['): {raw}")
        rest = text[closing + 1 :]
        if rest.startswith(":"):
            return text[1:closing], parse_port_text(rest[1:], raw)
        if rest:
            raise UsageError(f"invalid target: {raw}")
        return text[1:closing], None
    if text.count(":") == 1:  # host:port (a single colon cannot be bare IPv6)
        host, _, port_text = text.partition(":")
        return host, parse_port_text(port_text, raw)
    if text.count(":") > 1 and not is_ip_literal(text):
        # Multiple colons are only valid as a bare IPv6 literal; anything else is ambiguous.
        raise UsageError(
            f"invalid target: {raw}",
            hint="use [ipv6]:port to add a port to an IPv6 address",
        )
    return text, None  # bare hostname, IPv4, or bare IPv6 literal


def is_ip_literal(host: str) -> bool:
    """Return True when ``host`` parses as an IPv4 or IPv6 address literal."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def parse_port_text(text: str, raw: str) -> int:
    """Parse a shorthand port string, enforcing the 1-65535 range.

    Raises:
        UsageError: When the text is not an integer or is out of range.
    """
    try:
        value = int(text)
    except ValueError as exc:
        raise UsageError(f"invalid port in target: {raw}") from exc
    if not 1 <= value <= _MAX_PORT:
        raise UsageError(f"port must be between 1 and {_MAX_PORT}: {raw}")
    return value


def normalize_host(host: str) -> str:
    """Normalize a parsed host: strip whitespace and any trailing dot."""
    return host.strip().rstrip(".")


# --- net target model ---


@dataclass(frozen=True)
class NetTarget:
    """What the user asked to check (host, required port, protocol, family)."""

    host: str
    port: int
    protocol: Protocol = Protocol.TCP
    family: str | None = None  # "ipv4" | "ipv6" | None (no restriction)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this target."""
        return {
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol.value,
            "family": self.family,
        }


def parse_target(
    raw: str,
    *,
    port: int | None = None,
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,
) -> NetTarget:
    """Parse ``host:port``, ``[v6]:port``, or a bare host plus ``port=`` into a NetTarget.

    Unlike ``tls`` there is **no default port**: a target with no port anywhere is a
    usage error, raised before any network I/O (FR-001).

    Args:
        raw: The user-supplied target string.
        port: Explicit ``--port`` value; must agree with any shorthand port.
        protocol: Transport protocol the check will use.
        family: Address-family restriction (``"ipv4"``/``"ipv6"``/None).

    Raises:
        UsageError: For empty targets, missing/invalid ports, shorthand/option
            conflicts, or an unknown family.
    """
    text = raw.strip()
    if not text:
        raise UsageError("a target host is required")
    if family is not None and family not in _FAMILIES:
        raise UsageError(f"unknown address family: {family}")

    host, shorthand_port = split_host_port(text, raw)
    host = normalize_host(host)
    if not host:
        raise UsageError(f"invalid target (empty host): {raw}")

    if port is not None and shorthand_port is not None and port != shorthand_port:
        raise UsageError(
            f"conflicting ports: --port {port} vs target '{raw}'",
            hint="give the port once (either --port or host:port)",
        )
    effective_port = shorthand_port if shorthand_port is not None else port
    if effective_port is None:
        raise UsageError(
            f"no port given for target: {raw}",
            hint="append :port to the target (e.g. host:443) or pass --port",
        )
    if not 1 <= effective_port <= _MAX_PORT:
        raise UsageError(f"port must be between 1 and {_MAX_PORT}: {effective_port}")
    return NetTarget(host=host, port=effective_port, protocol=protocol, family=family)


# --- proxy model (005-net-proxy-checks) ---


@dataclass(frozen=True)
class ProxySpec:
    """The HTTP proxy a check tunnels through — redacted by construction.

    The ``password`` field is excluded from ``repr()`` and no default rendering
    contains it: :attr:`display` (also ``__str__``) is the only way output paths
    name the proxy, and it always masks the password (``user:***@host:port``).
    The raw secret is read solely by :attr:`authorization` at send time.
    """

    host: str
    port: int
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    @property
    def display(self) -> str:
        """The redacted rendering: username shown, password never (FR-014)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        if self.username is None and self.password is None:
            return f"{host}:{self.port}"
        return f"{self.username or ''}:***@{host}:{self.port}"

    def __str__(self) -> str:
        """Return the redacted :attr:`display` form."""
        return self.display

    @property
    def authorization(self) -> str | None:
        """The ``Proxy-Authorization`` header value (Basic), or None without creds."""
        if self.username is None and self.password is None:
            return None
        creds = f"{self.username or ''}:{self.password or ''}"
        token = base64.b64encode(creds.encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable (redacted) mapping of this proxy."""
        return {"proxy": self.display}


def _redact_proxy_raw(raw: str) -> str:
    """Mask any userinfo in a raw proxy string so error echoes never leak secrets."""
    if "@" not in raw:
        return raw
    head, _, tail = raw.rpartition("@")
    scheme, sep, _userinfo = head.partition("://")
    if sep:
        return f"{scheme}://***@{tail}"
    return f"***@{tail}"


def parse_proxy(raw: str) -> ProxySpec:
    """Parse a proxy specification into a :class:`ProxySpec` (validated, pre-I/O).

    Accepted forms: ``host:port``, ``http://host:port``, and
    ``http://user:pass@host:port`` (userinfo percent-decoded; bracketed IPv6 hosts
    accepted anywhere a host is). The scheme, when given, MUST be plain ``http`` —
    the ubiquitous corporate CONNECT proxy. A proxy without a port is an error.
    Error messages echo the input with any credentials masked (FR-014).

    Raises:
        UsageError: For unsupported schemes (https/socks5/...), a missing or invalid
            port, an empty host, embedded whitespace, or a non-empty path.
    """
    text = raw.strip()
    safe = _redact_proxy_raw(text)  # the only form error messages may echo
    if not text:
        raise UsageError("a proxy is required (host:port)")
    if any(ch.isspace() for ch in text):
        raise UsageError(f"invalid proxy (whitespace): {safe}")

    if "://" in text:
        scheme, _, rest = text.partition("://")
        if scheme.lower() != "http":
            raise UsageError(
                f"unsupported proxy scheme '{scheme}': {safe}",
                hint="only plain HTTP CONNECT proxies are supported (http://host:port)",
            )
        text = rest
    if text.endswith("/"):
        text = text[:-1]
    if "/" in text:
        raise UsageError(f"invalid proxy (unexpected path): {safe}")

    username: str | None = None
    password: str | None = None
    userinfo, sep, authority = text.rpartition("@")
    if sep:
        user_part, cred_sep, pass_part = userinfo.partition(":")
        username = unquote(user_part)
        password = unquote(pass_part) if cred_sep else None
    else:
        authority = text

    host, port = split_host_port(authority, safe)
    host = normalize_host(host)
    if not host:
        raise UsageError(f"invalid proxy (empty host): {safe}")
    if port is None:
        raise UsageError(
            f"no port given for proxy: {safe}",
            hint="append :port to the proxy (e.g. proxy.corp.example:3128)",
        )
    return ProxySpec(host=host, port=port, username=username, password=password)


def proxy_exempt(host: str, no_proxy: Sequence[str]) -> bool:
    """Return True when ``host`` matches a NO_PROXY-style exemption list.

    Matching is deliberately simple (spec assumption): case-insensitive exact host
    or domain-suffix match, tolerant of a leading dot on entries; ``*`` exempts
    everything. Entries may carry a ``:port`` suffix (ignored). CIDR/IP-range
    matching is out of scope.
    """
    candidate = normalize_host(host).lower()
    for raw_entry in no_proxy:
        entry = raw_entry.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        if entry.startswith("["):
            # Bracketed IPv6, optionally with a port: [2001:db8::1] / [::1]:443.
            entry = entry[1:].partition("]")[0]
        elif entry.count(":") == 1:
            # Exactly one colon = host:port; more = a bare IPv6 literal (no port).
            head, _, tail = entry.partition(":")
            if tail.isdigit():
                entry = head
        entry = entry.lstrip(".").rstrip(".")
        if not entry:
            continue
        if candidate == entry or candidate.endswith("." + entry):
            return True
    return False


@dataclass(frozen=True)
class Route:
    """How a target was actually checked: direct, or via which proxy (redacted).

    Attached to every check/probe result and emitted in every envelope (always
    present — an explicit ``direct`` when no proxy is in play), so mixed batches
    and env-nominated proxies are never silent.
    """

    via: str = "direct"  # "direct" | "http-proxy"
    proxy: str | None = None  # redacted display string; None when direct
    source: str = "default"  # "default" | "flag" | "env:<VAR>" | "no-proxy-exemption"

    @classmethod
    def direct(cls, source: str = "default") -> Route:
        """A direct route, optionally noting why (e.g. a NO_PROXY exemption)."""
        return cls(via="direct", proxy=None, source=source)

    @classmethod
    def via_proxy(cls, spec: ProxySpec, source: str) -> Route:
        """A proxied route through ``spec`` (stored redacted), noting its source."""
        return cls(via="http-proxy", proxy=spec.display, source=source)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable route object (envelope contract)."""
        return {"via": self.via, "proxy": self.proxy, "source": self.source}


_DIRECT_ROUTE = Route()


# --- result models ---


@dataclass(frozen=True)
class CheckResult:
    """The successful (OPEN) outcome of one reachability check.

    Non-open outcomes raise the matching typed error instead of returning
    (see the raise/return split in data-model.md).
    """

    target: NetTarget
    verdict: Verdict
    address: str
    family: (
        str  # "ipv4" | "ipv6" — the family actually used (the proxy hop when proxied)
    )
    port: int
    time_ms: float  # TCP connect / UDP reply round-trip / tunnel establishment
    route: Route = _DIRECT_ROUTE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the CLI envelope's result.

        The route is deliberately NOT part of this payload: it lives at the envelope
        level (one always-present ``route`` key per target, success or failure) so a
        failed target — whose ``result`` is ``null`` — still discloses its route.
        """
        return {
            "verdict": self.verdict.value,
            "address": self.address,
            "family": self.family,
            "port": self.port,
            "time_ms": round(self.time_ms, 3),
        }


@dataclass(frozen=True)
class ProbeAttempt:
    """One attempt in a repeated-probe run — failures are data, never raised (FR-009)."""

    index: int  # 1-based
    verdict: Verdict
    address: str | None = None
    family: str | None = None
    time_ms: float | None = None  # only for attempts that got an answer
    error: str | None = None  # one-line detail for failed attempts

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this attempt."""
        return {
            "index": self.index,
            "verdict": self.verdict.value,
            "address": self.address,
            "family": self.family,
            "time_ms": round(self.time_ms, 3) if self.time_ms is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class ProbeResult:
    """The aggregate of a repeated-probe run (statistics computed in the API layer)."""

    target: NetTarget
    attempts: tuple[ProbeAttempt, ...]
    requested: int
    completed: int
    successes: int
    failures: int
    replies: int  # UDP-mode breakdown; 0 for TCP
    closed_signals: int
    silent: int
    min_ms: float | None  # over answered attempts only; None when none answered
    avg_ms: float | None
    max_ms: float | None
    elapsed_ms: float
    route: Route = (
        _DIRECT_ROUTE  # one route per run; timings are tunnel times when proxied
    )

    def summary_dict(self) -> dict[str, Any]:
        """Return the summary statistics (the ``--jsonl`` summary envelope's result)."""
        return {
            "requested": self.requested,
            "completed": self.completed,
            "successes": self.successes,
            "failures": self.failures,
            "replies": self.replies,
            "closed_signals": self.closed_signals,
            "silent": self.silent,
            "min_ms": round(self.min_ms, 3) if self.min_ms is not None else None,
            "avg_ms": round(self.avg_ms, 3) if self.avg_ms is not None else None,
            "max_ms": round(self.max_ms, 3) if self.max_ms is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping: per-attempt results plus statistics."""
        return {
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            **self.summary_dict(),
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


@dataclass(frozen=True)
class ListenerSession:
    """Summary of one listener run — metadata only, no payload field exists (FR-010)."""

    protocol: Protocol
    port: int
    bound_addresses: tuple[str, ...]
    started_at: str  # ISO 8601 UTC
    stopped_at: str | None  # None while the session is still running
    stop_reason: StopReason | None  # None while the session is still running
    events_received: int
    max_duration_s: float | None
    max_events: int | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping matching the session envelope's result."""
        return {
            "protocol": self.protocol.value,
            "port": self.port,
            "bound_addresses": list(self.bound_addresses),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "events_received": self.events_received,
            "max_duration_s": self.max_duration_s,
            "max_events": self.max_events,
        }


@dataclass(frozen=True)
class InboundEvent:
    """One accepted connection / received datagram — peer metadata only (FR-010)."""

    index: int  # 1-based
    peer_address: str
    peer_port: int
    family: str  # "ipv4" | "ipv6"
    timestamp: str  # ISO 8601 UTC

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of this event."""
        return {
            "index": self.index,
            "peer_address": self.peer_address,
            "peer_port": self.peer_port,
            "family": self.family,
            "timestamp": self.timestamp,
        }
