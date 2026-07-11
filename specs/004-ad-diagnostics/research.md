# Phase 0 Research: Active Directory / LDAP Diagnostics

Decisions resolving every technical unknown in the plan's Technical Context. Format per
speckit: Decision / Rationale / Alternatives considered.

## R1. LDAP client — ldap3, shipped as the `opskit[ad]` extra, quarantined in one adapter

**Decision**: Use **ldap3** (`>=2.9,<3`), declared under `[project.optional-dependencies]`
as `ad = ["ldap3>=2.9,<3"]` — the first category extra, filling the slot PLAN.md reserved
in 2026-07-01's dependency decision. The dev extra also gains ldap3 (tests require it).
All ldap3 imports live in **one module**, `ad/directory.py`, imported lazily by `api.py`;
`ad/cli.py`, `models.py`, `errors.py`, `output.py`, `attributes.py`, `discovery.py` never
import it. When the import fails, commands raise `DependencyMissing` (usage class, exit 2)
with the hint `pip install "opskit[ad]"` — `opskit ad --help` and the docs-coverage gate
work without the extra installed.

**Rationale**: ldap3 is the only viable pure-Python LDAP client (Art. VI): full LDAPv3,
StartTLS/LDAPS, paged search, ranged-attribute retrieval, and an offline mock strategy that
solves the test-layer problem (R8). Its maintenance posture is the trade-off: last release
2.9.1 (2021), low activity — but it is MIT, pip-audit-clean, dependency-free, and remains
the ecosystem default. Mitigations: pinned major, adapter quarantine (a future swap touches
one module), and the extra keeps the base install slim so non-AD users carry zero exposure.
Logged in plan.md's Complexity Tracking.

