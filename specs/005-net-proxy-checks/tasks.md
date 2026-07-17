# Tasks: Proxy-Aware Reachability Checks

**Input**: Design documents from `/specs/005-net-proxy-checks/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R11), data-model.md, contracts/cli.md,
contracts/python-api.md, quickstart.md

**Tests**: INCLUDED — the constitution mandates them (coverage ≥ 90%, testing-depth rules)
and the spec's success criteria are test-defined (SC-002, SC-004, SC-006, SC-007). Loopback
stand-in proxy tests are the gate; nothing real-network ever gates CI.

**Organization**: grouped by user story (spec.md US1–US5) so each story is independently
implementable and testable. Within stories: tests first (write → watch fail → implement).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5 (user-story phases only)

## Phase 1: Setup

**Purpose**: branch + working baseline (no new dependencies, no scaffolding needed — the
`net` category already exists)

- [X] T001 Create feature branch `005-net-proxy-checks` from up-to-date `main`; confirm the
      baseline is green with `uv run pytest -q`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared model/error/test infrastructure every story builds on

**⚠️ CRITICAL**: no user-story work until this phase completes

- [X] T002 Add `TUNNEL_DENIED = 18`, `PROXY_GATEWAY = 19`, `NOT_A_PROXY = 20` to the
      `ExitCode` enum in `src/opskit/core/exit_codes.py` (enum members only — `core` stays
      category-agnostic; data-model.md "Error hierarchy")
- [X] T003 Implement the `ProxyError` subtree (7 types: `ProxyResolutionError`,
      `ProxyConnectRefused`, `ProxyConnectTimeout`, `ProxyAuthRequired`,
      `ProxyTunnelDenied`, `ProxyGatewayError`, `ProxyProtocolError`), each owning its
      exit code and `code` string per data-model.md, in `src/opskit/net/errors.py`
      (depends on T002)
- [X] T004 [P] Implement `ProxySpec` (frozen dataclass, `password` field `repr=False`,
      redacted `display`/`__str__` = `user:***@host:port`, `authorization` property) and
      `parse_proxy()` (accepted forms + `UsageError` cases per data-model.md) and
      `proxy_exempt()` (exact/suffix/leading-dot/`*` matching, case-insensitive) in
      `src/opskit/net/models.py` (research R2, R3)
- [X] T005 Add `Route` frozen dataclass (`via`/`proxy`/`source`, `Route.direct()`,
      `Route.via_proxy()`, `to_dict()`) and extend `CheckResult` + `ProbeResult` with a
      `route` field defaulting to direct in `src/opskit/net/models.py` (after T004 — same
      file). The **always-present** `route` object (Q3 clarification) is emitted at
      envelope level by the CLI — result `to_dict()` deliberately excludes it so failed
      targets (`result: null`) retain their route in the envelope
- [X] T006 Extend the `Verdict` enum with `AUTH_REQUIRED`, `TUNNEL_DENIED`,
      `GATEWAY_FAILED`, `NOT_A_PROXY` in `src/opskit/net/models.py` and extend
      `verdict_for()`/`_ERROR_VERDICTS` in `src/opskit/net/api.py` to map the `ProxyError`
      subtree (after T003, T005)
- [X] T007 [P] Build the scriptable stand-in CONNECT proxy for tests in
      `tests/loopback_proxy.py`: threaded loopback server with per-scenario behaviors —
      `200` (then close), `407` with configurable `Proxy-Authenticate` schemes, `403`,
      `502`, `503`, `504`, garbage banner, accept-then-silence, accept-then-close — plus
      capture of the received CONNECT line and `Proxy-Authorization` header; pytest fixture
      wiring in `tests/conftest.py` (research R9)
- [X] T008 [P] Unit tests for the foundational models in
      `tests/unit/test_net_proxy_spec.py`: `parse_proxy` accepted/rejected forms (+
      Hypothesis property tests), `proxy_exempt` matrix, `ProxySpec.display`/`repr`
      redaction, `Route.to_dict()` shapes (write alongside T004/T005; must pass before the
      checkpoint)

**Checkpoint**: models, errors, exit codes, and the stand-in proxy exist — user stories can
begin (in parallel if staffed)

---

## Phase 3: User Story 1 — Check a port through the corporate proxy (Priority: P1) 🎯 MVP

**Goal**: `opskit net check target:port --proxy host:port` (or via env fallback) establishes
a CONNECT tunnel and reports open-via-proxy with route disclosure; `--direct` and NO_PROXY
exemptions work; no-proxy-anywhere behavior is byte-identical to today.

**Independent Test**: quickstart.md §4 + §6 — against the stand-in proxy, an open verdict
with route `{via: http-proxy, source: env:HTTPS_PROXY}`; with nothing nominated, existing
net tests pass unchanged and route reads `direct`.

### Tests for User Story 1 (write first, watch them fail)

- [X] T009 [P] [US1] Unit tests for `connect_via_proxy()` happy path + proxy-hop
      refused/timeout normalization against the stand-in proxy in
      `tests/unit/test_net_proxy.py` (classification of non-200 comes in US2 — keep this
      file's scope to: 200 → socket + `TunnelConnection`, refused → `ProxyConnectRefused`,
      silence → `ProxyConnectTimeout` after retries, `tunnel_ms` populated)
- [X] T010 [P] [US1] Unit tests for `check(proxy=...)` in
      `tests/unit/test_net_api_proxy.py`: route stamping (`http-proxy` vs `direct`
      default), proxy-hop `address`/`family` reported, str proxy accepted, library never
      reads env (monkeypatch env + assert no effect without explicit arg)
- [X] T011 [P] [US1] CLI tests in `tests/unit/test_net_cli_proxy.py`: `--proxy` wins over
      env; env order `HTTPS_PROXY` → `HTTP_PROXY` → `ALL_PROXY` with uppercase-then-
      lowercase pairs (monkeypatched environ); `--direct` forces direct; `--no-proxy` flag
      replaces `NO_PROXY` env; exemption target checked directly with route source
      `no-proxy-exemption`; route `source` provenance (`flag` / `env:HTTPS_PROXY`);
      `route` object present in every `--json` envelope including `direct/default`
      (contracts/cli.md)

### Implementation for User Story 1

- [X] T012 [US1] Implement `src/opskit/net/proxy.py`: `TunnelConnection` dataclass and
      `connect_via_proxy(proxy, host, port, *, timeout, retries, family)` — reach the proxy
      via `tcp.connect()` (re-raise its `ResolutionError`/`ConnectRefused`/`ConnectTimeout`
      as the `Proxy*` counterparts naming the proxy), send the CONNECT request
      (`Host:` header; `Proxy-Authorization` when credentials present), read/drain the
      response under the per-stage timeout, return the socket + `TunnelConnection` on 2xx;
      retry-on-silence loop per research R8 (full classification of non-2xx lands in US2 —
      raise a provisional `ProxyProtocolError` for any non-2xx here) (research R1)
- [X] T013 [US1] Extend `check()` in `src/opskit/net/api.py` with keyword-only
      `proxy: Optional[Union[ProxySpec, str]] = None`: parse str via `parse_proxy`, route
      through `connect_via_proxy` (TCP only), close the tunnel immediately after the
      verdict, stamp `route` on `CheckResult` (contracts/python-api.md)
- [X] T014 [US1] Implement `resolve_proxy_config()` in `src/opskit/net/cli.py` — the ONLY
      env reader: flag > `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` (upper-then-lowercase) >
      built-in direct; NO_PROXY/`--no-proxy` exemption list; returns (spec or None,
      source); wire new eager-annotation options `--proxy` / `--no-proxy` / `--direct`
      onto `check` (usage error for `--proxy` + `--direct`); decide route per target via
      `proxy_exempt` and pass an explicit proxy (or None) to the API (research R3, R10;
      **no `from __future__ import annotations`, `Optional[X]` spellings**)
- [X] T015 [US1] Render the proxied verdict in `src/opskit/net/output.py` and the CLI
      envelope builder in `src/opskit/net/cli.py`: `via <redacted-proxy> (<source>)` line +
      tunnel-time label in human mode (all proxy-derived strings through
      `rich.markup.escape()`); `route` object in every envelope; redact the `--proxy` echo
      in the envelope `query` block (contracts/cli.md "Envelope")
- [X] T016 [US1] Verify byte-compatibility: run the pre-existing net test suite unchanged
      (`uv run pytest tests/ -q -k "net and not proxy"`) and fix any direct-path
      regression; confirm direct human output is untouched and direct envelopes differ
      only by `route` (SC-006)

**Checkpoint**: MVP — proxied open verdicts + env/flag/exemption routing + disclosure work
end-to-end against the stand-in proxy

---

## Phase 4: User Story 2 — Tell whether the proxy or the target is the problem (Priority: P1)

**Goal**: every FR-009 outcome is a distinct verdict with its own wording, hint, and exit
class; definitive answers never retried.

**Independent Test**: quickstart.md §3 + integration matrix — induce each stand-in-proxy
behavior and observe six distinguishable outcomes with the contract's exit codes
(contracts/cli.md verdict table).

### Tests for User Story 2 (write first, watch them fail)

- [X] T017 [P] [US2] Extend `tests/unit/test_net_proxy.py` with the full R4 classification
      matrix against the stand-in proxy: 407 → `ProxyAuthRequired` (schemes parsed), 403 +
      other-4xx → `ProxyTunnelDenied`, 504 → `ProxyGatewayError` "target silent" flavor,
      502/503 → `ProxyGatewayError` "unreachable from proxy" flavor, garbage banner →
      `ProxyProtocolError`, definitive answers NOT retried (assert single request seen by
      the stand-in), silence retried exactly `retries` times
- [X] T018 [P] [US2] Exit-code and attribution tests in
      `tests/unit/test_net_cli_proxy.py`: each outcome exits with its contract code (0 / 3
      / 8 / 6 / 14 / 18 / 19 / 20); every failure message names the at-fault hop (assert
      "proxy" appears in proxy-hop wording, and the gateway wording states the proxy hop is
      healthy); hints match contracts/cli.md
- [X] T019 [P] [US2] Integration matrix in `tests/integration/test_net_proxy_loopback.py`:
      end-to-end CLI runs against the stand-in proxy for all FR-009 outcomes; proxy-hop
      refused-vs-timeout asserted as the **`ProxyError` class family** (cross-OS tolerance
      — CLAUDE.md rule); proxy-name resolution failure → `ProxyResolutionError` with a
      hint pointing at `opskit dns`

### Implementation for User Story 2

- [X] T020 [US2] Complete the CONNECT status classification in `src/opskit/net/proxy.py`
      per the research R4 table: status-line parse, 407 `Proxy-Authenticate` scheme
      extraction, 4xx/5xx/garbage mapping to the typed errors with the contract's wording
      and hints (504 vs 502/503 flavor wording; "does not behave like an HTTP proxy" for
      non-HTTP), all messages built from `ProxySpec.display`
- [X] T021 [US2] Wire verdict rendering for the new outcomes in
      `src/opskit/net/output.py` + error/hint pass-through in `src/opskit/net/cli.py`
      (stderr in human mode as today; envelopes carry `error.code` for every new type);
      confirm `verdict_for()` covers the whole subtree (extends T006)

**Checkpoint**: SC-002/SC-003 hold — any failed proxied check tells the user which hop to
investigate, by exit code and by wording

---

## Phase 5: User Story 3 — Authenticate to the proxy without leaking credentials (Priority: P2)

**Goal**: `user:pass@proxy:port` credentials authenticate via Basic; the password appears in
zero bytes of any output on any path; unsupported schemes are reported honestly.

**Independent Test**: quickstart.md §5 — `grep -c hunter2` over all outputs prints 0 while
the authenticated check succeeds against the credential-checking stand-in proxy.

### Tests for User Story 3 (write first, watch them fail)

- [X] T022 [P] [US3] Auth behavior tests in `tests/unit/test_net_proxy.py`: correct
      `Proxy-Authorization: Basic …` header sent (stand-in captures it; percent-decoded
      credentials, UTF-8); no header when no credentials; 407-with-`Basic` after wrong
      credentials → `ProxyAuthRequired` "rejected" wording; 407-with-only-`Negotiate` →
      message names the unsupported scheme (FR-015)
- [X] T023 [P] [US3] The redaction matrix in `tests/unit/test_net_proxy_redaction.py`:
      for EVERY FR-009 outcome × {human stdout+stderr, `--json`, `--jsonl`} with
      credentials supplied, assert the password string appears in **zero** outputs and the
      redacted display (`user:***@`) appears where the proxy is named; include the envelope
      `query` echo and exception `repr()`/`str()` paths (SC-004)

### Implementation for User Story 3

- [X] T024 [US3] Finalize credential handling in `src/opskit/net/proxy.py` +
      `src/opskit/net/models.py`: `authorization` property builds the Basic value at send
      time only (base64, UTF-8); confirm every message/hint/envelope path uses
      `ProxySpec.display`; fix any leak the T023 matrix finds

**Checkpoint**: authenticated proxies work; leakage is structurally impossible and
matrix-verified

---

## Phase 6: User Story 4 — Proxied checks keep every existing contract (Priority: P2)

**Goal**: batch (args/file/stdin), `probe`, `--watch`, JSON/JSONL, exit aggregation, and the
UDP guard all honor the established contracts with per-target routes.

**Independent Test**: spec US4 independent test — a mixed batch (open / denied /
gateway-failed / exempt-direct) through the stand-in proxy: every target reported with its
route, aggregate exit follows 0/uniform/7-PARTIAL.

### Tests for User Story 4 (write first, watch them fail)

- [X] T025 [P] [US4] Batch + contract tests in `tests/unit/test_net_cli_proxy.py`: mixed
      batch never aborts; one envelope per target incl. failures with per-target `route`
      (exempt target shows `direct`/`no-proxy-exemption`); aggregate exit 0 / uniform new
      codes (e.g. all-18 → 18) / mixed → 7; `--jsonl` one line per target;
      `--udp` + `--proxy` (and `--udp` + env proxy) → exit 2 usage error naming the
      UDP/CONNECT mismatch with the `--direct` hint, before any network activity
- [X] T026 [P] [US4] Probe + watch tests in `tests/unit/test_net_api_proxy.py` (probe
      semantics) and `tests/unit/test_net_cli_proxy.py` (watch signature): probe pre-flight
      resolves the **proxy** (unresolvable proxy fails before attempt 1); fresh tunnel per
      attempt (stand-in sees N CONNECTs); per-attempt timings = tunnel times; attempt
      verdicts use the new `Verdict` members; `ProbeResult.route` in summary + JSONL
      stream; watch change-signature includes `via`+`proxy` so a route flip flags
- [X] T027 [P] [US4] Extend `tests/integration/test_net_proxy_loopback.py` with the
      end-to-end mixed-batch scenario (SC-005 shape: open + denied + gateway + exempt) and
      a probe run against the stand-in proxy

### Implementation for User Story 4

- [X] T028 [US4] Add the UDP guard (`UsageError` pre-I/O when proxy is in force with
      `Protocol.UDP`, FR-007) in `src/opskit/net/api.py`/`src/opskit/net/models.py` and
      the CLI-side early rejection with the `--direct` hint in `src/opskit/net/cli.py`
- [X] T029 [US4] Extend `probe()` in `src/opskit/net/api.py` with the `proxy=` parameter:
      route decided once per run, pre-flight resolves the proxy instead of the target when
      proxied, fresh tunnel per attempt via `connect_via_proxy`, `route` stamped on
      `ProbeResult`; wire `--proxy`/`--no-proxy`/`--direct` onto the `probe` command in
      `src/opskit/net/cli.py` (contracts/cli.md "probe specifics")
- [X] T030 [US4] Ensure batch per-target routing + envelopes in `src/opskit/net/cli.py`
      (per-target `proxy_exempt` decision from T014 feeding `_check_envelope`; route in
      every envelope incl. failures) and extend the `--watch` change signature
      (`_check_signature`) with `via`+`proxy` (FR-019)

**Checkpoint**: all four contract families (batch, probe, watch, machine output) hold with
proxies — Art. IX gate satisfiable

---

## Phase 7: User Story 5 — Use it from code (Priority: P3)

**Goal**: the typed API surface is public, documented, and distinguishes proxy-hop from
target-side failures without ambient state.

**Independent Test**: contracts/python-api.md examples run as written from a scratch script
(SC-008).

### Tests for User Story 5

- [X] T031 [P] [US5] Public-surface tests in `tests/unit/test_net_api_proxy.py`: all new
      names importable from `opskit.net` (`ProxySpec`, `parse_proxy`, `proxy_exempt`,
      `Route`, the 8 error types); `except ProxyError` catches every proxy-hop failure and
      `ProxyGatewayError` is separable; keyword-only/default-compat (existing positional
      calls unaffected); no print/exit/env-read anywhere in the library paths (capsys +
      monkeypatched environ)

### Implementation for User Story 5

- [X] T032 [US5] Re-export the new public names from `src/opskit/net/__init__.py`
      (additive `__all__`) and add Google-style docstrings on every new public
      module/class/function (`proxy.py`, new models/errors/api params) satisfying the
      docs gate (Art. II)

**Checkpoint**: all five stories functional and independently verified

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T033 [P] Update `src/opskit/net/README.md`: "Checking through an HTTP proxy" section,
      `--proxy`/`--no-proxy`/`--direct` option rows for `check` and `probe`, verdict/exit
      table incl. codes 18–20 (and 14 reuse), env-variable order, NO_PROXY semantics,
      always-present `route` envelope field, redaction statement, worst-case timing note
      (research R11; docs-coverage gate)
- [X] T034 [P] Confirm root `README.md` Commands-table link and JSON-schema notes still
      hold (route field mention where the envelope is documented); no structural change
      expected
- [X] T035 Run the quickstart validation end-to-end per
      `specs/005-net-proxy-checks/quickstart.md` §§1–6 and fix anything that deviates
- [X] T036 Full local gates: `uv run ruff format . && uv run ruff check .`,
      `uv run mypy src`, `uv run pyright`, `uv run pytest -q` (coverage ≥ 90%); confirm
      no `# noqa`/`# nosec` added; scan test asserts for the CodeQL
      URL-substring-sanitization gotcha (assert on codes/IPs, not `"host.tld" in output`)
