# Tasks: Active Directory / LDAP Diagnostics

**Input**: Design documents from `/specs/004-ad-diagnostics/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command (Arts. II/III, coverage
≥ 90%); the deterministic mock-directory/loopback strategy is research decision R8, and the
suite-wide credential-redaction scan is SC-006.

**Organization**: grouped by user story; each phase is an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US6 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/`, `tests/` at repo root (per plan.md structure).

---

## Phase 1: Setup

**Purpose**: dependency plumbing (first category extra) + package skeletons

- [X] T001 Wire the extra in pyproject.toml: `[project.optional-dependencies] ad = ["ldap3>=2.9,<3"]` (replacing the placeholder comment), add `"ldap3>=2.9,<3"` to the dev extra (tests need it), add scoped type-checker overrides for the untyped dependency (`[[tool.mypy.overrides]] module = "ldap3.*"`, `ignore_missing_imports = true`; pyright `reportMissingTypeStubs` scoped per R9 — global strictness untouched); run `uv sync --extra dev` and commit the lock update; verify `uv run pip-audit` stays clean
- [X] T002 [P] Create skeletons with module docstrings: src/opskit/ad/{__init__,errors,models,attributes,discovery,directory,api,cli,output}.py and src/opskit/ad/README.md placeholder — ad/cli.py carries the no-future-annotations note and eager `Optional[X]` annotations (CLAUDE.md rule); every other new module keeps `from __future__ import annotations`; **only directory.py may import ldap3, and only lazily**

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared primitives every story builds on — exit codes, errors, AD attribute
semantics, config/identifier models, discovery, the ldap3 adapter, fixtures, CLI plumbing

**⚠️ CRITICAL**: complete before any user-story phase

