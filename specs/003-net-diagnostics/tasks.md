# Tasks: Network Connectivity Diagnostics

**Input**: Design documents from `/specs/003-net-diagnostics/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command (Arts. II/III, coverage
≥ 90%); the deterministic loopback/mock strategy is research decision R6.

**Organization**: grouped by user story; each phase is an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US6 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/`, `tests/` at repo root (per plan.md structure).

---

## Phase 1: Setup

**Purpose**: package skeletons (zero new dependencies — stdlib sockets only)

- [X] T001 Create skeletons with module docstrings: src/opskit/net/{udp,listener,models,api,cli,output}.py and src/opskit/net/README.md placeholder — net/cli.py carries the no-future-annotations note and eager `Optional[X]` annotations (CLAUDE.md rule); every other new module keeps `from __future__ import annotations`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared primitives every story builds on — exit codes, errors, target parsing, family restriction, CLI plumbing extensions, loopback fixtures

**⚠️ CRITICAL**: complete before any user-story phase

- [X] T002 Add additive ExitCode members `PORT_IN_USE = 12`, `BIND_PERMISSION = 13` in src/opskit/core/exit_codes.py (no other core changes — R5)
- [X] T003 [P] Extend the net error hierarchy in src/opskit/net/errors.py: `UdpClosed` (exit 8), `UdpInconclusive` (exit 6 — message names both possibilities "open or filtered (inconclusive)", hint points at `net listen` and the protocol-aware-probe caveat), `PortInUse` (exit 12), `BindPermissionDenied` (exit 13) — each owns its exit_code (Art. VII); existing types untouched
- [X] T004 Extract the bracket-aware `host:port` splitter (`_split_host_port`, `_parse_port`, `_is_ip_literal`, trailing-dot normalization) from src/opskit/tls/models.py into src/opskit/net/models.py and make tls delegate to it — pure move, tls behavior unchanged, all existing tls tests stay green (R3)
- [X] T005 Implement net target model in src/opskit/net/models.py: `Protocol`/`Verdict`/`StopReason` enums, frozen `NetTarget` dataclass, `parse_target(raw, *, port, protocol, family)` with the **no-default-port** rule (missing port → UsageError before any I/O), shorthand/`--port` conflict → UsageError, `[v6]:port` handling per data-model.md; property tests (+ Hypothesis) in tests/unit/test_net_target.py
- [X] T006 Add optional `family: str | None` parameter to `resolve()` and `connect()` in src/opskit/net/tcp.py (mapped to getaddrinfo AF_INET/AF_INET6/AF_UNSPEC; empty requested family → ResolutionError saying so — R1/FR-003); additive, existing callers unaffected; unit tests in tests/unit/test_net_tcp.py
- [X] T007 Extend src/opskit/core/cliutils.py (category-agnostic, additive — existing signatures preserved): variadic-positional target collection (N positionals + input file, first-appearance order) and `--input-file -` reading targets from stdin with the same blank/`#` filtering (R7); unit tests in tests/unit/test_cliutils.py
- [X] T008 [P] Loopback fixtures in tests/integration/conftest.py: threaded TCP accept-listener helper, in-process UDP echo server, closed-port helpers (TCP + UDP), free-port allocator — real sockets, no external network (R6)

**Checkpoint**: primitives + fixtures ready — user stories can begin

---

## Phase 3: User Story 1 - Check whether a port is reachable (Priority: P1) 🎯 MVP

**Goal**: `opskit net check host:port` gives an immediate TCP verdict — open (address, family, connect time; exit 0), refused (exit 8), timeout/filtered (exit 6), unresolvable (exit 3) — identically on all platforms; port required.

**Independent Test**: quickstart US1 rows — open loopback port exits 0 with address+family+timing; closed port, mocked-filtered port, and unresolvable name each yield a distinct verdict, hint, and exit code; portless target rejected with exit 2 before any I/O.

### Implementation for User Story 1

