# Feature Specification: Active Directory / LDAP Diagnostics

**Feature Branch**: `004-ad-diagnostics`

**Created**: 2026-07-10

**Status**: Draft

**Input**: User description: "ad" — the Active Directory / LDAP category from the opskit
roadmap (docs/PLAN.md backlog): user account status (enabled/disabled, locked out,
password/account expiry) and group membership (direct and nested/effective), plus the natural
supporting diagnostics — a directory connectivity-and-authentication check and a read-only
lookup of a named user/group/computer object's key attributes — delivered under all
established opskit contracts (API-first, JSON envelope, structured exit codes, batch,
cross-platform, credential redaction, diagnostic-only scope per constitution Art. X).
Amended 2026-07-10: object lookup is explicitly bidirectional and batchable — user-side
facts (email, contact/organizational basics) and group-side member listing both first-class,
details retrievable for **multiple** named users/groups in one run; list-shaped human output
is rendered as structured tables alongside the standard JSON/NDJSON machine output.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Diagnose why an account can't sign in (Priority: P1)

A helpdesk or ops engineer gets the classic ticket — "my account doesn't work" — and needs the
directory's answer in one command instead of remembering per-OS incantations (`Get-ADUser` /
`net user /domain` vs hand-written `ldapsearch` filters and bit-mask arithmetic). They name the
user and get a plain-language status verdict: whether the account is enabled or disabled,
whether it is currently locked out, whether the password has expired (and when it expires or
that it never expires), whether the account itself has expired (and when), and when the
password was last set — the same way on Windows, macOS, and Linux.

**Why this priority**: This is the category's core value from the roadmap and the single most
frequent directory question in day-to-day operations; every other capability supports it.

**Independent Test**: Run the status check against a healthy account and confirm a clear
"no blockers" verdict and exit code 0; run it against a disabled account, a locked-out
account, and an account with an expired password, and confirm each yields a distinct,
plain-language finding, an actionable hint, and the appropriate exit outcome.

**Acceptance Scenarios**:

1. **Given** an enabled account with a current password, **When** the user checks its status,
   **Then** the output states the account is enabled, not locked, with password and account
   expiry reported (dates or "never"), and the process exits 0.
2. **Given** a disabled account, **When** the user checks its status, **Then** the verdict
   names "disabled" as a sign-in blocker with a hint that an administrator must re-enable it.
3. **Given** a locked-out account, **When** the user checks its status, **Then** the verdict
   names the lockout, shows when the lockout was recorded, and hints at unlocking or waiting
   out the lockout window.
4. **Given** an account whose password has expired, **When** the user checks its status,
   **Then** the verdict names the expired password with the expiry time and a hint to reset it.
5. **Given** an account with several simultaneous blockers (e.g., disabled *and* locked),
   **When** the user checks its status, **Then** every blocker is reported — not just the
   first one found.
6. **Given** a principal name that does not exist in the directory, **When** the user checks
   it, **Then** the outcome is a distinct "not found" failure with a hint (check spelling /
   identifier form / search scope), not a raw directory error.

---

### User Story 2 - Understand a principal's group membership (Priority: P2)

An engineer chasing an access problem ("why can't this user reach the share / app / VPN?")
needs to see what groups a user (or computer) actually belongs to. They name the principal and
get its direct group memberships — or, on request, its *effective* memberships with nesting
resolved, including membership acquired transitively and via the primary group. They can also
ask the pointed question directly: "is this user in this group?" and get a yes/no verdict with
the membership path that grants it.

**Why this priority**: Group membership is the second half of the roadmap scope and the
standard next question after account status; access debugging is impossible without it.

**Independent Test**: In a directory with nested groups, list a user's direct memberships and
confirm they match the directory; list effective memberships and confirm transitively acquired
groups (including via the primary group) appear; test membership against a group the user is
in only via nesting and confirm a positive verdict with the path shown.

**Acceptance Scenarios**:

1. **Given** a user who belongs to groups directly, **When** the user lists memberships,
   **Then** every direct group is reported with its name and location in the directory.
2. **Given** nested groups (user → group A → group B), **When** effective membership is
   requested, **Then** group B appears in the results, marked as acquired through nesting.
3. **Given** a principal and a target group, **When** a membership test is requested, **Then**
   the output is an explicit yes/no verdict; a positive verdict shows the chain that grants
   membership (direct, or the nesting path), and the exit code distinguishes member from
   non-member.
4. **Given** groups nested in a cycle (A contains B, B contains A), **When** effective
   membership is resolved, **Then** resolution terminates and reports each group once.
5. **Given** a very large group membership, **When** memberships are listed, **Then** the
   report is complete — no silent truncation at server paging limits.

