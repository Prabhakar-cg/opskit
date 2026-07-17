# Implementation Plan: Proxy-Aware Reachability Checks

**Branch**: `005-net-proxy-checks` | **Date**: 2026-07-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-net-proxy-checks/spec.md`

## Summary

Teach `opskit net check` and `opskit net probe` to test reachability **through an HTTP proxy**
via an HTTP CONNECT tunnel, so proxy-only egress networks stop reporting every target as
TIMEOUT. New work: a `net/proxy.py` CONNECT primitive built on the existing `tcp.connect()`
(hand-rolled minimal HTTP, zero new dependencies — R1); a `ProxySpec` parsed/redacted-by-
construction model (R2); a CLI-only env/flag resolution helper implementing the clarified
`--proxy` > `HTTPS_PROXY` → `HTTP_PROXY` → `ALL_PROXY` > direct precedence with NO_PROXY
exemptions (R3); a `ProxyError` subtree with three additive exit codes (18 TUNNEL_DENIED,
19 PROXY_GATEWAY, 20 NOT_A_PROXY; 407 reuses AUTH_FAILED 14 — R4/R5); and an always-present
`route` object in every check/probe envelope (R6). UDP + proxy is a pre-flight usage error.
`net listen` and the `tls`/`dns` categories are untouched. Technical decisions in
[research.md](research.md) (R1–R11).

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor)

**Primary Dependencies**: none new — stdlib `socket` + `urllib.parse` (spec parsing) +
`base64` (Basic header); existing typer/rich for the CLI layer.

**Storage**: N/A (stateless diagnostics; credentials held in memory only, never persisted)

**Testing**: pytest (+ Hypothesis for `parse_proxy`/`proxy_exempt`); in-process threaded
loopback stand-in proxy scriptable per FR-009 outcome (200/407/403/502/504/garbage/silence);
monkeypatched-environ CLI fallback tests; redaction asserted across every verdict × output
format; proxy-hop refused-vs-timeout asserted as error class family (cross-OS);
`@pytest.mark.network` real-proxy smoke excluded from CI; coverage ≥ 90%. (R9)

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13)

**Performance Goals**: proxied verdict < 10 s at defaults (SC-001); per-attempt timeout 5 s
applied per stage (proxy connect, CONNECT exchange — R8), retries 2 on silence only

**Constraints**: read-only/zero-telemetry — outbound traffic is exactly one connection to the
user-nominated proxy plus the CONNECT request (nothing ever sent through the tunnel); library
layer never prints/exits/reads env (proxy is an explicit argument — R7); `core` stays
category-agnostic (gains only three `ExitCode` enum members); credentials redacted by
construction (R2); no scanning/relaying affordances

**Scale/Scope**: zero new commands (a mode on `check`/`probe`); 7 new error types; 3 additive
exit codes; 1 new primitive module; batch behavior unchanged (~hundreds of targets)

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, no violations.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(net)` commits. PASS |
| II Documentation completeness | No new commands (docs gate keys on commands, all documented); `src/opskit/net/README.md` gains the proxy section, new options, exit codes 18–20, and the `route` envelope field; Google-style docstrings on all new public API (R11). PASS |
| III Zero security compromise | No new dependencies; credentials redacted **by construction** (`ProxySpec.password` is `repr=False`; only the redacted `display` ever renders — R2) and verified by a redaction test matrix (SC-004); Basic header built only at send time; proxy-derived strings `escape()`d before markup. PASS |
| IV Dependency freshness | No dependency changes. PASS |
| V Strict SemVer | Additive only → MINOR: new options, new exit codes 18–20, new `opskit.net` API surface, one always-present envelope field (`route`) under unchanged `schema_version "1"` (Q3 clarification); direct-check human output byte-identical (SC-006). PASS |
| VI Pure-Python parity | stdlib CONNECT implementation (R1) — no shelling out, no platform proxy discovery (`getproxies()` rejected for platform magic — R3); proxy-hop `OSError`s normalized through the existing `tcp.connect` path into typed errors. PASS |
| VII CLI/API parity, typed core | All logic in `opskit.net` typed API (`proxy=` params, `parse_proxy`, `proxy_exempt`, `connect_via_proxy`); env/NO_PROXY read **only** in `net/cli.py` (R3/R7); each new error owns its exit code; `core` gains only enum members. PASS |
| VIII Zero telemetry | The proxy is user-designated (flag/env/config); traffic is exactly the proxy connection + CONNECT request for the user's target; nothing else contacted, nothing sent through the tunnel. PASS |
| IX Output contract | `route` in every envelope (always present — Q3); batch rule unchanged (every target processed, per-item failures in JSON, 0/uniform/7 PARTIAL) with new codes participating in uniformity; NO_COLOR via `make_console`; NDJSON unchanged shape + additive field. PASS |
| X Diagnostic-only scope | One user-chosen hop to explicit user-listed targets; no proxy discovery/probing, no relaying, no CIDR/ranges (FR-022); CONNECT sends no payload — equivalent diagnostic footprint to the direct check. PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] No new dependencies (nothing to audit; lock untouched).
- [x] Commands ship tests + docs; output/exit-code contract extended additively only.
- [x] No secrets committed; proxy specs validated before any socket I/O; credentials redacted
      in all output; read-only, zero-telemetry scope preserved (Arts. VIII, X above).
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact).
- [x] SECURITY.md, branch protection, Dependabot unchanged.

