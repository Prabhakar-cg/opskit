# Feature Specification: Proxy-Aware Reachability Checks

**Feature Branch**: `005-net-proxy-checks`

**Created**: 2026-07-15

**Status**: Draft

**Input**: User description: "Proxy-aware reachability checks for the net category. Today
`opskit net check`/`net probe` always open a direct TCP connection, so on proxy-only egress
networks (direct outbound silently dropped by firewall) every target reports TIMEOUT even
though the endpoint is reachable through the corporate HTTP proxy. Add the ability to test
whether a target host:port is reachable *through* an HTTP proxy using an HTTP CONNECT tunnel:
a `--proxy <host:port>` option (with standard env-var fallback like HTTPS_PROXY/HTTP_PROXY/
NO_PROXY honored per the config precedence rules: flags > env > profile > config file >
built-in), distinct verdicts that separate 'proxy unreachable' from 'proxy refused the tunnel
(e.g. 403/502 from CONNECT)' from 'tunnel established' so users can tell whether the proxy or
the target is the problem, proxy authentication passthrough at minimum via proxy URL
credentials with redaction of credentials in all output/logs, and full batch/JSON/exit-code
contract compliance like the existing check command. Read-only: establish the tunnel, no
application data beyond the CONNECT request itself. UDP is out of scope (HTTP proxies don't
tunnel UDP) and should be a clear usage error when combined with --proxy."

## Clarifications

### Session 2026-07-15

- Q: Proxy authentication scope for v1 — Basic only, Basic + Negotiate/Kerberos, or none? →
  A: Basic only; proxies demanding Negotiate/NTLM get the honest "unsupported authentication
  method" verdict. SSO schemes may ship additively later.
- Q: Which proxy environment variables apply to a raw TCP tunnel, and in what order? → A:
  Fixed order regardless of target port: `HTTPS_PROXY` → `HTTP_PROXY` → `ALL_PROXY`, each
  with its lowercase form considered.
- Q: Should the machine-output route field always be present, or only on proxied runs? → A:
  Always present, with an explicit "direct" value when no proxy is used (one additive MINOR
  schema change); human-readable direct output stays unchanged.
- Q: Which exit class does "target unreachable via proxy" (gateway failure) use? → A: A new
  dedicated exit class, so every proxied outcome is scriptable by exit code alone; wording
  distinguishes the flavor (proxy reported the target silent vs unreachable/unresolvable).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Check a port through the corporate proxy (Priority: P1)

An engineer on a proxy-only network (direct outbound traffic silently dropped by the egress
firewall) needs to answer "can I reach this service?". A direct check reports every target as
timed out, which is technically true but useless — the path their applications actually use
goes through the corporate HTTP proxy. They run the same check command with a proxy nominated
(explicitly, or picked up from their environment) and get a verdict about the path that
matters: the tunnel to the target was established (open), or a specific, distinguishable
failure telling them exactly which hop broke.

**Why this priority**: This is the whole feature — without it, the net category is blind on
proxy-only networks, one of the most common enterprise environments.

**Independent Test**: Run a proxied check against a target that a local stand-in proxy can
reach and confirm an "open (via proxy)" verdict with tunnel timing and exit code 0; stop the
stand-in proxy and confirm a proxy-unreachable verdict clearly attributed to the proxy hop,
not the target.

**Acceptance Scenarios**:

1. **Given** a working proxy and a target reachable from it, **When** the user checks the
   target via the proxy, **Then** the verdict is open, the report names the proxy used and
   the tunnel establishment time, and the process exits 0.
2. **Given** a proxy nominated by environment configuration (no flag given), **When** the
   user runs a check, **Then** the check goes through that proxy and the report clearly
   discloses which proxy was used — the routing is never silent.
3. **Given** a proxy flag on the command line and a different proxy in the environment,
   **When** the user runs the check, **Then** the flag wins, per the established
   configuration precedence.
4. **Given** a target matching the user's proxy-exemption list (NO_PROXY), **When** the user
   runs a check with a proxy configured, **Then** that target is checked directly and the
   report discloses the direct route.
