<!--
SYNC IMPACT REPORT
Version change: (initial) → 1.0.0
Ratification: initial adoption 2026-07-01
Principles (all new):
  I.    Conventional Commits & Automated Changelog
  II.   Documentation Completeness
  III.  Zero Security Compromise
  IV.   Dependency Freshness
  V.    Strict Semantic Versioning
  VI.   Pure-Python Cross-Platform Parity
  VII.  CLI/API Parity via a Typed Core
  VIII. Privacy — Zero Telemetry
  IX.   Output & Interoperability Contract
  X.    Diagnostic-Only Scope (No Misuse)
Added sections:
  - Core Principles (I–X)
  - Security & Supply-Chain Requirements
  - Development Workflow & Quality Gates
  - Governance
Template consistency:
  ✅ .specify/templates/plan-template.md   (Constitution Check references are generic; compatible)
  ✅ .specify/templates/spec-template.md   (compatible)
  ✅ .specify/templates/tasks-template.md  (compatible)
Runtime guidance:
  ✅ docs/PLAN.md is the design source; principles derived from its Arts. I–X
Deferred TODOs: none
-->

# opskit Constitution

opskit is a cross-platform, pip-installable Python CLI **and** library that gives engineers,
developers, and operations teams one consistent set of troubleshooting/diagnostic commands
regardless of operating system. These principles are non-negotiable and are enforced by
automated gates; every feature is checked against them before it ships.

## Core Principles

### I. Conventional Commits & Automated Changelog
All commits MUST follow the Conventional Commits specification. A `CHANGELOG.md` (Keep a
Changelog format) MUST be maintained and generated automatically at release from commit
history; no user-facing change ships without a corresponding changelog entry.
**Gate:** commit-lint rejects non-conforming messages; release tooling regenerates the
changelog. **Rationale:** machine-readable history drives correct automated versioning and
transparent release notes.

### II. Documentation Completeness
Every command MUST ship with (a) inline help / docstring and (b) a documentation page before
it is considered "done". Every public API symbol MUST carry a Google-style docstring.
**Gate:** an automated test enumerates all registered commands and fails if any lacks help
text or a matching docs entry. **Rationale:** undocumented capability is unusable and erodes
trust.

### III. Zero Security Compromise
No known-vulnerable dependency, leaked secret, or insecure pattern may reach `main`.
**Gate:** `pip-audit`, Ruff `S`, bandit, secret scanning + push protection, GitHub CodeQL,
dependency review, and the SonarCloud quality gate MUST all pass; any high/critical finding
blocks merge. Security suppressions (`# noqa`/`# nosec`) require written justification and
review. Credentials MUST be redacted in all output and logs. **Rationale:** a diagnostics tool
runs in sensitive environments — trust is the product.

### IV. Dependency Freshness
The project MUST NOT depend on end-of-life Python or unmaintained/deprecated libraries.
Automated update PRs (Dependabot/Renovate) keep dependencies current and MUST pass the full CI
matrix before merge. **Rationale:** current dependencies minimize the vulnerability window
without chasing bleeding-edge breakage.

### V. Strict Semantic Versioning
Public behavior — CLI flags, exit codes, the Python API, and the `--json` schema — MUST follow
SemVer: breaking → MAJOR, additive → MINOR, fix → PATCH. The version MUST be single-sourced.
**Gate:** Conventional-Commits-driven release-please computes the bump, tag, and changelog; a
breaking change without a MAJOR bump fails the release check. **Rationale:** users and scripts
depend on predictable compatibility.

### VI. Pure-Python Cross-Platform Parity
Functionality MUST be implemented in pure Python (no shelling out to native OS tools) and MUST
behave identically on Windows, macOS, and Linux. OS-specific errors MUST be normalized into the
shared exception hierarchy. **Gate:** the CI matrix Windows/macOS/Linux × Python 3.9–3.13 MUST
pass. **Rationale:** the core purpose is one consistent tool regardless of operating system.

### VII. CLI/API Parity via a Typed Core
Every capability MUST live in an importable, fully typed Python API; the CLI is a thin
presentation client that contains no business logic. Results MUST be typed and render to both
human-readable and JSON output; a central exception hierarchy MUST map to structured exit codes
so no raw exception reaches the user. The library layer MUST NOT `print()` or call `sys.exit()`,
MUST NOT hold global mutable state, and MUST ship `py.typed` (PEP 561). **Rationale:**
embeddability and scriptability are first-class, not afterthoughts.