- [X] T009 [US1] `CheckResult` frozen dataclass (target, verdict, address, family, port, time_ms, `to_dict()`) in src/opskit/net/models.py per data-model.md
- [X] T010 [US1] `check()` orchestration (TCP path) in src/opskit/net/api.py: `parse_target` → `tcp.connect(..., family=...)` → close socket immediately (no application data — FR-006) → return `CheckResult(verdict=OPEN)`; non-open outcomes propagate as the typed errors (raise/return split per contracts/python-api.md); Google-style docstrings
- [X] T011 [P] [US1] Check rendering in src/opskit/net/output.py: verdict line (open/refused/timeout/unresolvable wording + hint), address/family/timing detail — `rich.markup.escape()` on every hostname/address/target string; consoles only via `make_console` (CLAUDE.md rules)
- [X] T012 [US1] Thin Typer command `check` in src/opskit/net/cli.py (variadic TARGETS via the T007 helper, `-p/--port`, `-4/--ipv4`/`-6/--ipv6` mutually exclusive, `--timeout` 5.0, `--retries` 2, `--json`/`--jsonl`/`--no-color`; panels Query / Query controls / Modes / Output; epilog examples per contracts/cli.md) and register the net sub-app in src/opskit/cli.py; envelope `command: "net.check"` per target incl. failures
- [X] T013 [P] [US1] API unit tests in tests/unit/test_net_api.py: open via loopback → CheckResult fields; ConnectRefused/ConnectTimeout/ResolutionError propagation with injected fakes; family restriction (ipv4/ipv6/no-address-in-family); socket closed after verdict
- [X] T014 [P] [US1] CLI unit tests in tests/unit/test_net_cli.py: envelope shape (query echo incl. protocol/family/controls, result per data-model), exit codes 0/8/6/3, missing-port and port-conflict → exit 2, `-4`+`-6` together → exit 2, human output smoke
- [X] T015 [P] [US1] Rendering tests incl. markup-injection escaping (`[bold]`-style hostnames/addresses) in tests/unit/test_net_output.py
- [X] T016 [US1] Loopback integration in tests/integration/test_net_loopback.py: open TCP port → verdict open with plausible timing, exit 0; **closed loopback port asserted as the NetError class family** (refused on Linux/macOS, may be timeout on Windows — the canonical CLAUDE.md rule, R6); trailing-dot hostname accepted; accept-then-immediately-close server still reports open

**Checkpoint**: MVP — single-target TCP reachability fully usable

---

## Phase 4: User Story 2 - Check a UDP port honestly (Priority: P2)

**Goal**: `opskit net check host:port --udp` reports open (reply received; exit 0), closed (port-unreachable signal; exit 8), or the explicitly inconclusive "no response — open or filtered" (exit 6) — never a false pass or fail (SC-007).

**Independent Test**: quickstart US2 rows — loopback UDP echo → open with response time; closed local UDP port → closed; silent (mocked) port → inconclusive with the both-possibilities wording and listener hint.

### Implementation for User Story 2

- [X] T017 [US2] `udp_probe()` in src/opskit/net/udp.py: connected-UDP socket per resolved candidate, single **zero-byte** probe datagram, reply → open with response time; `ConnectionRefusedError`/`ConnectionResetError` (Windows WSAECONNRESET on recv) → `UdpClosed`; silence after `retries` re-sends → `UdpInconclusive`; all other `OSError` normalized into the hierarchy — raw OSError never escapes (R2, Art. VI)
- [X] T018 [US2] Wire UDP into the stack: `protocol` dispatch in `check()` in src/opskit/net/api.py; `-u/--udp` option in src/opskit/net/cli.py (FR-004 — batch/watch/JSON identical to TCP); UDP verdict wording + inconclusive hint in src/opskit/net/output.py
- [X] T019 [P] [US2] UDP unit tests with injected/mocked sockets in tests/unit/test_net_udp.py: reply → open; ECONNREFUSED and Windows-style ConnectionResetError-on-recv → UdpClosed; silence → UdpInconclusive after exactly `retries`+1 sends; probe datagram is zero bytes (assert payload); other OSError → typed NetError
- [X] T020 [US2] Loopback UDP integration in tests/integration/test_net_loopback.py: echo server → open with timing, exit 0; closed loopback UDP port → asserted as **{UdpClosed, UdpInconclusive}** (ICMP delivery is platform-dependent — R6) while the mocked path pins UdpClosed exactly; CLI `--udp --json` envelope carries the inconclusive error object verbatim (never claims open)

