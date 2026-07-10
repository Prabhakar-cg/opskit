# Implementation Plan: Active Directory / LDAP Diagnostics

**Branch**: `004-ad-diagnostics` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-ad-diagnostics/spec.md`

## Summary

New `opskit ad` category with five commands: `ad check` (staged reach/secure/authenticate
verdict against a server or a domain with SRV-based DC discovery), `ad user` (account-status
verdict: enabled/locked/password- and account-expiry — batchable and watchable), `ad groups`
(direct or effective membership with nesting, cycles, and the primary group resolved),
`ad member` (explicit is-P-in-G verdict with the granting chain), and `ad show` (read-only
key-attribute lookup of named users/groups/computers — user email/contact facts and group
member lists both first-class; batchable with mixed types). List-shaped human output renders
as rich tables (spec FR-015 amendment 2026-07-10). First category shipped as an extra:
`pip install opskit[ad]` pulls `ldap3` (the only new runtime dependency, confined to one
adapter module); the sub-app registers and renders help without it, and commands fail with an
actionable install hint. Connection security is LDAPS-by-default with `--starttls` and an
explicit `--plaintext` opt-in; credentials come from prompt/env only and are redacted
everywhere (test-enforced). Four additive exit codes (14–17); connection/TLS failures reuse
the existing `net`/`tls` error classes for cross-category consistency. Technical decisions in
[research.md](research.md) (R1–R10).

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor)

**Primary Dependencies**: `ldap3>=2.9,<3` as the **first category extra** (`opskit[ad]`;
PLAN.md's reserved slot) — pure-Python LDAP client; low release activity is assessed and
mitigated in R1 (adapter isolation, pinned major, pip-audit-clean) and logged under
Complexity Tracking. Reuses in-tree `opskit.dns` (SRV discovery via existing dnspython),
`opskit.net` errors (connection classes), `opskit.tls` errors (certificate classes), stdlib
`ssl` (via ldap3's Tls wrapper). No other new dependencies.

**Storage**: N/A (stateless diagnostics; nothing cached or persisted)

**Testing**: pytest (+ Hypothesis for FILETIME/UAC/identifier parsing); ldap3's built-in
**offline mock server** (`MOCK_SYNC` strategy) as the injected-directory layer covering every
directory outcome (status permutations, nesting/cycles, paging, bind failures, missing
attributes); in-process **loopback sockets** for the connection stages (refused/timeout, and
the existing self-signed loopback TLS server for certificate failure); `@pytest.mark.network`
for a real DC, never gating CI; coverage ≥ 90%. Redaction verified by a suite-wide scan for
the test password in captured outputs (SC-006).

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13)

**Project Type**: library + CLI (existing single-project `src/` layout)

**Performance Goals**: complete status verdict < 10 s at defaults (SC-001); default network
timeout 5 s (consistent with dns/net/tls); one authenticated session reused across a whole
batch (SC-004) and across an `AdClient`'s lifetime

**Constraints**: strictly read-only (bind + search operations only — no add/modify/delete
ever issued); credentials never in any output/log/envelope (Art. III, FR-004); encrypted by
default, cleartext credentials only behind `--plaintext` (FR-002); no enumeration/filter
affordances — named principals only (Art. X, FR-020); library layer never prints/exits;
`core` stays category-agnostic; base install stays slim (ldap3 only via the extra)

**Scale/Scope**: five new CLI commands, ~7 new error types, 4 additive exit codes (14–17),
one new runtime dependency (extra-scoped), batch files of ~hundreds of principals, effective
membership over directories with deep nesting (cycle-safe BFS)

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, one documented risk
(ldap3 maintenance posture) justified under Complexity Tracking.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(ad)` commits. PASS |
| II Documentation completeness | All five commands ship `--help` + `src/opskit/ad/README.md` linked from the root README Commands table (docs-coverage gate enforces — commands register even without ldap3, so the gate sees them); Google-style docstrings on all public API. PASS |
| III Zero security compromise | Password only via hidden prompt or `OPSKIT_AD_PASSWORD` env — **no `--password` flag** (process lists/shell history); never echoed, logged, or emitted in any envelope (query echo records the bind account name only); suite-wide redaction scan (SC-006). LDAP filter values escaped via ldap3's `escape_filter_chars` (injection-proof, R6). ldap3 passes pip-audit (no known CVEs). PASS |
| IV Dependency freshness | ldap3 is pure Python, MIT, not EOL, pip-audit-clean, but low release activity — the only viable pure-Python LDAP client; risk mitigated by confining it to one adapter module (`ad/directory.py`) behind typed wrappers and pinning `<3`. Justified in Complexity Tracking + R1. PASS (with documented justification) |
| V Strict SemVer | New exit codes 14–17, new `opskit.ad` API, new commands, new extra — all **additive** → MINOR. No existing surface changes. PASS |
| VI Pure-Python parity | ldap3 is pure Python; no shelling out to `Get-ADUser`/`ldapsearch`/`net user`; ldap3's socket/TLS exceptions normalized into the shared hierarchy (reusing `net`/`tls` classes) so refused/timeout/cert failures classify identically everywhere (R3). PASS |
| VII CLI/API parity, typed core | All logic in `opskit.ad` typed API (functions + `AdClient` session); `ad/cli.py` is a thin client; each error type owns its exit code; `core` gains only 4 additive `ExitCode` members — no category imports (in-tree reuse is ad→dns/net/tls, matching the established tls→net direction). ldap3 is untyped: confined to `ad/directory.py` with typed wrappers + a scoped mypy/pyright override so `--strict` holds everywhere else (R9). PASS |
| VIII Zero telemetry | Network activity is exactly: the SRV discovery lookup the user's domain argument implies (system resolver, same as `opskit dns`) and the LDAP conversation with the chosen server. Nothing else. PASS |
| IX Output contract | Human + versioned `--json` on all five commands, `--jsonl` on the batchable `ad user` **and `ad show`**; list-shaped human output as rich tables (FR-015); NO_COLOR via `make_console`; batch rule via existing `collect_outcomes`/`aggregate_exit` (0/uniform/7 PARTIAL, per-name envelopes incl. failures); `--watch` on `ad user` via `run_or_watch`. PASS |
| X Diagnostic-only scope | Read-only bind+search only; **named principals only — no filter expressions, no wildcards, no dump-all affordances** (FR-013/FR-020); one credential per invocation (no spraying, FR-003); no unlock/reset/modify. The Art. X-sanctioned "read-only AD queries with the operator's own credentials", exactly. PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes needed; existing matrix covers the new tests).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] New dependency `ldap3` passes pip-audit today; added **pinned** (`>=2.9,<3`) via the `ad` extra and the uv lock; Dependabot will track it. Maintenance-posture risk documented (Complexity Tracking).
- [x] New commands ship tests + docs and preserve the output/exit-code contract (additive only).
- [x] No secrets committed; identifiers/filters escaped before any directory I/O; read-only, zero-telemetry scope preserved (Arts. VIII, X — see principle rows above).
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact; the extra is declared in `pyproject.toml` only).
- [x] SECURITY.md, branch protection, Dependabot unchanged.

