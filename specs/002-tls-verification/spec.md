# Feature Specification: TLS Verification Diagnostics

**Feature Branch**: `002-tls-verification`

**Created**: 2026-07-04

**Status**: Draft

**Input**: User description: "TLS verification diagnostics (opskit tls) — a read-only, cross-platform TLS/certificate inspection command group for opskit. Engineers can verify the TLS health of any endpoint given a hostname or IP address, with a configurable port defaulting to 443. Capabilities: layered failure reporting (DNS vs TCP vs TLS vs certificate), certificate/chain inspection, name and trust validation, negotiated protocol/cipher reporting, SNI support, expiry warnings, and all established opskit contracts (API-first, JSON envelope, structured exit codes, batch, watch)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verify an endpoint's TLS health (Priority: P1)

An engineer suspects a service's certificate is the cause of client errors. They run a single
command with the hostname and immediately see a clear verdict: the certificate's subject, who
issued it, when it expires, whether it matches the name they asked for, whether it chains to a
trusted authority, and which TLS protocol/cipher was negotiated — the same way on Windows, macOS,
and Linux, replacing `openssl s_client` incantations and browser padlock spelunking.

**Why this priority**: This is the core value — one consistent, readable answer to "is TLS healthy
on this endpoint?" Everything else builds on it.

**Independent Test**: Run the check against a known-good public endpoint and confirm a passing
verdict with certificate summary, negotiated protocol, and exit code 0; run it against endpoints
with known problems (expired, self-signed, wrong host) and confirm each yields a failing verdict
naming the specific problem and a distinct exit code.

**Acceptance Scenarios**:

1. **Given** a reachable endpoint with a valid certificate, **When** the user checks it by
   hostname, **Then** the output shows subject, issuer, validity window, days until expiry,
   name-match result, trust result, negotiated protocol and cipher, and the process exits 0.
2. **Given** an endpoint whose certificate is expired, **When** the user checks it, **Then** the
   verdict states the certificate is expired (with the expiry date), full certificate details are
   still shown, and the exit code is the certificate-invalid class.
3. **Given** an endpoint whose certificate does not cover the requested name, **When** the user
   checks it, **Then** the verdict names the mismatch (requested name vs names on the
   certificate) and the exit code is the certificate-invalid class.
4. **Given** an endpoint presenting a self-signed or untrusted certificate, **When** the user
   checks it, **Then** the verdict identifies the condition distinctly (self-signed vs untrusted
   chain), details are still shown, and the exit code is the certificate-invalid class.

---

### User Story 2 - Check non-standard ports and IP targets (Priority: P1)

An engineer needs to verify TLS on services that don't live on 443: an admin panel on 8443, IMAPS
on 993, SMTPS on 465, LDAPS on 636 — or on a raw IP address before DNS is set up.

**Why this priority**: Port flexibility and IP targets were explicitly requested and are daily
reality in ops work; without them the tool only covers the easy case.

**Independent Test**: Check a TLS service on a non-443 port via the port option and via
`host:port` shorthand; check a target given as an IPv4 and an IPv6 address; confirm identical
report structure in all cases.

**Acceptance Scenarios**:

1. **Given** a TLS service on port 8443, **When** the user supplies the port explicitly, **Then**
   the check runs against that port and reports normally.
2. **Given** no port is supplied, **When** the user checks a target, **Then** port 443 is used.
3. **Given** a target written as `host:8443` (or `[2001:db8::1]:8443` for IPv6), **When** the
   user checks it, **Then** the embedded port is used without a separate option.
4. **Given** a target that is an IP address, **When** the user checks it, **Then** the check runs
   (server name indication is omitted as it does not apply to IPs), and name validation is
   performed against the IP itself, with the report noting how the certificate was matched.

---

### User Story 3 - Pinpoint which layer failed (Priority: P2)

A service is down and the engineer needs to know *where* it broke without re-running different
tools: does the name not resolve, is the port unreachable/refused, does the TLS handshake itself
fail, or is only the certificate bad?

**Why this priority**: Layer isolation is the difference between a diagnostic tool and a
pass/fail probe; it directly reduces time-to-cause.

**Independent Test**: Induce each failure class (unresolvable name, filtered port, refused port,
plain-text service on the port, invalid certificate) and confirm each produces a distinct
outcome, message, actionable hint, and exit code.

**Acceptance Scenarios**:

1. **Given** an unresolvable hostname, **When** the user checks it, **Then** the outcome is a
   resolution failure (not a TLS error) with its own exit code.