- [X] T037 Self-review against the plan's Constitution Check and the CLAUDE.md
      cross-cutting checklist (eager annotations in cli.py, escape() coverage, no raw
      OSError to CLI, batch rule, class-family asserts); then open the PR
      (`feat(net): proxy-aware reachability checks via HTTP CONNECT`) — squash-merge,
      Conventional-Commit title

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** blocks US2 (needs `connect_via_proxy` + CLI wiring) and is the MVP.
- **US2 (Phase 4)** needs US1's primitive; **US3 (Phase 5)** needs US2's 407 path.
- **US4 (Phase 6)** needs US1 (routing) — batch/watch parts can start right after US1,
  independent of US2/US3.
- **US5 (Phase 7)** anytime after the names it exports exist (practically: after US2).
- **Polish (Phase 8)** last; T033/T034 can be drafted any time after US4.

### Task-level notes

- T004 → T005 → T006 touch `net/models.py` sequentially (same file).
- T007 (stand-in proxy) gates every test task (T009–T011, T017–T019, T022–T023, T025–T027).
- T012 → T013 → T014 → T015 are sequential (primitive → API → CLI → rendering).
- Within each story, [P] test tasks run in parallel (different files), then implementation.

### Parallel Opportunities

- Phase 2: T004 ∥ T007 (T008 alongside T004/T005); T003 right after T002.
- After US1: US2 (one dev) ∥ US4's batch/probe work (another dev) — different test files,
  `cli.py` merge point at T030.
