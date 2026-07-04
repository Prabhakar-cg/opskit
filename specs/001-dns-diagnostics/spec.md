# Feature Specification: DNS Diagnostics

**Feature Branch**: `001-dns-diagnostics`

**Created**: 2026-07-01

**Status**: Draft

**Input**: User description: "DNS diagnostics for opskit v1 — forward/reverse lookup, custom resolver, query controls, multi-resolver diff, timing, trace, profiles, watch, batch input, structured output and exit codes, cross-platform parity, read-only diagnostics, usable as CLI and as an embeddable library."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Resolve a name to its DNS records (Priority: P1)

An engineer troubleshooting a service needs to look up a hostname and see its DNS records
(A, AAAA, MX, TXT, CNAME, NS, SOA, SRV) — getting the same result and format regardless of
which operating system they are on.

**Why this priority**: This is the core value and the minimum viable product. A cross-platform,
consistent forward lookup already replaces the per-OS juggling of `nslookup`/`dig`/PowerShell
cmdlets and delivers standalone value.

**Independent Test**: Run a lookup for a known hostname requesting one or more record types and
confirm the returned records, a human-readable rendering by default, a machine-readable rendering
on request, and an outcome-appropriate exit code — on Windows, macOS, and Linux.

**Acceptance Scenarios**:

1. **Given** a resolvable hostname, **When** the user looks it up for the default record type,
   **Then** the matching records are returned in human-readable form with a success exit code.
2. **Given** a resolvable hostname, **When** the user requests specific record types (e.g. MX and
   TXT), **Then** only those record types are returned.
3. **Given** a hostname that does not exist, **When** the user looks it up, **Then** a clear
   "name does not exist" result is shown with a distinct, non-success exit code.
4. **Given** the same query on Windows, macOS, and Linux, **When** it is run, **Then** the records
   and output format are identical.

---

### User Story 2 - Reverse lookup an IP address (Priority: P2)

An operator has an IP address from a log or firewall and needs the hostname(s) it maps to.

**Why this priority**: Reverse resolution is a frequent, standalone diagnostic complementing
forward lookup; commonly needed but secondary to forward lookup.

**Independent Test**: Provide an IPv4 and an IPv6 address with known PTR records and confirm the
returned hostname(s), with the same output/exit-code behavior as forward lookup.

**Acceptance Scenarios**:

1. **Given** an IP address with a PTR record, **When** the user reverse-looks-it-up, **Then** the
   associated hostname(s) are returned.
2. **Given** an IP address with no PTR record, **When** the user reverse-looks-it-up, **Then** a
   clear "no record" result is shown with the appropriate exit code.

---

### User Story 3 - Query a specific resolver with controlled parameters (Priority: P2)

An engineer in a restricted/corporate network needs to query a chosen DNS server (not the system
resolver) and adjust how the query is made — timeout, retries, transport, and port — to diagnose
resolver-specific or firewall-related problems.

