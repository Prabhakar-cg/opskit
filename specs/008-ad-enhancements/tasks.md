# Tasks: AD Diagnostics Enhancements

**Input**: Design documents from `/specs/008-ad-enhancements/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command/behavior change
(Arts. II/III, coverage ≥ 90%); all four stories extend the existing deterministic
mock-directory (MOCK_SYNC) / directory-classification-unit test layers from
004-ad-diagnostics's R8 — no new test layer is introduced (research E5).

**Organization**: grouped by user story; each phase is an independently testable increment.
Note: User Stories 1 and 2 are **both P1** (the spec's two most disruptive gaps) — order
between them is arbitrary; either can ship first.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/ad/`, `tests/` at repo root (per plan.md — no new package).

---

## Phase 1: Setup

**Purpose**: N/A for this feature. No new dependency, no new package, no new skeleton file —
every task below edits a file that already exists in `src/opskit/ad/` or `tests/`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: N/A for this feature. The four user stories touch disjoint functions
(`_find_one`'s not-found branch; a new `_expand_members`/`members()` path;
`_identifier_clause`'s UPN branch; `classify_connect_error`'s hint text) with no shared
new infrastructure between them — each can be implemented, tested, and shipped
independently, in any order.

---

## Phase 3: User Story 1 - Get redirected when a group name is queried the wrong way (Priority: P1) 🎯 MVP

**Goal**: A principal-scoped lookup (`ad user`, `ad groups`, `ad member`'s principal
argument) that finds no user/computer but matches exactly one group reports a clear
group-redirect error instead of the generic not-found message.

**Independent Test**: Run `opskit ad groups "VPN Users"` against the existing mock-directory
fixture (a group with that name, no principal by that name) — the error must name it as a
group and suggest `ad members`/`ad member`; `opskit ad groups <nonexistent>` must be unchanged.

### Tests for User Story 1

- [X] T001 [P] [US1] Unit tests for the redirect path in `tests/unit/test_ad_api.py`:
      `user_status("VPN Users")`, `membership("VPN Users")`, and
      `is_member("VPN Users", "VPN Users")` (principal argument) each raise
      `PrincipalIsGroup` naming `"VPN Users"` with a hint mentioning `ad members` and
      `ad member`; a truly nonexistent identifier still raises the existing
      `PrincipalNotFound` unchanged (regression case, FR-002); use the existing
      `ad_client`/`ad_session_factory` fixtures in `tests/conftest.py` — no new fixture
      entries needed (the mock directory already has groups with no matching principal)
- [X] T002 [P] [US1] CLI-level test in `tests/unit/test_ad_cli.py`: `opskit ad groups
      "VPN Users"` exits **16** (`NOT_FOUND`, unchanged exit code) with stderr naming the
      group-redirect message and hint; `--json` envelope's `error.code == "principal_is_group"`

### Implementation for User Story 1

- [X] T003 [US1] Add `PrincipalIsGroup(AdError)` in `src/opskit/ad/errors.py`:
      `code = "principal_is_group"`, `exit_code = ExitCode.NOT_FOUND` (reused, no new exit
      code) per data-model.md
- [X] T004 [US1] In `_find_one()` (`src/opskit/ad/api.py`), when `kind_filter == "principal"`
      and the principal-scoped search returns zero matches, run one additional search using
      the same `_identifier_clause(id_kind, value)` against `_CLASS_FILTERS["group"]`; if it
      returns exactly one match, raise `PrincipalIsGroup` naming the resolved group (its
      first-RDN display name) with a hint pointing at `opskit ad members <name>` and
      `opskit ad member <principal> <name>`; zero or >1 matches fall through to the existing
      `PrincipalNotFound` unchanged (depends on T003)
- [X] T005 [P] [US1] Export `PrincipalIsGroup` from `src/opskit/ad/__init__.py`'s `__all__`

**Checkpoint**: User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 - See a group's full effective membership in one command (Priority: P1)

**Goal**: A new `opskit ad members <group>` command reports every account that ultimately
belongs to a group, including through nested groups, cycle-safe, with acquisition paths —
mirroring `ad groups --effective` in the opposite direction.

**Independent Test**: Run `opskit ad members "Remote Access"` against the existing mock
fixture (`Remote Access` → member `VPN Users` → member `jdoe`) — output must include both
`VPN Users` (direct) and `jdoe` (nested, path `("VPN Users",)`); `--direct` must show only
`VPN Users`; `opskit ad members "Cycle A"` (the existing `Cycle A`⇄`Cycle B` fixture) must
terminate and report each distinct member once.