**New-category cross-cutting checklist** (CLAUDE.md — applies to category *changes* too):
- [x] `net/cli.py` already uses eager annotations + `Optional[X]`; new options follow suit
      (no `from __future__ import annotations` there; new `proxy.py` keeps future annotations).
- [x] All proxy-derived strings (proxy host, redacted display, CONNECT status reasons,
      `Proxy-Authenticate` scheme names) pass `rich.markup.escape()` before markup output;
      consoles via `make_console` (default `no_color=None`); `typer.echo` paths unescaped.
- [x] Proxy-hop socket errors normalized via the existing `tcp.connect` path; CONNECT-stage
      raw `OSError`/`socket.timeout` caught in `proxy.py` and re-raised as `ProxyError`
      subclasses with actionable hints; each owns its exit code; `core` untouched by category
      types (R4/R5).
- [x] Batch: unchanged `collect_outcomes`/`aggregate_exit` flow — proxied failures are just
      typed errors; every target processed; envelopes for all targets incl. failures; new
      exit codes participate in the uniform-class rule.
- [x] Docs-coverage gate: commands unchanged; `src/opskit/net/README.md` updated with the
      proxy mode (R11) — gate stays green by construction.
- [x] Cross-OS variance: proxy-hop refused-vs-timeout tests assert the `ProxyError` class
      family, not one subclass (Windows times out where Linux refuses on closed loopback
      ports); stand-in proxy runs on loopback everywhere; no real-network CI dependency (R9).

## Project Structure

### Documentation (this feature)

```text
specs/005-net-proxy-checks/
├── plan.md              # This file
├── research.md          # Phase 0 output (R1–R11)
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # --proxy/--no-proxy/--direct surface, verdicts, exit codes, route envelope
│   └── python-api.md    # ProxySpec, parse_proxy, proxy_exempt, connect_via_proxy, check/probe params
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/
├── core/
│   └── exit_codes.py    # + TUNNEL_DENIED=18, PROXY_GATEWAY=19, NOT_A_PROXY=20 (additive enum only)
└── net/
    ├── __init__.py      # + re-export ProxySpec, parse_proxy, proxy_exempt, proxy errors (additive)
    ├── README.md        # + "Checking through an HTTP proxy" section, options, exit codes, route field
    ├── errors.py        # + ProxyError subtree (7 types, each owning its exit code — R5)
    ├── models.py        # + ProxySpec (redacted-by-construction), Route, parse_proxy, proxy_exempt;
    │                    #   CheckResult/ProbeResult gain route (default direct); UDP+proxy guard
    ├── proxy.py         # NEW — connect_via_proxy(): tcp.connect to proxy + CONNECT exchange,
    │                    #   status classification per R4, retry-on-silence per R8
    ├── api.py           # check()/probe() gain proxy=... param; route stamped on results (R7)
    ├── cli.py           # check/probe: --proxy / --no-proxy / --direct (eager annotations);
    │                    #   resolve_proxy_config() env fallback (R3) — the ONLY env reader
    └── output.py        # via-proxy line, tunnel-time label, route rendering (escape()d)

tests/
├── unit/
│   ├── test_net_proxy_spec.py    # parse_proxy + proxy_exempt (+ Hypothesis); redacted display
│   ├── test_net_proxy.py         # connect_via_proxy classification vs scripted stand-in (R4 table)
│   ├── test_net_api_proxy.py     # check/probe with proxy=: route stamping, UDP guard, retries
│   ├── test_net_cli_proxy.py     # env fallback order, --direct/--no-proxy, provenance, envelopes
│   └── test_net_proxy_redaction.py  # password appears in ZERO outputs: every verdict × format
├── integration/
│   └── test_net_proxy_loopback.py   # stand-in proxy end-to-end: all FR-009 outcomes, batch
│                                    #   mixing exempt+proxied, class-family asserts (cross-OS)
└── helpers (with existing loopback servers)
    └── stand-in proxy               # threaded scriptable CONNECT responder (R9)
```

**Structure Decision**: a mode of the existing `net` category, grown in place — `proxy.py` is
a sibling primitive to `tcp.py`/`udp.py` under the same `models/api/cli/output` layout; no
new command, no new category, no `tls`/`dns`/`listen` changes. `core` changes are three
additive enum members. Env/NO_PROXY resolution lives exclusively in `net/cli.py` so the
library stays explicit-arguments-only (Art. VII); the profile/config-file rungs of the
precedence chain don't exist in the codebase yet and slot into that single helper when
config support lands (R3).

## Complexity Tracking

No constitutional violations — table not required.