---

### User Story 3 - Verify directory connectivity and credentials (Priority: P2)

Before blaming an account, an engineer needs to know the directory conversation itself works:
"can I reach this directory server, negotiate a secure connection, and authenticate with these
credentials?" They run a check against a server (or let the tool find the domain's directory
servers from the domain name) and get a staged verdict — reached / secured / authenticated —
with timing, so a network problem, a TLS problem, and a credential problem are never confused
with one another.

**Why this priority**: It is the first isolation step of every directory incident and the
natural companion the roadmap implies — but it exists to support the account-level questions
above.

**Independent Test**: Run the check against a reachable directory with valid credentials and
confirm a full success report with timings; against a nonexistent host, a host with the port
blocked, a server with an untrusted certificate, and with a wrong password — confirming four
distinct verdicts, hints, and exit classes.

**Acceptance Scenarios**:

1. **Given** a reachable directory server and valid credentials, **When** the user runs the
   connectivity check, **Then** the report confirms the server was reached, the connection
   secured, and the credentials accepted, with the server's basic identity information and
   per-stage timing, and the process exits 0.
2. **Given** an unreachable server (refused or no answer), **When** the check runs, **Then**
   the verdict is a connection-class failure (refused vs timeout distinguished, as elsewhere
   in opskit) that never mentions credentials.
3. **Given** a server whose certificate cannot be verified, **When** the check runs over an
   encrypted connection, **Then** the verdict is a TLS-class failure naming the certificate
   problem, with a hint pointing at the TLS diagnostics category for a deep inspection.
4. **Given** valid connectivity but wrong credentials, **When** the check runs, **Then** the
   verdict is an authentication-class failure ("server reached and secured; credentials
   rejected") — clearly distinct from network and TLS failures — and the supplied password
   never appears in any output, log, or error message.
5. **Given** only a domain name (no explicit server), **When** the check runs, **Then** the
   tool locates the domain's directory servers automatically and reports which server it used.

---

### User Story 4 - Audit account status across many users (Priority: P2)

During an incident ("multiple people can't log in") or a hygiene review, an engineer checks
account status for a list of users supplied as arguments, a file via `--input-file` / `-i`, or
a pipe. One unknown user must not abort the run, and the results must be consumable by scripts.

**Why this priority**: Fleet checks are where the tool replaces ad-hoc scripting loops, but
they depend on the single-user flow being right first.

**Independent Test**: Supply a file mixing healthy, disabled, locked, and nonexistent users;
confirm every line is processed, machine output contains one entry per user including
failures, and the aggregate exit code follows the established batch rule.

**Acceptance Scenarios**:

1. **Given** users supplied as multiple arguments, an input file via `--input-file` / `-i`
   (one principal per line, blank lines and `#` comments ignored), or standard input, **When**
   the user runs a batch status check, **Then** every principal is checked and reported — no
   abort on first failure — using a single directory connection and a single set of
   credentials for the whole run.
2. **Given** a mixed batch where some principals fail (e.g., not found), **When**
   machine-readable output is requested, **Then** every principal appears in the output —
   failed ones with their error, never silently dropped — and the exit code is 0 only if all
   succeed, the uniform class if all share one outcome, else the partial class.

---

### User Story 5 - Inspect directory objects' key attributes, singly or in bulk (Priority: P3)

An engineer needs the raw facts about named objects — users, groups, or computers — without
writing an LDAP filter: where each lives in the directory, its identifiers, when it was
created and changed, description, and the small set of type-specific facts that matter (for
a user: email address and contact/organizational basics; for a group: its kind and its
direct member list; for a computer: its dNS name and operating-system info). The lookup
works in both directions — user-side facts and group-side member listing are both
first-class — and accepts **multiple names in one run** (arguments, `--input-file` / `-i`,
or a pipe) so details for a list of users and/or groups come back in a single structured
report. Read-only, by name only.

**Why this priority**: A convenient escape hatch that rounds out the category; the targeted
commands above answer the common questions first.

**Independent Test**: Look up an existing user, group, and computer by name and confirm each
renders its key attributes (including the user's email and the group's members) with
directory location; feed a mixed list of user and group names and confirm one complete
report entry per name; look up a nonexistent name and confirm the distinct not-found
outcome; confirm an ambiguous name lists the candidates instead of guessing.

**Acceptance Scenarios**:

1. **Given** an existing object name and type, **When** the user looks it up, **Then** key
   attributes for that object type are reported — including a user's email address and a
   group's direct members — with its directory location and identifiers, and values are
   rendered safely regardless of their content.
2. **Given** a name matching more than one object, **When** the user looks it up, **Then** the
   tool refuses to guess: it reports each candidate with its directory location and asks the
   user to disambiguate (e.g., by distinguished name), using the usage-error class.
3. **Given** a group object, **When** the user looks it up, **Then** its direct members are
   listed as part of its key attributes.
4. **Given** several names supplied as arguments, an input file via `--input-file` / `-i`, or
   standard input — mixing users and groups, **When** the user runs the lookup, **Then**
   every name is processed and reported (failures included, never aborting the run), the
   human output presents the results as structured tables, and machine output emits one
   envelope per name following the established batch exit-code rule.

---

### User Story 6 - Use it from code (Priority: P3)

A platform engineer embeds the same directory diagnostics in their own tooling: a typed
programmatic interface returns structured results and raises typed errors, without printing
or exiting the process, and reuses one authenticated session across many queries.

**Why this priority**: API parity is an opskit constitutional guarantee and enables
integration into provisioning/monitoring tooling, but it serves the flows above.

**Independent Test**: From a short script, run a status check and a membership resolution
programmatically against a test directory, read the typed fields, reuse one session for both
calls, and catch a specific typed error for an induced authentication failure and an induced
not-found.

**Acceptance Scenarios**:

1. **Given** the library interface, **When** a status check succeeds, **Then** the caller
   receives a typed result exposing each status fact (enabled, locked, expiries, password age)
   as data — and nothing is printed.
2. **Given** an induced failure (bad credentials, unknown principal, unreachable server),
   **When** the query runs programmatically, **Then** a typed exception of the matching
   failure class is raised with an actionable message and no credential material in it.

---

### Edge Cases

- **Identifier forms**: principals are addressable by short account name, user-principal-name
  (`user@domain`), or distinguished name; the form used is detected, not guessed wrong. An
  identifier matching nothing yields not-found; matching more than one object yields the
  explicit ambiguity error (never a silent first-match).
- **Unreachable vs unauthenticated vs unauthorized**: connection failures, credential
  rejection, and permission-denied on a query (bound but not allowed to read an attribute) are
  three distinct outcomes with distinct hints.
- **Credential handling**: the password can be supplied interactively (never echoed) or via
  environment/config; it never appears in output, logs, JSON, error text, or the recorded
  query echo — the constitution's redaction guarantee. When input is piped (stdin used for
  batch), interactive prompting is impossible: the tool says so and names the alternatives
  instead of hanging.