- [X] T003 Add additive ExitCode members `AUTH_FAILED = 14`, `PERMISSION_DENIED = 15`, `NOT_FOUND = 16`, `NOT_MEMBER = 17` in src/opskit/core/exit_codes.py (no other core changes — R3)
- [X] T004 [P] Implement the ad error hierarchy in src/opskit/ad/errors.py per data-model.md: `AdError` (exit 1), `DependencyMissing` (exit 2, hint `pip install "opskit[ad]"`), `CleartextRefused` (exit 2), `AmbiguousPrincipal` (exit 2, message lists candidate DNs), `DiscoveryError` (exit 3), `AuthenticationFailed` (exit 14) with the AD bind `data` sub-code decode table (52e/530/531/532/533/701/775 → hint text, R3), `PermissionDenied` (exit 15), `PrincipalNotFound` (exit 16) — each owns its exit_code (Art. VII); no ldap3 imports
- [X] T005 [P] Implement AD attribute semantics as pure functions in src/opskit/ad/attributes.py: FILETIME↔aware-UTC-datetime with sentinel handling (0, 0x7FFFFFFFFFFFFFFF → never; string/int wire forms; out-of-range safe), `userAccountControl` bit readers (0x2 disabled, 0x10000 never-expires), computed-UAC readers (0x10 locked, 0x800000 password-expired), SID parse + primary-group-SID derivation from `objectSid` + `primaryGroupID`, boundary coercions (no `Any` leaks); Hypothesis property tests (FILETIME round-trip + sentinels over the full range) in tests/unit/test_ad_attributes.py
- [X] T006 Implement config + identifier models in src/opskit/ad/models.py: frozen `DirectoryConfig` per data-model.md (password `repr=False` and **no serialization path**; server/domain mutual requirement; password-without-TLS → `CleartextRefused` unless security explicitly `"plaintext"`; timeout > 0 — all validated before any I/O), identifier-form detection (`=`→DN, `@`→UPN, else sAMAccountName; `DOMAIN\name` prefix strip), security-mode → default-port mapping (636/389/389); tests in tests/unit/test_ad_models.py
- [X] T007 Implement SRV DC discovery in src/opskit/ad/discovery.py via `opskit.dns.lookup`: `_ldap._tcp.dc._msdcs.<domain>` first, `_ldap._tcp.<domain>` fallback, priority-asc/weight-desc deterministic ordering, no records / unresolvable → `DiscoveryError` (R4); unit tests with an injected dns lookup in tests/unit/test_ad_discovery.py
- [X] T008 Implement the ldap3 adapter in src/opskit/ad/directory.py (THE only ldap3 module, imported lazily; `ImportError` → `DependencyMissing`): staged connect (ldaps default / starttls upgrade-mandatory / plaintext) with per-stage timing capture, `Tls(validate=CERT_REQUIRED)` with platform store or `ca_file`, bind (anonymous or simple), rootDSE read (`defaultNamingContext`, server identity basics), paged search (`paged_size=500`, cookie loop) with `auto_range=True`, and **error normalization**: socket open failures → `net.ConnectRefused`/`net.ConnectTimeout`, TLS/StartTLS failures → `tls.HandshakeError`, cert verification → `tls.CertificateInvalid` (hint → `opskit tls`), resultCode 49 → `AuthenticationFailed` (decode `data` sub-code), resultCode 50 → `PermissionDenied`, anything else → `AdError` — no ldap3 exception or raw OSError escapes (Art. VI); typed wrappers only (R9); normalization-branch unit tests with monkeypatched ldap3 fakes in tests/unit/test_ad_directory.py
- [X] T009 [P] Test fixtures in tests/integration/conftest.py (+ shared helpers importable by unit tests): ldap3 `MOCK_SYNC` directory builder per R8 — user entries covering every status permutation (enabled/disabled × locked/stale-locked × password expired/never-expires/must-change × account expired/never), group topology with nesting, a cycle (A∈B∧B∈A), a primary group, a >1000-member group, an ambiguous sAMAccountName, users with `mail`/contact attributes, a computer account; bind success/invalid-credential paths; **suite-wide redaction fixture** capturing all stdout/stderr/log output of ad tests and asserting the test password never appears (SC-006)
- [X] T010 Register the ad sub-app in src/opskit/cli.py (one line; import-safe without ldap3) and implement shared CLI plumbing in src/opskit/ad/cli.py: connection options on every command (`-s/--server` env `OPSKIT_AD_SERVER`, `-d/--domain` env `OPSKIT_AD_DOMAIN`, `-U/--user` env `OPSKIT_AD_USER`, `--starttls`, `--plaintext`, `--ca-file`, `--base-dn`, `--timeout` 5.0 — typer `envvar=`, CLI-only per Art. VII), password resolution (env `OPSKIT_AD_PASSWORD` → hidden prompt iff `--user` given ∧ no env ∧ stdin is a TTY → else usage error naming the env var; **no `--password` option exists**), `DirectoryConfig` construction, and the envelope query-echo builder (records server/port/security/bind_user — never any password field) per contracts/cli.md; plumbing tests in tests/unit/test_ad_cli.py

**Checkpoint**: adapter + fixtures + plumbing ready — user stories can begin

---

## Phase 3: User Story 1 - Diagnose why an account can't sign in (Priority: P1) 🎯 MVP

**Goal**: `opskit ad user jdoe` returns the directory's answer in one command — enabled/disabled, locked (with time), password expired/expiry/never, account expiry, password-last-set — with **all** active blockers listed and a plain-language verdict; unknown principal → exit 16.

**Independent Test**: quickstart §2 — healthy account exits 0 with "no sign-in blockers"; disabled, locked, expired-password, and disabled+locked fixtures each report every applicable blocker with hints; unknown principal exits 16; facts degrade to "not available" on a fixture lacking AD attributes.

### Implementation for User Story 1