2. **Given** a reachable host whose port is closed or filtered, **When** the user checks it,
   **Then** the outcome distinguishes "connection refused" from "no response before timeout" and
   uses the connection-failure exit class.
3. **Given** a port serving a non-TLS protocol (e.g., plain HTTP on 80), **When** the user checks
   it, **Then** the outcome is a handshake failure with a hint that the service may not speak TLS
   on that port (e.g., it may require opportunistic/STARTTLS upgrade, which is out of scope).
4. **Given** a completed handshake with a bad certificate, **When** the user checks it, **Then**
   the outcome is the certificate-invalid class — clearly distinct from connection and handshake
   failures.

---

### User Story 4 - Inspect the full certificate and chain (Priority: P2)

An engineer renewing or debugging certificates needs the details: every name on the certificate,
the full issuer chain as presented by the server, serial number, signature algorithm, key type
and size, and the validity window of each certificate in the chain.

**Why this priority**: Deep inspection answers the follow-up questions the P1 verdict raises,
and is required for debugging chain and renewal problems.

**Independent Test**: Inspect a public endpoint and verify all listed fields appear for the leaf
certificate and each presented intermediate; verify an incomplete chain served by a misconfigured
endpoint is reported as such.

**Acceptance Scenarios**:

1. **Given** a healthy endpoint, **When** the user requests certificate details, **Then** the
   output lists, for the leaf: subject, issuer, all subject alternative names, validity window,
   days until expiry, serial number, signature algorithm, and public key type/size.
2. **Given** the server presents intermediates, **When** the user inspects the chain, **Then**
   each presented certificate is listed in order with subject, issuer, and validity window.
3. **Given** a server that fails to present a required intermediate, **When** the user inspects
   it, **Then** the report identifies the chain as incomplete/untrusted rather than silently
   passing or failing without explanation.

---

### User Story 5 - Catch certificates before they expire (Priority: P2)

A team wants to check endpoints ahead of renewals: a certificate that is still valid but expires
within a configurable window (default 30 days) should be flagged loudly enough for scripts and
humans to act on.

**Why this priority**: Expiry is the most common real-world TLS failure; catching it early is a
primary reason ops teams reach for a TLS checker.

**Independent Test**: Check an endpoint whose certificate expires within the threshold and
confirm a warning outcome with its own exit code; lower the threshold below the remaining
lifetime and confirm a clean pass.

**Acceptance Scenarios**:

1. **Given** a certificate expiring within the warning threshold, **When** the user checks it,
   **Then** the verdict is "valid but expiring soon" with days remaining, and the exit code is
   the expiring-soon class (distinct from both success and invalid).
2. **Given** the user overrides the threshold, **When** the check runs, **Then** the override is
   honored (including 0 to disable the warning).
3. **Given** `--watch` is active during a certificate rotation, **When** the served certificate
   changes, **Then** the change is flagged on the next interval.

---

### User Story 6 - Bulk verification for many endpoints (Priority: P3)

An engineer audits an estate of endpoints (mixed hostnames, ports, IPs) from a file and consumes
the results in a pipeline, with per-target tolerance: one dead endpoint must not abort the audit.

**Why this priority**: Fleet audits multiply the tool's value but depend on the single-target
flows being right first.

**Independent Test**: Supply a file mixing healthy, expiring, invalid, and unreachable targets;
confirm every line is processed, machine output contains one entry per target including the
failures, and the aggregate exit code follows the established batch rule.

**Acceptance Scenarios**:

1. **Given** an input file with one target per line (supporting `host`, `host:port`, IPs, blank
   lines and `#` comments), **When** the user runs a batch check, **Then** every target is
   checked and reported.
2. **Given** a mixed batch where some targets fail, **When** machine-readable output is
   requested, **Then** failed targets appear in the output with their error (never silently
   dropped), and the exit code is 0 only if every target passes, the uniform class if all share
   one failure class, else the partial class.

---

### User Story 7 - Use it from code (Priority: P3)

A platform engineer embeds the same checks in their own tooling: a typed programmatic interface
returns structured results and raises typed errors, without printing or exiting the process.

**Why this priority**: API parity is an opskit constitutional guarantee and enables monitoring
integrations, but it serves the flows above.

**Independent Test**: From a short script, run a check programmatically, read the verdict fields
and certificate attributes, and catch a specific typed error for an induced failure.

**Acceptance Scenarios**:

