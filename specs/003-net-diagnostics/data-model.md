# Phase 1 Data Model: Network Connectivity Diagnostics

Conceptual model for the `net` category. Concrete types are frozen stdlib `@dataclass`es in
`src/opskit/net/models.py` (plus the existing `net/tcp.py` types) with `to_dict()` for the
JSON envelope. No persistence; no global state.

## Enumerations

- **Protocol**: `TCP`, `UDP` — the check/listen mode (FR-004). Serialized `"tcp"` / `"udp"`.
- **Verdict**: `OPEN`, `REFUSED`, `TIMEOUT`, `CLOSED`, `INCONCLUSIVE`, `RESOLVE_FAILED` —
  the per-attempt outcome classification shared by check and probe. TCP uses
  `OPEN`/`REFUSED`/`TIMEOUT`/`RESOLVE_FAILED`; UDP uses
  `OPEN`/`CLOSED`/`INCONCLUSIVE`/`RESOLVE_FAILED` (spec Key Entities; FR-005/FR-008).
- **StopReason**: `INTERRUPT`, `MAX_DURATION`, `MAX_EVENTS`, `ERROR` — why a listener
  session ended (FR-011).
- **ExitCode** (shared enum, additive members): existing `OK=0`, `ERROR=1`, `USAGE=2`,
  `NXDOMAIN=3` (resolution class), `TIMEOUT=6` (no-response class), `PARTIAL=7`,
  `CONNECT_FAILED=8` (refused/closed class) **+ new** `PORT_IN_USE=12`,
  `BIND_PERMISSION=13` (research [R5](research.md#r5-exit-code-allocation--two-additive-members-udp-verdicts-reuse-existing-classes)).

## Entities

### NetTarget *(what was asked — `net/models.py`)*

| Field | Type | Notes |
|-------|------|-------|
| `host` | `str` | hostname, IPv4, or IPv6 literal (normalized: trailing dot stripped, brackets removed) |
| `port` | `int` | **required** — from `host:port` shorthand or `--port`; **no default** (FR-001) |
| `protocol` | `Protocol` | `tcp` default; `udp` via `-u/--udp` |
| `family` | `str \| None` | requested restriction: `"ipv4"` / `"ipv6"` / `None` (FR-003) |

**Validation** (all before any network I/O — FR-001/002): non-empty host;
`1 <= port <= 65535`; missing port → `UsageError`; shorthand/`--port` conflict →
`UsageError`; malformed brackets / ambiguous bare-v6-with-port → `UsageError`; parsing
handles `host:port`, `v6-literal`, `[v6]:port` via the shared splitter extracted from
`tls/models.py` ([R3](research.md#r3-target-parsing--extract-the-bracket-aware-splitter-into-netmodelspy-port-required)) —
`tls.parse_target` delegates to it, behavior unchanged (default 443 stays a **tls** rule).

### CheckResult *(single-shot success outcome — returned by `opskit.net.check`)*

| Field | Type | Notes |
|-------|------|-------|
| `target` | `NetTarget` | |
| `verdict` | `Verdict` | `OPEN` — non-open outcomes **raise** (see raise/return split below) |
| `address` | `str` | IP actually connected to / that replied (dual-stack: first success) |
| `family` | `str` | `ipv4` / `ipv6` — the family actually used (spec edge case) |
| `port` | `int` | |
| `time_ms` | `float` | TCP connect time / UDP reply round-trip (FR-006/FR-008) |

### ProbeAttempt *(one attempt in a repeated-probe run)*

| Field | Type | Notes |
|-------|------|-------|
| `index` | `int` | 1-based |
| `verdict` | `Verdict` | full enum — failures are **data** here, never raised (FR-009) |
| `address` | `str \| None` | populated when an address was attempted/answered |
| `family` | `str \| None` | |
| `time_ms` | `float \| None` | only for attempts that got an answer (UDP: reply received) |
| `error` | `str \| None` | one-line detail for failed attempts |

### ProbeResult *(the aggregate — returned/finalized by `opskit.net.probe`)*

| Field | Type | Notes |
|-------|------|-------|
| `target` | `NetTarget` | |
| `attempts` | `tuple[ProbeAttempt, ...]` | per-attempt results, in order (may be fewer than requested after interrupt) |
| `requested` | `int` | `--count` |
| `completed` / `successes` / `failures` | `int` | counts (FR-009) |
| `replies` / `closed_signals` / `silent` | `int` | UDP-mode breakdown (spec edge case); 0/0/0 for TCP |
| `min_ms` / `avg_ms` / `max_ms` | `float \| None` | over answered attempts only; `None` when none answered |
| `elapsed_ms` | `float` | whole run |

**Derivation rules**: statistics are computed in `api.probe()` (never the CLI); an
interrupted run summarizes completed attempts ([R9](research.md#r9-probe-semantics--streaming-output-shape));
`successes` = attempts with `verdict is OPEN`.

### ListenerSession *(one listener run — `opskit.net.Listener.session`)*

| Field | Type | Notes |
|-------|------|-------|
| `protocol` | `Protocol` | |
| `port` | `int` | |
| `bound_addresses` | `tuple[str, ...]` | wildcard addresses actually bound (one or both families — [R4](research.md#r4-listener-architecture--poll-loop-over-non-blocking-sockets-metadata-only-events)) |
| `started_at` / `stopped_at` | `str` | ISO 8601 UTC |
| `stop_reason` | `StopReason` | |
| `events_received` | `int` | total connections/datagrams (FR-011 summary) |
| `max_duration_s` | `float \| None` | configured stop condition |
| `max_events` | `int \| None` | configured stop condition |

### InboundEvent *(one accepted connection / received datagram — metadata only)*

| Field | Type | Notes |
|-------|------|-------|
| `index` | `int` | 1-based |
| `peer_address` | `str` | |
| `peer_port` | `int` | |
| `family` | `str` | `ipv4` / `ipv6` |
| `timestamp` | `str` | ISO 8601 UTC |

**Invariant (FR-010)**: no payload field exists anywhere in the model — TCP payloads are
never read (socket closed on accept), UDP datagram bytes are discarded at receive time.
Peer strings pass `rich.markup.escape()` at render time (CLAUDE.md cross-cutting rule).

## Error hierarchy (additive — `opskit/net/errors.py`)

```
OpskitError (exit ERROR=1)
├── UsageError (exit USAGE=2)                            [existing]
└── NetError                                             [existing]
    ├── ResolutionError        (exit NXDOMAIN=3)         [existing — reused]
    ├── ConnectRefused         (exit CONNECT_FAILED=8)   [existing — reused]
    ├── ConnectTimeout         (exit TIMEOUT=6)          [existing — reused]
    ├── UdpClosed              (exit CONNECT_FAILED=8)   [new — host signaled port unreachable]
    ├── UdpInconclusive        (exit TIMEOUT=6)          [new — "open or filtered"; hint → listener]
    ├── PortInUse              (exit PORT_IN_USE=12)     [new — listener bind EADDRINUSE]
    └── BindPermissionDenied   (exit BIND_PERMISSION=13) [new — listener bind EACCES/10013]
```

Each type owns its `exit_code` (Art. VII); `core` gains only the two `ExitCode` members.
`UdpInconclusive.message` must name **both** possibilities ("no response — open or filtered
(inconclusive)") and its hint points at the service-side listener and the
protocol-aware-probe caveat (FR-008, spec edge cases).

**Raise/return split**: `check()` **returns** a `CheckResult` only for `OPEN`; every
non-open single-shot outcome **raises** the matching typed error (US6's contract: induced
refusal/timeout raise; the CLI maps them to verdict lines + exit codes via the existing
`collect_outcomes` path). `probe()` never raises for per-attempt failures — attempts are
data (`ProbeAttempt.verdict`) so a failing attempt cannot abort the run (FR-009); it raises
only for pre-flight problems (usage, and resolution failure before the first attempt).
`Listener` raises bind-time errors (`PortInUse`, `BindPermissionDenied`) and never raises
for inbound activity.

## JSON envelope shapes

All commands use the established envelope (`schema_version "1"`, `command`, `query`,
`result`, `error`, `elapsed_ms`). `query` echoes the parsed target + effective controls.

- **`net.check`** — `result` = CheckResult.to_dict() on success; failures per the batch
  contract (`result: null`, populated `error`) — one envelope per target, never dropped
  (FR-013/FR-014).
- **`net.probe`** — `--json`: one envelope; `result` = ProbeResult.to_dict() (attempts +
  stats). `--jsonl`: one envelope per attempt (`result.kind = "attempt"`) then a summary
  envelope (`result.kind = "summary"`) ([R9](research.md#r9-probe-semantics--streaming-output-shape)).
- **`net.listen`** — `--json`: one envelope; `result` = session + events. `--jsonl`: one
  envelope per event (`result.kind = "event"`) then `result.kind = "session"`.

Full field-level examples live in [contracts/cli.md](contracts/cli.md).
