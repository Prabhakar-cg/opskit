# Tasks: DNS Diagnostics

**Feature**: `001-dns-diagnostics` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

Tasks are organized by user story (from spec.md) so each is an independently testable increment.
Tests are included because the constitution mandates layered testing + ≥90% coverage. `[P]` = can run
in parallel (different files, no incomplete deps). Story labels `[US1]`–`[US9]` map to spec.md.

**Paths** follow plan.md: logic in `src/opskit/dns/api.py` + `resolver.py`; shared concerns in
`src/opskit/core/`; thin CLI in `src/opskit/dns/cli.py`; tests under `tests/{unit,integration,contract,network}/`.

---

## Phase 1: Setup

- [X] T001 Create package skeletons `src/opskit/core/__init__.py` and `src/opskit/dns/__init__.py`
- [X] T002 [P] Create test dirs + `tests/conftest.py`, `tests/integration/`, `tests/contract/`, `tests/network/` (with `__init__.py` where needed)
- [X] T003 [P] Register a `dns` Typer sub-app placeholder on the root app in `src/opskit/cli.py` (zero logic)

## Phase 2: Foundational (blocking — shared core; must complete before user stories)

- [X] T004 [P] Define `RecordType`, `Transport`, `Outcome` enums in `src/opskit/dns/models.py`
- [X] T005 [P] Define `ExitCode` enum in `src/opskit/core/exit_codes.py`
- [X] T006 Implement base exception hierarchy `OpskitError`, `UsageError` in `src/opskit/core/errors.py`
- [X] T007 Implement DNS exceptions (`DnsError` → `NxDomain`/`ServerFailure`/`DnsRefused`/`DnsTimeout`/`DnssecError`) in `src/opskit/dns/errors.py`
- [X] T008 Implement exception→`ExitCode` mapping in `src/opskit/core/exit_codes.py`
- [X] T009 Implement typed result dataclasses (`DnsRecord`, `DnsQuery`, `LookupResult`, `Resolver`, `ResolverComparison`, `TraceStep`) with `to_dict()` and `.ok`/iteration in `src/opskit/dns/models.py`
- [X] T010 Implement versioned JSON envelope (`schema_version`, `command`, `query`, `result`, `error`, `elapsed_ms`) + `to_json()` in `src/opskit/core/result.py`
- [X] T011 Implement output rendering (rich human tables, `--json`, `--jsonl`; honor `NO_COLOR` + auto-plain-when-piped) in `src/opskit/core/output.py`
- [ ] T012 Implement config precedence (flags > env `OPSKIT_*` > profile > file `[default]` > built-in) + TOML load (`tomllib`/`tomli`) in `src/opskit/core/config.py`
- [ ] T013 [P] Implement bounded worker pool for batch/multi-resolver in `src/opskit/core/concurrency.py`
- [X] T014 Implement injectable resolver abstraction wrapping `dnspython` in `src/opskit/dns/resolver.py`
- [X] T015 [P] Add injected mock-resolver fixture (canned answers, every rcode, timeouts) in `tests/conftest.py`
- [ ] T016 Add in-process loopback DNS server fixture (`dnslib`; UDP/TCP, drop, REFUSED, TC-bit, injected latency, per-resolver answers) in `tests/integration/conftest.py`
- [X] T017 Configure `logging.getLogger("opskit")` + `NullHandler` in `src/opskit/__init__.py`

**Checkpoint:** core contracts compile, mypy/pyright clean, fixtures usable — user stories can start.

---

## Phase 3: User Story 1 — Forward lookup (Priority: P1) 🎯 MVP

**Goal:** `opskit dns lookup <name> -t <type>` returns records with human + `--json` output and correct exit codes, identically on all OSes.
**Independent test:** run a lookup for a known name (mock + loopback), assert records, envelope, and exit code.

