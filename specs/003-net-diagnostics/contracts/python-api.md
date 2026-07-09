# Contract: Python API — `opskit.net`

API-first (constitution Art. VII): the CLI is a client of these. The library raises typed
exceptions, never prints or exits, holds no global state, ships `py.typed`. Signatures are
illustrative; they define the SemVer-governed public contract. Everything here is
**additive** over the existing `opskit.net` surface (`resolve`, `connect`,
`AddressCandidate`, `TcpConnection`, `NetError`, `ResolutionError`, `ConnectRefused`,
`ConnectTimeout` — all unchanged).

## Public surface — `opskit.net.__all__` (after this feature)

```python
from opskit.net import (
    # existing (unchanged)
    resolve, connect, AddressCandidate, TcpConnection,
    NetError, ResolutionError, ConnectRefused, ConnectTimeout,
    # new — functions / classes
    check, probe, Listener, parse_target,
    # new — models / enums
    NetTarget, CheckResult, ProbeAttempt, ProbeResult,
    ListenerSession, InboundEvent, Protocol, Verdict, StopReason,
    # new — errors
    UdpClosed, UdpInconclusive, PortInUse, BindPermissionDenied,
)
```

## Convenience functions

```python
def check(
    target: str,                     # "host:port", "[v6]:port", or "host" + port=
    *,
    port: int | None = None,         # required somewhere — no default port (FR-001)
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,       # "ipv4" | "ipv6" | None (FR-003)
    timeout: float = 5.0,
    retries: int = 2,                # timeouts/silence only; refusal is definitive
) -> CheckResult: ...
```

**Raise/return split** (per [data-model.md](../data-model.md)): `check` **returns** only the
`OPEN` verdict; every other single-shot outcome **raises** the matching typed error —
`UsageError` (bad/missing port, conflict), `ResolutionError` (incl. empty requested
family), `ConnectRefused` / `ConnectTimeout` (TCP), `UdpClosed` / `UdpInconclusive` (UDP).
This is US6's contract: programmatic callers catch the specific failure class; nothing is
printed. The TCP connection is closed before returning; no application data is ever sent
(UDP sends one zero-byte probe datagram — research R2).

```python
def probe(
    target: str,
    *,
    port: int | None = None,
    protocol: Protocol = Protocol.TCP,
    family: str | None = None,
    count: int = 4,
    interval: float = 1.0,           # seconds between attempt starts
    timeout: float = 5.0,
    retries: int = 0,                # within one attempt
    on_attempt: Callable[[ProbeAttempt], None] | None = None,  # streaming hook (CLI/live UIs)
) -> ProbeResult: ...
```

`probe` raises only pre-flight (`UsageError`; `ResolutionError` before the first attempt).
Per-attempt failures are **data** (`ProbeAttempt.verdict`) and never abort the run
(FR-009). `on_attempt` fires after each attempt completes so callers can stream without
threads; a `KeyboardInterrupt` raised from the hook (or during the run) still yields a
`ProbeResult` over the completed attempts via the exception's context — the CLI uses this
for the interrupted-summary behavior; statistics (min/avg/max over answered attempts, UDP
replies/closed/silent split) are computed here, never by callers.

## Listener

```python
class Listener:
    def __init__(
        self,
        port: int,
        *,
        protocol: Protocol = Protocol.TCP,
        max_duration: float | None = None,   # seconds
        max_events: int | None = None,
    ) -> None: ...

    def __enter__(self) -> "Listener": ...      # binds; raises PortInUse / BindPermissionDenied
    def __exit__(self, *exc: object) -> None: ...  # closes sockets, finalizes session

    def events(self) -> Iterator[InboundEvent]: ...
        # yields events as they arrive; returns when a stop condition fires;
        # KeyboardInterrupt propagates after the session is finalized (clean stop)

    @property
    def session(self) -> ListenerSession: ...   # bound addresses immediately after __enter__;
                                                # stop_reason/counters final after events() ends
```

Bind failures raise **at `__enter__`**: `PortInUse` (exit 12) or `BindPermissionDenied`
(exit 13), each with an actionable hint (FR-012). The poll-loop design (research R4)
guarantees Ctrl-C interrupts `events()` promptly on every platform. Payload bytes are never
exposed: `InboundEvent` carries metadata only (FR-010).

## Existing primitives (additive change only)

```python
def resolve(host: str, port: int, *, timeout: float = 5.0,
            family: str | None = None) -> list[AddressCandidate]: ...
def connect(host: str, port: int, *, timeout: float = 5.0, retries: int = 2,
            family: str | None = None) -> tuple[socket.socket, TcpConnection]: ...
```

`family` (new, default `None` = both) restricts `getaddrinfo`; no address in the requested
family raises `ResolutionError`. Existing callers (`opskit.tls`) are unaffected.

## Target parsing

```python
def parse_target(raw: str, *, port: int | None = None,
                 protocol: Protocol = Protocol.TCP,
                 family: str | None = None) -> NetTarget: ...
```

Accepts `host:port`, `[v6]:port`, bare host/IP (+ `port=`); trailing-dot hostnames
normalized; missing port → `UsageError` (no default); shorthand/option conflict →
`UsageError`. The bracket-aware splitter moves here from `tls/models.py` and
`tls.parse_target` delegates to it (research R3) — tls's public behavior (default 443,
SNI rules) is unchanged.

## Usage example (documented in net/README.md; must run as written — SC-008)

```python
from opskit.net import check, probe, Listener, Protocol, ConnectRefused, ConnectTimeout

result = check("db.example.com:5432")
print(result.verdict.value, result.address, result.family, result.time_ms)

try:
    check("db.example.com", port=5433)
except ConnectRefused as exc:
    print(exc.message, "—", exc.hint)
except ConnectTimeout as exc:
    print("filtered?", exc.message)

stats = probe("api.example.com:443", count=10, interval=0.5)
print(stats.successes, "/", stats.completed, "min/avg/max:",
      stats.min_ms, stats.avg_ms, stats.max_ms)

with Listener(8080, protocol=Protocol.TCP, max_events=1) as listener:
    for event in listener.events():
        print("inbound:", event.peer_address, event.peer_port, event.timestamp)
print(listener.session.stop_reason.value, listener.session.events_received)
```

## Compatibility rules

- New exit codes (12, 13), new names in `opskit.net.__all__`, the `family` parameter, and
  the three CLI commands are all **additive** → MINOR release.
- Existing `opskit.net` names keep their exact signatures and semantics; `tls.parse_target`
  behavior is unchanged (proven by the existing tls test suite).
- `CheckResult.to_dict()` / `ProbeResult.to_dict()` / `ListenerSession.to_dict()` /
  `InboundEvent.to_dict()` match the CLI envelopes' `result` objects exactly
  ([contracts/cli.md](cli.md)).