- All [P]-marked test-authoring tasks within a phase.

---

## Parallel Example: User Story 2

```bash
# After US1 checkpoint, author the three US2 test suites in parallel:
Task: "R4 classification matrix in tests/unit/test_net_proxy.py"            # T017
Task: "exit codes + attribution wording in tests/unit/test_net_cli_proxy.py" # T018
Task: "loopback integration matrix in tests/integration/test_net_proxy_loopback.py" # T019
# then implement T020 → T021 sequentially (proxy.py → output/cli wiring)
```

---

## Implementation Strategy

### MVP First (US1)

1. Phases 1–2 (setup + foundational), then Phase 3 (US1).
2. **STOP and VALIDATE**: quickstart §§2, 4, 6 — proxied open verdict, env precedence,
   byte-compat. That alone already fixes the original user pain ("everything times out on
   proxy-only networks") for the success path.

### Incremental Delivery

- US1 → verdicts for the happy path + routing (MVP, demoable)
- US2 → the diagnostic core: six distinguishable failure outcomes (this is the release-worthy
  cut — SC-002/SC-003)
- US3 → authenticated proxies + the redaction matrix (required before shipping to users with
  credentials — Art. III)
- US4 → batch/probe/watch/UDP-guard contract completion (required for the Art. IX gate)
- US5 + Polish → public API exports, docs gate, full quality gates, PR

Realistic ship point: **all phases** — Arts. II/III/IX make US3/US4/T033 mandatory before
merge; the story split is for ordering and independent validation, not for shipping partial.
