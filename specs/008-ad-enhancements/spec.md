# Feature Specification: AD Diagnostics Enhancements

**Feature Branch**: `008-ad-enhancements`

**Created**: 2026-08-08

**Status**: Draft

**Input**: User description: "AD diagnostics enhancements for the existing `opskit ad` category, addressing real-world troubleshooting gaps gathered on 2026-07-16 (see project memory ad-006-backlog): (1) cross-type 'did you mean' error when a group-lookup identifier actually matches a group; (2) a new `ad members <group>` command with nested/effective expansion, mirroring `ad groups --effective`; (3) UPN/mail matching so a `user@domain` identifier also matches the `mail` attribute, preserving refuse-on-ambiguity; (4) sharper TLS verification hints (StartTLS does not bypass verification; WSL doesn't inherit the Windows trust store) plus a README section on trusting a corporate CA. Anonymous-bind hints and Global Catalog cross-domain lookups are explicitly out of scope."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get redirected when a group name is queried the wrong way (Priority: P1)

An engineer runs `opskit ad groups <name>` (or `opskit ad member <name> <group>`) meaning to
look up a user's or computer's group memberships, but `<name>` is actually the name of a
group, not a user or computer account. Today the tool reports a generic "no user or computer
account found" error, which reads as a typo or missing-account problem and sends the engineer
down the wrong troubleshooting path. Instead, the tool should recognize that the identifier
resolves to a group and tell the engineer so, pointing them at the commands that actually
answer their likely question: `opskit ad members` (see that group's members) or
`opskit ad member` (test whether a specific principal belongs to it).

**Why this priority**: This is the single most disruptive gap — it actively misleads rather
than merely under-informing, and it was the concrete failure that triggered this backlog.

**Independent Test**: Run `opskit ad groups <name>` (or `ad member`) against a directory where
`<name>` matches an existing group and no user/computer account. Verify the error names the
mismatch and points at `ad members` / `ad member` instead of reporting a bare not-found.

**Acceptance Scenarios**:

1. **Given** a directory containing a group named `sg-gs-test-unix` and no user or computer
   account by that name, **When** the engineer runs `opskit ad groups sg-gs-test-unix`,
   **Then** the command fails with an error stating that `sg-gs-test-unix` is a group, not a
   user or computer account, and suggesting `opskit ad members sg-gs-test-unix` and
   `opskit ad member <principal> sg-gs-test-unix`.
2. **Given** the same directory, **When** the engineer runs
   `opskit ad member sg-gs-test-unix sg-gs-test-unix` (using the group name where a principal
   was expected), **Then** the command fails with the same group-redirect error for the
   principal argument, not the generic not-found error.
3. **Given** an identifier that matches neither a user/computer nor a group, **When** the
   engineer runs `opskit ad groups <identifier>`, **Then** the existing generic not-found
   error is unchanged (no regression for the true not-found case).

---

### User Story 2 - See a group's full effective membership in one command (Priority: P1)

An engineer wants to know everyone who ultimately belongs to a group, including through
nested groups, not just the accounts listed directly on that group's `member` attribute.
Today, `opskit ad show <group>` only lists direct members, and there is no reverse
equivalent of `opskit ad groups --effective` (which already expands a *user's* nested group
memberships). The engineer needs a dedicated command that walks nested group membership
outward from a group to its full member set.

**Why this priority**: This is the second concrete gap Prabha hit directly, and it closes an
existing asymmetry in the tool (nested expansion exists for user→groups but not group→members).

**Independent Test**: Run `opskit ad members <group>` against a directory with a group that
has both direct user members and a nested child group with its own members. Verify the output
includes members gained only through the nested group, each annotated with how they were
reached.

**Acceptance Scenarios**:

1. **Given** a group `G1` with direct user member `alice` and nested child group `G2` (itself
   a member of `G1`), and `G2` has direct user member `bob`, **When** the engineer runs
   `opskit ad members G1`, **Then** the output lists `alice` (direct), `G2` itself (direct —
   it is a member of `G1`, same as `alice`), and `bob` (via `G2`), each showing its
   membership path.
2. **Given** the same setup, **When** the engineer runs `opskit ad members G1 --direct`,
   **Then** only `alice` and `G2` are listed (both direct members of `G1`), and `bob` is not
   (nested expansion did not run).
3. **Given** a nested group cycle (`G1` contains `G2`, `G2` contains `G1`), **When** the
   engineer runs `opskit ad members G1`, **Then** the command completes without an infinite
   loop or crash and reports each distinct member once.
4. **Given** multiple group names or `--input-file`/stdin batch input, **When** the engineer
   runs `opskit ad members` against them, **Then** every target is processed independently
   (a failure on one does not abort the others), matching the batch/JSON contract of the
   other `ad` commands.

---

### User Story 3 - Find a user by their email address (Priority: P2)

An engineer looks up a user using their email address, e.g.
`opskit ad show jane.doe@example.com`. In this directory, `jane.doe@example.com` is her
`mail` attribute, while her actual `userPrincipalName` is a different string (a common
mismatch in real AD environments with multiple UPN suffixes). Today the lookup only checks
`userPrincipalName` for anything containing `@`, so it reports not-found even though the
account exists and the address is right there in the directory.

**Why this priority**: Real but narrower than P1 items — it affects a specific identifier
form (email-shaped input) rather than every invocation of a command.

**Independent Test**: Run `opskit ad show <email>` (or `ad user`, `ad groups`, `ad member`)
against a directory where `<email>` matches only the `mail` attribute of one account and no
account's `userPrincipalName`. Verify the account is found.

**Acceptance Scenarios**:

1. **Given** a user whose `mail` is `jane.doe@example.com` and whose `userPrincipalName` is
   `jdoe@corp.example.net`, **When** the engineer runs
   `opskit ad show jane.doe@example.com`, **Then** the command returns that user's summary.
2. **Given** two different accounts where one has `userPrincipalName=x@example.com` and the
   other has `mail=x@example.com`, **When** the engineer runs a lookup for `x@example.com`,
   **Then** the command refuses to guess and reports an ambiguous-match error listing both
   candidates, exactly as it already does for other multi-match cases.
3. **Given** a user whose `userPrincipalName` and `mail` are both `x@example.com` (the common
   case), **When** the engineer looks up `x@example.com`, **Then** exactly one account is
   returned (not reported as ambiguous merely because both attributes matched the same entry).

---

### User Story 4 - Understand what a certificate-verification failure means and how to fix it (Priority: P3)

An engineer hits a TLS certificate verification failure while running `opskit ad check` with
`--starttls`, and mistakenly believes that switching to StartTLS avoids certificate checking
the way it might for a "just get me connected" tool. Separately, when working from WSL, the
engineer doesn't realize their Windows-side corporate CA trust isn't visible to the Linux
process running opskit. Today's hint text doesn't address either misconception, and there is
no documentation describing how to actually resolve it (obtain the corporate root CA PEM and
pass `--ca-file`).

**Why this priority**: Lowest priority of the four — it improves error-message clarity and
documentation rather than fixing a functional gap, and the engineer can still self-resolve
the underlying TLS issue (as Prabha did) without it.

**Independent Test**: Trigger a certificate verification failure with `--starttls` against a
loopback/mock server presenting an untrusted certificate. Verify the resulting hint text
states that StartTLS does not skip certificate verification, and (on a simulated/documented
WSL context) mentions that WSL does not inherit the Windows trust store. Verify the `ad`
README has a section explaining how to obtain a corporate root CA PEM and use `--ca-file`.

**Acceptance Scenarios**:

1. **Given** a directory server presenting a certificate not trusted by the platform store,
   **When** the engineer runs `opskit ad check <server> --starttls`, **Then** the failure's
   hint text explicitly states that `--starttls` does not bypass certificate verification.
2. **Given** the same failure, **When** the engineer reads the hint text, **Then** it
   mentions that WSL/Linux does not automatically trust the Windows certificate store and
   that a private CA needs `--ca-file`.
3. **Given** the `opskit ad` README, **When** the engineer looks for TLS troubleshooting
   guidance, **Then** they find a section titled to indicate how to trust a corporate CA,
   describing how to obtain the root CA PEM and pass it via `--ca-file`.

---

### Edge Cases

- An identifier that matches both a group and (separately) a user/computer account is not
  expected to occur for the exact-match filters this tool uses (`sAMAccountName`, `cn`,
  `userPrincipalName`, `mail` all scoped by object class) — group and principal filters are
  disjoint by object class, so this case cannot arise from a single identifier evaluated
  against a single class-scoped filter. The `ad groups`/`ad member` fallback query for the
  group-redirect check (User Story 1) is itself class-scoped to groups only.
- `ad members` on a group with zero members (direct or nested) reports an empty member list,
  not an error.
- `ad members` on an identifier that matches neither a group nor anything else reports the
  existing not-found error, scoped to "group" (unchanged from today's `ad show --type group`
  behavior).
- A `mail` attribute value that isn't `@`-shaped is not a concern here: mail-matching is only
  ever attempted for identifiers already classified as UPN-shaped (containing `@`); the
  attribute is compared by exact value, not re-parsed.
- Nested-group cycles (a group that is, directly or transitively, its own member) must
  terminate and de-duplicate, matching the existing cycle-safety guarantee already documented
  for `ad groups --effective`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a lookup scoped to user/computer accounts (used by `ad groups`, `ad
  member`, `ad user`) finds no matching user or computer but the same identifier matches
  exactly one group, the system MUST report an error stating that the identifier is a group
  (naming it) rather than the generic not-found error, and MUST suggest `ad members` and
  `ad member` as the applicable commands.
- **FR-002**: When the identifier matches neither a user/computer account nor a group, the
  system MUST continue to report the existing generic not-found error unchanged.
- **FR-003**: The system MUST provide a new command, `ad members <group>`, that reports the
  full set of members that ultimately belong to a named group, including members gained
  through nested (transitive) group membership, by default. A nested group that is itself a
  direct member of the queried group is included as an entry in its own right (it *is* a
  direct member, literally), alongside — not instead of — the accounts reached through it;
  the report is not filtered down to leaf user/computer accounts only.
- **FR-004**: `ad members` MUST support a direct-only mode (`--direct`) that reports only
  members listed directly on the group (which may themselves be users, computers, or
  groups), without expanding any nested group found there.
- **FR-005**: `ad members` MUST report, for each member reached only through nesting, the
  membership path (which intermediate group(s) it came through), consistent with how
  `ad groups --effective` already reports acquisition paths for the reverse direction.
- **FR-006**: `ad members` MUST terminate and de-duplicate correctly when nested group
  membership contains a cycle, returning each distinct member exactly once.
- **FR-007**: `ad members` MUST accept multiple group targets (positional arguments,
  `--input-file`, or stdin) and process every target independently, matching the batch
  processing and JSON/JSONL envelope contract used by the other `ad` commands (a failure on
  one target must not abort the others).
- **FR-008**: For any identifier already classified as UPN-shaped (containing `@`), lookups
  performed by `ad user`, `ad show`, `ad groups`, and `ad member` MUST match against either
  the `userPrincipalName` attribute or the `mail` attribute (an "or" match), not
  `userPrincipalName` alone.
- **FR-009**: If a UPN-shaped identifier's combined `userPrincipalName`-or-`mail` match
  resolves to more than one distinct directory object, the system MUST refuse to guess and
  report the existing ambiguous-match error listing the candidates, exactly as it does for
  other multi-match lookups today.
- **FR-010**: If a UPN-shaped identifier matches a single directory object via `mail`,
  `userPrincipalName`, or both, the system MUST resolve to that one object (matching on both
  attributes of the same entry is not treated as an ambiguous multi-match).
- **FR-011**: When a certificate verification failure occurs while `--starttls` was
  requested, the failure's hint text MUST state that `--starttls` does not bypass or weaken
  certificate verification.
- **FR-012**: The certificate-verification failure hint text MUST additionally note that a
  WSL/Linux environment does not automatically inherit a Windows host's certificate trust
  store, and that a private/corporate CA requires `--ca-file`.
- **FR-013**: The `opskit ad` documentation (README) MUST include a section explaining how to
  obtain a corporate root CA certificate and use it via `--ca-file`.
- **FR-014**: This feature MUST NOT introduce any new network destination, write path, or
  *unscoped* directory scanning/cross-target discovery capability (e.g. no arbitrary
  filters, no enumerate-everything mode). `ad members`' effective-mode traversal — recursing
  into nested groups found *while resolving the one group the caller named* — is
  target-scoped, not unscoped scanning, and is explicitly permitted (FR-003/FR-007); it
  never reads any object other than the ones reachable from that single named starting
  point. Article X remains fully read-only for the `ad` category — no write-path exception,
  unlike `file`.
- **FR-015**: Anonymous-bind hints and Global Catalog (port 3268/3269) cross-domain lookups
  are explicitly out of scope for this feature and MUST NOT be implemented as part of it.

### Key Entities

- **Group redirect error**: A new outcome of principal-scoped lookups indicating "this
  identifier is a group, not a user/computer account," carrying the resolved group's name
  and a suggestion of which commands to use instead.
- **Effective group membership (members direction)**: The full set of accounts belonging to
  a group, each with a flag/path indicating direct vs. nested acquisition — the mirror
  concept of the existing "effective group membership (groups direction)" already reported by
  `ad groups --effective`.
- **UPN/mail identifier match**: The resolution rule for an `@`-shaped identifier, now
  covering two attributes (`userPrincipalName`, `mail`) instead of one, still resolving to at
  most one object or an explicit ambiguity error.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An engineer who queries a group name through a principal-scoped command (`ad
  groups`, `ad member`) receives an error that correctly identifies the name as a group and
  names the right command to use next, in 100% of cases where that identifier matches exactly
  one group and no user/computer account.
- **SC-002**: An engineer can retrieve a group's complete effective (nested-inclusive)
  membership using a single `opskit ad members` invocation, with no manual cross-referencing
  of multiple command outputs.
- **SC-003**: Looking up an account by an email address that matches its `mail` attribute
  (but not its `userPrincipalName`) succeeds on the first attempt, with no change in behavior
  for identifiers that only ever matched `userPrincipalName` before.
- **SC-004**: An engineer who hits a certificate verification failure while using `--starttls`
  can determine from the error text alone, without external research, that StartTLS does not
  bypass verification and that a WSL trust-store mismatch is a likely cause.
- **SC-005**: None of the four changes alters the observable behavior, output shape, or exit
  codes of any existing `ad` command invocation that does not exercise one of the four new
  code paths (no regressions to `ad check`, `ad user`, `ad show`, `ad groups`, `ad member` for
  their previously working cases).

## Assumptions

- All four changes extend the existing `src/opskit/ad/` package; no new top-level category or
  CLI sub-app is created.
- This remains a strictly read-only diagnostics category; the Article X write-path exception
  (introduced for the `file` category) does not apply here and is not being requested.
- "Nested/effective" group-membership semantics for `ad members` mirror the existing
  `memberOf`-walk cycle-safety and path-reporting behavior already implemented for
  `ad groups --effective`, applied in the reverse traversal direction (walking a group's
  `member` attribute, and its child groups' `member` attributes, outward).
- The `mail` attribute is assumed to be single-valued for matching purposes, consistent with
  how `userPrincipalName` is already treated as single-valued equality today.
- Anonymous-bind hints and Global Catalog cross-domain lookups, both flagged as lower-priority
  "possibly" candidates in the originating backlog, are deferred to a future feature and are
  not addressed here.
- The CLI flag for `ad members`' direct-only mode is `--direct` (resolved during planning;
  see `contracts/cli.md`) — the inverse of `ad groups`'s `-e/--effective` opt-in, since
  `ad members` defaults to effective/nested rather than direct (see data-model.md's note on
  this intentional asymmetry).