**Checkpoint**: honest UDP verdicts shipping alongside TCP

---

## Phase 5: User Story 3 - Measure connection latency and stability (Priority: P2)

**Goal**: `opskit net probe target` runs N attempts (default 4, interval 1s) with per-attempt outcomes/timings as they happen plus a summary (attempts/successes/failures, min/avg/max); `--watch` on `check` flags verdict changes.

**Independent Test**: quickstart US3 rows — 10-probe run against a loopback listener reports 10 attempts + plausible stats; a mid-run failure is counted, not aborting; interrupted run still summarizes; watch flags open→refused when the listener stops.

### Implementation for User Story 3

- [X] T021 [US3] `ProbeAttempt` + `ProbeResult` frozen dataclasses (counts incl. UDP replies/closed_signals/silent split, min/avg/max over answered attempts, `to_dict()`) in src/opskit/net/models.py per data-model.md
- [X] T022 [US3] `probe()` in src/opskit/net/api.py: attempt loop over the check classification with `retries=0` per attempt, `interval` sleep between attempt starts, failures captured as `ProbeAttempt` data (never raised — FR-009), `on_attempt` streaming hook, statistics computed here, interrupted runs finalize over completed attempts (R9); raises only pre-flight (usage / resolution)
- [X] T023 [P] [US3] Probe rendering in src/opskit/net/output.py: per-attempt line as each completes + summary block (escape() on all external strings)
- [X] T024 [US3] Thin Typer command `probe` in src/opskit/net/cli.py: single TARGET, `-c/--count` 4, `--interval` 1s (reuse `parse_interval` grammar), shared protocol/family/timeout options, `--retries` default 0; exit via `aggregate_exit` over attempt codes (0 / uniform / 7); `--jsonl` streams one envelope per attempt (`result.kind: "attempt"`) then a summary envelope (`kind: "summary"`); KeyboardInterrupt mid-run still renders/emits the summary
- [X] T025 [US3] `--watch` wiring on `check` in src/opskit/net/cli.py via `run_or_watch` with change signature (target, verdict class, connected address, family) per R8 — timing jitter never flags; tests patching `time.sleep` (dns pattern) in tests/unit/test_net_cli.py
- [X] T026 [P] [US3] Probe unit tests in tests/unit/test_net_api.py + tests/unit/test_net_cli.py: stats math (min/avg/max, None when nothing answered), mixed-outcome counting, UDP reply/closed/silent split, interrupt-summarizes-completed, per-attempt NDJSON + summary envelope shapes, aggregate exit 0/uniform/7
- [X] T027 [US3] Loopback probe integration in tests/integration/test_net_loopback.py: 10 attempts against live listener → 10 results + plausible stats; listener stopped mid-run → failures counted, run completes

**Checkpoint**: snapshot → diagnosis; ping-style flows usable

---

## Phase 6: User Story 4 - Audit many endpoints at once (Priority: P2)

**Goal**: `opskit net check` takes many targets via variadic args, `--input-file`/`-i` (incl. `-` = stdin), or a pipe; every target processed; failures never dropped from machine output; aggregate exit 0 / uniform / 7.

**Independent Test**: quickstart US4 rows — a file mixing open/refused/unresolvable targets yields one envelope per line including failures and exit 7; piped stdin targets all checked.

### Implementation for User Story 4

- [X] T028 [US4] Batch wiring in src/opskit/net/cli.py: combine variadic TARGETS + `--input-file` (and `-` stdin) via the T007 helpers; `collect_outcomes` per-target tolerance (no abort on first failure); batch headers escaped; failures to stderr in human mode only; `aggregate_exit` for the process code; `-p/--port` applied to portless file/stdin lines with shorthand-agreement enforcement
- [X] T029 [US4] Batch contract tests in tests/unit/test_net_cli.py: mixed 50-target batch (SC-004) processes all and exits 7; uniform-failure batch exits that class; all-pass exits 0; `--json`/`--jsonl` contain an envelope for **every** target incl. failures (`result: null`, populated `error` — Art. IX); stdin via `-i -`; blank lines/`#` comments ignored; assert on IPs/record values, never `"host.tld" in output` (CodeQL rule)