5. **Given** no proxy flag, no proxy environment configuration, and no profile/config proxy,
   **When** the user runs a check, **Then** behavior is exactly today's direct check —
   existing users see no change beyond the always-present route field ("direct") in machine
   output.

---

### User Story 2 - Tell whether the proxy or the target is the problem (Priority: P1)

An engineer gets a failure on a proxied check and needs to know where to look next: is the
proxy itself down or misconfigured, is the proxy refusing to serve them (policy or
credentials), or did the proxy try and find the target dead? Each of these has a different
owner and a different fix, so each must be a distinct, unambiguous outcome.

**Why this priority**: Distinguishable failure attribution is the diagnostic value of the
feature; a lumped "failed via proxy" would be barely better than today's timeout.

**Independent Test**: Against a controllable stand-in proxy, induce each failure mode in turn
(proxy down; proxy demands credentials; proxy denies the tunnel by policy; proxy cannot reach
the target) and confirm four distinct verdicts, each with an actionable hint naming the hop at
fault and using the documented exit class.

**Acceptance Scenarios**:

1. **Given** a proxy address that does not answer (or refuses), **When** the user runs a
   proxied check, **Then** the verdict states the **proxy itself** is unreachable — wording
   and hint point at the proxy address/port and local network, never at the target — using
   the matching refused/timeout exit class.
2. **Given** a proxy that requires authentication and none (or wrong credentials) supplied,
   **When** the user runs a proxied check, **Then** the verdict states the proxy requires or
   rejected authentication, with a hint on how to supply credentials, using a distinct exit
   class from "target problem" outcomes.
3. **Given** a proxy that refuses to tunnel to this destination (policy denial), **When** the
   user runs a proxied check, **Then** the verdict states the proxy denied the tunnel, with a
   hint that the destination/port may not be allowed by proxy policy.
4. **Given** a proxy that accepts the request but cannot reach the target (gateway failure),
   **When** the user runs a proxied check, **Then** the verdict states the target is
   unreachable **from the proxy** — the proxy hop is explicitly reported as healthy — so the
   user knows to investigate the target, not their proxy settings.
5. **Given** a nominated proxy address that answers but does not speak the expected proxy
   protocol, **When** the user runs a proxied check, **Then** the outcome says the nominated
   endpoint does not behave like a proxy, rather than a raw parse or protocol error.

---

### User Story 3 - Authenticate to the proxy without leaking credentials (Priority: P2)

An engineer whose proxy requires authentication supplies credentials as part of the proxy
address (the ubiquitous `user:password@proxy:port` convention their other tools already use).
The check authenticates on their behalf — and no output of any kind (human, machine, logs,
error messages, echoed queries) ever contains the password.

**Why this priority**: Authenticated proxies are the norm in enterprises; but credential
leakage in a diagnostics tool is a trust-destroying defect, so redaction is inseparable from
the capability.

**Independent Test**: Run checks (success and each failure mode) against a stand-in proxy
requiring authentication, with credentials in the proxy address; assert the password appears
nowhere in human output, machine output, or logs, while the check succeeds.

**Acceptance Scenarios**:

1. **Given** credentials embedded in the proxy address, **When** the proxy accepts them,
   **Then** the tunnel is established and the verdict is open.
2. **Given** credentials embedded in the proxy address, **When** any output is produced
   (human, JSON, logs, errors — success or failure), **Then** the password is redacted
   everywhere it would otherwise appear, including any echo of the user's own input.
3. **Given** a proxy demanding an authentication method the tool does not support, **When**
   the user runs a proxied check, **Then** the outcome names the unsupported requirement
   honestly instead of misreporting it as bad credentials.

---

### User Story 4 - Proxied checks keep every existing contract (Priority: P2)

An engineer uses the proxied check exactly the way they use the direct one: many targets in a
batch (arguments, input file, stdin), repeated probes for latency/stability, watch mode,
machine-readable output for scripts — and everything behaves per the established opskit
contracts, with the route (direct or via which proxy) visible per target in machine output.

**Why this priority**: The feature multiplies existing capabilities rather than adding a
one-off mode; contract compliance is constitutionally mandatory but depends on Stories 1–2
existing first.