**New-category cross-cutting checklist** (CLAUDE.md — baked in from the start):
- [x] `src/opskit/ad/cli.py` uses **eager** annotations + `Optional[X]`/`List[X]` — no
      `from __future__ import annotations` (every other new module keeps future annotations).
- [x] All directory-derived strings (DNs, names, descriptions, server hosts, SRV targets,
      bind-account echo) pass `rich.markup.escape()` before markup output; consoles via
      `make_console` with default `no_color=None`; `typer.echo` paths stay unescaped.
- [x] ldap3's exception zoo (`LDAPSocketOpenError`, `LDAPSocketReceiveError`,
      `LDAPBindError`, `LDAPInvalidCredentialsResult`, SSL errors, raw `OSError`) is
      normalized in `ad/directory.py` into typed errors with actionable hints — reusing
      `net.ConnectRefused`/`ConnectTimeout` and `tls.HandshakeError`/`CertificateInvalid` for
      the shared classes, new `ad` errors for auth/permission/not-found (R3); each owns its
      exit code; `core` untouched by category types.
- [x] `ad user` and `ad show` batch: every name processed via `collect_outcomes` over one
      session; aggregate via `aggregate_exit` (0 / uniform / 7 PARTIAL); JSON envelope for
      every name incl. failures.
- [x] Docs-coverage gate: `ad check`/`ad user`/`ad groups`/`ad member`/`ad show` entries in
      `src/opskit/ad/README.md`, linked from the root README Commands table.
- [x] Cross-OS variance handled by design: connection-stage classification reuses the proven
      `net` classes and loopback tests assert the error *class family* where platforms differ
      (closed loopback port: refused on Linux/macOS, may time out on Windows); all
      directory-semantics tests run on the offline mock (platform-independent); interrupt
      paths (`ad user --watch`, password prompt) use existing `cliutils`/typer machinery
      already proven on Windows.

## Project Structure

### Documentation (this feature)