**Checkpoint**: fleet audits replace ad-hoc shell loops

---

## Phase 7: User Story 5 - Verify inbound reachability with a temporary listener (Priority: P3)

**Goal**: `opskit net listen PORT [--udp]` reports each inbound connection/datagram (peer address/port/timestamp — metadata only, payload never read/shown/stored), stops cleanly on Ctrl-C or `--max-duration`/`--max-events`, with distinct errors for busy port (12) and bind permission (13).

**Independent Test**: quickstart US5 rows — listener + `net check` on loopback shows the connection's peer details end-to-end (TCP and UDP); busy port → exit 12; privileged port → exit 13; zero-event duration expiry → summary + exit 6.

### Implementation for User Story 5

- [X] T030 [US5] `ListenerSession` + `InboundEvent` frozen dataclasses (`StopReason`, bound addresses, counts, ISO-8601 timestamps, `to_dict()`) in src/opskit/net/models.py per data-model.md — no payload field exists anywhere (FR-010)
- [X] T031 [US5] `Listener` in src/opskit/net/listener.py: bind wildcard on both available families (one AF_INET + one AF_INET6 socket; one family failing is tolerated, both failing errors), non-blocking sockets multiplexed via `selectors` with ~0.25 s poll timeout (Windows Ctrl-C works — R4), TCP `accept()`→record→**close unread**, UDP `recvfrom`→record→discard bytes, stop conditions (deadline/count) checked per tick, context manager + `events()` iterator + `session` summary per contracts/python-api.md; bind `EADDRINUSE`→`PortInUse`, `EACCES`/WinError 10013→`BindPermissionDenied`, other bind OSError→typed NetError (FR-012)
- [X] T032 [US5] Listener rendering in src/opskit/net/output.py (listening banner with escaped bound addresses, per-event lines with escaped peer strings, summary block) and thin Typer command `listen` in src/opskit/net/cli.py: positional PORT, `-u/--udp`, `--max-duration` (interval grammar), `--max-events`, `--json`/`--jsonl` (`result.kind: "event"` stream + `kind: "session"` summary); exit semantics per R4 — Ctrl-C/max-events → 0, duration expiry with ≥1 event → 0, with zero events → 6
- [X] T033 [P] [US5] Listener unit tests in tests/unit/test_net_listener.py: busy port (pre-bound socket) → PortInUse exit 12; permission denial (mocked bind EACCES + WinError-style) → BindPermissionDenied exit 13; max-events and max-duration stop reasons; event metadata capture; payload bytes never surface in events or output
- [X] T034 [US5] Loopback pairing integration in tests/integration/test_net_loopback.py: `Listener` ⇄ `net check` end-to-end in **both TCP and UDP** modes — exact peer metadata, clean stop, summary counts (SC-006); zero-event duration expiry → exit 6; interrupt (injected KeyboardInterrupt) → clean stop + summary, exit 0

**Checkpoint**: inbound direction closed — the definitive UDP answer exists

---

## Phase 8: User Story 6 - Use it from code (Priority: P3)

**Goal**: everything importable from `opskit.net` — typed results, typed errors, no printing/exiting; the documented example runs as written (SC-008).

**Independent Test**: quickstart US6 rows — the contracts/python-api.md example executes unmodified against loopback; ConnectRefused/ConnectTimeout catchable specifically.

### Implementation for User Story 6

- [X] T035 [US6] Finalize src/opskit/net/__init__.py `__all__` per contracts/python-api.md (existing names unchanged + `check`, `probe`, `Listener`, `parse_target`, models, enums, new errors); add a test executing the python-api.md usage example against loopback fixtures in tests/unit/test_net_api.py; assert the library layer never prints (capsys clean on API calls)