### VIII. Privacy — Zero Telemetry
opskit MUST make no network connection except the diagnostic query the user explicitly
requested. No analytics, phone-home, auto-update checks, or third-party crash reporting — ever.
**Gate:** a CI audit asserts no outbound calls to non-user-specified hosts and forbids any
telemetry/analytics dependency. **Rationale:** operators must trust the tool not to exfiltrate
anything from their environment.

### IX. Output & Interoperability Contract
Every command MUST provide: a human-readable default; `--json` emitting a **versioned envelope**
(`schema_version`, `query`, `result`, `error`, `elapsed_ms`); `--jsonl` (NDJSON) where results
are batchable; honor `NO_COLOR` and auto-plain output when piped; and structured exit codes.
The JSON schema MUST be published and its changes governed by Principle V. **Gate:** a test
walks all commands and asserts these behaviors. **Rationale:** composability and automation
require a stable, predictable contract.

### X. Diagnostic-Only Scope (No Misuse)
opskit is a **read-only diagnostic/troubleshooting** tool for operators acting on their own
**authorized** environments. It MUST NOT ship offensive or abuse features — no exploitation,
no credential brute-forcing/guessing, no mass/range scanning, no traffic interception/spoofing,
no detection-evasion. Legitimate operator diagnostics (explicit connectivity checks, a temporary
listener for one's own troubleshooting, read-only directory queries with the operator's own
credentials) are in scope; anything enabling attack or misuse is out. **Gate:** every feature
spec is checked against this boundary in the Constitution Check and misuse-enabling capabilities
are rejected. **Rationale:** opskit is a helper for engineers — explicitly not a hacking tool.

## Security & Supply-Chain Requirements

- Full scanner suite runs on every PR (Principle III): `pip-audit`, Ruff `S`, bandit, CodeQL,
  secret scanning + push protection, dependency review, and SonarCloud.
- CI supply-chain hardening: all GitHub Actions pinned to commit SHAs, `step-security/harden-runner`
  egress control on every job, and an OpenSSF Scorecard workflow.
- Releases publish to PyPI via **Trusted Publishing (OIDC)** — no long-lived tokens — with a
  CycloneDX SBOM and PEP 740 attestations.
- A final `pip-audit` / dependency re-scan MUST pass immediately before publish; a nightly
  scheduled security scan MUST run on `main` to catch newly-disclosed CVEs between releases.

## Development Workflow & Quality Gates

- **Structure & tooling:** `src/` layout; `uv` for env/build; Ruff for format + lint (incl. `PL`);
  `mypy --strict` + `pyright` for types; `pytest` (+ Hypothesis) with coverage **≥ 90%**
  (`--cov-fail-under=90`); `nox` to drive the matrix; pre-commit hooks mirror CI.
- **Testing depth:** MUST cover real-world / tightened-network scenarios via unit tests +
  injected mock resolver + an in-process loopback server; opt-in real-network smoke tests
  (`@pytest.mark.network`) never gate CI.
- **Branching & merge:** changes reach `main` only via PR; **squash-merge**; the PR title MUST be
  a Conventional Commit.
- **CI matrix:** reduced matrix on PR; full matrix on `main` + nightly.
- **Delivery (Continuous Delivery):** release-please maintains a release PR; the full CI +
  security suite re-run on the release PR (via a GitHub App token); publishing to PyPI requires
  human approval through a protected `pypi` environment.
- **Configuration precedence** is fixed: CLI flags > env vars > active profile > config-file
  default > built-in defaults. The library layer MUST NOT auto-read env/config files (Principle VII).

## Governance

- This constitution supersedes all other practices; where guidance conflicts, the constitution wins.
- **Compliance:** every `/speckit-specify` and `/speckit-plan` MUST pass a Constitution Check
  against these principles. Violations block progress unless accompanied by an explicit,
  documented justification approved in review.
- **Amendments:** proposed via PR; MUST update this file, bump its version, and update any
  dependent templates/docs; require maintainer approval.
- **Constitution versioning** follows SemVer: MAJOR for principle removals/redefinitions, MINOR
  for added or materially expanded principles, PATCH for clarifications and wording.
- **Design source of record:** `docs/PLAN.md` captures the rationale and full decision log behind
  these principles.

**Version**: 1.0.0 | **Ratified**: 2026-07-01 | **Last Amended**: 2026-07-01