- [X] T018 [P] [US1] Unit tests for `lookup()` (A/AAAA/MX/TXT/CNAME/NS/SOA/SRV; NXDOMAIN/SERVFAIL/REFUSED → exceptions) via mock resolver in `tests/unit/test_dns_lookup.py`
- [ ] T019 [P] [US1] Loopback integration test for forward lookup in `tests/integration/test_lookup_loopback.py`
- [X] T020 [P] [US1] Contract test for the `--json` lookup envelope in `tests/contract/test_json_envelope.py`
- [X] T021 [US1] Implement `lookup()` in `src/opskit/dns/api.py` (record types, per-query `elapsed_ms`, typed `LookupResult`, raises `DnsError` subclasses)
- [X] T022 [US1] Implement `dns lookup` command (thin; `-t/--type` repeatable, `--json`) in `src/opskit/dns/cli.py`, delegating to api and mapping exceptions→exit codes
- [X] T023 [P] [US1] CLI test (CliRunner: human output, exit codes, `--json`, `NO_COLOR`) in `tests/unit/test_cli_lookup.py`

**Checkpoint:** MVP works — a cross-platform forward lookup with structured output + exit codes.

---

## Phase 4: User Story 2 — Reverse lookup (Priority: P2)

**Goal:** `opskit dns reverse <ip>` returns PTR hostname(s). **Independent test:** reverse a known IPv4/IPv6 (loopback) → hostname(s) + exit code.

- [X] T024 [P] [US2] Tests for reverse (PTR, no-record case) via mock + loopback in `tests/integration/test_reverse.py`
- [X] T025 [US2] Implement `reverse()` in `src/opskit/dns/api.py`
- [X] T026 [US2] Implement `dns reverse` command in `src/opskit/dns/cli.py`

## Phase 5: User Story 3 — Custom resolver & query controls (Priority: P2)

**Goal:** target a specific resolver and tune timeout/retries/transport/port; auto TCP fallback on truncation. **Independent test:** loopback resolver returns REFUSED/timeout/TC-bit → correct distinct outcomes.

- [ ] T027 [P] [US3] Integration tests: `--server` targeting, transport, timeout/retries, TC-bit→TCP fallback (loopback) in `tests/integration/test_query_controls.py`
- [ ] T028 [US3] Add query params (servers, transport, timeout, retries, port) to `lookup`/`reverse` in `src/opskit/dns/api.py`
- [ ] T029 [US3] Implement transport selection + TCP fallback on truncation + OS-error normalization in `src/opskit/dns/resolver.py`
- [ ] T030 [US3] Add CLI flags `--server/--transport/--timeout/--retries/--port` in `src/opskit/dns/cli.py`

## Phase 6: User Story 4 — Multi-resolver diff (Priority: P2)

**Goal:** query several resolvers and highlight differences. **Independent test:** two loopback resolvers with differing answers → differences surfaced; matching → reported consistent.

- [X] T031 [P] [US4] Integration tests: consistent vs differing answers (loopback split-horizon) in `tests/integration/test_compare.py`
- [X] T032 [US4] Implement `compare()` producing `ResolverComparison` (concurrent via core pool) in `src/opskit/dns/api.py`
- [X] T033 [US4] Implement `--diff` rendering (per-resolver difference highlighting) in `src/opskit/core/output.py` and wire `--diff` in `src/opskit/dns/cli.py`

## Phase 7: User Story 9 — Programmatic API (Priority: P2)

**Goal:** the same capabilities are callable from code with structured results + catchable errors, never printing/exiting. **Independent test:** `from opskit.dns import lookup` returns a result; failures raise typed exceptions.

- [ ] T034 [P] [US9] Tests: public import surface, structured results, exceptions raised, no stdout/`sys.exit` from library in `tests/unit/test_public_api.py`
- [ ] T035 [US9] Finalize public API + `__all__` re-exports in `src/opskit/dns/__init__.py` and `src/opskit/__init__.py`
- [ ] T036 [US9] Implement configurable `DnsClient` (shared defaults) + `lookup_many()` in `src/opskit/dns/api.py`

## Phase 8: User Story 5 — Batch & scripting (Priority: P3)

**Goal:** targets from args/file/stdin; NDJSON; scriptable aggregate exit code. **Independent test:** 100 names via stdin → one envelope per line; exit code reflects aggregate.

- [X] T037 [P] [US5] Tests: args/file/stdin ingestion, `--jsonl`, aggregate exit-code rule in `tests/unit/test_batch.py`
- [ ] T038 [US5] Implement target ingestion (args/`--file`/stdin `-`) + batch dispatch in `src/opskit/dns/cli.py`
- [X] T039 [US5] Implement `--jsonl` NDJSON output + aggregate exit-code rule (non-success if any target fails) in `src/opskit/core/output.py`

## Phase 9: User Story 6 — Watch mode (Priority: P3)