### Tests for User Story 2

- [X] T006 [P] [US2] `to_dict()` shape tests for `GroupMemberEntry`/`GroupMembersReport` in
      `tests/unit/test_ad_models.py` per data-model.md
- [X] T007 [P] [US2] Unit tests for `_expand_members()`/`AdClient.members()` in
      `tests/unit/test_ad_api.py` using the existing `ad_client` fixture and the existing
      `Remote Access`/`VPN Users`/`Cycle A`/`Cycle B` fixture entries in
      `tests/conftest.py` (no new fixture entries needed): direct-only mode lists only
      `member` values on the queried group; effective/default mode adds nested members with
      correct `via`/`path`/`object_type`; the `Cycle A`⇄`Cycle B` topology terminates and
      reports each distinct member exactly once (FR-006); an empty group returns
      `members=()` without error (depends on T009, T010)
- [X] T008 [P] [US2] CLI batch-contract tests in `tests/unit/test_ad_cli.py`: `ad members`
      with multiple group targets (positionals, `-i/--input-file`, stdin `-`) processes every
      target over one session, never aborting on failure; `--jsonl` emits one envelope per
      group including failures; aggregate exit 0/uniform/7 (Art. IX); `--direct` flag
      toggles the report's `effective` field (depends on T011, T012)
- [X] T009 [P] [US2] End-to-end CLI test in `tests/integration/test_ad_mock_directory.py`
      (mirroring the existing `test_quickstart_membership_flow` pattern): full typer
      invocation of `ad members "Remote Access"` and `ad members "Remote Access" --direct`
      against the mock directory, asserting rendered table content (depends on T011, T012)

### Implementation for User Story 2

- [X] T010 [P] [US2] Add `GroupMemberEntry` and `GroupMembersReport` frozen dataclasses with
      `to_dict()` in `src/opskit/ad/models.py` per data-model.md
- [X] T011 [US2] Implement `_expand_members()` in `src/opskit/ad/api.py`: BFS over a group's
      `member` attribute mirroring `_expand_nested()`'s `memberOf` BFS (R7 of 004) — seed the
      visited set with the queried group's own DN, classify each newly-visited member via
      `_object_type_of()` (reusing the existing helper), enqueue only `group`-typed members
      for further expansion, record `via="direct"`/`"nested"` and the acquisition path; a
      direct-only fast path skips the per-member `objectClass` classification entirely
      (research E2); add `AdClient.members(group, *, effective=True)` resolving the group via
      `_find_one(kind_filter="group")` then running the traversal, and a module-level
      `members()` convenience function mirroring `membership()`'s (depends on T010)
- [X] T012 [P] [US2] Add `render_group_members()` in `src/opskit/ad/output.py`: a rich table
      (name, object type, via, path) matching `render_membership`'s existing convention,
      every directory-derived string passed through `rich.markup.escape()`
- [X] T013 [US2] Add the `members` command in `src/opskit/ad/cli.py`: variadic `GROUPS...` +
      `-i/--input-file` (`-` = stdin) via the existing `collect_target_list`/`_run_batch`/
      `_emit_batch` helpers (the same pattern already used by `user`/`show`), `--direct`
      flag (default off = effective/nested), shared connection options, `--json`/`--jsonl`/
      `--no-color`; envelope command name `ad.members` (depends on T011, T012)
- [X] T014 [P] [US2] Export `GroupMemberEntry`, `GroupMembersReport`, and the `members()`
      convenience function from `src/opskit/ad/__init__.py`'s `__all__`

**Checkpoint**: User Stories 1 and 2 both work independently.

---

## Phase 5: User Story 3 - Find a user by their email address (Priority: P2)

**Goal**: A UPN-shaped identifier (contains `@`) matches either `userPrincipalName` or
`mail`, so a user found by email when their `mail` differs from their `userPrincipalName`
resolves correctly, while the existing refuse-on-ambiguity rule still applies when two
different accounts each match one of the two attributes.

**Independent Test**: Add a mail-only-match fixture user; run `opskit ad show
<their-mail-address>` — must resolve to that user. Add an ambiguous UPN/mail pair; the same
form of lookup must raise the existing ambiguous-match error listing both.