- [X] T011 [US1] `AccountStatusReport` frozen dataclass in src/opskit/ad/models.py per data-model.md (tri-state facts, `blockers` list, `facts_unavailable`, `lockout_stale_possible`, `to_dict()` with ISO-8601/never-flag serialization) + blocker derivation from the T005 attribute readers (computed-UAC preferred, raw fallback with stale-lockout honesty — R5)
- [X] T012 [US1] `user_status()` in src/opskit/ad/api.py: minimal `AdClient` (config → lazy adapter connect+bind, reusable session, context manager, `close()`) + principal resolution (form detection → escaped equality filter via ldap3 `escape_filter_chars`, `user`/`computer` object classes, rootDSE default base or `--base-dn`; 0 matches → `PrincipalNotFound`, >1 → `AmbiguousPrincipal` — R6) + status-attribute fetch (incl. `msDS-*` constructed attributes) → `AccountStatusReport`; convenience function `ad.user_status(...)`; Google-style docstrings
- [X] T013 [P] [US1] Status rendering in src/opskit/ad/output.py: verdict line + rich **table** of facts (enabled/locked/password/account rows; "never" wording; unavailable facts marked; blockers highlighted; plaintext-connection warning banner) — `rich.markup.escape()` on every directory-derived string (DNs, names); consoles only via `make_console` (FR-015 table rule)
- [X] T014 [US1] Thin Typer command `user` (single-principal path) in src/opskit/ad/cli.py using the T010 plumbing: envelope `command: "ad.user"` per contracts/cli.md, exit 0 / error classes; help text panels + epilog examples
- [X] T015 [P] [US1] API tests over the mock directory in tests/unit/test_ad_api.py + tests/integration/test_ad_mock_directory.py: every status permutation fixture → expected facts/blockers (multi-blocker case asserts **all** listed), constructed-attribute-missing degradation (`facts_unavailable`, stale-lockout wording), not-found → exit-16 error, ambiguous → candidate DNs in message
- [X] T016 [P] [US1] CLI tests in tests/unit/test_ad_cli.py: `--json` envelope shape (query echo has no password key — also covered by the T009 redaction fixture), exit codes 0/16/2, password env-vs-prompt-vs-piped-stdin rules, missing-extra path (simulated ImportError → exit 2 with install hint), human table smoke; assert on DNs/values, never `"host.tld" in output` (CodeQL rule)

**Checkpoint**: MVP — the "why can't this user sign in" question answered end-to-end

---

## Phase 4: User Story 2 - Understand a principal's group membership (Priority: P2)

**Goal**: `opskit ad groups jdoe` lists direct memberships (+ primary group, marked); `--effective` resolves nesting cycle-safely with shortest acquisition paths; `opskit ad member jdoe G` gives a yes/no verdict with the granting chain — exit 0 member / 17 not.

**Independent Test**: quickstart §3 — direct list matches the fixture; nested group appears marked `nested` with its path; the cycle fixture terminates reporting each group once; the >1000-member group resolves completely; membership test exits 0 with chain vs 17.

### Implementation for User Story 2

- [X] T017 [US2] `MembershipReport`/`MembershipEntry`/`MembershipVerdict` frozen dataclasses in src/opskit/ad/models.py per data-model.md (via direct/nested/primary, shortest `path`, sorted direct-first, `to_dict()`)
- [X] T018 [US2] Membership logic in src/opskit/ad/api.py: direct = `memberOf` + primary group (T005 SID derivation → base-scope lookup, marked `primary`); effective = BFS over group `memberOf` with visited-set cycle safety recording shortest paths (R7); `is_member()` = same BFS short-circuiting on the target group → `MembershipVerdict`; paging/`auto_range` guarantees completeness (FR-012); `AdClient.membership()/is_member()` + convenience functions
- [X] T019 [US2] CLI + rendering: `groups` command (`-e/--effective`) and `member` command (exit **0 member / 17 NOT_MEMBER**, error classes otherwise) in src/opskit/ad/cli.py; membership **table** (group, location, via, path) and verdict-with-chain rendering in src/opskit/ad/output.py (escaped); envelopes `ad.groups`/`ad.member`
- [X] T020 [P] [US2] Tests in tests/unit/test_ad_api.py + tests/unit/test_ad_cli.py + tests/integration/test_ad_mock_directory.py: direct vs effective marking, primary-group inclusion, cycle termination (each group once), shortest-path assertion on the fixture topology (SC-005), large-group completeness, verdict exits 0/17, empty membership = success exit 0