1. **Given** the library interface, **When** a check succeeds, **Then** the caller receives a
   typed result exposing the verdict, certificate details, chain, protocol, and timing — and
   nothing is printed.
2. **Given** an induced failure, **When** the check runs programmatically, **Then** a typed
   exception of the matching failure class is raised with an actionable message.

---

### Edge Cases

- **Expired vs not-yet-valid**: both reported distinctly, with dates.
- **Self-signed vs untrusted chain vs incomplete chain**: three distinct explanations.
- **Wildcard certificates**: `*.example.com` matches `a.example.com` but not `a.b.example.com`
  or the bare `example.com`; matching rules are exercised by tests.
- **Certificate with no subject alternative names**: reported as such (legacy CN-only
  certificates fail modern name validation and the report says why).
- **SNI-dependent servers**: a server that returns a default certificate without SNI — the
  check sends the target hostname by default and allows an explicit override for split
  configurations; the report states which server name was sent.
- **IP targets**: no server name is sent; name validation uses the IP; the report notes this.
- **Plain-text service on the checked port**: handshake failure with a "service may not speak
  TLS / may require STARTTLS" hint — not a crash or a raw traceback.
- **Server closes the connection mid-handshake**: reported as a handshake failure with timing.
- **Timeout at each layer**: connect timeout and handshake timeout reported distinctly from
  refusal; retries honored.
- **IPv6**: bracketed `[addr]:port` syntax accepted everywhere a target is accepted.
- **Trailing-dot hostnames** (`example.com.`): accepted and normalized.
- **Very short `--watch` intervals against slow endpoints**: intervals do not stack or overlap.
- **Host with multiple addresses (dual-stack)**: connection behavior is deterministic and
  documented; the report shows the address actually connected to.
- **System trust stores differ across platforms**: validation uses the platform's trust store by
  default and results state which condition failed, so a platform-specific trust difference is
  explainable; a user-supplied CA bundle overrides the system store for private PKI.

## Requirements *(mandatory)*

### Functional Requirements

**Targets & controls**

- **FR-001**: Users MUST be able to check a target given as a hostname, IPv4 address, or IPv6
  address.
- **FR-002**: The port MUST default to 443 and MUST be overridable per check, both via an option
  and via `host:port` / `[ipv6]:port` shorthand; the shorthand and the option MUST agree or the
  input is rejected as a usage error.
- **FR-003**: Users MUST be able to control timeout and retry behavior; invalid controls are
  rejected before any network activity with a usage-error exit code.
- **FR-004**: The check MUST send the target hostname for server name indication by default,
  MUST allow an explicit server-name override, and MUST omit it for IP targets.

**Verification behavior**

- **FR-005**: The system MUST establish a connection and perform a TLS handshake to the target,
  sending no application data, and MUST report each layer's outcome distinctly: name resolution
  failure, connection refused, connection timeout, handshake failure, certificate invalid,
  success.
- **FR-006**: The system MUST retrieve and report certificate details even when validation
  fails (an expired or untrusted certificate is still fully displayed alongside the failing
  verdict).
- **FR-007**: The system MUST validate the certificate for: expiry (expired / not yet valid),
  name coverage of the requested target (including wildcard rules), and chain of trust against
  the platform trust store — reporting each failed condition distinctly (expired, not yet valid,
  name mismatch, self-signed, untrusted or incomplete chain).
- **FR-008**: Users MUST be able to supply their own CA bundle to validate against a private
  PKI instead of the platform trust store.
- **FR-009**: The system MUST report the negotiated TLS protocol version and cipher suite, and
  MUST flag negotiated protocol versions below TLS 1.2 as a warning in the report.
- **FR-010**: The system MUST warn when a valid certificate expires within a configurable
  threshold (default 30 days; 0 disables), using a distinct expiring-soon outcome and exit code.
- **FR-011**: For the leaf certificate the report MUST include: subject, issuer, all subject
  alternative names, validity window, days until expiry, serial number, signature algorithm, and
  public key type/size; each certificate presented in the chain MUST be listed with subject,
  issuer, and validity window.

**Contracts (per constitution)**

- **FR-012**: The command MUST honor the opskit output contract: human-readable default,
  versioned JSON envelope, NDJSON for batches, `NO_COLOR`/auto-plain behavior, and structured
  exit codes with distinct classes for usage error, resolution failure, connection failure,
  timeout, handshake failure, certificate invalid, expiring-soon, and partial batch.