- **Cleartext protection**: by default credentials are only sent over an encrypted connection;
  requesting an unencrypted connection with a password requires an explicit opt-in flag, and
  the report notes the connection was unencrypted.
- **Certificate failure on an encrypted connection**: reported as a TLS-class failure with a
  hint to the TLS category — never a generic "can't connect".
- **Stale lockout**: a lockout whose policy window has already elapsed may still be recorded
  on the account; the verdict reports what the directory recorded (lockout time) without
  inventing a certainty the data doesn't support.
- **"Never expires" values**: password-never-expires and account-never-expires are rendered
  as "never", not as sentinel timestamps or absurd dates.
- **Simultaneous blockers**: disabled + locked + expired are all reported together.
- **Nested-group cycles**: effective-membership resolution terminates on cycles and reports
  each group once.
- **Primary group**: effective membership includes the principal's primary group even though
  the directory stores it differently from ordinary memberships.
- **Very large result sets**: membership lists and group member lists are complete even when
  the server returns results in pages or ranges — no silent truncation.
- **Cross-domain referrals**: results from the queried directory are reported; referrals to
  other domains are surfaced as such (where the answer may be incomplete, the output says so)
  rather than silently chased or silently dropped.
- **Non-AD LDAP servers**: the connectivity check and object lookup work against any LDAP
  directory; status facts that depend on Active Directory semantics are reported as
  "not available from this server" rather than crashing or fabricating values.
- **Untrusted string content**: directory-sourced values (names, descriptions, DNs) render
  safely in styled output regardless of content.
- **Timeout controls**: connection and query timeouts are configurable; invalid controls are
  rejected before any network activity.

## Requirements *(mandatory)*

### Functional Requirements

**Targets, connection & credentials**

- **FR-001**: Users MUST be able to name the directory explicitly (server host, optional
  port) or supply only a domain name, in which case the system MUST locate the domain's
  directory servers automatically via the domain's published service records and report which
  server was used.
- **FR-002**: Connections MUST be encrypted by default (directory-over-TLS or an upgrade to
  TLS on the standard port, selectable); users MAY opt into an unencrypted connection only via
  an explicit flag, and any output for such a run MUST state the connection was unencrypted.
