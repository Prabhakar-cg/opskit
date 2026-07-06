# Tasks: TLS Verification Diagnostics

**Input**: Design documents from `/specs/002-tls-verification/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command (Arts. II/III, coverage
≥ 90%); the deterministic loopback strategy is research decision R6.

**Organization**: grouped by user story; each phase is an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US7 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/`, `tests/` at repo root (per plan.md structure).

---

## Phase 1: Setup

**Purpose**: dependencies and package skeletons

- [ ] T001 Add runtime deps `pyopenssl>=24` and `cryptography>=42` to `[project.dependencies]` in pyproject.toml; run `uv lock` + `uv sync --extra dev`; verify pip-audit clean
- [ ] T002 [P] Create package skeletons with module docstrings: src/opskit/net/{__init__,errors,tcp}.py and src/opskit/tls/{__init__,api,cli,errors,handshake,inspect,models,output}.py — tls/cli.py carries the no-future-annotations note (CLAUDE.md rule)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared primitives every story builds on — exit codes, net primitive, tls models/errors, target parsing, shared CLI plumbing, test-certificate infrastructure

**⚠️ CRITICAL**: complete before any user-story phase

- [ ] T003 Add additive ExitCode members `CONNECT_FAILED=8`, `HANDSHAKE_FAILED=9`, `CERT_INVALID=10`, `CERT_EXPIRING=11` in src/opskit/core/exit_codes.py (no other core changes)
- [ ] T004 [P] Implement net error hierarchy in src/opskit/net/errors.py: `NetError(OpskitError)`, `ResolutionError` (exit 3), `ConnectRefused` (exit 8), `ConnectTimeout` (exit 6) — each owns its exit_code (Art. VII)
- [ ] T005 Implement reusable TCP primitive in src/opskit/net/tcp.py: `resolve()` (getaddrinfo → ordered candidates), `connect()` (dual-stack order, timeout/retries, OSError→NetError normalization, returns socket + `TcpConnection` with address/family/connect_ms) per contracts/python-api.md
- [ ] T006 [P] Finalize src/opskit/net/__init__.py public exports (`resolve`, `connect`, `TcpConnection`, net errors) with `__all__`
- [ ] T007 [P] Unit tests for the net primitive in tests/unit/test_net_tcp.py: loopback listener success, closed-port → ConnectRefused, mocked-socket timeout → ConnectTimeout after retries, unresolvable → ResolutionError, dual-stack candidate order
- [ ] T008 Extract the category-agnostic CLI helpers from src/opskit/dns/cli.py into a new src/opskit/core/cliutils.py (`read_input_file`, `parse_interval`, `watch`, `run_or_watch`, `collect_outcomes`, `echo_failures`, `emit_envelopes`, `aggregate_exit`) and re-point dns/cli.py to them — pure move, zero behavior change, all 91 existing tests stay green (prevents tls duplicating dns's CLI plumbing — the exact duplication class Sonar flagged)
- [ ] T009 [P] Implement tls models in src/opskit/tls/models.py: `TlsOutcome`, `FindingCode`, frozen dataclasses `TlsTarget`, `TcpConnection` re-use, `CertificateInfo` (incl. fingerprint_sha256, is_self_signed), `ValidationFinding`, `TlsCheckResult` (`.ok`, `to_dict()`) per data-model.md
- [ ] T010 [P] Implement tls errors in src/opskit/tls/errors.py: `TlsError(OpskitError)`, `HandshakeError` (exit 9), `CertificateInvalid` (exit 10, carries findings), `CertificateExpiring` (exit 11)
- [ ] T011 Implement target parsing `parse_target()` in src/opskit/tls/models.py: `host`, `host:port`, IPv4/IPv6 literals, `[v6]:port`, trailing-dot normalization, shorthand/--port agreement → UsageError; property tests (+ Hypothesis) in tests/unit/test_tls_target.py
- [ ] T012 [P] Runtime certificate factory in tests/integration/conftest.py using cryptography: session-scoped root→intermediate→leaf chains plus expired / not-yet-valid / wrong-name / no-SAN / self-signed / short-lived variants — generated at runtime, nothing committed (R6, Art. III)
- [ ] T013 [P] Loopback server fixtures in tests/integration/conftest.py: threaded stdlib-ssl TLS server parameterized by cert/chain, plain-TCP listener (non-TLS port), closed-port helper

**Checkpoint**: primitives + fixtures ready — user stories can begin

---

## Phase 3: User Story 1 - Verify an endpoint's TLS health (Priority: P1) 🎯 MVP

**Goal**: `opskit tls check host` returns a full verdict (validity, name match, trust, expiry, protocol/cipher) with certificate details shown even when validation fails; exit 0 / 10 per outcome.

**Independent Test**: quickstart US1 rows — healthy endpoint exits 0 with leaf+chain+protocol; expired / wrong-host / self-signed each yield the named finding, full details, exit 10.

### Implementation for User Story 1

- [ ] T014 [US1] Trust-store builder in src/opskit/tls/handshake.py: stdlib `ssl` default certs exported into a pyOpenSSL `X509Store`; `ca_file` replaces the store (R2)
- [ ] T015 [US1] pyOpenSSL handshake in src/opskit/tls/handshake.py: recording verify callback (never aborts; captures OpenSSL error codes per depth), SNI control, chain extraction (`get_peer_cert_chain`), negotiated protocol/cipher; normalize handshake SSL/OS errors → `HandshakeError` with non-TLS/STARTTLS hint (R1)
- [ ] T016 [P] [US1] Certificate parsing in src/opskit/tls/inspect.py: pyOpenSSL→cryptography conversion → `CertificateInfo` (subject/issuer RFC 4514, SANs, validity, days, serial, sig alg, key type/bits, SHA-256 fingerprint, self-signed detection); unit tests in tests/unit/test_tls_inspect.py using the T012 factory
- [ ] T017 [P] [US1] RFC 6125 matcher `match_hostname()` in src/opskit/tls/inspect.py: exact DNS-SAN, single left-most wildcard label, IP-SAN matching, no CN fallback (R3); property tests (+ Hypothesis) in tests/unit/test_tls_match.py covering spec wildcard edge cases
- [ ] T018 [US1] Findings assembly in src/opskit/tls/inspect.py: map verify-callback codes + parsed certs + match result → `ValidationFinding`s (EXPIRED, NOT_YET_VALID, NAME_MISMATCH, SELF_SIGNED, UNTRUSTED_CHAIN, INCOMPLETE_CHAIN, NO_SANS, LEGACY_PROTOCOL) with messages ("requested X; certificate covers Y") and hints
- [ ] T019 [US1] `check()` orchestration in src/opskit/tls/api.py: parse → resolve/connect (opskit.net) → handshake → validate; outcome = first failing layer; raise/return split + `raise_on_invalid` per contracts/python-api.md; Google-style docstrings
- [ ] T020 [P] [US1] Category rendering in src/opskit/tls/output.py: verdict line, leaf table, chain table, protocol/cipher line, findings with hints — `rich.markup.escape()` on every certificate/server-derived string (CLAUDE.md rule)
- [ ] T021 [US1] Thin Typer command `check` in src/opskit/tls/cli.py (panels: Query / Query controls / Modes / Output; epilog examples; `--json`/`--jsonl`/`--no-color`; Optional[...] annotations, no future import) and register the sub-app in src/opskit/cli.py
- [ ] T022 [P] [US1] API unit tests with injected fake handshake/connect in tests/unit/test_tls_api.py: OK, each cert-invalid finding, outcome derivation, raise_on_invalid
- [ ] T023 [P] [US1] CLI unit tests in tests/unit/test_tls_cli.py: envelope shape (`command: "tls.check"`, query echo, result per data-model), exit codes 0/10, human output smoke
- [ ] T024 [P] [US1] Rendering tests incl. markup-injection escaping in tests/unit/test_tls_output.py
- [ ] T025 [US1] Loopback integration in tests/integration/test_tls_loopback.py: valid chain → exit 0; expired / wrong-name / self-signed / untrusted-root → named finding + details + exit 10

**Checkpoint**: MVP — single-target verification fully usable

---

## Phase 4: User Story 2 - Non-standard ports and IP targets (Priority: P1)

**Goal**: default 443; `-p/--port` and `host:port`/`[v6]:port` shorthand (agreement enforced); IP targets run without SNI and match against IP SANs.

**Independent Test**: quickstart US2 rows — alt-port check via option and shorthand; IPv4/IPv6 literal targets produce the same report structure with the IP-matching note.

- [ ] T026 [US2] Port precedence wiring in src/opskit/tls/cli.py + api (`--port` default 443, shorthand agreement → usage exit 2); tests in tests/unit/test_tls_cli.py
- [ ] T027 [US2] IP-target behavior in src/opskit/tls/api.py + inspect.py: SNI omitted, name validation against IP SANs, report notes the matching mode and the connected address; unit tests in tests/unit/test_tls_api.py
- [ ] T028 [P] [US2] Loopback integration on non-443 ports incl. IPv6 loopback (`[::1]:PORT`, skip gracefully where IPv6 unavailable) in tests/integration/test_tls_loopback.py

**Checkpoint**: any host/IP:port combination checkable

---

## Phase 5: User Story 3 - Pinpoint which layer failed (Priority: P2)

**Goal**: resolve / connect-refused / timeout / handshake / cert failures each produce a distinct outcome, message, hint, and exit code (3/8/6/9/10).

**Independent Test**: quickstart US3 rows — unresolvable → 3; closed port → 8; plain-TCP port → 9 with STARTTLS/non-TLS hint; mocked timeouts → 6.

- [ ] T029 [US3] Layer mapping tests: unresolvable → ResolutionError exit 3; mocked connect/handshake timeout → exit 6 after retries; verify outcome never masks an earlier layer — in tests/unit/test_tls_api.py + tests/unit/test_tls_cli.py
- [ ] T030 [US3] Loopback integration in tests/integration/test_tls_loopback.py: closed port → exit 8 (refused); plain-TCP listener → exit 9 with hint text; assert one-line explanations per SC-002

**Checkpoint**: every failure layer distinguishable by code + message

---

## Phase 6: User Story 4 - Inspect the full certificate and chain (Priority: P2)

**Goal**: leaf detail completeness (FR-011) and per-chain listing; incomplete chains identified.

**Independent Test**: quickstart US4 row — `--json | jq .result.chain` lists one object per presented cert; a served chain missing its intermediate is reported incomplete/untrusted.

- [ ] T031 [US4] Incomplete-chain detection in src/opskit/tls/inspect.py (distinguish INCOMPLETE_CHAIN from UNTRUSTED_CHAIN/SELF_SIGNED via verify-error codes at depth); loopback test serving leaf-without-intermediate in tests/integration/test_tls_loopback.py
- [ ] T032 [P] [US4] Chain JSON contract test in tests/unit/test_tls_cli.py: every FR-011 leaf field present; chain entries carry subject/issuer/validity

**Checkpoint**: chain debugging complete

---

## Phase 7: User Story 5 - Catch certificates before they expire (Priority: P2)

**Goal**: `--warn-days` (default 30, 0 disables) → EXPIRING_SOON outcome, exit 11; `--watch` flags outcome/cert changes.

**Independent Test**: quickstart US5 rows — short-lived loopback cert inside threshold → exit 11 with days remaining; threshold 0 → exit 0; watch flags a cert swap.

- [ ] T033 [US5] `warn_days` logic in src/opskit/tls/api.py (EXPIRING_SOON only when otherwise valid; CertificateExpiring for raise_on_invalid) + `--warn-days` in cli.py; short-lived-cert loopback test in tests/integration/test_tls_loopback.py + unit tests in tests/unit/test_tls_api.py
- [ ] T034 [US5] `--watch` wiring via core cliutils with change signature (outcome, leaf fingerprint, not_after, tls_version) per R8; tests patching `time.sleep` (dns pattern — patch at call time) in tests/unit/test_tls_cli.py

**Checkpoint**: renewal-watch flows usable

---

## Phase 8: User Story 6 - Bulk verification (Priority: P3)

**Goal**: `-i/--input-file` with `host[:port]` lines; every target processed; failures present in JSON; batch exit rule (0 / uniform / 7).

**Independent Test**: quickstart US6 row — mixed file (healthy, expiring, invalid, unreachable) yields one NDJSON envelope per line incl. failures and the documented aggregate code.

- [ ] T035 [US6] Batch wiring in src/opskit/tls/cli.py via core cliutils (input-file parsing incl. per-line ports, per-target tolerance, `_envelope`-style error entries, aggregate exit); mixed-outcome unit tests in tests/unit/test_tls_cli.py asserting failed targets appear in `--json`/`--jsonl` (Art. IX)

**Checkpoint**: fleet audits supported

---

## Phase 9: User Story 7 - Use it from code (Priority: P3)

**Goal**: `opskit.tls` / `opskit.net` public, typed, documented; contract example runs as written.

**Independent Test**: quickstart US7 row + SC-006 — the python-api.md example executes unmodified.

- [ ] T036 [US7] Finalize src/opskit/tls/__init__.py `__all__` (check, models, enums, errors) and re-export docs; add a test executing the contracts/python-api.md usage example against the loopback server in tests/unit/test_tls_api.py

**Checkpoint**: API parity delivered

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T037 [P] Write src/opskit/tls/README.md (command reference mirroring dns/README.md: options table, layer outcomes, exit codes 8–11, JSON sample, library section) and add the `opskit tls` row + link in the root README.md Commands table (docs gate, Art. II)
- [ ] T038 [P] Real-endpoint smoke tests `@pytest.mark.network` (example.com, expired./wrong.host./self-signed.badssl.com) in tests/integration/test_tls_network.py — excluded from CI by default
- [ ] T039 Run the full quickstart validation matrix + all gates on 3.9 and default: `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pyright && uv run pytest` (coverage ≥ 90%); fix any drift
- [ ] T040 Reconcile design docs with as-built reality (specs/002-tls-verification/contracts/, data-model.md) and update specs/001-dns-diagnostics/contracts/cli.md exit-code table with codes 8–11 (shared enum documented once)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)** → **Foundational (P2)** → user stories.
- **US1 (Phase 3)** depends only on Foundational; it is the MVP.
- **US2 (Phase 4)** depends on US1's api/cli existing (T019/T021).
- **US3 (Phase 5)** depends on US1 (outcomes exist) — mostly tests + mapping.
- **US4 (Phase 6)** depends on US1's inspect.py (T016/T018).
- **US5 (Phase 7)** depends on US1 + T008 (cliutils watch helpers).
- **US6 (Phase 8)** depends on US1 + T008 (cliutils batch helpers).
- **US7 (Phase 9)** depends on US1 (public surface finalization).
- **Polish (Phase 10)** last; T037/T038 can start once US1 is stable.