```text
specs/004-ad-diagnostics/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # ad check/user/groups/member/show surface, options, exit codes, envelopes
│   └── python-api.md    # opskit.ad public API contract (all additive)
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
pyproject.toml           # + [project.optional-dependencies] ad = ["ldap3>=2.9,<3"];
                         #   dev extra gains ldap3 (tests need it); mypy/pyright overrides
                         #   for the untyped ldap3 module (scoped, not global)

src/opskit/
├── cli.py               # + register ad sub-app (one line; safe without ldap3)
├── core/
│   └── exit_codes.py    # + AUTH_FAILED=14, PERMISSION_DENIED=15, NOT_FOUND=16,
│                        #   NOT_MEMBER=17 (additive)
└── ad/                  # NEW category package
    ├── __init__.py      # re-exports (functions, AdClient, models, errors) — no ldap3 import
    ├── README.md        # NEW — command reference (linked from root README Commands table)
    ├── errors.py        # AdError base + DependencyMissing(2), DiscoveryError(3),
    │                    #   AuthenticationFailed(14), PermissionDenied(15),
    │                    #   PrincipalNotFound(16), AmbiguousPrincipal(2), CleartextRefused(2)
    ├── models.py        # frozen dataclasses: DirectoryConfig, ConnectivityReport (stages),
    │                    #   AccountStatusReport, MembershipReport/Entry, MembershipVerdict,
    │                    #   ObjectSummary (+ to_dict), identifier-kind detection
    ├── attributes.py    # AD attribute semantics: FILETIME↔datetime (sentinels → "never"),
    │                    #   userAccountControl bits, msDS-* computed-attribute readers,
    │                    #   SID parsing + primary-group SID derivation (pure functions)
    ├── discovery.py     # SRV-based DC discovery via opskit.dns (_ldap._tcp.dc._msdcs.<dom>,
    │                    #   fallback _ldap._tcp.<dom>), priority/weight ordering
    ├── directory.py     # THE ONLY MODULE IMPORTING ldap3 (lazy adapter): connect stages,
    │                    #   bind, paged+ranged search, error normalization → typed hierarchy
    ├── api.py           # logic: check(), user_status(), membership(), is_member(), show(),
    │                    #   AdClient (session reuse); lazy-imports directory.py
    ├── cli.py           # thin Typer sub-app: check/user/groups/member/show (eager annotations)
    └── output.py        # category-owned rich rendering (escape() on all directory strings)

tests/
├── unit/
│   ├── test_ad_attributes.py   # FILETIME/sentinels/UAC bits/SID math (+ Hypothesis)
│   ├── test_ad_models.py       # identifier-kind detection, to_dict shapes
│   ├── test_ad_discovery.py    # SRV ordering, fallback, no-record → DiscoveryError (mock dns)
│   ├── test_ad_api.py          # status/membership/member/show logic over the mock directory
│   ├── test_ad_cli.py          # CLI: options, envelopes, exit codes, batch, prompt/env
│   │                           #   password, missing-extra hint, redaction scan
│   └── test_ad_output.py       # rendering incl. markup escaping of DNs/names
└── integration/
    ├── test_ad_mock_directory.py  # ldap3 MOCK_SYNC fixture directory: status permutations,
    │                              #   nesting/cycles/primary group, paging, bind failures
    ├── test_ad_loopback.py        # real sockets: refused/timeout stage classification
    │                              #   (class-family assert), self-signed TLS → CertificateInvalid
    └── test_ad_network.py         # @pytest.mark.network — real DC smoke, never gates CI
```

**Structure Decision**: follows the established category pattern (`api.py`/`cli.py`/
`models.py`/`output.py`/`errors.py` + README) with two ad-specific additions: `directory.py`
as the **single ldap3 adapter** (lazy-imported so the base install works without the extra,
and the untyped dependency is quarantined behind typed wrappers) and `attributes.py` for the
pure-function AD semantics (FILETIME, UAC, SIDs) that carry most of the unit-test weight.
In-tree reuse follows the existing dependency direction: `ad` imports `opskit.dns` (SRV
discovery), `opskit.net` errors and `opskit.tls` errors (shared outcome classes) — never the
reverse. `core` changes are strictly additive and category-agnostic (four `ExitCode`
members).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| ldap3's low release activity strains Art. IV's "no unmaintained libraries" reading | It is the only maintained-enough **pure-Python** LDAP client (Art. VI forbids C-binding alternatives), is MIT-licensed, has no known CVEs (pip-audit-clean), and remains the ecosystem default for exactly this use | `python-ldap`/`bonsai` (C bindings → breaks pure-Python parity and wheels-everywhere); `msldap` (offensive-tooling lineage conflicts with Art. X posture); hand-rolled LDAP/BER client (an order of magnitude more code and risk than the feature itself). Mitigation: extra-scoped, pinned `<3`, quarantined in `ad/directory.py` behind typed wrappers so a future swap touches one module |
