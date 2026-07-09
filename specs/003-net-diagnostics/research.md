# Phase 0 Research: Network Connectivity Diagnostics

Decisions resolving every technical unknown in the plan's Technical Context. Format per
speckit: Decision / Rationale / Alternatives considered.

## R1. TCP check primitive — reuse `opskit.net.tcp` with an additive `family` parameter

**Decision**: `net check` (TCP mode) and `net probe` are built directly on the **existing**
`opskit.net.tcp.resolve()` / `connect()` (shipped by the TLS feature as the reusable seam,
its FR-018). The only change is an additive keyword parameter `family: str | None = None`
(`"ipv4"` / `"ipv6"` / `None`) on both functions, mapped to `getaddrinfo`'s `family` argument
(`AF_INET` / `AF_INET6` / `AF_UNSPEC`). A requested family with no addresses surfaces as
`ResolutionError` ("no IPv6 address for host…"), satisfying FR-003's resolution-class rule.

**Rationale**: `connect()` already implements everything US1 needs — platform dual-stack
candidate order, timeout retries, refusal-is-definitive short-circuit, `OSError`
normalization into `ResolutionError`/`ConnectRefused`/`ConnectTimeout`, and a
`TcpConnection` report (address, family, port, connect_ms). Re-implementing any of it would
create the exact duplicate-logic problem the seam was built to prevent. The check simply
closes the returned socket immediately (FR-006: no application data).

**Alternatives considered**: a separate `check`-specific connect path (rejected: divergent
retry/classification semantics between `tls` and `net` would violate SC-003's cross-category
consistency); adding `family` to a new wrapper only (rejected: `tls` gains the same
capability for free by putting it on the primitive).

## R2. UDP probe mechanics — connected-UDP socket, one empty datagram, honest tri-state

**Decision**: New `opskit/net/udp.py` with `udp_probe(host, port, *, timeout, retries,
family)`. Implementation:

1. Resolve candidates via the shared resolver (`SOCK_DGRAM` hint).
2. Create a UDP socket and **`connect()` it** to the candidate (connected-UDP): the OS then
   delivers ICMP *port unreachable* back to this socket as a socket error instead of
   silently dropping it.
3. Send a **single zero-byte datagram** (`send(b"")`) — a valid, deliverable UDP packet with
   no protocol payload (FR-008 / FR-018).
4. `recv()` with the per-attempt timeout and classify:
   - **reply datagram received** → `open`, with response time;
   - **`ConnectionRefusedError` (Linux/macOS `ECONNREFUSED`) or `ConnectionResetError`
     (Windows `WSAECONNRESET`, surfaced on the `recv` following the ICMP)** → `closed` →
     raise `UdpClosed` (connection-refused exit class 8, per FR-008);
   - **timeout with no reply and no ICMP** → after exhausting `retries` re-sends, raise
     `UdpInconclusive` (no-response exit class 6) whose message says
     "no response — open or filtered (inconclusive)" and whose hint suggests the
     service-side listener and names the protocol-aware-tooling caveat;
   - **any other `OSError`** (network unreachable, invalid argument, …) → normalized into
     the shared hierarchy (`ConnectRefused` for unreachable-class, else `NetError`) — a raw
     `OSError` never escapes (Art. VI).

Retries apply **only to the silent case** (a re-sent probe after silence, mirroring
"retries apply to timeouts"); a received ICMP unreachable is definitive like a TCP refusal.

**Rationale**: connected-UDP is the only pure-stdlib, unprivileged way to see ICMP port
unreachable on all three platforms — raw ICMP sockets need root/administrator, violating the
"any user, identical everywhere" assumption. The zero-byte datagram honors the
no-application-data stance while still eliciting a reply from services that answer any
datagram and an ICMP from closed ports. The tri-state mapping implements the spec's honesty
contract exactly: `open` is claimed **only** on a received reply (FR-008, SC-007).

