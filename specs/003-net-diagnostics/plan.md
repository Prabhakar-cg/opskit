# Implementation Plan: Network Connectivity Diagnostics

**Branch**: `003-net-diagnostics` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-net-diagnostics/spec.md`

## Summary

Promote the existing library-only `opskit.net` package (built as the reusable seam by the TLS
feature, per its FR-018) into a full CLI category with three commands: `opskit net check`
(single-shot TCP/UDP port verdict — open / refused / timeout-filtered / unresolvable, plus UDP's
honest open / closed / inconclusive — batchable and watchable), `opskit net probe` (ping-style
repeated probes with per-attempt timings and min/avg/max statistics), and `opskit net listen`
(nc-style temporary listener reporting inbound connections/datagrams, metadata only). Zero new
runtime dependencies — stdlib sockets throughout. TCP connect logic already exists
(`net/tcp.py`); new work is the UDP probe primitive, the listener, the models/API/CLI/output
layers, two additive exit codes for bind failures, and small additive extensions to
`core/cliutils.py` (variadic targets, stdin via `--input-file -`). Technical decisions in
[research.md](research.md) (R1–R9).

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor)

**Primary Dependencies**: none new — stdlib `socket`/`select`/`threading`/`time` only; existing
typer/rich for the CLI layer. (The category with zero dependency risk.)

**Storage**: N/A (stateless diagnostics)

**Testing**: pytest (+ Hypothesis for the `host:port` target parser); in-process loopback TCP
and UDP servers/sockets for real socket paths; injected fakes for timeout/filtered paths;
`@pytest.mark.network` smoke excluded from CI; coverage ≥ 90%. Closed-port refused-vs-timeout
platform variance asserted as error *class family* (the canonical `net` lesson from CLAUDE.md).

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13)

**Project Type**: library + CLI (existing single-project `src/` layout)

**Performance Goals**: single check verdict < 10 s at defaults (SC-001); default per-attempt
timeout 5 s, retries 2 (consistent with dns/tls); probe default count 4, interval 1 s

**Constraints**: read-only/zero-telemetry — outbound traffic is exactly the user-requested
connection attempt (TCP: no application data; UDP: one empty probe datagram, no protocol
payload); listener binds only the user's port and never sends; library layer never
prints/exits; `core` stays category-agnostic; no scanning affordances (no port/address ranges)

**Scale/Scope**: three new CLI commands, ~4 new error types, 2 additive exit codes (12, 13),
batch files of ~hundreds of targets, listener sessions of ~hours with ~thousands of events

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, no violations.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(net)` commits. PASS |
| II Documentation completeness | All three commands ship `--help` + `src/opskit/net/README.md` linked from the root README Commands table (docs-coverage gate enforces); Google-style docstrings on all public API. PASS |
| III Zero security compromise | No new dependencies at all; no secrets; listener never renders payload bytes (metadata only, and peer strings are `escape()`d). PASS |
| IV Dependency freshness | No dependency changes. PASS |
| V Strict SemVer | New exit codes 12–13, new `opskit.net` API surface, new commands — all **additive** → MINOR. Existing `opskit.net` public names (`resolve`, `connect`, models, errors) unchanged. PASS |
| VI Pure-Python parity | stdlib sockets only; no shelling out to ping/nc/telnet; raw `OSError`/`ConnectionResetError` variants (incl. Windows `WSAECONNRESET` on UDP) normalized into the shared hierarchy (R2, R4). PASS |
| VII CLI/API parity, typed core | All logic in `opskit.net` typed API (`check`, `probe`, `Listener`); `net/cli.py` is a thin client; each new error type owns its exit code; `core` receives only additive `ExitCode` members + category-agnostic `cliutils` extensions (variadic targets, stdin) — no category imports. PASS |
| VIII Zero telemetry | Traffic is exactly the requested probe/connection; the UDP probe datagram is empty (no payload, no identifying data); listener is receive-only. PASS |
| IX Output contract | Human + versioned `--json`/`--jsonl` on all three commands; NDJSON streams per-target (check), per-attempt (probe), per-event (listen); NO_COLOR via `make_console`; batch rule (process all, per-item failures in JSON, 0/uniform/PARTIAL) via existing `cliutils.aggregate_exit`. PASS |
| X Diagnostic-only scope | Explicit, user-listed endpoints only — **no port ranges, no CIDR expansion, no host discovery** (spec FR-019); the temporary listener is the exact Art. X-sanctioned example (foreground, single port, sends nothing, metadata only, always stops). PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes needed).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] No new dependencies (nothing to audit; lock untouched except version bump at release).
- [x] New commands ship tests + docs and preserve the output/exit-code contract (additive only).
- [x] No secrets committed; targets/ports validated before any socket I/O; read-only,
      zero-telemetry scope preserved (Arts. VIII, X — see principle rows above).
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact).
- [x] SECURITY.md, branch protection, Dependabot unchanged.

