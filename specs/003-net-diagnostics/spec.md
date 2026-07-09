# Feature Specification: Network Connectivity Diagnostics

**Feature Branch**: `003-net-diagnostics`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "net" — the network-connectivity category from the opskit roadmap
(docs/PLAN.md backlog): TCP connect check (telnet-style), ping-style reachability/latency
probing, and an nc-style temporary port listener, delivered under all established opskit
contracts (API-first, JSON envelope, structured exit codes, batch, watch, cross-platform).
Amended 2026-07-08: UDP port checks added to scope (honest open/closed/inconclusive
semantics, plus a UDP listener mode as the definitive inbound companion); batch file input
is explicitly `--input-file` / `-i`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Check whether a port is reachable (Priority: P1)

An engineer needs to answer the most common ops question — "can I reach this service?" — without
remembering per-OS incantations (`telnet` vs `Test-NetConnection` vs `nc -zv`). They run one
command with a host and port and get an immediate verdict: the port is open (with the address
actually connected to and how long the connection took), the connection was refused, nothing
answered before the timeout, or the name didn't resolve — the same way on Windows, macOS, and
Linux.

**Why this priority**: This is the category's core value and the single most frequent
connectivity question in day-to-day operations; every other capability builds on it.

**Independent Test**: Run the check against a listening service and confirm an "open" verdict
with connected address and timing and exit code 0; run it against a closed port, a filtered
(non-answering) port, and an unresolvable name, and confirm each yields a distinct verdict,
actionable hint, and distinct exit code.

**Acceptance Scenarios**:

1. **Given** a service listening on `host:port`, **When** the user checks it, **Then** the output
   reports the port as open, shows the address actually connected to (and its address family),
   the connection time, and the process exits 0.
2. **Given** a reachable host with nothing listening on the port, **When** the user checks it,
   **Then** the verdict is "connection refused" with a hint that no service is listening there,
   using the connection-refused exit class.
3. **Given** a port that never answers (silently dropped/filtered), **When** the user checks it,
   **Then** the verdict is "no response before timeout" with a hint that the port may be
   filtered by a firewall — clearly distinct from refusal — using the timeout exit class.
4. **Given** an unresolvable hostname, **When** the user checks it, **Then** the outcome is a
   resolution failure (not a connection error) with a hint pointing at the DNS diagnostics
   category, using the resolution-failure exit class.
5. **Given** a target with no port (and no port option), **When** the user runs the check,
   **Then** the input is rejected before any network activity as a usage error.

---

### User Story 2 - Check a UDP port honestly (Priority: P2)

An engineer needs to verify reachability of a UDP service — DNS on 53, NTP on 123, syslog on
514, a VPN endpoint. UDP has no handshake, so unlike TCP a silent port proves nothing; existing
tools either can't do UDP or imply more certainty than the protocol allows. The user selects
UDP mode for the same check command and gets an honest verdict: a reply came back (open), the
host answered that nothing listens there (closed), or nothing answered at all — reported
explicitly as "open or filtered — inconclusive", never as a false pass or false fail, with a
hint on how to get a definitive answer (check from the service side, e.g. with the listener).

**Why this priority**: UDP services are everyday ops reality and the most common source of
false conclusions in connectivity debugging; an honest UDP verdict is more valuable than a
confident wrong one.

**Independent Test**: Run a UDP check against a closed local port and confirm a "closed"
verdict; against a responding UDP service and confirm "open" with response time; against a
silent/filtered port and confirm the explicitly inconclusive verdict with its explanatory hint.

**Acceptance Scenarios**:

1. **Given** a UDP service that replies to a probe datagram, **When** the user checks it in UDP
   mode, **Then** the verdict is open, with the responding address and the response time, and
   the process exits 0.
2. **Given** a reachable host with nothing listening on the UDP port, **When** the user checks
   it, **Then** the verdict is closed (the host signaled the port unreachable), using the
   connection-refused exit class.
3. **Given** a UDP port that never answers, **When** the user checks it, **Then** the verdict is
   "no response — open or filtered (inconclusive)" using the no-response exit class, with
   wording that names both possibilities and a hint suggesting a service-side check (e.g., the
   temporary listener) for a definitive answer.
4. **Given** UDP mode, **When** the user combines it with repeated probes or batch input,
   **Then** those work exactly as they do for TCP, with response times reported for attempts
   that received a reply.