**Independent Test**: Feed a mixed batch (tunnel-ok, proxy-denied, target-unreachable-via-
proxy, plus a NO_PROXY-exempt direct target) through a stand-in proxy; confirm every target
is processed and reported, machine output carries one envelope per target including failures
and each target's route, and the aggregate exit code follows the established batch rule.

**Acceptance Scenarios**:

1. **Given** multiple targets and a proxy, **When** the user runs a batch check, **Then**
   every target is tunneled and reported — no abort on first failure — and the aggregate exit
   code follows the established rule (0 all-pass / uniform class / else partial).
2. **Given** machine-readable output is requested, **When** a proxied run completes, **Then**
   every target's envelope includes the route taken (direct, or via which proxy — credentials
   redacted) alongside the existing verdict fields, and failed targets are never dropped.
3. **Given** repeated probes via a proxy, **When** the run completes, **Then** each attempt
   establishes a fresh tunnel, per-attempt timings and the summary reflect tunnel
   establishment time, and a failing attempt does not abort the run.
4. **Given** watch mode with a proxy, **When** the outcome changes between runs (e.g. the
   proxy starts refusing during a policy change), **Then** the change is flagged exactly as
   direct-mode changes are.
5. **Given** UDP mode combined with a proxy, **When** the user runs the command, **Then** the
   input is rejected before any network activity as a usage error explaining that HTTP
   proxies cannot tunnel UDP.

---

### User Story 5 - Use it from code (Priority: P3)

A platform engineer embeds proxied reachability checks in their own tooling: the programmatic
interface takes an explicit proxy specification (their code decides where it comes from),
returns typed results including the route, and raises typed errors that distinguish
proxy-hop failures from target failures — without printing, exiting, or reading the process
environment behind the caller's back.

**Why this priority**: API parity is constitutionally guaranteed, but it serves the flows
above.

**Independent Test**: From a short script, run a proxied check with an explicit proxy
argument, read the route and verdict from the typed result, and catch distinct typed errors
for an induced proxy-unreachable and an induced tunnel-denied failure.

**Acceptance Scenarios**:

1. **Given** the library interface with an explicit proxy specification, **When** a proxied
   check succeeds, **Then** the caller receives a typed result exposing the verdict, the
   route, target, port, and timing — and nothing is printed.
2. **Given** the library interface, **When** no proxy is passed explicitly, **Then** the
   library never consults environment variables or configuration files itself — proxy
   discovery from the environment is exclusively the command-line layer's job.
3. **Given** an induced proxy-hop failure and an induced target-side failure, **When** checks
   run programmatically, **Then** typed exceptions of distinct classes are raised, each with
   an actionable message with credentials redacted.

---

### Edge Cases

- **Failure attribution must survive ambiguity**: a proxy answering CONNECT with a gateway
  failure means "proxy fine, target bad"; a proxy connection timeout means "proxy bad". The
  two must never share wording, hint, or (where classes differ) exit code.
- **Target name resolution moves to the proxy**: on a proxied check the tool passes the
  target name through the tunnel request and the **proxy** resolves it. A locally
  unresolvable name can therefore still succeed via proxy, and a proxy gateway failure may
  actually be a resolution failure at the proxy — the hint for that verdict names this
  possibility.
- **Local resolution failure of the proxy itself**: reported as a resolution-class failure
  clearly naming the proxy, with a hint pointing at the DNS category.
- **Credentials in the query echo**: machine output echoes the user's query parameters; the
  proxy specification within it must appear with the password redacted, on success and on
  every failure path.
- **Proxy exemption list semantics**: exemptions (NO_PROXY-style) match exact hosts and
  domain suffixes; a batch may therefore legitimately mix proxied and direct targets in one
  run, and each target's report shows its actual route.
- **Non-proxy endpoint nominated as proxy**: a service that accepts the connection but
  responds with something that is not a proxy response yields a distinct "not an HTTP proxy"
  outcome, not a crash or raw protocol error.
- **Proxy accepts the connection but never answers the tunnel request**: classified as a
  timeout attributed to the proxy hop, honoring the configured timeout; retries apply as they
  do to direct timeouts.