**Checkpoint**: access-debugging flows complete

---

## Phase 5: User Story 3 - Verify directory connectivity and credentials (Priority: P2)

**Goal**: `opskit ad check dc01` (or `-d corp.example.com` with SRV discovery) reports staged reached→secured→authenticated verdicts with timing and server identity; network vs TLS vs credential failures never conflated (exits 8/6 vs 9/10 vs 14); discovery reports the server used.

**Independent Test**: quickstart §4 — valid creds → full staged report exit 0; refused/timeout loopback ports → 8/6 without mentioning credentials; self-signed TLS → 10 with tls-category hint; wrong password → 14 with decoded AD reason; `-d` discovery reports which candidate DC answered.

### Implementation for User Story 3

- [X] T021 [US3] `ConnectivityReport` (+ `Stage`, `ServerInfo`) frozen dataclasses in src/opskit/ad/models.py per data-model.md; `check()` in src/opskit/ad/api.py assembling the staged result from the T008 adapter's timing capture, wiring T007 discovery when `domain` is set (candidates tried in order; `server_used`/`candidates_tried`/`discovered` recorded) and `encrypted: false` marking for plaintext runs; `AdClient.check()` + convenience function
- [X] T022 [US3] CLI + rendering: `check` command in src/opskit/ad/cli.py (positional SERVER xor `-d/--domain` — exactly one), staged **table** (stage/ok/elapsed) + server-info block + prominent plaintext warning in src/opskit/ad/output.py (escaped); envelope `ad.check`; failing stage's error class drives the exit code
- [X] T023 [P] [US3] Stage-classification tests: loopback integration in tests/integration/test_ad_loopback.py — connection to a closed loopback port asserted as the **`NetError` class family** (refused on Linux/macOS, may time out on Windows — CLAUDE.md canonical rule), never-answering port → timeout family, existing self-signed loopback TLS fixture → `tls.CertificateInvalid` exit 10 with tls hint; mock/fake tests for auth failure (exit 14, decoded `data 52e` hint present, no credential text in output) and discovery flow (server_used reported, exhausted candidates → connect-class error) in tests/unit/test_ad_api.py

**Checkpoint**: the isolation step of every directory incident ships

---

## Phase 6: User Story 4 - Audit account status across many users (Priority: P2)

**Goal**: `opskit ad user` takes many principals (variadic args, `-i FILE`, `-i -` stdin), processes all over one authenticated session, never aborts, emits per-principal envelopes incl. failures, exits 0/uniform/7; `--watch` flags blocker changes.

**Independent Test**: quickstart §5 — piped mixed list (healthy/locked/unknown) yields three NDJSON envelopes (failure included, `result: null`) and exit 7; all-healthy → 0; prompt never fires on piped stdin; watch flags a lockout clearing between iterations.

### Implementation for User Story 4

- [X] T024 [US4] Batch + watch wiring for `user` in src/opskit/ad/cli.py: variadic PRINCIPALS + `-i/--input-file` (`-` = stdin) via existing `collect_target_list`; one `AdClient` session opened once and reused inside `collect_outcomes` (SC-004); `--jsonl` per-principal envelopes incl. failures; failures to stderr in human mode; `aggregate_exit` (0 / uniform / 7 PARTIAL); batch results table; `--watch` via `run_or_watch` with the R10 change signature (blockers + core facts, no timing jitter)
- [X] T025 [P] [US4] Batch contract tests in tests/unit/test_ad_cli.py: 50-principal mixed batch processes all → exit 7 with an envelope for **every** principal (Art. IX; SC-004), uniform-failure → that class, all-pass → 0, stdin via `-i -` with blank/`#` filtering, session-reuse assertion (one bind for N principals — count adapter binds), watch change-detection with patched `time.sleep` (dns pattern)