**Why this priority**: Diagnosing issues in tightened networks (the tool's signature use case)
requires pointing at a specific resolver and tuning the query; high value but builds on P1.

**Independent Test**: Run the same lookup against the system resolver and against an explicitly
specified resolver, and confirm the target resolver is used; confirm timeout/retry/transport/port
controls change behavior as specified.

**Acceptance Scenarios**:

1. **Given** a specific resolver address, **When** the user runs a lookup against it, **Then** the
   answer comes from that resolver rather than the system default.
2. **Given** a resolver that does not answer over the default transport, **When** the query is
   made, **Then** the tool falls back to the alternate transport where applicable and reports the
   outcome.
3. **Given** an unreachable or non-responding resolver, **When** the user runs a lookup with a set
   timeout and retry count, **Then** the tool waits and retries as configured, then reports a
   distinct timeout outcome with guidance.

---

### User Story 4 - Compare answers across multiple resolvers (Priority: P2)

An engineer investigating DNS propagation delays or split-horizon behavior needs to ask several
resolvers the same question at once and see where the answers differ.

**Why this priority**: The multi-resolver diff is a standout troubleshooting capability that is
hard to do manually; it is high value but depends on the single-resolver query (P1/P3).

**Independent Test**: Query a name across two or more resolvers that are configured to return
different answers and confirm the differences are clearly highlighted; when answers agree, confirm
they are reported as consistent.

**Acceptance Scenarios**:

1. **Given** multiple resolvers returning the same answer, **When** the user compares them, **Then**
   the result indicates the answers are consistent.
2. **Given** multiple resolvers returning different answers, **When** the user compares them,
   **Then** the differences are clearly identified per resolver.

---

### User Story 5 - Batch lookups and scripting (Priority: P3)

An engineer needs to check many names at once — from command arguments, a file, or piped stdin —
and consume the results in an automated pipeline.

**Why this priority**: Composability multiplies the tool's usefulness in automation, but the
single-target flows deliver value first.

**Independent Test**: Supply a set of targets via each input method (args, file, stdin) and confirm
each is resolved, results are emitted in a machine-consumable form suitable for streaming, and the
overall exit code reflects aggregate success/failure per this rule: the exit code is `0` only when
**every** target succeeds; if all targets share the same single failure class it is that class's
code (see the exit-code classes: 3 NXDOMAIN, 4 SERVFAIL, 5 REFUSED, 6 TIMEOUT); and any **mixed**
batch — some targets succeed and some fail — aggregates to `7` (PARTIAL).

**Acceptance Scenarios**:

1. **Given** a list of hostnames in a file, **When** the user runs a batch lookup, **Then** each
   name is resolved and results are returned for all of them.
2. **Given** hostnames piped via stdin, **When** the user runs a batch lookup, **Then** results are
   produced per target in a streamable machine-readable form.

---

### User Story 6 - Watch for changes over time (Priority: P3)

An engineer waiting on DNS propagation or observing a failover needs to re-run a query on an
interval and see when the answer changes.

**Why this priority**: Convenience for live observation; valuable but non-essential to core use.

**Independent Test**: Start a watch on a name at a set interval and confirm it re-queries on that
interval and surfaces changes when the answer changes.

**Acceptance Scenarios**:

1. **Given** a watch interval, **When** the answer stays the same, **Then** the tool keeps polling
   without signalling a change.
2. **Given** a watch interval, **When** the answer changes, **Then** the change is surfaced to the
   user.

---

### User Story 7 - Saved profiles (Priority: P3)

A user who repeatedly targets the same resolver/settings wants to save them under a name and reuse
them, instead of retyping options each time.

**Why this priority**: Ergonomics that reduce friction for repeat users; not required for core value.

**Independent Test**: Save a named profile with a resolver/settings, then run a lookup referencing
that profile and confirm the saved settings are applied.

**Acceptance Scenarios**:

1. **Given** a saved profile, **When** the user runs a lookup with that profile, **Then** the
   profile's resolver/settings are used.
2. **Given** both a profile and an explicit option, **When** they conflict, **Then** the explicit
   option takes precedence.

---

### User Story 8 - Resolution trace (Priority: P3)

An engineer doing deep troubleshooting wants to see the path taken to resolve a name.

**Why this priority**: Advanced diagnostic detail; valuable for hard cases but the least commonly
needed.

**Independent Test**: Run a lookup with trace enabled and confirm the resolution path/steps are
shown alongside the final answer.

**Acceptance Scenarios**:

1. **Given** trace mode, **When** a name is resolved, **Then** the steps taken to reach the answer
   are shown in addition to the result.

---

### User Story 9 - Use the same capabilities from code (Priority: P2)

An engineer building automation wants to call the same DNS diagnostics from within their own
program and receive structured results and typed errors, not parse console text.

**Why this priority**: Being embeddable (not only a CLI) is an explicit product goal and unlocks
automation use cases; it parallels the CLI and is high value.

**Independent Test**: Invoke the diagnostics programmatically for a lookup and confirm structured
results are returned and that failures raise distinguishable, catchable errors rather than exiting
the host process or printing to its console.

**Acceptance Scenarios**:

1. **Given** a programmatic call for a resolvable name, **When** it runs, **Then** a structured
   result is returned to the caller.
2. **Given** a programmatic call that fails (e.g. nonexistent name, timeout), **When** it runs,
   **Then** a specific, catchable error is raised and the host program is neither terminated nor
   written to.

---

### Edge Cases

- **UDP blocked, TCP allowed**: the query still succeeds by falling back to the allowed transport.
- **Resolver refuses the query**: reported distinctly from a timeout, with its own outcome/exit code.
- **Resolver silently drops packets**: waits the configured timeout, retries, then reports a
  distinct "no response — possibly filtered" outcome with guidance.
- **Server failure vs nonexistent name**: SERVFAIL and NXDOMAIN are reported as different outcomes.
- **Oversized/truncated response**: automatically completed over the alternate transport.
- **Split-horizon / propagation**: differing answers across resolvers are surfaced by the compare flow.
- **Local hosts-file overrides**: **bypassed**. opskit queries the chosen/system DNS resolver
  directly, so `/etc/hosts` (and the Windows `hosts` file) entries are never consulted; results
  always reflect DNS, consistently across platforms.
- **IPv4-only, IPv6-only, and dual-stack environments**: handled without crashing; missing family
  reported clearly.
- **DNSSEC validation failure**: surfaced as a clear, distinct signal.
- **Empty answers and CNAME chains**: reported/followed correctly.
- **Invalid input** (malformed name/IP, unknown record type, non-numeric timeout): rejected with a
  clear usage error and a distinct exit code, before any network activity.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to perform a forward DNS lookup for a hostname and retrieve records
  of types A, AAAA, MX, TXT, CNAME, NS, SOA, and SRV, selecting one or more types.
- **FR-002**: Users MUST be able to perform a reverse (PTR) lookup for an IPv4 or IPv6 address.
- **FR-003**: Users MUST be able to direct a query to a specific resolver instead of the system
  resolver.
- **FR-004**: Users MUST be able to control query parameters: timeout, retry count, transport
  (connection-oriented vs datagram), and destination port.
- **FR-005**: The tool MUST automatically complete a query over the alternate transport when a
  response indicates it is required (e.g. truncation), and SHOULD fall back when the primary
  transport is unavailable.
- **FR-006**: Users MUST be able to query multiple resolvers for the same name and see a comparison
  that highlights any differences among their answers.
- **FR-007**: The tool MUST report per-query timing (latency) for each resolution.
- **FR-008**: Users MUST be able to enable a trace that shows the resolution path in addition to the
  final answer.
- **FR-009**: Users MUST be able to provide targets as direct arguments, from a file, or via stdin,
  and process them as a batch.
- **FR-010**: Users MUST be able to re-run a query on a fixed interval (watch) and be shown when the
  answer changes.
- **FR-011**: Users MUST be able to save named profiles of resolver/query settings and reference a
  profile to apply those settings.
- **FR-012**: Every command MUST provide a human-readable default output and a machine-readable
  structured output on request; batched results MUST additionally be available in a streamable
  per-record form.
- **FR-013**: The machine-readable output MUST follow a stable, versioned structure that includes
  the query, the result, any error, and timing; its schema changes MUST follow semantic versioning.
- **FR-014**: The tool MUST return distinct, documented exit codes per outcome class (success,
  name-does-not-exist, server-failure, refused, timeout/no-response, usage error) suitable for
  scripting.
- **FR-015**: Output MUST auto-adapt to context — colorized/tabular for an interactive terminal,
  plain when piped — and MUST honor a "no color" preference.
- **FR-016**: Failure messages MUST be actionable, explaining the likely cause and suggesting a next
  step (e.g. "no response — the resolver may be filtered; try a different `--server` or transport").
- **FR-017**: For the same query and inputs, behavior and output MUST be identical across Windows,
  macOS, and Linux; platform-specific error conditions MUST be normalized into consistent outcomes.
- **FR-018**: All capabilities MUST be available programmatically (as an embeddable interface), not
  only via the command line; programmatic failures MUST surface as distinguishable, catchable errors
  and MUST NOT terminate or write to the host program.
- **FR-019**: The tool MUST be read-only: it performs only DNS diagnostic queries the user requests
  and MUST NOT perform any modifying, offensive, or intrusive action.
- **FR-020**: The tool MUST make no network connection other than the diagnostic query the user
  requested (to the chosen or system resolver); it MUST NOT send telemetry or otherwise phone home.
- **FR-021**: Setting resolution MUST follow a single, documented precedence order (explicit option
  > environment > selected profile > configured default > built-in default).

### Key Entities *(include if feature involves data)*

- **DNS Query**: what the user asked — target (name or IP), requested record type(s), chosen
  resolver(s), and query parameters (timeout, retries, transport, port).
- **DNS Record**: a single returned datum — its type, value, and time-to-live.
- **Resolver**: a DNS server that answers a query — address and, optionally, a friendly label.
- **Lookup Result**: the outcome of a query — the records returned (or the specific failure), timing,
  and the resolver that answered.
- **Resolver Comparison**: a set of per-resolver results for the same name, plus the identified
  agreements/differences.
- **Profile**: a named, saved set of resolver/query settings for reuse.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can resolve a hostname and read its records in a single command without
  consulting documentation for basic usage.
- **SC-002**: The same query produces identical records and output format on Windows, macOS, and
  Linux (100% parity across supported platforms).
- **SC-003**: In an environment where the datagram transport is blocked but the connection-oriented
  transport is allowed, a lookup still returns the correct answer within the configured timeout.
- **SC-004**: Distinct outcomes (success, nonexistent name, server failure, refused, timeout) are
  each mapped to a distinct exit code, enabling a script to branch on outcome without parsing text.
- **SC-005**: A batch of at least 100 targets supplied via file or stdin produces a machine-readable
  result for every target.
- **SC-006**: Comparing a name across multiple resolvers clearly identifies whether their answers
  agree or differ.
- **SC-007**: Every failure message names a probable cause and a concrete next step.
- **SC-008**: Across a full run, the tool opens no network connection other than to the requested or
  system resolver(s) (verifiable by network capture) — zero telemetry.
- **SC-009**: An engineer can obtain the same structured results by calling the tool from their own
  program as they get from the command line.

## Assumptions

- Target users are engineers/operators with permission to query the DNS resolvers they point the
  tool at; the tool is used for legitimate diagnostics on their own/authorized environments.
- The host has network reachability to whichever resolver is targeted (system or specified); lack of
  reachability is itself a diagnostic outcome the tool reports.
- Discovery of the system's configured resolver(s) relies on the host operating system's standard
  configuration.
- Internationalized domain names are accepted and handled consistently.
- "Batch" scale for v1 targets interactive/operational use (hundreds of names), not bulk-scanning
  workloads (which are out of scope per the read-only, non-intrusive scope).
- Persisted profiles are stored in a per-user location following each operating system's conventions.