**Goal:** `--watch <interval>` re-runs and surfaces changes. **Independent test:** interval re-runs; a changed answer is surfaced; Ctrl+C exits cleanly.

- [X] T040 [P] [US6] Tests: interval re-run + change detection in `tests/unit/test_watch.py`
- [X] T041 [US6] Implement `--watch` loop + change detection + KeyboardInterrupt handling in `src/opskit/dns/cli.py`

## Phase 10: User Story 7 — Saved profiles (Priority: P3)

**Goal:** save named resolver/settings profiles and apply via `--profile`. **Independent test:** save profile, run with `--profile` → settings applied; explicit flag overrides profile.

- [ ] T042 [P] [US7] Tests: save/use profile, precedence (flag > profile) in `tests/unit/test_config_profiles.py`
- [ ] T043 [US7] Implement profile store (TOML at platformdirs path) + `--profile` resolution in `src/opskit/core/config.py`
- [ ] T044 [US7] Add profile management CLI (`--profile`, save command) in `src/opskit/dns/cli.py`

## Phase 11: User Story 8 — Resolution trace (Priority: P3)

**Goal:** `--trace` shows the resolution path. **Independent test:** trace mode returns ordered `TraceStep`s alongside the answer.

- [X] T045 [P] [US8] Tests: trace steps present + ordered in `tests/integration/test_trace.py`
- [X] T046 [US8] Implement `--trace` capture (`TraceStep`) in `src/opskit/dns/resolver.py` + `api.py`
- [X] T047 [US8] Render trace in human + `--json` in `src/opskit/core/output.py`

---

## Phase 12: Polish & Cross-Cutting Concerns

- [ ] T048 [P] Add actionable `hint`s (what to try next) to error rendering across `src/opskit/dns/errors.py` + `src/opskit/core/output.py` (FR-016)
- [ ] T049 [P] Publish JSON Schema for the envelope in `src/opskit/core/schema/envelope-v1.json` + validate sample outputs in `tests/contract/test_schema.py`
- [ ] T050 [P] Add a docs page per command in `docs/commands/dns.md` + a docs-coverage test enumerating Typer commands in `tests/unit/test_docs_coverage.py` (Art. II gate)
- [ ] T051 [P] Wire shell completion + `--quiet/--verbose` in `src/opskit/cli.py`
- [ ] T052 [P] Opt-in real-network smoke tests (`@pytest.mark.network`) in `tests/network/test_real_resolvers.py`
- [ ] T053 Verify quality gates green: coverage ≥90%, `ruff`, `mypy --strict`, `pyright` (`uv run nox`)
- [ ] T054 Constitution / OpenSSF gate check (no new unpinned actions; tokens least-privilege; every command has tests + docs; read-only/zero-telemetry preserved)
- [ ] T055 Update `docs/PLAN.md` handoff + decision log with DNS implementation status; add a `CHANGELOG` `feat` entry via a Conventional Commit

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2)** → user stories. Foundational blocks everything.
- **US1 (P1)** is the MVP and unblocks the shared api/cli patterns. US2–US4, US9 build on US1's api.
- **US3** (query params) is used by US4/US5/US6/US8 — do it before those for cleanest flow, though each phase remains independently testable.
- **US5–US8 (P3)** are independent of each other; parallelizable across contributors.
- **Polish** last (needs commands to exist for docs-coverage/schema/gates).

## Parallel Opportunities

- Setup: T002, T003 in parallel with T001 done first.
- Foundational: T004, T005, T013, T015 are `[P]`; T006–T012, T014, T016 touch shared files — sequence.
- Within each story, the `[P]` test tasks can be written first (TDD) in parallel with each other.
- Across stories: once Foundational + US1 land, US2/US3/US4/US9 can proceed on separate branches.

## Implementation Strategy

1. **MVP = Phases 1–3** (Setup + Foundational + US1): a working `opskit dns lookup` with human/JSON output and exit codes. Ship/checkpoint here.
2. Add **US2, US3, US4, US9** (the rest of P2) for a genuinely useful DNS toolkit.
3. Layer **US5–US8** (P3 conveniences) incrementally.
4. **Polish** — schema, docs-coverage, gates — before the feature PR to `main`.

**MVP scope:** User Story 1 (forward lookup). **Total tasks:** 55 across 12 phases.
