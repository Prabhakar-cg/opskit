# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Confirm this feature upholds every applicable principle in `.specify/memory/constitution.md`.
Record PASS/violation for each; a violation requires an entry in Complexity Tracking with a
documented justification.

**Core principles (I–X):** _map each to how the feature complies (or N/A)._

**OpenSSF Scorecard & Best-Practices Baseline** (enforced continuously; solo-limited *(team)*/
*(maintainer)* items are aspirational, not gating):
- [ ] Any new/edited GitHub Action is **SHA-pinned** (with `# vX.Y.Z` comment).
- [ ] Workflow tokens are **least-privilege** (read-only default; per-job write scopes only).
- [ ] No **dangerous-workflow** patterns (untrusted checkout / unsanitized `github.event.*`).
- [ ] New dependencies pass `pip-audit` + Snyk; none are EOL/unmaintained; deps stay pinned via lock.
- [ ] New commands ship **tests** + **docs** (Arts. II, VII) and preserve the output/exit-code contract.
- [ ] No secrets committed; inputs validated; read-only, zero-telemetry scope preserved (Arts. VIII, X).
- [ ] Release/packaging path keeps **Trusted Publishing + SBOM + attestations** intact.
- [ ] `SECURITY.md`, branch protection, and Dependabot remain in force.

**New-category cross-cutting checklist** (from CLAUDE.md "Cross-cutting rules for new
categories" — each item cost rework on `dns`; confirm the plan bakes them in up front):
- [ ] CLI module (`src/opskit/<cat>/cli.py`) uses **eager** annotations + `Optional[X]` — no
      `from __future__ import annotations` — so Typer keeps `Annotated` metadata on Python 3.9.
- [ ] Every resolver/network/user-supplied string is `rich.markup.escape()`d before markup
      output; consoles are built via `make_console` (honors `NO_COLOR`).
- [ ] Network code normalizes raw `OSError`/timeouts into the shared typed hierarchy with an
      actionable hint; each error type owns its exit code; `core` stays category-agnostic.
- [ ] Batchable commands process **every** target, aggregate exit codes (0 all-ok / uniform
      class / else `7` PARTIAL), and emit a JSON envelope per item including failures (Art. IX).
- [ ] Docs-coverage gate satisfied: each command documented in `src/opskit/<cat>/README.md`,
      that README linked from the root README's Commands table (`tests/unit/test_docs_coverage.py`).
- [ ] Cross-OS behavior (socket/TLS/filesystem/path) tested tolerant of platform variance
      (assert the error *class family*, not one subclass); loopback/mock layers cover it — don't
      rely on the reduced PR matrix ("a green PR is not a green `main`").

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