### Tests for User Story 3

- [X] T015 [US3] Extend `default_ad_entries()` in `tests/conftest.py`: add a user whose
      `mail` differs from their (default, auto-generated) `userPrincipalName` (e.g.
      `dmailonly` with `extra={"mail": "jane.doe@example.com"}`), and a distinct pair of
      users where one's default `userPrincipalName` equals a chosen string and the other's
      `mail` (via `extra`) equals the *same* string, to exercise the ambiguous case
- [X] T016 [P] [US3] Unit tests in `tests/unit/test_ad_api.py`: looking up the mail-only
      user's mail address via `user_status`/`show` resolves that user (FR-008); looking up
      the shared ambiguous string raises `AmbiguousPrincipal` listing both candidate DNs
      (FR-009); looking up `jdoe`'s address (whose `mail` and `userPrincipalName` are
      already identical in the existing fixture) still resolves to exactly one match, not
      ambiguous (FR-010) (depends on T015, T017)
- [X] T017 [P] [US3] CLI-level test in `tests/unit/test_ad_cli.py`: `opskit ad show
      <mail-only-address>` resolves via the CLI end-to-end (depends on T015, T018)

### Implementation for User Story 3

- [X] T018 [US3] In `_identifier_clause()` (`src/opskit/ad/api.py`), change the
      `IdentifierKind.UPN` branch from `(userPrincipalName={value})` to
      `(|(userPrincipalName={value})(mail={value}))`; no other identifier-classification
      logic changes (research E3)

**Checkpoint**: User Stories 1, 2, and 3 all work independently.

---

## Phase 6: User Story 4 - Understand what a certificate-verification failure means and how to fix it (Priority: P3)

**Goal**: A certificate-verification failure's hint text states that `--starttls` does not
bypass verification (when starttls was in use) and that WSL doesn't inherit the Windows
trust store (always); the README documents how to trust a corporate CA.

**Independent Test**: Call `classify_connect_error()` directly with an injected
`ssl.SSLCertVerificationError` and `security="starttls"` vs. the default — assert the
StartTLS clause's presence/absence and the WSL note's unconditional presence in the
resulting hint.

### Tests for User Story 4

- [X] T019 [P] [US4] Extend `TestClassifyConnectError` in `tests/unit/test_ad_directory.py`:
      parametrize the existing `_classify` helper with an optional `security` argument
      (default `"ldaps"`); add cases asserting `CertificateInvalid.hint` contains the WSL
      trust-store note regardless of `security`, and contains the `--starttls` clarification
      only when `security="starttls"` — covering both the exception-chain and the
      string-fallback classification branches (depends on T020)

### Implementation for User Story 4

- [X] T020 [US4] In `src/opskit/ad/directory.py`: add `security: str = "ldaps"` to
      `classify_connect_error()`'s signature; build the `CertificateInvalid` hint (both the
      `ssl.SSLCertVerificationError` branch and the `"certificate" in text` fallback branch)
      from a shared helper that always appends the WSL trust-store note and additionally
      appends the StartTLS clarification when `security == "starttls"` (research E4); update
      all four call sites (`connect_session()`'s `conn.open()`, its `conn.start_tls()`
      upgrade, its bind-stage exception handler, and `DirectorySession.search()`'s exception
      handler) to pass `security=config.security` / `security=self.config.security` instead
      of relying on the default
- [X] T021 [P] [US4] Add a "Trusting a corporate CA" section to `src/opskit/ad/README.md`
      (FR-013): how to obtain the corporate root CA PEM and use it via `--ca-file`, plus a
      one-line cross-reference from the existing TLS/security-mode section

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T022 [P] Add an `ad members` options-table row to `src/opskit/ad/README.md` (mirroring
      the existing `ad groups` row: positionals/batch input, `--direct`, shared connection
      options) — docs-coverage gate (Art. II); no root README change needed (its existing
      `opskit ad` row/link already covers the category)
- [X] T023 Run the full quickstart validation matrix + all gates: `uv run ruff format
      --check . && uv run ruff check . && uv run mypy src && uv run pyright && uv run
      pytest -q` (coverage ≥ 90%); execute every manual scenario in quickstart.md against a
      lab/real directory if available; run the SC-005 regression check (existing
      `ad check`/`ad user`/`ad show`/`ad groups`/`ad member` invocations from
      specs/004-ad-diagnostics/quickstart.md produce identical output/exit codes); fix any
      drift