**Alternatives considered**: `python-ldap` (rejected: C bindings to OpenLDAP — no Windows
wheels story, breaks pure-Python parity); `bonsai` (rejected: C extension, same problem);
`msldap` (rejected: maintained but built as offensive tooling — wrong lineage for an
Art. X-scoped project, and asyncio-first); hand-rolled LDAP over stdlib (rejected: BER/ASN.1
encoding, SASL, referrals — more code than the whole category); making ldap3 a base
dependency (rejected: PLAN.md's extras decision exists precisely to keep the base slim).

## R2. Connection security — LDAPS by default; `--starttls`; `--plaintext` as explicit opt-in

**Decision**: Three connection modes; default **LDAPS** (implicit TLS, default port 636):

- default: LDAPS, `ssl.CERT_REQUIRED` against the platform trust store (`ssl.create_default_context()`
  semantics via ldap3's `Tls` object), `--ca-file` replacing the store for private PKI —
  mirroring the tls category's option exactly;
- `--starttls`: plain connect on 389 then mandatory TLS upgrade before any bind; upgrade
  failure is a TLS-class error (never a silent fallback to cleartext);
- `--plaintext`: no TLS at all, port 389. Supplying a password in this mode requires the
  flag (without it, a password + no TLS is `CleartextRefused`, usage class); every report
  rendered from a plaintext run carries a visible "connection was not encrypted" marker and
  the JSON query echo records `"security": "plaintext"`.

Ports default by mode (636 / 389 / 389); an explicit `:port` on the server always wins.

**Rationale**: FR-002 mandates encrypted-by-default with visible cleartext opt-in. Modern AD
rejects simple binds on cleartext connections anyway (LDAP signing/channel-binding
hardening), so LDAPS-first is also the path that *works* against real DCs. Certificate
verification defaulting to the platform store with `--ca-file` matches the tls category's
established contract (spec assumption) — one mental model across categories.

**Alternatives considered**: StartTLS as default (rejected: two round-trips to the same
guarantee, and AD environments conventionally expose 636; LDAPS is the stronger default);
an `--insecure` skip-verify flag like curl's (rejected: unverified TLS to a server you're
about to send a password to is a credential-exposure feature — private PKI is served by
`--ca-file`, and cert *diagnosis* belongs to `opskit tls`); refusing cleartext entirely
(rejected: anonymous-bind lab/legacy diagnostics are legitimate; the flag + visible marker
keeps it honest).

## R3. Error normalization — reuse net/tls classes for shared outcomes; new ad errors for the rest

**Decision**: `ad/directory.py` catches ldap3's exception zoo and raw `OSError`s and
re-raises the shared hierarchy:

- server unreachable → `net.ConnectRefused` / `net.ConnectTimeout` (exit 8 / 6) — the same
  user-visible classes and codes as `net check`/`tls check`;
- TLS establishment/upgrade failure → `tls.HandshakeError` (exit 9); certificate
  verification failure → `tls.CertificateInvalid` (exit 10), hint pointing at
  `opskit tls check <server>:636`;
- bind rejected (LDAP resultCode 49) → **`AuthenticationFailed`** (new, exit 14). AD encodes
  the *reason* in the diagnostic message (`data 52e` bad password, `530/531` logon
  time/workstation restriction, `532` password expired, `533` account disabled, `701`
  account expired, `775` locked out): parse it when present and put the decoded reason in
  the hint — turning `ad check` into a sign-in diagnostic even when the bind itself is the
  thing failing;
- authorization failures on a query (resultCode 50 `insufficientAccessRights`, plus
  AD's read-denied surfacings) → **`PermissionDenied`** (new, exit 15, hint: bound account
  lacks read rights / try different credentials);
- search matched nothing → **`PrincipalNotFound`** (new, exit 16); matched >1 →
  **`AmbiguousPrincipal`** (usage class, exit 2) listing each candidate DN;
- SRV discovery found no records / domain unresolvable → **`DiscoveryError`**
  (resolution class, exit 3 — same class as DNS NXDOMAIN, which is what it is);
- anything else from ldap3 → `AdError` (exit 1) with the original message — a raw ldap3
  exception or `OSError` never reaches the CLI (Art. VI).

**Rationale**: FR-015 requires nine distinguishable classes; reusing net/tls classes where
the outcome *is* the same class (refused, timeout, TLS, cert) keeps scripts' exit-code
branching consistent across categories (the tls research's "a timeout is a timeout" rule)
and reuses proven cross-platform classification. Only genuinely new outcome classes get new
codes: 14–17 (17 is R7's membership verdict, not an error). The AD `data`-code decoding is
the single highest-value hint in the category — it answers US1's question at bind time.

**Alternatives considered**: ad-local duplicates of refused/timeout/TLS errors (rejected:
same failure, two codes — breaks SC-003-style cross-category consistency and doubles the
documented surface); mapping not-found onto exit 3 (rejected: FR-015 lists discovery failure
and principal-not-found as distinct classes — "can't find a DC" and "no such user" need
different script branches); a single `AdQueryError` for auth+permission+not-found
(rejected: the spec's US3/edge cases explicitly require the three to be distinct).

## R4. DC discovery — SRV via the in-tree dns category; AD-specific record first

**Decision**: `ad/discovery.py` resolves a bare domain into an ordered candidate list by
querying SRV `_ldap._tcp.dc._msdcs.<domain>` first (AD's DC-specific record), falling back
to `_ldap._tcp.<domain>` (generic LDAP), using **`opskit.dns.lookup`** with the system
resolver. Candidates are ordered by SRV priority (ascending) then weight (descending —
deterministic, no weighted randomness); the connection layer tries candidates in order until
one completes the reach stage, and the used server is recorded in every report
(`ad check` acceptance scenario 5). SRV-supplied ports are ignored in favor of the security
mode's port (SRV advertises 389; LDAPS-by-default needs 636 on the same host) — documented
in the README. An explicit `--server` bypasses discovery entirely.

**Rationale**: SRV discovery is the standard, read-only, pure-lookup way to find a DC and
reuses the project's own dns machinery (zero new code paths for resolver selection, error
normalization already done). Deterministic ordering keeps behavior identical everywhere
(Art. VI) and testable. The `dc._msdcs` record filters out non-DC LDAP servers registered
under the generic name.

**Alternatives considered**: dnspython directly (rejected: re-implements resolver discovery
and error normalization `opskit.dns` already owns; in-tree reuse mirrors tls→net);
CLDAP ping to pick the "closest" DC (rejected: netlogon-protocol complexity for a
diagnostic that just needs *a* working DC; candidates are tried in order anyway); honoring
SRV weight randomization per RFC 2782 (rejected: nondeterminism across runs hurts
reproducible diagnostics and tests; priority order preserved, which is the part that
matters).

## R5. Account status semantics — computed attributes first, raw attributes as fallback

**Decision**: `ad user` reads, per principal:
`userAccountControl`, `lockoutTime`, `pwdLastSet`, `accountExpires`, `whenCreated`,
`sAMAccountName`, `userPrincipalName`, `distinguishedName`, plus the AD **constructed**
attributes `msDS-User-Account-Control-Computed` and `msDS-UserPasswordExpiryTimeComputed`.
Fact derivation (all in pure-function `ad/attributes.py`):

- **enabled**: NOT `userAccountControl & 0x2` (ACCOUNTDISABLE);
- **locked**: `msDS-User-Account-Control-Computed & 0x10` (UF_LOCKOUT) — the server-side
  computation that already accounts for an elapsed lockout window; fallback when the
  constructed attribute is unreadable: `lockoutTime > 0`, reported with the spec's
  "recorded lockout, may be stale" honesty wording; lockout time rendered from
  `lockoutTime` either way;
- **password expired**: `msDS-User-Account-Control-Computed & 0x800000`
  (UF_PASSWORD_EXPIRED); expiry instant from `msDS-UserPasswordExpiryTimeComputed`
  (sentinel `0x7FFFFFFFFFFFFFFF`/absent → "never", consistent with
  `userAccountControl & 0x10000` DONT_EXPIRE_PASSWORD); `pwdLastSet == 0` reported as
  "must change password at next sign-in";
- **account expired**: `accountExpires` (0 or `0x7FFFFFFFFFFFFFFF` → "never"; else expired
  iff in the past);
- **verdict**: every active blocker listed (disabled, locked, password expired, must-change,
  account expired) — never just the first (spec scenario 5); zero blockers → "no sign-in
  blockers found".

FILETIME values (100 ns ticks since 1601-01-01 UTC) convert via a dedicated helper handling
both sentinels, string/int wire forms, and out-of-`datetime`-range values; Hypothesis
property-tests the conversion. On non-AD servers every missing attribute degrades that fact
to "not available from this server" (FR-009) — never a crash, never a guess.

**Rationale**: the constructed attributes are the directory's *own* answers (lockout with
policy applied; the actual computed password-expiry instant, which otherwise requires
reading domain policy and fine-grained password-policy objects) — using them is both more
correct and less code. Raw-attribute fallbacks keep the command useful against older/
restricted servers with the honesty wording the spec requires.

**Alternatives considered**: computing expiry from `pwdLastSet` + domain `maxPwdAge`
(rejected as primary: wrong under fine-grained password policies (PSOs); kept conceptually
as what the constructed attribute abstracts away); treating `lockoutTime > 0` as
authoritative (rejected: stale-lockout false positives — the spec's own edge case);
`LDAP_SERVER_EXTENDED_DN` / tokenGroups tricks for status (rejected: not status data).

## R6. Principal identification — form detection, escaped equality filters, ambiguity refusal

**Decision**: Identifier handling in `models.py`/`directory.py`:

- **form detection**: contains `=` → DN (searched by base-scope read of that DN); contains
  `@` → `userPrincipalName` equality; else → `sAMAccountName` equality. `DOMAIN\name` input
  strips the netbios prefix and uses the name part (documented);
- **every value interpolated into a filter passes RFC 4515 escaping**
  (`models.escape_filter_value`, semantically equivalent to ldap3's
  `escape_filter_chars` but implemented in-house so filter building never needs the
  optional dependency; property-tested) — user input can never alter filter structure
  (LDAP-injection-proof);
- **object-class scoping per command**: `ad user`/`ad groups`/`ad member` principal →
  `user`/`computer` objects (a `$`-suffixed name is a computer account and works
  unchanged); `ad member` group + `ad show --type` → the matching class; `ad show` default
  `--type auto` searches across user/group/computer;
- **search base**: the server's `defaultNamingContext` from the rootDSE (read during
  connect, always readable), overridable with `--base-dn`;
- zero matches → `PrincipalNotFound` (exit 16); multiple matches → `AmbiguousPrincipal`
  (exit 2) listing each candidate's DN and telling the user to pass a DN.

**Rationale**: the three-form rule matches how operators actually paste identifiers, is
deterministic (no fallback cascade that could match the wrong object silently — the spec's
"detected, not guessed wrong"), and each form maps to exactly one indexed AD attribute.
Escaping at the single choke point where filters are built makes injection structurally
impossible rather than reviewed-per-callsite.

**Alternatives considered**: trying sAMAccountName then UPN then CN in sequence (rejected:
a user named like someone else's UPN silently resolves to the wrong object — the exact
failure the ambiguity rule exists to prevent); exposing a raw `--filter` option (rejected:
FR-013/FR-020 — that is the enumeration affordance the spec forbids); ANR
(ambiguous-name-resolution) search (rejected: fuzzy multi-attribute matching guarantees
ambiguity noise and is AD-only).

## R7. Membership resolution — client-side BFS over `memberOf` + primary group; paths; verdict code

**Decision**:

- **direct** (`ad groups P`): the principal's `memberOf` values plus the **primary group**
  (resolved from `primaryGroupID` + the principal's `objectSid` domain prefix → group SID →
  base-scope lookup), the latter marked `via: primary`;
- **effective** (`ad groups P --effective`): breadth-first traversal of each direct group's
  `memberOf`, visited-set for cycle safety (each group reported once, spec scenario 4),
  recording for every group the **first (shortest) acquisition path**; entries marked
  `direct` / `nested` / `primary` with the path rendered for nested entries;
- **membership test** (`ad member P G`): resolve G, run the same BFS until G is found →
  verdict `member` with the granting chain; exhausted → `not a member`. Exit code:
  **0** member / **17 `NOT_MEMBER`** not-a-member (a verdict, not an error — modeled like
  tls's `CERT_EXPIRING = 11` verdict code); directory errors keep their own classes;
- **completeness**: searches use ldap3 paged search (`paged_size=500`, cookie loop) and
  `auto_range=True` so `member`/`memberOf` sets beyond server limits (AD pages at 1000,
  ranges multi-valued attributes at 1500) are always complete (FR-012);
- large-group member listing in `ad show <group>` uses the same paging/ranging.

**Rationale**: BFS gives everything the spec asks for in one mechanism — direct/nested
marking, shortest granting chain (US2 scenario 3), cycle termination, and it works on any
LDAP server (nested-group semantics permitting). Per-principal traversal depth in real
directories is small; one paged query per *distinct group* is the cost, bounded by the
visited set. Shortest-path-first (BFS, not DFS) makes the reported chain the most useful
one.

**Alternatives considered**: AD's transitive-expansion matching rule
`memberOf:1.2.840.113556.1.4.1941:=<DN>` in a single server-side query (rejected as
primary: returns the *set* but no acquisition paths — cannot mark direct-vs-nested or show
the granting chain the spec requires; also AD-only and notoriously slow on large
directories; noted as a possible future `--fast` set-only mode); `tokenGroups` constructed
attribute (rejected: SIDs only, requires resolving each SID back to a group, loses paths,
and includes SID-history noise); DFS traversal (rejected: first-found path may be
arbitrarily long — BFS's shortest chain is the diagnostic answer).

## R8. Test strategy — ldap3 offline mock as the directory layer; loopback for the socket stages

**Decision**: Three deterministic layers gate CI; real-DC tests never do
(`@pytest.mark.network`):

1. **Unit + Hypothesis** (`tests/unit/`): `attributes.py` pure functions — FILETIME
   conversion (property: round-trip + sentinel handling over the full value range), UAC bit
   derivations, SID parse/derive, identifier-form detection, filter-escaping wrapper;
   discovery ordering with an injected dns lookup; models `to_dict` shapes.
2. **Offline mock directory** (ldap3 `MOCK_SYNC` strategy with a fixture directory built in
   `tests/integration/test_ad_mock_directory.py` + a shared conftest builder): user entries
   covering every status permutation (enabled/disabled × locked/stale-locked × password
   expired/never-expires/must-change × account expired/never), group topology with
   nesting, a cycle (A∈B, B∈A), a primary group, and a >paging-limit group; bind
   success/invalid-credentials; missing-constructed-attribute degradation; ambiguous
   sAMAccountName. This layer pins every directory-semantics branch platform-independently.
   Where MOCK_SYNC can't express a behavior (StartTLS upgrade, AD `data`-code bind
   messages, paged-search cookies), targeted monkeypatched fakes at the `directory.py` seam
   pin those branches.
3. **Loopback sockets** (`tests/integration/test_ad_loopback.py`): refused/timeout connect
   stages against closed/never-answering loopback ports asserted as the **`NetError` class
   family** (the canonical cross-OS lesson); the existing self-signed loopback TLS fixture
   proving certificate failure → `tls.CertificateInvalid` with the tls-category hint.

CLI-level tests cover: prompt vs `OPSKIT_AD_PASSWORD` env sourcing, piped-stdin-forbids-
prompt behavior, missing-extra hint, batch envelopes/aggregate codes, and the **suite-wide
redaction scan** — a fixture that captures every stdout/stderr/log record produced by ad
tests and asserts the test password never appears (SC-006).

**Rationale**: there is no in-process real-LDAP-server equivalent of dnslib — MOCK_SYNC is
ldap3's supported answer and covers exactly the layer (directory semantics) that needs
hundreds of permutations; socket-stage behavior is already proven cross-platform by the
net/tls loopback patterns, so ad reuses them rather than inventing a protocol server. The
split keeps every branch deterministic while the `network` marker keeps real-AD truth
observable out-of-band.

**Alternatives considered**: spinning up OpenLDAP/Samba in CI containers (rejected: not
available on macOS/Windows runners — would create Linux-only coverage, violating the
"green PR is not a green main" rule); mocking at the `opskit.ad.api` level (rejected: leaves
`directory.py`'s normalization — the riskiest code — untested); vendoring a toy LDAP server
(rejected: writing a BER parser to test a BER client).

## R9. Typing the untyped dependency — adapter quarantine + scoped checker overrides

**Decision**: ldap3 ships no `py.typed` and no stubs exist. Confine it to `ad/directory.py`
whose public functions expose only opskit types (typed dataclasses in, typed
models/exceptions out). Add scoped overrides — mypy:
`[[tool.mypy.overrides]] module = "ldap3.*"` with `ignore_missing_imports = true`; pyright:
`reportMissingTypeStubs = false` scoped via a `directory.py`-local `# pyright: ...` comment
or the module override table — keeping `--strict` fully intact for every other module.
Values read from ldap3 entries are coerced at the boundary (`str()`/`int()` with
wire-form handling in `attributes.py`) so no `Any` leaks past the adapter.

**Rationale**: this is the same "quarantine the untyped world at one seam" pattern the
codebase already applies to OS sockets (net) and pyopenssl surfaces (tls), applied to a
whole library; it keeps the Art. VII typed-core guarantee meaningful and makes the future
ldap3-replacement scenario (R1 risk) a one-module job.

**Alternatives considered**: global `ignore_missing_imports` (rejected: silently weakens
every future import); writing full ldap3 stubs (rejected: stubbing a large library to use
six functions; the adapter's typed wrappers are the useful subset of that work).

## R10. CLI shape — five commands; connection options shared; env-var credential path

**Decision**: One Typer sub-app `opskit ad` with five commands mapping one-to-one onto the
spec's stories:

- **`opskit ad check [SERVER]`** — staged connectivity/bind verdict. Positional = explicit
  server (`host` / `host:port`); `-d/--domain` = discover DCs instead (exactly one of the
  two required).
- **`opskit ad user [PRINCIPALS]...`** — account status; variadic + `-i/--input-file`
  (`-` = stdin) batch over **one session**; `--watch` (signature = the JSON of each
  principal's blocker set + enabled/locked/expiry facts — flags a lockout clearing, ignores
  timing jitter).
- **`opskit ad groups PRINCIPAL`** — direct memberships; `-e/--effective` resolves nesting
  (direct/nested/primary marking + paths).
- **`opskit ad member PRINCIPAL GROUP`** — membership test; exit 0 member / 17 not-member;
  chain rendered on success.
- **`opskit ad show [NAMES]...`** — key attributes (user: email/contact facts; group:
  complete direct member list); `--type user|group|computer|auto` (default auto; ambiguity
  across types → the standard ambiguity error). **Batchable** like `ad user`: variadic +
  `-i/--input-file` (`-` = stdin), users and groups mixable, one session, per-name
  envelopes under `--jsonl` (spec amendment 2026-07-10).

Shared connection options on every command: `-s/--server` (env `OPSKIT_AD_SERVER`),
`-d/--domain` (env `OPSKIT_AD_DOMAIN`), `-U/--user` bind account (env `OPSKIT_AD_USER`;
UPN / `DOMAIN\name` / DN), password **only** from env `OPSKIT_AD_PASSWORD` or an interactive
hidden prompt (prompted iff `--user` given, no env set, and stdin is a TTY; piped stdin +
no env → usage error naming the env var — never a hang, never an echo), `--starttls`,
`--plaintext`, `--ca-file`, `--base-dn`, `--timeout` (default 5.0), plus the standard
`--json` / `--jsonl` (on the batchable `ad user`/`ad show`) / `--no-color`. Human rendering
in `output.py` uses rich **tables** for every list-shaped result (status facts, membership
lists, group members, batch summaries) per FR-015. There is **no `--password` option**
(process listings, shell history — Art. III). Env vars are wired with typer's `envvar=`
(CLI-layer only; the API never reads env, Art. VII). Envelope command names:
`ad.check` / `ad.user` / `ad.groups` / `ad.member` / `ad.show`, `schema_version "1"`.

**Rationale**: five commands keep each output contract crisp (a staged report, a status
report, a membership list, a verdict, an attribute sheet) rather than overloading one
command with modes; noun-style `ad user`/`ad groups` reads as the question being asked.
The env-var set makes the common case (operator works one domain all day) zero-friction
without persisting secrets; the prompt rule follows the spec's piped-stdin edge case
verbatim.

**Alternatives considered**: `--password` flag (rejected: Art. III footgun); a config-file
`password` key (rejected for v1: plaintext secret at rest; env/prompt cover the flows —
revisit only with OS-keyring support, out of scope); merging `member` into
`groups --check G` (rejected: different result shape and exit-code semantics deserve their
own command); `ad status` as the status command name (rejected: `ad user jdoe` matches
`Get-ADUser`/`net user` muscle memory and leaves `status` free); connection args as a typer
callback on the sub-app (considered fine either way — implementation detail left to tasks;
options must render in each command's `--help` regardless for the docs gate).