---

### User Story 3 - Measure connection latency and stability (Priority: P2)

An engineer suspects a flaky or slow network path to a service. They probe the same target a
configurable number of times and get per-attempt timings plus a summary — attempts made,
successes, failures, and minimum/average/maximum connection time — the way `ping` answers "is it
slow or lossy?", but for a TCP service and without requiring elevated privileges.

**Why this priority**: "It's reachable but sometimes slow/dropping" is the immediate follow-up
to the P1 verdict; repeated probing turns a snapshot into a diagnosis.

**Independent Test**: Probe a healthy target N times and confirm N per-attempt results plus a
summary with plausible min/avg/max; probe a target that intermittently fails and confirm the
summary counts successes and failures separately while the run still completes.

**Acceptance Scenarios**:

1. **Given** a reachable target and a probe count, **When** the user runs a repeated probe,
   **Then** each attempt's outcome and connection time is reported as it happens, followed by a
   summary with attempts, successes, failures, and min/avg/max connection time.
2. **Given** some attempts fail mid-run, **When** the run completes, **Then** failures are
   counted (not aborting the run) and the exit code reflects the outcome: success if all
   attempts succeeded, the uniform failure class if all failed identically, else the partial
   class.
3. **Given** `--watch` mode, **When** the user watches a target, **Then** the check re-runs on
   the chosen interval until interrupted, flagging when the outcome changes (e.g., open →
   refused during a deploy).

---

### User Story 4 - Audit many endpoints at once (Priority: P2)

An engineer validates connectivity for an estate — a dependency list, a firewall change ticket,
a migration runbook — by feeding many `host:port` targets from arguments, a file via
`--input-file` / `-i`, or a pipe. One dead endpoint must not abort the audit, and the results
must be consumable by scripts.

**Why this priority**: Fleet checks are where the tool replaces ad-hoc shell loops, but they
depend on the single-target flow being right first.

**Independent Test**: Supply a file mixing open, refused, filtered, and unresolvable targets;
confirm every line is processed, machine output contains one entry per target including
failures, and the aggregate exit code follows the established batch rule.

**Acceptance Scenarios**:

1. **Given** targets supplied as multiple arguments, an input file via `--input-file` / `-i`
   (one target per line, blank lines and `#` comments ignored), or standard input, **When** the
   user runs a batch check, **Then** every target is checked and reported — no abort on first
   failure.
2. **Given** a mixed batch where some targets fail, **When** machine-readable output is
   requested, **Then** every target appears in the output — failed ones with their error, never
   silently dropped — and the exit code is 0 only if all pass, the uniform class if all share
   one failure class, else the partial class.

---

### User Story 5 - Verify inbound reachability with a temporary listener (Priority: P3)

An engineer on their own machine needs to prove the *other* direction: "can traffic reach me on
this port?" — typically to validate a firewall rule or load-balancer target before the real
service exists. They start a temporary listener on a chosen port; it accepts connections (or,
in UDP mode, receives datagrams), reports each one (peer address and port, timestamp), sends
nothing back, stores nothing of the payload, and stops on interrupt or an optional limit
(duration or connection count). Paired with the outbound check from another machine, this
closes the loop on any connectivity question — and for UDP it is the *only* definitive answer
to an inconclusive check.