- [X] T024 Reconcile design docs with as-built reality: append any research.md/data-model.md
      addenda for discoveries made during implementation, matching the precedent set by
      004/006/007's own as-built addenda

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** / **Foundational (Phase 2)**: both empty for this feature — proceed
  directly to user stories.
- **User Stories (Phase 3–6)**: fully independent of each other (disjoint functions/files);
  any order or parallel staffing works. Suggested order follows spec priority: US1, US2
  (both P1), then US3 (P2), then US4 (P3).
- **Polish (Phase 7)**: depends on all four user stories being complete.

### Within Each User Story

- US1: T003 (error) → T004 (redirect logic, depends on T003) → T005 (export); tests T001/T002
  depend on T004.
- US2: T010 (models) → T011 (`_expand_members`/`members()`, depends on T010); T012 (output)
  is independent of T011 but T013 (CLI) depends on both T011 and T012; T014 (export)
  independent. Tests T006 depends on T010; T007 depends on T011 (via `AdClient.members`,
  which needs `_find_one` unchanged — no new dependency there); T008/T009 depend on T013.
- US3: T015 (fixture) is independent of T018 (code change); T016 depends on both T015 and
  T018; T017 depends on T015 and T018.
- US4: T020 (code) before T019 (tests); T021 (README) independent of both.

### Parallel Opportunities

- Across stories: US1, US2, US3, US4 can be implemented by different people simultaneously
  (no shared foundational phase, no shared file edits — `api.py` is touched by US1, US2, and
  US3 but in disjoint functions: `_find_one`'s not-found branch, `_expand_members`/
  `members()`, and `_identifier_clause` respectively; sequence those three within `api.py`
  to avoid merge churn if worked by the same person).
- Within US1: T001, T002, T005 in parallel once T003/T004 land.
- Within US2: T006, T010 in parallel; T012 in parallel with T011; T007/T008/T009 in parallel
  once their respective implementation tasks land.
- Within US3: T015 can start immediately (no code dependency); T018 can start immediately in
  parallel; T016/T017 wait on both.
- Within US4: T021 (README) in parallel with T020/T019.

---

## Parallel Example: User Story 2

```bash
# Once T010 (models) and T011 (_expand_members/members()) and T012 (output) land:
Task: "CLI batch-contract tests for ad members in tests/unit/test_ad_cli.py"
Task: "End-to-end CLI test for ad members in tests/integration/test_ad_mock_directory.py"
Task: "Unit tests for _expand_members()/AdClient.members() in tests/unit/test_ad_api.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 and 2 — both P1)

1. Implement US1 (Phase 3) — the redirect error. Validate independently.
2. Implement US2 (Phase 4) — the `ad members` command. Validate independently.
3. **STOP and VALIDATE**: both P1 gaps from the originating backlog are closed; this alone
   is a shippable increment.

### Incremental Delivery

1. US1 → validate → ship.
2. US2 → validate → ship.
3. US3 (mail matching) → validate → ship.
4. US4 (TLS hints + README) → validate → ship.
5. Phase 7 polish once all four are in.

---

## Notes

- No new `ExitCode` member is introduced anywhere in this feature — `PrincipalIsGroup`
  reuses `NOT_FOUND` (16); every other change is a hint-text or matching-rule refinement.
- No new runtime dependency; no new top-level test module — every test task extends a file
  that already exists (`test_ad_api.py`, `test_ad_cli.py`, `test_ad_directory.py`,
  `test_ad_models.py`, `test_ad_mock_directory.py`, `conftest.py`), matching how `show` was
  added onto the existing `ad` test suite in 004 rather than spawning new files.
- Commit after each task or logical group; verify tests fail before implementing where a
  test task is listed ahead of its implementation task in numbering (T001/T002 precede T003/
  T004 deliberately — write them first, watch them fail, then implement).
- **As-built**: T023's full-repo `uv run pytest` surfaced 15 pre-existing, unrelated
  failures (rich-highlighting regression in `test_ad_output.py`/`test_net_cli.py`/
  `test_net_output.py`/`test_storage_output.py`'s `_console()` test helpers — not caused by
  this feature; see research.md's Addenda). Every `ad`-scoped test this feature added or
  touched passes; `ruff format --check`, `ruff check`, `mypy src`, and `pyright` are all
  clean repo-wide.