- **FR-003**: Users MUST be able to authenticate with their own account name and password;
  the password MUST be acceptable interactively (never echoed) and via environment/config per
  the fixed configuration precedence. One invocation uses exactly one credential; supplying
  multiple credentials per run is not a capability (no spraying by design).
- **FR-004**: Credential material MUST never appear in any output, log, JSON envelope, or
  error message; the query echo in machine output MUST record *that* authentication was used
  (and the account name) but never the secret.
- **FR-005**: Connection and query timeouts MUST be configurable; invalid controls are
  rejected before any network activity as usage errors.

**Connectivity check**

- **FR-006**: The system MUST provide a connectivity-and-authentication check that reports,
  as separately attributed stages with timing: server reached, connection secured, credentials
  accepted, plus basic server identity information — and on failure reports exactly one
  failing stage with a distinct exit class per stage (connection refused, timeout, TLS
  failure, authentication rejected).
- **FR-007**: Connection-stage failures MUST be classified identically on Windows, macOS, and
  Linux (refused vs timeout, as established by the net category), and TLS-stage failures MUST
  hint at the TLS diagnostics category.

**Account status**

- **FR-008**: The system MUST report, for a named user account: enabled/disabled, locked out
  (with recorded lockout time), password expired / expiry time / never-expires, account
  expiry time / never-expires, and when the password was last set — each as an explicit fact,
  with all sign-in blockers reported together and summarized in a plain-language verdict.
- **FR-009**: Status facts that the queried server does not expose (e.g., a non-AD directory)
  MUST be reported as unavailable, never fabricated and never a crash.

**Group membership**

- **FR-010**: The system MUST list a named principal's direct group memberships, and on
  request its effective memberships with nesting resolved — including membership via the
  primary group — marking each result as direct or transitively acquired. Resolution MUST
  terminate on nested-group cycles and report each group once.
- **FR-011**: The system MUST answer a direct membership test — "is principal P in group G?"
  — with an explicit yes/no verdict, the granting chain when positive (direct or the nesting
  path), and exit codes that distinguish member from non-member.
- **FR-012**: Membership results MUST be complete even when the server pages or ranges large
  result sets; truncation is never silent.

**Object lookup**

- **FR-013**: The system MUST provide a read-only lookup of named objects (user, group, or
  computer) reporting each one's key attributes for its type, including directory location,
  identifiers, created/changed times, a user's email address and contact/organizational
  basics, and — for a group — its direct members. Lookups take object names/identifiers
  only; arbitrary directory filter expressions are not a capability. Multiple names —
  users and groups mixable in one run — MUST be accepted with the same batch semantics as
  the status check (FR-016).
- **FR-014**: An identifier matching no object MUST yield a distinct not-found outcome with
  an actionable hint; an identifier matching multiple objects MUST list the candidates and
  refuse to guess (usage-error class).

**Contracts (per constitution)**

- **FR-015**: All commands MUST honor the opskit output contract: human-readable default,
  versioned JSON envelope, NDJSON where output is batchable, `NO_COLOR`/auto-plain behavior,
  and structured exit codes with distinct classes for usage error, resolution/discovery
  failure, connection refused, timeout, TLS failure, authentication failure, permission
  denied, not-found, and partial batch. List-shaped human output (status facts, membership
  lists, group member lists, batch results) MUST be rendered as structured tables, not free
  text.
- **FR-016**: The status check and the object lookup MUST support batch input via multiple
  arguments, an input file supplied with `--input-file` / `-i` (one name per line, blank
  lines and `#` comments ignored), or standard input; every name MUST be processed over a
  single authenticated session; failed names MUST appear in machine output with their error;
  the aggregate exit code MUST follow the established batch rule (0 all-pass / uniform class
  / else partial).
- **FR-017**: Every capability MUST be available programmatically with typed results and
  typed errors, including a reusable authenticated session object for multiple queries; the
  programmatic layer never prints, never terminates the process, and never reads
  environment/config on its own.
- **FR-018**: Behavior and output MUST be identical on Windows, macOS, and Linux; OS- and
  library-specific connection errors MUST be normalized into the shared error classification.