**Why this priority**: Explicitly on the roadmap and explicitly sanctioned by the project's
diagnostic-only scope (a temporary listener for one's own troubleshooting), but it serves fewer
situations than the outbound checks.

**Independent Test**: Start a listener on a free port, connect to it from the check command, and
confirm the listener reports the inbound connection's peer details; confirm it stops cleanly on
interrupt and that a busy or permission-restricted port yields an actionable error, not a crash.

**Acceptance Scenarios**:

1. **Given** a free port, **When** the user starts a listener, **Then** it reports that it is
   listening (port and addresses), reports each accepted connection with peer address, peer
   port, and timestamp, and exits cleanly on interrupt with a summary count.
2. **Given** an optional stop condition (maximum duration or maximum number of connections),
   **When** the condition is reached, **Then** the listener stops on its own with a summary, and
   the exit code is 0 if at least the expected activity occurred as configured.
3. **Given** the port is already in use, **When** the user starts a listener, **Then** the error
   says the port is busy (and by implication how to pick another), using a distinct exit class.
4. **Given** a port the user lacks permission to bind (e.g., a privileged low port), **When**
   the user starts a listener, **Then** the error states the permission problem and suggests an
   unprivileged port, using a distinct exit class.
5. **Given** a connected peer sends data, **When** the listener reports the connection, **Then**
   payload content is neither displayed nor stored — only connection metadata is reported.
6. **Given** the listener in UDP mode, **When** a datagram arrives on the port, **Then** the
   arrival is reported with peer address, peer port, and timestamp (payload never shown or
   stored) — giving the definitive service-side answer to an inconclusive outbound UDP check.

---

### User Story 6 - Use it from code (Priority: P3)

A platform engineer embeds the same connectivity checks in their own tooling: a typed
programmatic interface returns structured results and raises typed errors, without printing or
exiting the process.

**Why this priority**: API parity is an opskit constitutional guarantee and enables monitoring
integrations, but it serves the flows above.

**Independent Test**: From a short script, run a check and a multi-probe programmatically, read
the verdict/statistics fields, and catch a specific typed error for an induced refusal and an
induced timeout.

**Acceptance Scenarios**:

1. **Given** the library interface, **When** a check succeeds, **Then** the caller receives a
   typed result exposing the verdict, connected address and family, port, and timing — and
   nothing is printed.
2. **Given** an induced failure, **When** the check runs programmatically, **Then** a typed
   exception of the matching failure class (resolution, refused, timeout) is raised with an
   actionable message.

---

### Edge Cases

- **Refused vs filtered**: the two must never be conflated — refusal means the host answered
  and nothing listens; timeout means nothing answered at all. Platforms surface these
  differently at the OS level; the user-visible classification is identical everywhere.
- **IPv6 targets**: bracketed `[addr]:port` syntax accepted everywhere a target is accepted;
  the report shows the address family used.
- **Dual-stack hosts**: candidate addresses are tried in the platform's recommended order; the
  report always shows the address actually connected to. A host whose addresses partially fail
  (e.g., broken IPv6, working IPv4) still yields an open verdict via the working family.
- **Address family constraints**: the user can restrict a check to IPv4 or IPv6; a host with no
  address in the requested family reports a resolution-class failure saying so.
- **Invalid targets**: missing port, port outside 1–65535, malformed brackets, and empty input
  are rejected as usage errors before any network activity.
- **Trailing-dot hostnames** (`example.com.`): accepted and normalized.
- **Service accepts then immediately closes**: still reported as open (the connection was
  established); no hang and no crash.
- **Slow handshake near the timeout boundary**: attempt timing is reported; retries are honored
  for timeouts but a refusal is definitive and is not retried.
- **Very short `--watch` intervals against slow targets**: intervals do not stack or overlap.
- **Probe run interrupted mid-way**: statistics for completed attempts are still summarized.
- **UDP closed-port detection depends on the host's unreachable signal**: such signals may be
  rate-limited or suppressed by the remote host or intermediate firewalls, so a genuinely
  closed UDP port can present as inconclusive — the inconclusive wording and hint account for
  this; the check never invents a "closed" verdict without the signal.
- **UDP services that only answer well-formed protocol requests**: a live service may ignore
  the generic probe and look inconclusive; the hint names this possibility (and points to
  protocol-aware tooling such as the DNS category for DNS ports).
- **UDP with repeated probes**: response times are reported only for attempts that received a
  reply; a mixed run summarizes replies, closed signals, and silence separately.
- **Listener on `0` connections when a limit expires**: reports zero connections received —
  itself a diagnostic answer ("the firewall rule isn't working").
- **Listener interrupt**: Ctrl+C always stops the listener cleanly with a summary, on every
  platform.
- **Checking one's own listener on localhost**: works, and serves as the documented smoke test
  pairing the two commands.

## Requirements *(mandatory)*

### Functional Requirements

**Targets & controls**

- **FR-001**: Users MUST be able to check a target given as a hostname, IPv4 address, or IPv6
  address, with the port supplied via `host:port` / `[ipv6]:port` shorthand or a port option;
  a target with no port is a usage error (there is no default port). Shorthand and option MUST
  agree or the input is rejected as a usage error.
- **FR-002**: Users MUST be able to control timeout and retry behavior; invalid controls are
  rejected before any network activity with a usage-error exit code.
- **FR-003**: Users MUST be able to restrict a check to IPv4 or IPv6; when the requested family
  has no addresses for the target, the outcome is a resolution-class failure that says so.
- **FR-004**: Users MUST be able to select the check protocol per run: TCP (the default) or
  UDP. Repeated probes, batch input, watch mode, and machine output apply identically to both.

**Outbound check behavior**

- **FR-005**: In TCP mode the system MUST attempt a connection to the target and report exactly
  one of these outcomes distinctly, each with its own exit class and an actionable hint: open
  (success), connection refused, no response before timeout (filtered), name resolution failure.
- **FR-006**: A successful TCP check MUST report the address actually connected to, its address
  family, the port, and the connection time; the connection MUST be closed immediately after
  the verdict with no application data sent.
- **FR-007**: The refused-vs-timeout distinction MUST be identical on Windows, macOS, and Linux
  regardless of how the underlying platform surfaces the condition.
- **FR-008**: In UDP mode the system MUST send a single minimal probe datagram carrying no
  protocol payload and MUST report exactly one of: open (a reply datagram was received, with
  response time), closed (the host signaled the port unreachable; connection-refused exit
  class), or "no response — open or filtered (inconclusive)" (no-response exit class, wording
  that names both possibilities, and a hint pointing at a service-side check). The system MUST
  NEVER report a UDP port as open without having received a reply.
- **FR-009**: Users MUST be able to run repeated probes against one target (configurable count
  and inter-probe interval), receiving per-attempt outcomes and timings plus a summary of
  attempts, successes, failures, and minimum/average/maximum connection or response time; a
  failing attempt MUST NOT abort the run. In UDP mode, timings cover only attempts that
  received a reply.

**Temporary listener**

- **FR-010**: Users MUST be able to start a temporary listener on a chosen port that accepts
  TCP connections — or, in UDP mode, receives datagrams — and reports, for each accepted
  connection or received datagram: peer address, peer port, and timestamp. The listener MUST
  NOT send any application data and MUST NOT display or store payload content.
- **FR-011**: The listener MUST run until interrupted, or until an optional user-supplied stop
  condition (maximum duration or maximum accepted connections/datagrams) is reached, and MUST
  always end with a summary of what was received. Interrupt MUST produce a clean stop on all
  platforms.
- **FR-012**: A port that is already in use and a port the user may not bind MUST each produce
  a distinct, actionable error and exit class — never a raw system error.

**Contracts (per constitution)**

- **FR-013**: All commands MUST honor the opskit output contract: human-readable default,
  versioned JSON envelope, NDJSON where output is batchable or streamed (batch results;
  per-attempt probe results; per-connection listener events), `NO_COLOR`/auto-plain behavior,
  and structured exit codes with distinct classes for usage error, resolution failure,
  connection refused, timeout/no-response, listener bind failure, and partial batch.
- **FR-014**: The outbound check MUST support batch input via multiple arguments, an input file
  supplied with `--input-file` / `-i` (one target per line, `host:port` syntax, blank lines and
  `#` comments ignored), or standard input; every target MUST be processed; failed targets MUST
  appear in machine output with their error; the aggregate exit code MUST follow the
  established batch rule (0 all-pass / uniform class / else partial).
- **FR-015**: A `--watch` mode MUST re-run the outbound check on an interval until interrupted,
  flagging when the outcome changes.
- **FR-016**: Every capability MUST be available programmatically with typed results and typed
  errors; the programmatic layer never prints or terminates the process.
- **FR-017**: Behavior and output MUST be identical on Windows, macOS, and Linux; OS-specific
  error conditions MUST be normalized into the shared error classification.
- **FR-018**: The feature MUST be read-only and zero-telemetry: the check connects only to
  user-specified targets and sends no application data beyond the UDP probe datagram (which
  carries no protocol payload); the listener binds only the user-specified port and sends
  nothing. No other network activity occurs.
- **FR-019**: The feature MUST NOT provide scanning affordances: no port ranges, no address
  ranges/CIDR expansion, no host discovery. Multiple targets are always explicit, user-listed
  endpoints.

### Key Entities

- **Connectivity Target**: what the user asked to check — host (name or IP), port, protocol
  (TCP or UDP), requested address family constraint, and the resolved address actually
  attempted/connected.
- **Check Result**: the single-shot outcome — verdict (TCP: open / refused / timeout /
  unresolvable; UDP: open / closed / inconclusive / unresolvable), address and family, port,
  connection or response time, and hint on failure or ambiguity.
- **Probe Statistics**: the repeated-probe aggregate — attempts, successes, failures (and for
  UDP, replies vs closed signals vs silence), and min/avg/max connection or response time,
  plus the per-attempt results it summarizes.
- **Listener Session**: one temporary listener run — protocol, port, bound addresses,
  configured stop condition, start/stop times, and total connections/datagrams received.
- **Inbound Event**: one accepted TCP connection or received UDP datagram — peer address, peer
  port, timestamp (metadata only; never payload).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a reachable target, an engineer gets an open/refused/timeout/unresolvable
  verdict with a single command in under 10 seconds at default settings.
- **SC-002**: Every defined failure class (usage error, unresolvable, refused, timeout, bind
  failure) is distinguishable from the others by exit code and by a one-line explanation in the
  output, verified by tests for 100% of the classes.
- **SC-003**: The same checks produce structurally identical reports on Windows, macOS, and
  Linux (verified by the CI matrix), including the refused-vs-timeout classification.
- **SC-004**: A 50-target batch with mixed outcomes completes without aborting, reports all 50
  targets in machine output, and yields the documented aggregate exit code.
- **SC-005**: A 10-probe run against a healthy local target reports 10 attempts with plausible
  timings and a correct summary; an interrupted or partially failing run still summarizes what
  completed.
- **SC-006**: The listener paired with the check on the same machine demonstrates an inbound
  connection end-to-end — in both TCP and UDP modes: the listener reports exactly the
  connections/datagrams sent, with correct peer metadata, and stops cleanly.
- **SC-007**: A UDP check against a closed local port reports "closed"; against a silent
  target it reports the explicitly inconclusive open-or-filtered outcome — and no test or
  documented example ever shows a UDP port claimed open without a received reply.
- **SC-008**: All capabilities are usable programmatically with typed results; the documented
  examples run as written.

## Assumptions

- **Reachability is TCP-based, not ICMP**: raw ICMP ping requires elevated privileges on every
  platform, which conflicts with "works identically everywhere for any user" (recorded in
  docs/PLAN.md). "Ping-style" here means repeated TCP connection probes to a service port.
- **UDP verdicts are honest by design**: UDP is connectionless, so silence is genuinely
  ambiguous — the check reports "open or filtered (inconclusive)" rather than guessing, maps a
  host's port-unreachable signal to "closed", and claims "open" only on a received reply. The
  probe is a single minimal datagram with no protocol payload (consistent with the
  no-application-data stance); protocol-aware probes (a real DNS query, NTP request, etc.) are
  out of scope — that is the job of protocol-specific categories (e.g. `opskit dns`). The
  definitive inbound answer is the listener's UDP mode on the service side.
- **No scanning by design (constitution Art. X)**: port ranges, CIDR/address-range expansion,
  and host discovery are deliberately excluded — this is a connectivity checker for known,
  explicit endpoints, not a scanner. Batch input of explicitly listed targets is the supported
  multi-target path.
- **The temporary listener is in scope** as sanctioned by Art. X ("a temporary listener for
  one's own troubleshooting"): it is foreground-only, single-port, sends nothing, reports
  metadata only, and always stops on interrupt or a user-set limit. It is not a service, proxy,
  or relay.
- **No default port for checks**: unlike TLS (443), there is no natural default for a generic
  connectivity check; requiring an explicit port avoids silently checking the wrong thing.
- **Latency is TCP connection-establishment time**, measured per attempt; it is a service-level
  reachability metric, not a raw network round-trip time.
- **Name resolution reuses system resolver behavior**; rich DNS diagnostics remain the `dns`
  category's job — a resolution failure here points the user to `opskit dns`.
- **Dual-stack behavior**: candidate addresses are tried in the platform's recommended order and
  the first successful connection wins; the connected address is always reported. This matches
  the connection behavior already established by the TLS category.
- **Defaults**: timeout and retry defaults follow the values already established for connection
  behavior in opskit (5-second timeout, retries applying to timeouts only — a refusal is
  definitive); probe count defaults to a small fixed number (ping-like, e.g. 4) unless
  overridden.
- **The listener does not gate on payloads**: it accepts, records metadata, and (on connection
  or limit) closes. Anything content-aware (echo, HTTP responses, protocol probes) is out of
  scope.