**Checkpoint**: fleet status audits replace ad-hoc scripting loops

---

## Phase 7: User Story 5 - Inspect directory objects' key attributes, singly or in bulk (Priority: P3)

**Goal**: `opskit ad show NAME...` reports each named user/group/computer's key attributes — user email/contact facts, group kind + complete direct member list, computer dNS/OS — batchable with mixed types over one session, rendered as one attribute table per object, ambiguity refused.

**Independent Test**: quickstart §5 amendment — `ad show jdoe "VPN Users" wks-042$` renders three typed attribute tables (user row includes email; group includes the member table); piped mixed list under `--jsonl` yields one envelope per name incl. failures with batch exit codes; ambiguous name → exit 2 listing candidates; unknown → 16.

### Implementation for User Story 5

- [X] T026 [US5] `ObjectSummary` frozen dataclass in src/opskit/ad/models.py per data-model.md (identifiers incl. SID, created/changed, description, `type_facts` per type — user: mail/display_name/title/department; group: group_kind + complete `members` via paged/ranged search; computer: dns_host_name/OS) + `show()` in src/opskit/ad/api.py with `--type auto|user|group|computer` scoping (auto searches the three classes; cross-type multi-match → `AmbiguousPrincipal`) (R6); `AdClient.show()` + convenience function
- [X] T027 [US5] CLI + rendering: `show` command in src/opskit/ad/cli.py — **batchable like `user`** (variadic NAMES + `-i`/stdin, mixed types, one session, `--jsonl`, aggregate exit; spec FR-013/FR-016) with `--type` applied to every name; per-object attribute **table** (group members as nested table, email row for users) in src/opskit/ad/output.py (escaped); envelope `ad.show`
- [X] T028 [P] [US5] Tests in tests/unit/test_ad_api.py + tests/unit/test_ad_cli.py + tests/integration/test_ad_mock_directory.py: user summary carries mail/contact facts, group summary lists the >1000-member fixture completely, computer facts, auto-type resolution + cross-type ambiguity → exit 2, mixed 50-name batch (users+groups) → per-name envelopes + batch exit rule (SC-004), unknown name → 16

**Checkpoint**: bidirectional lookup (user→facts/groups, group→members) complete

---

## Phase 8: User Story 6 - Use it from code (Priority: P3)

**Goal**: everything importable from `opskit.ad` — typed results/errors, reusable `AdClient` session, no printing/exiting, no env reads; `import opskit.ad` works without the extra; the documented example runs as written (SC-008).

**Independent Test**: quickstart §1 + contracts/python-api.md example executed unmodified against the mock directory; `AuthenticationFailed`/`PrincipalNotFound` catchable specifically; capsys stays clean across API calls.

### Implementation for User Story 6

- [X] T029 [US6] Finalize src/opskit/ad/__init__.py `__all__` per contracts/python-api.md (functions, `AdClient`, `DirectoryConfig`, all result models, all errors — importable without ldap3); add a test executing the python-api.md usage example against the mock directory in tests/unit/test_ad_api.py; assert the library layer never prints (capsys clean), never reads `OPSKIT_*` env (monkeypatched env ignored by API), and `repr(DirectoryConfig)`/`to_dict()` never contain the password