**Checkpoint**: API parity delivered

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T036 [P] Write src/opskit/net/README.md (command reference mirroring dns/tls READMEs: `net check`/`net probe`/`net listen` entries, options tables, verdict/exit-code matrix incl. 12–13, UDP-honesty section, JSON/NDJSON samples, library section with the api example) and add the `opskit net` rows + link in the root README.md Commands table (docs-coverage gate, Art. II)
- [X] T037 [P] Real-endpoint smoke tests `@pytest.mark.network` in tests/integration/test_net_network.py (example.com:443 open; TEST-NET filtered timeout; UDP inconclusive) — excluded from CI by default
- [X] T038 Run the full quickstart validation matrix + all gates on 3.9 and default: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pyright && uv run pytest` (coverage ≥ 90%); verify `net check`/`probe`/`listen` help text passes the docs-coverage gate; fix any drift
- [X] T039 Reconcile design docs with as-built reality (specs/003-net-diagnostics/contracts/, data-model.md) and update specs/001-dns-diagnostics/ + specs/002-tls-verification/ contracts' exit-code tables with codes 12–13 (shared enum documented once)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** depends only on Foundational; it is the MVP.
- **US2 (Phase 4)** depends on US1's api/cli/output existing (T010/T012/T011).
- **US3 (Phase 5)** depends on US1 (check classification reused per attempt); UDP probe rows also need US2 (T017/T018).
- **US4 (Phase 6)** depends on US1 + T007 (cliutils batch helpers); exercises US2's UDP path if present but doesn't require it.
- **US5 (Phase 7)** depends only on Foundational (T002/T003/T005/T008) — independent of US1–US4; its pairing test (T034) needs US1's `check` (and T018 for the UDP leg).
- **US6 (Phase 8)** depends on US1–US5 surfaces existing (finalizes exports).
- **Polish (Phase 9)** last; T036/T037 can start once US1 is stable.

### Key task-level dependencies

- T003 needs T002; T005 needs T004; T012 needs T007+T010+T011; T016 needs T008+T012.
- T018 needs T017+T010+T012; T020 needs T008+T018.
- T022 needs T010 (+T018 for UDP attempts); T024 needs T022+T023; T025 needs T012; T027 needs T008+T024.
- T028 needs T007+T012; T029 needs T028.
- T031 needs T003+T005; T032 needs T031; T034 needs T031+T012 (+T018 for UDP).
- T035 needs T010/T022/T031 public surfaces; T038 needs everything.

### Parallel Opportunities

- Phase 2: T003, T006, T007, T008 run in parallel after T002/T004/T005 land (T004→T005 sequential; T003 only needs T002).
- Phase 3: T011 parallel with T010; T013/T014/T015 parallel once their targets exist.
- Phase 4: T019 parallel with T018 (after T017).
- Phase 5: T021+T023 parallel; T026 parallel with T027.
- **US5 (T030–T034) is fully parallel with US2–US4** for a second contributor — disjoint files (listener.py vs udp.py/api.py) after Foundational.
- Phase 9: T036/T037 parallel.

## Parallel Example: User Story 1

```bash
# After T010 (api.check) exists, run in parallel:
Task: "T011 check rendering in src/opskit/net/output.py"
Task: "T013 API unit tests in tests/unit/test_net_api.py"
# After T012 (cli):
Task: "T014 CLI unit tests"  |  Task: "T015 output/escaping tests"
```

## Implementation Strategy

**MVP first**: Phases 1–3 (T001–T016) deliver a fully usable cross-platform TCP reachability
checker — stop, validate against quickstart US1, demo. **Incremental**: each subsequent phase
is an independently testable increment ending in a checkpoint; US2 (honest UDP) is the
highest-value follow-on, then US3/US4 turn it into a diagnosis/fleet tool, and US5 closes the
inbound loop. Commit per task or logical group with Conventional Commits (`feat(net): …`); run
the four gates before each commit batch (CLAUDE.md). Remember the platform-variance rules
baked into T016/T020/T031 — they are the difference between a green PR and a green `main`.