### Key task-level dependencies

- T005 needs T004; T007 needs T005; T011 needs T009; T013 needs T012.
- T015 needs T014; T018 needs T016+T017; T019 needs T005+T009+T010+T011+T015+T018; T021 needs T019+T020+T008.
- T025 needs T021+T013; all later loopback tests need T013.

### Parallel Opportunities

- Phase 2: T004, T006 (after T005), T007 (after T005), T009, T010, T012, T013 largely parallel; T008 independent of the net/tls tracks.
- Phase 3: T016+T017 in parallel; then T018; T020 parallel with T019; T022/T023/T024 in parallel once their targets exist.
- Phases 4–9 touch mostly disjoint files after US1 and can interleave; T037/T038 are parallel polish.

## Parallel Example: User Story 1

```bash
# After T015 completes, run in parallel:
Task: "T016 certificate parsing in src/opskit/tls/inspect.py"
Task: "T017 RFC 6125 matcher + Hypothesis tests"
# After T019/T020/T021:
Task: "T022 API unit tests"  |  Task: "T023 CLI unit tests"  |  Task: "T024 output tests"
```

## Implementation Strategy

**MVP first**: Phases 1–3 (T001–T025) deliver a fully usable single-target verifier — stop,
validate against quickstart US1, demo. **Incremental**: each subsequent phase is an
independently testable increment ending in a checkpoint; commit per task or logical group with
Conventional Commits; run the four gates before each commit batch (CLAUDE.md). US2 is also P1 —
treat Phases 3+4 together as the release-worthy core if demoing beyond loopback.