**Checkpoint**: API parity delivered

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T030 [P] Write src/opskit/ad/README.md (command reference mirroring dns/net/tls READMEs: `ad check`/`ad user`/`ad groups`/`ad member`/`ad show` entries, shared connection/credential options incl. the no-password-flag rule and `OPSKIT_AD_*` env vars, security-mode section, exit-code matrix incl. 14–17, JSON/NDJSON samples, library section with the api example, extra-install note) and add the `opskit ad` rows + link in the root README.md Commands table (docs-coverage gate, Art. II); note the SRV-port-vs-security-mode discovery rule (R4)
- [X] T031 [P] Real-DC smoke tests `@pytest.mark.network` in tests/integration/test_ad_network.py driven by `OPSKIT_AD_*` env (check/user/groups against a real domain; validates constructed-attribute and bind-sub-code assumptions) — excluded from CI by default, skips cleanly when env unset
- [X] T032 Run the full quickstart validation matrix + all gates on 3.9 and default: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pyright && uv run pytest` (coverage ≥ 90%, redaction scan green); verify all five commands pass the docs-coverage gate; verify base-install behavior (`import opskit.ad` + actionable exit-2 hint without ldap3, per quickstart §1); fix any drift
- [X] T033 Reconcile design docs with as-built reality (specs/004-ad-diagnostics/contracts/, data-model.md) and update specs/001–003 contracts' exit-code tables with codes 14–17 (shared enum documented once)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** depends only on Foundational; it is the MVP.
- **US2 (Phase 4)** depends on US1's `AdClient`/resolution core (T012) + output/cli patterns.
- **US3 (Phase 5)** depends on Foundational (T007/T008); independent of US1's status logic — only the thin `AdClient.check()` wrapper touches api.py after T012 lands.
- **US4 (Phase 6)** depends on US1 (T012/T014) + existing core cliutils (no new core work).
- **US5 (Phase 7)** depends on US1's resolution core (T012); batch wiring mirrors US4's (T024) but is independent code.
- **US6 (Phase 8)** depends on US1–US5 surfaces existing (finalizes exports).
- **Polish (Phase 9)** last; T030/T031 can start once US1 is stable.

### Key task-level dependencies

- T004 needs T003; T006 needs T004; T007 needs T004; T008 needs T004+T006; T009 needs T008 (mock strategy); T010 needs T006+T004.
- T011 needs T005+T006; T012 needs T008+T011; T014 needs T010+T012+T013; T015/T016 need T012/T014 + T009.
- T017 needs T011 patterns; T018 needs T012+T017; T019 needs T018; T020 needs T019+T009.
- T021 needs T007+T008; T022 needs T021; T023 needs T022+T009 (+ existing tls loopback fixture).
- T024 needs T014; T025 needs T024.
- T026 needs T012; T027 needs T026 (+T024's batch pattern); T028 needs T027.
- T029 needs T012/T018/T021/T026 public surfaces; T032 needs everything.

### Parallel Opportunities

- Phase 1: T002 parallel with T001.
- Phase 2: T004, T005 in parallel after T003; T007, T009 parallel once their deps land; T005 is parallel with everything except T011.
- Phase 3: T013 parallel with T012; T015/T016 parallel once T014 exists.
- **US3 (T021–T023) is fully parallel with US2 (T017–T020)** for a second contributor — disjoint concerns (connection stages vs membership traversal) after US1.
- Phase 6/7: T025 and T026 can proceed in parallel (cli batch vs api show).
- Phase 9: T030/T031 parallel.

## Parallel Example: User Story 1

```bash
# After T012 (api.user_status) exists, run in parallel:
Task: "T013 status table rendering in src/opskit/ad/output.py"
Task: "T015 API tests over the mock directory"
# After T014 (cli):
Task: "T016 CLI/envelope/redaction tests"
```

## Implementation Strategy

**MVP first**: Phases 1–3 (T001–T016) deliver the category's core promise — a cross-platform
"why can't this user sign in" answer — stop, validate against quickstart §2, demo.
**Incremental**: US2 (membership) is the highest-value follow-on and pairs with US3
(connectivity isolation) for a second contributor; US4/US5 turn it into a fleet tool; US6
finalizes API parity. Commit per task or logical group with Conventional Commits
(`feat(ad): …`); run the four gates before each commit batch (CLAUDE.md). Watch the
ad-specific traps baked into the tasks: ldap3 stays quarantined in directory.py (T008),
passwords have no flag and no serialization path (T006/T010, redaction fixture T009), and
connection-stage tests assert the error class family, not one subclass (T023) — the
difference between a green PR and a green `main`.