- **FR-019**: The feature MUST be strictly read-only and zero-telemetry: only read/search
  operations are ever issued to the directory; the only network activity is to the
  user-specified (or discovered-for-the-user's-domain) directory servers and the service-record
  lookup that discovery requires.
- **FR-020**: The feature MUST NOT provide misuse affordances (constitution Art. X): no
  credential guessing or multi-credential testing, no wildcard or filter-driven enumeration of
  the directory, no write/modify/unlock operations. Every query names its principal(s)
  explicitly.

### Key Entities

- **Directory Target**: where and how to ask — server (explicit or discovered from a domain
  name), port, connection security mode, and the authenticating account (secret excluded by
  design).
- **Account Status Report**: the status facts for one principal — enabled, lockout state and
  time, password expiry state/time, account expiry state/time, password-last-set — plus the
  derived plain-language verdict and the list of active sign-in blockers.
- **Membership Report**: the groups for one principal — each entry with group name, directory
  location, and how membership was acquired (direct, nested via a path, primary group); or,
  for a membership test, the verdict plus granting chain.
- **Directory Object Summary**: the key attributes of one named user/group/computer — type,
  identifiers, directory location, created/changed times, and type-specific facts (group kind
  and direct members; computer dNS name and OS info; user email address and
  contact/organizational basics).
- **Connectivity Report**: the staged result of the connectivity check — per-stage outcome
  and timing (reached, secured, authenticated) and the server's basic identity information.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a reachable directory, an engineer gets a complete account-status verdict
  (all blockers included) with a single command in under 10 seconds at default settings.
- **SC-002**: Every defined failure class (usage error, discovery failure, refused, timeout,
  TLS failure, authentication failure, permission denied, not-found, partial) is
  distinguishable from the others by exit code and by a one-line explanation in the output,
  verified by tests for 100% of the classes.
- **SC-003**: The same queries produce structurally identical reports on Windows, macOS, and
  Linux (verified by the CI matrix), including the refused-vs-timeout classification.
- **SC-004**: A 50-name batch with mixed outcomes — for the status check and for the object
  lookup (users and groups mixed) — completes over one authenticated session without
  aborting, reports all 50 names in machine output, and yields the documented aggregate
  exit code.
- **SC-005**: In a test directory with nested groups (including a cycle and a primary group),
  effective-membership resolution returns exactly the directory's true transitive closure,
  each group once, with acquisition paths — verified against the fixture's known topology.
- **SC-006**: No credential material appears in any human, JSON, NDJSON, log, or error output
  in the entire test suite — verified by an automated scan of captured outputs for the test
  secrets.
- **SC-007**: Network-unreachable, certificate-invalid, wrong-password, and
  permission-denied conditions each produce their own verdict and hint; no test or documented
  example conflates any two.
- **SC-008**: All capabilities are usable programmatically with typed results and a reusable
  session; the documented examples run as written.

## Assumptions

- **Active-Directory-first, LDAP-general**: the category is designed around AD semantics
  (lockout, password/account expiry, primary group, nested groups) because that is the
  roadmap's problem statement; the connectivity check and object lookup work against any
  LDAP-speaking directory, and AD-specific facts degrade to "not available" elsewhere.
- **Authentication is the operator's own username + password** (Art. X wording). Single
  sign-on / Kerberos tickets and client certificates are out of scope for v1: they require
  platform-native machinery that conflicts with the pure-Python identical-everywhere
  guarantee. The password may come from an interactive prompt, environment, or config —
  never from a positional argument.
- **Encrypted by default**: modern directories reject cleartext password binds; opskit
  defaults to encrypted connections on the standard secure port (or TLS upgrade on the
  standard port) and treats deliberate cleartext as an explicit, visible opt-in for lab use.
- **Server discovery from a domain name is in scope**: operators know their domain more often
  than a specific DC; the domain's published directory service records are the standard,
  pure-lookup way to find one, and it reuses capability the project already has. An explicit
  server always wins over discovery.
- **Certificate verification uses the platform trust store by default**, consistent with the
  TLS category, with the same escape hatches (custom CA bundle); deep certificate diagnosis
  is the TLS category's job and failures point there.
- **Lockout/expiry interpretation is honest**: verdicts derive from what the directory
  records (and its policy information where readable); where the data cannot support
  certainty (stale lockout, unreadable policy), the output reports the recorded facts and
  says what is uncertain rather than guessing.
- **No enumeration by design (Art. X)**: every command takes explicitly named principals;
  there are no filter expressions, wildcard searches, or "dump all users/groups" affordances.
  Listing a *named* group's direct members is a targeted diagnostic, not enumeration.
- **One credential per invocation**: batch varies the *queried* principals only, never the
  authenticating credential — multi-credential testing is a misuse affordance and excluded.
- **Defaults follow the established opskit conventions**: 5-second network timeout, standard
  directory ports by security mode, batch file conventions (`--input-file` / `-i`, comments,
  blank lines) exactly as in the net category.
- **Testing relies on an in-process loopback/mock directory** (as DNS/net/TLS did with their
  loopback layers) covering every failure class; tests against a real domain controller are
  opt-in `network`-marked and never gate CI.
