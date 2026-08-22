# Implementation Plan: AD Diagnostics Enhancements

**Branch**: `008-ad-enhancements` | **Date**: 2026-08-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-ad-enhancements/spec.md`

## Summary

Four targeted refinements to the existing `opskit ad` category, all read-only (Art. X
unaffected — no write-path exception is used here): (1) a group-redirect error when a
principal-scoped lookup's identifier turns out to be a group; (2) a new `ad members
<group>` command doing nested/effective member expansion, mirroring the existing
`ad groups --effective` traversal in the opposite direction; (3) UPN-shaped identifiers now
also match the `mail` attribute, not `userPrincipalName` alone; (4) sharper certificate-
verification hint text (StartTLS does not bypass verification; WSL does not inherit the
Windows trust store) plus a new README section on trusting a corporate CA. No new runtime
dependency needed by the feature itself, no new top-level category, no new exit codes — all
four changes extend `src/opskit/ad/{__init__,api,cli,directory,errors,models,output}.py`
and `src/opskit/ad/README.md` in place (the `__init__.py` change is export-only: `members()`,
`GroupMemberEntry`, `GroupMembersReport`, `PrincipalIsGroup` added to `__all__` per T005/T014).
Separately, during Phase 7 gate validation a pre-existing `cryptography` CVE was found and
fixed (see Constraints below) — unrelated to the feature's own logic but pulled forward onto
this branch to keep its CI green.

## Technical Context

**Language/Version**: Python (project floor 3.9; mypy configures `python_version = "3.10"`
exactly in `pyproject.toml`; pyright and the CI compatibility leg cover 3.9)

**Primary Dependencies**: `ldap3` (already an `opskit[ad]` extra) — no new dependency added
by this feature. Unrelated to the feature's logic but landed on this branch: `cryptography`
bumped `48.0.1` → `50.0.0` (fixes PYSEC-2026-3552/3553/3554), pulling `pyopenssl` to `26.4.0`
as its dependent; `uv run pip-audit` clean afterward, `tls` category's full suite re-verified
green, no Python 3.9 compatibility impact.

**Storage**: N/A (read-only LDAP queries against the caller's directory)

**Testing**: pytest + Hypothesis; ldap3 `MOCK_SYNC` offline directory layer (existing
`tests/integration/test_ad_mock_directory.py` fixture builder, extended) + existing
loopback socket layer for connect-stage classification

**Target Platform**: Linux/macOS/Windows, matching the existing `ad` category (CI matrix
unchanged — this feature adds no new OS-sensitive code path)

**Project Type**: CLI + library (existing `opskit.ad` package; no new package)

**Performance Goals**: N/A beyond existing category norms — `ad members --effective`'s BFS
cost is bounded by the group's distinct nested membership, mirroring the already-shipped
`ad groups --effective` traversal's cost model (one paged/ranged read per distinct group
visited)

**Constraints**: No new network destination, no new write path, no new exit code; every
change must be additive (MINOR SemVer) with zero behavior change to any existing invocation
that doesn't exercise one of the four new/changed code paths (SC-005)

**Scale/Scope**: Four scoped changes inside one existing category package; no new CLI
sub-app, no new top-level command group

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Core principles (I–X):**

- **I. Conventional Commits & Automated Changelog** — PASS. Single `feat(ad): ...` commit
  series; release-please handles the version bump.
- **II. Documentation Completeness** — PASS. New `ad members` command needs help text +
  README options-table entry + root README Commands-table link (docs-coverage gate already
  enforces this); the other three changes update existing README prose (new "trusting your
  corporate CA" section) and existing hint text, not new commands.
- **III. Zero Security Compromise** — PASS. No new secret-handling path; the UPN/mail filter
  change still routes every interpolated value through the existing
  `escape_filter_value` choke point (R6 of 004's research, unchanged).
- **IV. Dependency Freshness** — PASS. No new/changed dependency.
- **V. Strict Semantic Versioning** — PASS. New command + refined error/hint text = additive,
  MINOR. No existing command's exit code, envelope shape, or documented behavior changes for
  inputs that don't hit the new logic (SC-005 is the explicit regression gate).
- **VI. Pure-Python Cross-Platform Parity** — PASS. No new socket/OS-specific code; the group-
  redirect check and UPN/mail matching are pure LDAP-filter/query changes; the TLS hint text
  change is a string edit in the existing `classify_connect_error` normalization point.
- **VII. CLI/API Parity via a Typed Core** — PASS. New logic lives in `ad/api.py`
  (`AdClient.members()` + a module-level `members()` convenience function) and
  `ad/models.py` (`GroupMemberEntry`, `GroupMembersReport`); `ad/cli.py` gets one thin new
  command reusing the existing `_run_batch`/`_emit_batch` helpers already shared by
  `ad user`/`ad show`. `ad/errors.py` gets one new typed error (`PrincipalIsGroup`).
- **VIII. Privacy — Zero Telemetry** — PASS. No new data collection; no behavior change here.
- **IX. Output & Interoperability Contract** — PASS. `ad members` follows the same
  batch/JSON contract as `ad user`/`ad show` (FR-007): every target processed, exit 0
  all-ok / uniform class / 7 PARTIAL, per-target envelope under `--jsonl` including
  failures. The three non-command changes (redirect error, mail matching, TLS hints) reuse
  the existing single-target error/hint rendering path unchanged in shape.
- **X. Diagnostic-Only Scope & Guarded File Utilities** — PASS. Strictly read-only; no
  write-path exception requested or used. `ad members` is a read query, same as
  `ad groups`/`ad show`. No enumeration/scanning affordance is added — identifiers are still
  resolved one at a time via exact-match, class-scoped filters (FR-014, FR-015 explicitly
  exclude Global Catalog cross-domain enumeration).

**OpenSSF Scorecard & Best-Practices Baseline** (enforced continuously):

- [x] No GitHub Action changes in this feature.
- [x] No workflow token changes.
- [x] No dangerous-workflow patterns introduced.
- [x] No new dependencies (nothing to `pip-audit`/Snyk-check beyond CI's existing baseline).
- [x] New command (`ad members`) ships tests + docs; the three refinements extend existing
      tested/documented commands' test suites in place.
- [x] No secrets committed; read-only scope preserved throughout; zero-telemetry unaffected.
- [x] No packaging/release path changes.
- [x] `SECURITY.md`, branch protection, Dependabot unaffected.

**New-category cross-cutting checklist**: N/A as a whole — this is not a new category, so
most items don't apply to fresh code. The two that remain relevant to the new/changed code:

- [x] `ad/cli.py` already follows the eager-annotations/`Optional[X]` rule (existing file);
      the new `members` command is added following the same pattern as the adjacent
      `groups`/`show` commands in that file — no `from __future__ import annotations` is
      introduced.
- [x] Every directory-derived string in the new `members` rendering path (group/member
      names, DNs) is escaped via `rich.markup.escape()`, matching `render_membership`'s
      existing pattern in `ad/output.py`.
- [x] `ad members` is batchable and follows Art. IX exactly via the existing
      `_run_batch`/`_emit_batch` helpers (no new batch machinery to get wrong).
- [x] Docs-coverage gate: `ad members` gets a help string, a README options-table entry, and
      stays linked from the root README's existing `opskit ad` Commands-table row (no new
      row needed — the row already points at the category README).

## Project Structure

### Documentation (this feature)

```text
specs/008-ad-enhancements/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (delta contracts over specs/004's baseline)
│   ├── cli.md
│   └── python-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/ad/
├── __init__.py     # __all__ gains GroupMemberEntry, GroupMembersReport, members(), PrincipalIsGroup
├── api.py          # + AdClient.members(), module-level members(); _find_one() group-redirect
                     #   check; _identifier_clause() UPN branch becomes UPN-or-mail
├── cli.py          # + `members` command (reuses _run_batch/_emit_batch)
├── directory.py    # classify_connect_error() gains a `security` param; CertificateInvalid
                     #   hint text gains the StartTLS/WSL clauses
├── errors.py       # + PrincipalIsGroup(AdError), exit_code = ExitCode.NOT_FOUND (no new code)
├── models.py       # + GroupMemberEntry, GroupMembersReport dataclasses
├── output.py       # + render_group_members()
└── README.md       # + `ad members` entry in the options table; + "Trusting a corporate CA" section

tests/
├── unit/
│   ├── test_ad_api.py          # + group-redirect, UPN/mail matching, members() unit cases
│   ├── test_ad_cli.py          # + `members` CLI envelope/exit-code tests
│   ├── test_ad_directory.py    # + hint-text assertions for the starttls/WSL cert-failure case
│   └── test_ad_models.py       # + GroupMemberEntry/GroupMembersReport to_dict() shape tests
└── integration/
    └── test_ad_mock_directory.py   # extend the existing MOCK_SYNC fixture: a mail-only user,
                                     # a name that is a group but no principal, a nested
                                     # group topology reused from the existing cycle fixture
```

**Structure Decision**: Everything lands inside the existing `src/opskit/ad/` package and
its existing test files — no new package, no new top-level test module beyond what's listed
(the new command's tests are added as new test functions in the already-existing
`test_ad_api.py`/`test_ad_cli.py`, matching how `show` was added in 004 rather than
spawning `test_ad_members.py`).

## Complexity Tracking

*No Constitution Check violations — table intentionally empty.*