- **Definitive proxy answers are not retried**: an authentication demand or a policy denial
  is definitive (like a direct refusal) — retries apply only to silence/timeouts.
- **Unsupported proxy schemes**: a proxy specification with a scheme other than plain HTTP
  (e.g. SOCKS, TLS-to-proxy) is rejected as a usage error naming the unsupported scheme,
  before any network activity.
- **IPv6 targets via proxy**: bracketed target syntax is accepted and correctly conveyed in
  the tunnel request; the address-family restriction flags constrain the connection the tool
  itself makes (the proxy hop), since the proxy chooses how to reach the target.
- **Timing semantics change and are labeled**: reported timing on a proxied check is tunnel
  establishment time (connection to proxy plus tunnel setup), not a direct connection time;
  output labels it so proxied and direct timings are not silently compared.
- **Malformed proxy specifications** (empty host, missing/invalid port, embedded whitespace)
  are rejected as usage errors before any network activity.
- **The temporary listener is unaffected**: it has no outbound path, takes no proxy, and its
  behavior does not change when proxy environment variables are set.
- **Watch mode with an intermittently failing proxy**: outcome-change flagging treats a route
  or verdict change (open via proxy → proxy unreachable) as a change, exactly like direct
  verdict flips.

## Requirements *(mandatory)*

### Functional Requirements

**Proxy nomination & precedence**

- **FR-001**: Users MUST be able to nominate an HTTP proxy for outbound checks and probes via
  a command option accepting `host:port` or a full proxy address with optional embedded
  credentials (`user:password@host:port`, with or without an explicit HTTP scheme).
- **FR-002**: When no proxy option is given, the command-line layer MUST fall back to the
  standard proxy environment variables in a fixed order regardless of the target's port —
  `HTTPS_PROXY`, then `HTTP_PROXY`, then `ALL_PROXY`, each with its lowercase form
  considered — and then to profile/config-file values, per the established precedence
  (flags > env > profile > config file > built-in). The built-in default is **no proxy**
  (direct), so existing invocations behave exactly as today.
- **FR-003**: Users MUST be able to force a direct check from the command line even when the
  environment or configuration nominates a proxy.
- **FR-004**: A NO_PROXY-style exemption list (same precedence chain) MUST be honored:
  matching targets (exact host or domain suffix) are checked directly. Each target's actual
  route MUST be visible in its report.
- **FR-005**: The programmatic interface MUST accept the proxy (and exemptions) only as
  explicit arguments and MUST NOT read environment variables or configuration files itself.
- **FR-006**: Invalid proxy specifications (malformed address, missing/invalid port,
  unsupported scheme) MUST be rejected as usage errors before any network activity.
- **FR-007**: Combining UDP mode with a proxy MUST be rejected as a usage error, before any
  network activity, with wording explaining that HTTP proxies cannot tunnel UDP.

**Proxied check behavior**