**New-category cross-cutting checklist** (CLAUDE.md — baked in from the start):
- [x] `src/opskit/net/cli.py` uses **eager** annotations + `Optional[X]`/`List[X]` — no
      `from __future__ import annotations` (every other new module keeps future annotations).
- [x] All network/user-derived strings (hostnames, resolved addresses, peer addresses, batch
      headers) pass `rich.markup.escape()` before markup output; consoles via `make_console`
      with default `no_color=None`; `typer.echo` paths stay unescaped.
- [x] UDP/listener socket code catches raw `OSError` families (`ConnectionResetError`,
      `EADDRINUSE`, `EACCES`/`WinError 10013`, ICMP-unreachable surfacings) and re-raises typed
      `NetError` subclasses with actionable hints; each owns its exit code; `core` untouched by
      category types (R2, R4).
- [x] `net check` batch: every target processed via `collect_outcomes`; aggregate via
      `aggregate_exit` (0 / uniform / 7 PARTIAL); JSON envelope for every target incl. failures.
      `net probe` applies the same aggregate rule across attempts.
- [x] Docs-coverage gate: `net check`/`net probe`/`net listen` entries in
      `src/opskit/net/README.md`, linked from the root README Commands table.
- [x] Cross-OS variance handled by design: closed-loopback-port TCP tests assert the
      `NetError` class family (refused on Linux/macOS vs timeout on Windows); UDP
      closed-vs-inconclusive tests tolerate `{UdpClosed, UdpInconclusive}` where ICMP delivery
      is platform-dependent; listener Ctrl-C uses a poll loop so Windows interrupts work (R4, R6).

## Project Structure

### Documentation (this feature)

```text
specs/003-net-diagnostics/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # net check/probe/listen surface, options, exit codes, envelopes
│   └── python-api.md    # opskit.net public API contract (additive over the existing surface)
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/
├── cli.py               # + register net sub-app (one line)
├── core/
│   ├── exit_codes.py    # + PORT_IN_USE=12, BIND_PERMISSION=13 (additive)
│   └── cliutils.py      # + variadic-positional target collection; `--input-file -` = stdin
│                        #   (category-agnostic, additive; existing signatures preserved)
└── net/                 # EXISTING library-only package → becomes the full category
    ├── __init__.py      # + re-export check, probe, Listener, new models/errors (additive)
    ├── README.md        # NEW — command reference (linked from root README Commands table)
    ├── errors.py        # + UdpClosed(8), UdpInconclusive(6), PortInUse(12), BindPermissionDenied(13)
    ├── tcp.py           # EXISTING resolve()/connect() — gains optional `family` param (additive)
    ├── udp.py           # NEW — udp_probe(): connected-UDP empty-datagram probe (R2)
    ├── listener.py      # NEW — Listener: poll-loop accept/recv, stop conditions, event iterator (R4)
    ├── models.py        # NEW — parse_target (port required), CheckResult, ProbeAttempt,
    │                    #   ProbeResult, ListenerSession, InboundEvent (frozen dataclasses)
    ├── api.py           # NEW — check(), probe() orchestration over tcp/udp primitives
    ├── cli.py           # NEW — thin Typer sub-app: check / probe / listen (eager annotations)
    └── output.py        # NEW — category-owned rich rendering (escape() on all external strings)

src/opskit/tls/
└── models.py            # parse_target delegates host:port splitting to the shared helper
                         # moved into net/models.py (behavior unchanged; tests prove it)

tests/
├── unit/
│   ├── test_net_target.py       # host:port/[v6]:port parsing, port-required rule (+ Hypothesis)
│   ├── test_net_udp.py          # udp_probe outcomes with injected/mocked sockets
│   ├── test_net_api.py          # check()/probe() orchestration, family restriction, stats math
│   ├── test_net_listener.py     # bind errors, stop conditions, event capture (loopback)
│   ├── test_net_cli.py          # CLI: options, envelopes, exit codes, batch, stdin, watch
│   └── test_net_output.py       # rendering incl. markup escaping of peer/host strings
└── integration/
    └── test_net_loopback.py     # real sockets: open TCP, closed port (class-family assert),
                                 # UDP echo→open, UDP closed→{closed|inconclusive},
                                 # listener⇄check pairing end-to-end (TCP + UDP)
```

**Structure Decision**: completes the established category pattern by growing the existing
`opskit/net` package in place — `tcp.py` (already shipped for TLS) is reused untouched apart
from an additive `family` parameter, and the new `udp.py`/`listener.py` primitives sit beside
it under the same `api.py`/`cli.py`/`models.py`/`output.py` layout as `dns` and `tls`. `core`
changes are strictly additive and category-agnostic (two `ExitCode` members; `cliutils`
variadic-targets + stdin support usable by every category). The tricky bracket-aware
`host:port` splitting is extracted from `tls/models.py` into `net/models.py` and `tls`
delegates to it, eliminating a duplicate parser (tls behavior and tests unchanged).

## Complexity Tracking

No constitutional violations — table not required.