- **FR-013**: Batch input MUST be supported via a positional target and/or an input file (one
  target per line, `host[:port]` syntax, blank lines and `#` comments ignored); every target MUST
  be processed; failed targets MUST appear in machine output with their error; the aggregate exit
  code MUST follow the established batch rule (0 all-pass / uniform class / else partial).
- **FR-014**: A `--watch` mode MUST re-run the check on an interval until interrupted, flagging
  when the outcome or the served certificate changes.
- **FR-015**: Every capability MUST be available programmatically with typed results and typed
  errors; the programmatic layer never prints or terminates the process.
- **FR-016**: Behavior MUST be identical on Windows, macOS, and Linux except where the platform
  trust store legitimately differs, and such differences MUST be attributable from the output.
- **FR-017**: The feature MUST be read-only and zero-telemetry: it connects only to the
  user-specified endpoint, sends no application data, and performs no other network activity.
- **FR-018**: The connection step MUST be designed as a reusable capability so a future
  network-reachability category can share it rather than reimplement it.

### Key Entities

- **TLS Target**: what the user asked to check — host (name or IP), port, effective server name
  for SNI, and resolved address actually connected to.
- **Check Result**: the layered outcome — per-layer status (resolve, connect, handshake,
  validate), overall verdict, negotiated protocol and cipher, timing, and the certificate set.
- **Certificate**: one certificate's descriptive attributes (subject, issuer, alternative names,
  validity window, serial, signature algorithm, key type/size) plus derived facts (days to
  expiry, self-signed?).
- **Chain**: the ordered certificates the server presented, with a completeness/trust assessment.
- **Validation Finding**: a single failed or warned condition (expired, name mismatch, untrusted,
  expiring soon, legacy protocol) with its explanation and hint.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a reachable public endpoint, an engineer gets a complete TLS verdict
  (validity, expiry, name match, trust, protocol) with a single command in under 10 seconds.
- **SC-002**: Every defined failure class (unresolvable, refused, timeout, non-TLS service,
  expired, not-yet-valid, name mismatch, self-signed, untrusted chain) is distinguishable from
  the others by exit code and by a one-line explanation in the output, verified by tests for
  100% of the classes.
- **SC-003**: The same checks produce structurally identical reports on Windows, macOS, and
  Linux (verified by the CI matrix), with any trust-store-derived difference explicitly named in
  the output.
- **SC-004**: A 50-target batch with mixed outcomes completes without aborting, reports all 50
  targets in machine output, and yields the documented aggregate exit code.
- **SC-005**: A certificate inside the expiry-warning window is flagged with days remaining and
  a distinct exit code, catchable by an unattended script without parsing text.
- **SC-006**: All capabilities are usable programmatically with typed results; the documented
  examples run as written.

## Assumptions

- **Expiring-soon is a distinct non-zero exit class** (not silent success): unattended scripts
  are a primary consumer, and a warning that maps to exit 0 is invisible to them. Users who
  want expiring-soon to pass can set the threshold to 0.
- **STARTTLS (opportunistic TLS upgrade on ports like 25/587/143) is out of scope for v1**; the
  handshake-failure hint names it so users are not misled. Implicit-TLS services (443, 8443,
  993, 465, 636) are fully in scope.
- **Revocation checking (OCSP/CRL) is out of scope for v1** — it requires third-party network
  calls that conflict with the "connects only to the user-specified endpoint" privacy stance;
  it may be revisited as an explicit opt-in later.
- **Client certificates / mutual TLS are out of scope for v1** (diagnostic is anonymous).
- **Validation uses the platform trust store by default**; a user-supplied CA bundle replaces it
  for private PKI. No bundled CA list is shipped.
- **Default expiry-warning threshold is 30 days**, aligned with common ops renewal practice.
- **The DNS resolution step reuses the system resolver behavior**; rich DNS diagnostics remain
  the `dns` category's job — a resolution failure here points the user to `opskit dns`.
- **Dual-stack behavior**: addresses are tried in the platform's default order and the first
  successful connection is used; the connected address is always reported.
- **Protocol floor (implementation decision, 2026-07-07)**: the client requires **TLS 1.2+**
  (secure-by-default; satisfies the CodeQL/SonarCloud insecure-protocol gates without
  suppressions). Consequently a server offering only SSLv3/TLS 1.0/1.1 fails the handshake
  (exit 9) with a hint, rather than being connected to and reported as "negotiated <legacy>".
  FR-009's below-1.2 warning is retained defensively; an opt-in `--allow-legacy`/`--min-tls`
  flag to diagnose such endpoints is deferred to a future iteration.