- **FR-008**: A proxied check MUST establish an HTTP CONNECT tunnel to the target through the
  nominated proxy and MUST send no application data beyond the tunnel-establishment request
  itself; the connection MUST be closed immediately after the verdict (read-only, matching
  the direct check's no-application-data stance).
- **FR-009**: A proxied check MUST report exactly one of these outcomes distinctly, each with
  its own wording, actionable hint, and documented exit class:
  1. **open (via proxy)** — tunnel established; reports the proxy used and tunnel
     establishment time;
  2. **proxy unresolvable** — the proxy's own name did not resolve locally
     (resolution class, hint names the proxy and points at DNS diagnostics);
  3. **proxy unreachable** — connecting to the proxy was refused or timed out (matching
     refused/timeout classes, wording attributing the failure to the proxy hop);
  4. **proxy authentication required/rejected** — the proxy demanded credentials that were
     absent, wrong, or of an unsupported kind (distinct exit class; hint says how to supply
     credentials, or names the unsupported requirement);
  5. **tunnel denied by proxy** — the proxy refused to open the tunnel (policy denial;
     distinct exit class; hint that the destination/port may be disallowed);
  6. **target unreachable via proxy** — the proxy accepted the request but could not reach
     the target (gateway failure; its own dedicated exit class, so exit code alone separates
     it from a proxy-hop failure; wording states the proxy hop is healthy and distinguishes
     the flavor where the proxy reports it — target silent vs unreachable/unresolvable at
     the proxy).
- **FR-010**: Verdict attribution MUST be identical on Windows, macOS, and Linux; OS-specific
  connection errors on the proxy hop MUST be normalized exactly as direct-check errors are.
- **FR-011**: Retries MUST apply only to silence/timeouts (on the proxy connection or the
  tunnel request); a definitive proxy answer (authentication demand, policy denial, gateway
  failure) MUST NOT be retried.
- **FR-012**: Repeated probes via a proxy MUST establish a fresh tunnel per attempt and
  report per-attempt tunnel establishment times plus the standard summary; a failing attempt
  MUST NOT abort the run.

**Authentication & credential safety**

- **FR-013**: Credentials embedded in the proxy specification MUST be used to authenticate to
  the proxy on the user's behalf.
- **FR-014**: Credentials MUST be redacted in **all** output: human-readable reports, machine
  envelopes (including any echo of the user's query), log records, error messages, and hints
  — on success and on every failure path. The password never appears; whether a username
  appears is fixed and documented.
- **FR-015**: When a proxy demands an authentication method the tool does not support, the
  outcome MUST say so honestly (naming the unsupported requirement) rather than reporting a
  credential failure.

**Contracts (per constitution)**

- **FR-016**: Proxied checks and probes MUST honor the full output contract: human-readable
  default, versioned JSON envelope, NDJSON where batchable, `NO_COLOR`/auto-plain behavior,
  and structured exit codes covering every outcome in FR-009.
- **FR-017**: The machine-output envelope for **every** target MUST include a route field —
  an explicit "direct" value, or the proxy used (credentials redacted) — alongside the
  existing verdict fields. The field is always present (no conditional schema); its addition
  is a single additive MINOR schema change, and human-readable output for direct checks is
  unchanged.
- **FR-018**: Batch input via arguments, input file, or stdin MUST work with a proxy exactly
  as it does directly: every target processed, failures never dropped from machine output,
  aggregate exit code per the established rule (0 all-pass / uniform class / else partial).
- **FR-019**: Watch mode MUST work with a proxy, flagging outcome changes including
  route/verdict changes caused by the proxy itself.
- **FR-020**: Every capability MUST be available programmatically with typed results and
  typed errors distinguishing proxy-hop failures from target-side failures; the programmatic
  layer never prints, exits, or reads ambient configuration.
- **FR-021**: The feature MUST remain read-only and zero-telemetry: the only network activity
  is the connection to the user-nominated proxy (or the direct check for exempt targets) and
  the single tunnel-establishment request for the user-specified target. No other hosts are
  contacted; nothing beyond the tunnel request is ever sent.
- **FR-022**: The feature MUST NOT add scanning affordances: no port ranges, no address
  ranges, no proxy discovery/probing beyond the single user-nominated proxy. Targets remain
  explicit, user-listed endpoints.

### Key Entities

- **Proxy Specification**: the proxy the user nominated — host, port, optional credentials
  (never rendered). Provenance (flag, environment, profile, config) and the exemption list
  in force are run-level facts resolved by the calling layer (the CLI); the library takes
  the resolved specification explicitly, exposes `proxy_exempt()` for caller-side routing,
  and surfaces provenance per target through the Route.
- **Route**: how a given target was actually checked — direct, or via which proxy — attached
  to every result so mixed batches stay unambiguous.
- **Proxied Check Result**: the single-shot outcome — one of the FR-009 verdicts, the route,
  target and port, tunnel establishment time (when established), and hint on failure.
- **Proxy-hop Failure**: the family of typed failures attributable to the proxy itself
  (unresolvable, unreachable, authentication, denial), distinct from target-side outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a proxy-only network (direct egress blocked), an engineer gets a correct
  open-or-why-not verdict for a target through their proxy with a single command in under 10
  seconds at default settings — where today the same question costs a multi-tool manual
  investigation.
- **SC-002**: Every FR-009 outcome is distinguishable from every other by exit code and/or a
  one-line explanation naming the hop at fault, verified by tests covering 100% of the
  outcomes against a controllable stand-in proxy.
- **SC-003**: Given any failed proxied check, the output alone tells the user whether to
  investigate the proxy or the target — no failure message is attributable to both hops.
- **SC-004**: Credentials supplied in a proxy specification appear in **zero** bytes of
  output across all formats and all outcome paths, verified by tests asserting redaction on
  every FR-009 verdict in human, JSON, and NDJSON output.
- **SC-005**: A mixed 20-target batch through a stand-in proxy (tunnel-ok, denied,
  gateway-failed, and exemption-list direct targets) completes without aborting, reports all
  20 targets with their routes in machine output, and yields the documented aggregate exit
  code.
- **SC-006**: Runs with no proxy nominated anywhere produce human-readable output
  byte-for-byte identical to today's, and machine output identical except for the
  always-present route field reading "direct" — verified by the existing test suite passing
  with only that one schema-additive adjustment.
- **SC-007**: The same proxied checks produce structurally identical reports on Windows,
  macOS, and Linux, verified by the CI matrix against the in-process stand-in proxy.
- **SC-008**: All capabilities are usable programmatically with typed results and distinct
  proxy-hop vs target-side error classes; the documented examples run as written.

## Assumptions

- **HTTP CONNECT only, v1**: the supported proxy type is a plain-HTTP proxy speaking CONNECT
  — the ubiquitous corporate egress case. SOCKS proxies and TLS-to-the-proxy (an HTTPS
  proxy endpoint) are out of scope for v1 and rejected as clear usage errors; they can be
  additive later per SemVer.
- **Basic authentication only, v1** *(confirmed in clarification)*: credentials from the
  proxy specification are sent using the Basic scheme. Enterprise SSO schemes
  (NTLM/Kerberos/Negotiate) are out of scope — they require platform-specific credential
  machinery that conflicts with the pure-Python parity principle — and a proxy demanding
  only those yields the honest "unsupported authentication method" outcome (FR-015). This is
  a documented limitation, not a silent failure; SSO support can ship additively later.
- **Environment fallback is opt-in by environment**: honoring HTTPS_PROXY/HTTP_PROXY when no
  flag is given matches the universal convention (curl, pip, requests) and the project's
  fixed configuration precedence; the risk of surprising routing is mitigated by mandatory
  route disclosure in every report (FR-017) and a force-direct override (FR-003). Only the
  command-line layer reads the environment (constitution Art. VII).
- **The proxy is a user-specified host** (constitution Art. VIII): whether nominated by flag,
  environment, or config, the proxy is chosen by the user; the tool contacts no host the
  user did not designate.
- **No misuse surface (constitution Art. X)**: tunneling a single explicit target through the
  user's own designated proxy is operator diagnostics, equivalent to the direct check. No
  proxy discovery, no relaying, no listening, no anonymization chains — one user-chosen hop,
  read-only.
- **Timing is tunnel establishment time**: connection to the proxy plus tunnel setup —
  a path-level reachability metric. It is labeled as such and not comparable to direct
  connection times.
- **UDP stays direct-only**: HTTP CONNECT tunnels are stream-oriented; UDP-over-proxy would
  require SOCKS5, which is out of scope. The combination is a usage error (FR-007).
- **Exemption matching is deliberately simple**: exact host and domain-suffix matching,
  consistent with common NO_PROXY interpretation; CIDR/IP-range matching in exemptions is
  out of scope for v1.
- **Testing uses an in-process stand-in proxy**, consistent with the project's loopback-
  server testing approach: every FR-009 outcome (accept, auth-demand, deny, gateway-fail,
  garbage response, silence) is inducible locally; real-proxy tests are opt-in and never
  gate CI.
- **The `tls` category is untouched for now**: extending proxied connections to TLS
  inspection is a natural follow-up but out of scope here; this feature covers `net`
  check/probe only. The listener takes no proxy.