**Alternatives considered**: raw ICMP sockets (rejected: privileged, per-OS divergent);
protocol-aware payloads such as a DNS query on 53 (rejected by the spec — the `dns`
category's job; also violates the no-payload stance); unconnected `recvfrom` +
`sendto` (rejected: ICMP errors are not reliably delivered to unconnected UDP sockets, so
`closed` would be undetectable); declaring silence "closed" after N probes (rejected:
exactly the false conclusion the spec forbids).

## R3. Target parsing — extract the bracket-aware splitter into `net/models.py`; port required

**Decision**: Move the `host:port` / `[v6]:port` splitting helpers (`_split_host_port`,
`_parse_port`, `_is_ip_literal`, trailing-dot normalization) from `tls/models.py` into
`net/models.py` as the shared, semi-private parsing core. `net.models.parse_target(raw, *,
port=None, default_port=None)` builds on it with the net rule: **no default port** — a
target with no port anywhere is a `UsageError` before any I/O (FR-001), and shorthand vs
`--port` conflicts stay usage errors. `tls.models.parse_target` keeps its exact public
behavior (default 443, SNI handling) by delegating the splitting to the shared helper;
existing tls tests prove no behavior change.

**Rationale**: the splitter is the trickiest parsing in the codebase (bare-IPv6 colon
ambiguity, unclosed brackets) and exists today only inside `tls`; `net` needs identical
semantics. One implementation, property-tested once with Hypothesis, eliminates the
duplicate-parser risk. Direction of the move (tls → net) follows the dependency that already
exists (`tls` imports `opskit.net`; never the reverse).

**Alternatives considered**: copy the parser into `net` (rejected: guaranteed drift);
hoist it into `core` (rejected: `core` stays category-agnostic and this is
target-of-a-socket parsing, which the net category owns; `tls` importing `net` is already
established).

## R4. Listener architecture — poll loop over non-blocking sockets; metadata-only events

**Decision**: New `opskit/net/listener.py` with a `Listener` class:

- **Binding**: bind the user's port on the wildcard address of **both** families where
  available — one `AF_INET` socket and one `AF_INET6` socket (with `IPV6_V6ONLY` left at
  the platform default on the v6 socket since v4 traffic has its own socket) — and report
  every bound address in the session. If one family's bind fails (stack absent), listen on
  the other; only both failing is an error.
- **Event loop**: sockets are non-blocking, multiplexed with `selectors.DefaultSelector`
  using a **short poll timeout (~0.25 s)**. Each tick checks the stop conditions
  (deadline from `--max-duration`, count from `--max-events`) and lets `KeyboardInterrupt`
  be delivered — on Windows a blocking `accept()`/`recv()` cannot be interrupted by
  Ctrl-C, so the poll loop is what makes "interrupt always stops cleanly on every
  platform" (FR-011) true.
- **TCP events**: `accept()` → record `InboundEvent(peer_address, peer_port, family,
  timestamp)` → **immediately close** the accepted socket without reading (payload never
  read, never stored — FR-010).
- **UDP events**: `recvfrom(65535)` → record the same metadata; the datagram's bytes are an
  unavoidable artifact of receiving and are **discarded immediately, never rendered or
  persisted**.
- **API shape**: `Listener` is a context manager whose `events()` generator yields
  `InboundEvent`s as they arrive and returns when a stop condition fires;
  `listener.session` then holds the `ListenerSession` summary (bound addresses, stop
  reason, counts, timings). No threads, no global state (Art. VII); the CLI iterates and
  renders, the library never prints.
- **Bind-error normalization**: `EADDRINUSE` (incl. Windows `WSAEADDRINUSE` 10048) →
  `PortInUse` (exit 12, hint: pick another port / find what's using it);
  `EACCES`/`EPERM` (incl. Windows `WSAEACCES` 10013) → `BindPermissionDenied` (exit 13,
  hint: ports below 1024 need elevation — choose an unprivileged port). Any other bind
  `OSError` → typed `NetError`. FR-012 satisfied; raw `OSError` never reaches the CLI.
- **Exit semantics** (resolving the spec's "0 if at least the expected activity occurred"):
  Ctrl-C → exit 0 always (the user chose to stop; the summary is the answer). `--max-events N`
  reached → exit 0 (the expected activity occurred). `--max-duration` expiring with **zero**
  events → exit 6 (no-response class): the run is a valid diagnostic ("nothing reached me"),
  and scripts pairing listener + remote check need that answer to be branchable; with ≥1
  event → exit 0.

**Rationale**: a single-threaded poll loop is the simplest design that is simultaneously
interrupt-safe on Windows, dual-stack, and free of shared mutable state; `selectors` is the
stdlib's portable readiness API. Closing accepted TCP sockets unread is the strongest
possible implementation of "payload neither displayed nor stored".

**Alternatives considered**: blocking `accept()` in a thread + signal coordination
(rejected: Windows Ctrl-C + thread-shutdown complexity for zero benefit); `asyncio`
(rejected: an event-loop framework for two sockets complicates the typed, synchronous
library surface); one dual-stack `V6ONLY=0` socket (rejected: default and configurability
differ per platform — two explicit sockets behave identically everywhere, Art. VI);
exposing a `--bind` address option (rejected for v1: the diagnostic question is "can
traffic reach me on this port", wildcard answers it; additive later if needed).

## R5. Exit-code allocation — two additive members; UDP verdicts reuse existing classes

**Decision**: Extend the shared `ExitCode` enum with **`PORT_IN_USE = 12`** and
**`BIND_PERMISSION = 13`**. Everything else reuses the documented classes: usage 2,
resolution failure 3 (shared with DNS NXDOMAIN / tls resolve), timeout / no-response 6,
PARTIAL 7, connection refused 8 (`CONNECT_FAILED`). UDP maps `closed` → 8 (the host
actively signaled "nothing here" — same outcome class as a TCP refusal, per FR-008) and
`inconclusive` → 6 (nothing answered — same class as a TCP filtered timeout). Each new
error type owns its code (`UdpClosed`→8, `UdpInconclusive`→6, `PortInUse`→12,
`BindPermissionDenied`→13); `core` gains only the two enum members.

**Rationale**: FR-013 demands distinct classes for usage / resolution / refused /
no-response / bind failure / partial — this allocation gives each a distinct code while
keeping the public code space compact and semantically consistent across categories
(established by tls research R5: "a timeout is a timeout"). Scripts can already branch on
3/6/7/8; only the two genuinely new outcome classes get new numbers.

**Alternatives considered**: distinct codes for UDP closed vs TCP refused (rejected: the
spec itself assigns UDP closed "the connection-refused exit class"); one combined
`BIND_FAILED` code (rejected: FR-012 requires busy-port and permission-denied to be
distinct, and the remediation differs — wait/pick another port vs use an unprivileged
port); per-category ranges (rejected in 002 already).

## R6. Cross-platform variance & deterministic test strategy

**Decision**: Three deterministic layers gate CI; real-network tests never do
(`@pytest.mark.network`):

1. **Unit + Hypothesis**: `parse_target` (port-required rule, `[v6]:port`, bare-v6
   ambiguity, trailing dots, port bounds) property-tested; stats math (min/avg/max,
   mixed-outcome counting) unit-tested; interval/count validation.
2. **Injected fakes**: timeout and filtered paths (monkeypatched socket layer — loopback
   cannot produce real filtering), UDP silent path, Windows-style
   `ConnectionResetError`-on-recv, resolution failures. Every rcode-equivalent failure
   class is reachable without a network.
3. **In-process loopback (real sockets)**: open TCP (listening socket) → `open` with
   timing; **closed TCP port asserted as the `NetError` class family** — Linux/macOS
   refuse, Windows loopback can time out (the canonical CLAUDE.md lesson; SC-003's
   classification consistency is proven by asserting the *user-visible class* is one of
   the documented pair per platform, and by the mock layer pinning each classification
   branch exactly); loopback UDP echo server → `open`; closed loopback UDP port →
   asserted as **`{UdpClosed, UdpInconclusive}`** because ICMP-to-socket-error delivery
   is platform/rate-limit dependent (the spec's own edge case) while the mocked ICMP path
   pins `UdpClosed` exactly; listener⇄check pairing end-to-end on loopback in both TCP and
   UDP modes (SC-006), including stop-condition and zero-event runs.

**Rationale**: the PR matrix runs only min+max Python and all OS variance lives in socket
behavior — encoding platform tolerance into the loopback assertions (class family, verdict
set) while pinning exact classifications in the mock layer gives full branch coverage
without flaky OS-dependent tests. This is the "green PR is not a green main" rule applied
from the start rather than retrofitted.

**Alternatives considered**: skip-markers per OS (rejected: leaves classification branches
untested on the skipping OS); asserting exact per-OS outcomes with `sys.platform` switches
(rejected: brittle to kernel/firewall config — e.g. a local firewall turns loopback refusal
into timeout legitimately); Docker-network fault injection (rejected: not available on CI
macOS/Windows runners).

## R7. CLI shape & option surface — three commands; variadic targets; stdin batch

**Decision**: One Typer sub-app `opskit net` with three commands:

- **`opskit net check [TARGETS]... `** — variadic positional targets (`host:port`,
  `[v6]:port`), plus `-p/--port` (applies to any target given without a port; must agree
  with shorthand), `-u/--udp` (protocol switch, default TCP per FR-004), `-4/--ipv4` /
  `-6/--ipv6` (mutually exclusive family restriction), `--timeout` 5.0 / `--retries` 2,
  `-i/--input-file` (with **`-` meaning stdin**), `--watch`, `--json/--jsonl/--no-color`.
- **`opskit net probe TARGET`** — single target; `--count` 4 / `--interval` 1s (reusing the
  `parse_interval` grammar: `500ms`, `2s`, `1m`) plus the same protocol/family/timeout/
  retries/output options; no `--input-file` (probe is one target's stability story, batch
  is `check`'s job).
- **`opskit net listen PORT`** — positional port; `-u/--udp`, `--max-duration` (interval
  grammar) and `--max-events` stop conditions, `--json/--jsonl/--no-color`. No `--watch`
  (it is already long-running).

`core/cliutils.py` gains two **additive, category-agnostic** helpers: variadic-positional
target collection (`collect_targets` today takes one optional positional; a sibling
accepting a list joins N positionals + file/stdin, first-appearance order) and
`--input-file -` reading targets from stdin (same blank/`#` filtering as files). Existing
signatures are untouched (dns/tls keep working unmodified).

**Rationale**: three commands map one-to-one onto the spec's three capabilities — merging
probe into check behind a `--count` flag was considered but makes the output contract
muddy (a single-verdict envelope vs a per-attempt stream) and buries the ping-style story;
`nc`-style `-u` is the conventional, memorable protocol switch; variadic positionals are
what "audit many endpoints" naturally looks like in a shell (`opskit net check a:443 b:22
c:5432`); stdin input satisfies FR-014's pipe requirement using the established `-`
convention. All cross-cutting CLAUDE.md rules (eager annotations, `Optional[X]`, escape,
`make_console`) apply from the start.

**Alternatives considered**: `--protocol tcp|udp` enum option (rejected: more typing for
the same information, `-u` matches nc muscle memory; the enum can be added later without
breaking `-u`); batch support on `probe` (rejected: interleaved per-attempt streams from
multiple targets are unreadable and un-scriptable; `check --watch`/batch covers fleet
monitoring); a separate `udp-check` command (rejected: FR-004 frames protocol as a mode of
the same check, and everything else — batch, watch, JSON — is identical).

## R8. Watch-change signature for `net check`

**Decision**: The `--watch` change signature is the JSON of per-target
**(target, verdict class, connected address, address family)** — timings are excluded.
For failure outcomes the error `code` is the verdict class (e.g. `connect_refused` →
`connect_timeout` flags a change); for successes a change of connected address or family
(e.g. IPv6 path recovering, DNS repointing) also flags.

**Rationale**: matches dns/tls watch semantics — flag *meaningful* state transitions
(open → refused during a deploy is US3's example), never timing jitter. Address/family in
the signature catches the dual-stack failover case the spec's edge cases call out.

**Alternatives considered**: verdict-only signature (rejected: silently misses a failover
from IPv6 to IPv4 that an operator watching a dual-stack cutover cares about); including
connect_ms buckets (rejected: latency alarming is `probe`'s job, and any bucketing
threshold is arbitrary noise).

## R9. Probe semantics & streaming output shape

**Decision**:

- **Attempt loop**: `--count` attempts (default 4, ping-like), sleeping `--interval`
  (default 1 s) between attempt *starts*; every attempt runs regardless of prior failures
  (FR-009 — a refusal is definitive *for that attempt* but the run continues, because the
  question is stability over time); per-attempt outcome uses the same classification as
  `check` with `retries=0` inside an attempt (the count IS the retry story).
- **Statistics**: attempts / successes / failures, min/avg/max over attempts that got an
  answer (TCP: connected; UDP: reply received). UDP additionally counts replies vs closed
  signals vs silence separately (spec edge case). Computed in `api.probe()`, returned as
  `ProbeResult` — never computed in the CLI.
- **Interrupt**: `KeyboardInterrupt` mid-run finalizes statistics over completed attempts
  and renders the summary before exiting (spec edge case); the API supports this by
  yielding attempts as they complete (generator) with the summary built from whatever
  completed.
- **Exit code**: `aggregate_exit` over per-attempt codes — 0 all-success, the uniform
  class if every attempt failed identically, else 7 PARTIAL (plan's "same aggregate rule
  across attempts").
- **NDJSON stream shapes** (`--jsonl`): `net check` emits one envelope per **target**
  (established batch shape). `net probe` emits one envelope per **attempt** as it
  completes, then one **summary** envelope; the `result` object carries
  `"kind": "attempt"` / `"kind": "summary"` so consumers can filter a uniform stream.
  `net listen` emits one envelope per **event** plus a final `"kind": "session"` summary
  envelope. With `--json` (non-streaming), each command emits a single envelope whose
  result contains the full aggregate (probe: attempts + stats; listen: session + events).
  All envelopes keep the established `schema_version "1"` shape with commands
  `net.check` / `net.probe` / `net.listen`.

**Rationale**: per-attempt streaming is what makes `probe --jsonl` pipeable into live
tooling (the ping analogy demands attempt-by-attempt feedback, FR-013 names per-attempt
results as a batchable stream); the `kind` discriminator keeps one NDJSON stream
self-describing without inventing a second envelope schema (additive within
`schema_version 1` — new commands, no changes to existing envelopes).

**Alternatives considered**: summary-only probe JSON (rejected: FR-013 explicitly lists
per-attempt probe results as streamable); a distinct envelope schema for stream events
(rejected: two schemas to govern under SemVer where one suffices); emitting the summary on
stderr (rejected: machine output goes to stdout, and Art. IX forbids dropping data from
machine streams).
