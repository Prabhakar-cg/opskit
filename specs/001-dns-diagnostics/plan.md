# Implementation Plan: DNS Diagnostics

**Branch**: `001-dns-diagnostics` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-dns-diagnostics/spec.md`

## Summary

Deliver opskit's v1 DNS diagnostics as an **API-first** capability: a fully typed, importable
Python API (`opskit.dns`) with convenience functions and a configurable `DnsClient`, plus a thin
Typer CLI (`opskit dns …`) that only parses arguments and renders results. DNS is implemented in
pure Python via `dnspython` (no shelling out), giving identical behavior on Windows/macOS/Linux.
Output follows the versioned `--json` envelope + `--jsonl`, with structured exit codes; failures
raise typed exceptions in the library and map to those exit codes in the CLI. Testing is layered
(unit → injected mock resolver → in-process loopback DNS server) so tightened-network scenarios are
reproduced deterministically in the cross-platform CI matrix.

## Technical Context

**Language/Version**: Python 3.9+ (supported matrix: 3.9, 3.10, 3.11, 3.12, 3.13)

**Primary Dependencies**: `dnspython` (resolution + wire protocol), `typer` (CLI), `rich`
(color/tables/TTY detection/`NO_COLOR`), `platformdirs` (per-OS config path), `tomli` (TOML read on
Python < 3.11 only; `tomllib` on ≥ 3.11).

**Storage**: TOML config + profiles at the `platformdirs` user config dir (overridable via
`--config`), plus optional project-local `./.opskit.toml`. No database.

**Testing**: `pytest` + `pytest-cov` (coverage ≥ 90%, `--cov-fail-under=90`), `hypothesis`
(parser/formatter properties), `dnslib` (in-process loopback DNS server for real-socket tests).
Real-network tests are `@pytest.mark.network` and never gate CI.

**Target Platform**: Windows, macOS, Linux — as both a CLI and an importable library.

**Project Type**: Single project — CLI + library, `src/` layout.

**Performance Goals**: A single lookup completes as fast as the target resolver answers (tool
overhead negligible); a batch of hundreds of targets resolves concurrently (bounded worker pool);
`--watch` overhead is negligible between intervals. Not a bulk scanner (scope-limited).

**Constraints**: Pure Python, no native shell-out; behavior/output identical across OSes; zero
telemetry (no network except the user's query); the library layer never `print()`s or `sys.exit()`s
and holds no global mutable state; ships `py.typed`.

**Scale/Scope**: v1 = DNS only (forward/reverse/custom-resolver/controls/diff/timing/trace/batch/
watch/profiles). Framework is extensible to net/TLS/AD categories later via in-tree sub-apps.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Art. | Principle | How this plan complies | Status |
|------|-----------|------------------------|--------|
| I | Conventional Commits + changelog | Feature on `001-dns-diagnostics`; squash-merge with CC title; changelog automated | PASS |
| II | Documentation completeness | Each command ships help + a docs page; docs-coverage test enumerates commands | PASS |
| III | Zero security compromise | Full scanner suite in CI; no secrets; pure-Python (no shell/subprocess surface) | PASS |
| IV | Dependency freshness | Small, maintained deps (dnspython/typer/rich/platformdirs); Dependabot; Python 3.9+ | PASS |
| V | Strict SemVer | Public API, CLI flags, exit codes, and `--json` schema versioned; release-please | PASS |
| VI | Pure-Python cross-platform parity | `dnspython`/stdlib only; OS socket errors normalized; CI matrix 3 OS × 5 Py | PASS |
| VII | CLI/API parity via typed core | Logic in `dns/api.py`; `dns/cli.py` thin; typed dataclasses; central exit-code map; `py.typed` | PASS |
| VIII | Privacy — zero telemetry | Only queries the chosen/system resolver; no analytics/phone-home; CI audit | PASS |
| IX | Output & interoperability contract | Human + `--json` envelope + `--jsonl`; `NO_COLOR`/auto-plain; structured exit codes | PASS |
| X | Diagnostic-only scope | Read-only DNS queries only; no offensive features; batch bounded to operational scale | PASS |

**Result**: PASS — no violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/001-dns-diagnostics/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions/rationale/alternatives
├── data-model.md        # Phase 1 — entities & types
├── quickstart.md        # Phase 1 — validation guide
├── contracts/           # Phase 1 — CLI, Python API, and --json envelope contracts
│   ├── cli.md
│   ├── python-api.md
│   └── json-envelope.md
└── tasks.md             # Phase 2 — created by /speckit-tasks (not here)
```

### Source Code (repository root)

```text
src/opskit/
├── __init__.py          # version + public API re-exports
├── __main__.py          # `python -m opskit`
├── py.typed             # PEP 561 marker
├── cli.py               # root Typer app; registers command groups (zero logic)
├── core/
│   ├── __init__.py
│   ├── result.py        # base Result envelope + schema_version + to_dict/to_json
│   ├── errors.py        # OpskitError hierarchy
│   ├── exit_codes.py    # ExitCode enum; exception→exit-code mapping
│   ├── config.py        # precedence resolution (flags>env>profile>file>default); TOML load
│   ├── output.py        # human vs json vs jsonl rendering; NO_COLOR/TTY handling (rich)
│   └── concurrency.py   # bounded worker pool for batch/multi-resolver
└── dns/
    ├── __init__.py      # public: lookup, reverse, DnsClient, errors, models
    ├── api.py           # resolution logic (pure); returns typed results
    ├── models.py        # DnsRecord, DnsQuery, LookupResult, ResolverComparison dataclasses
    ├── resolver.py      # resolver abstraction (injectable; wraps dnspython) for testability
    ├── errors.py        # DnsError hierarchy (NxDomain, DnsTimeout, DnsRefused, ...)
    └── cli.py           # thin Typer sub-app: lookup / reverse (+ flags), delegates to api

tests/
├── unit/                # parsing, models, error mapping, config precedence
├── integration/         # in-process loopback DNS server (real sockets) + injected mock resolver
├── contract/            # --json envelope schema validation; CLI flag/exit-code contract
└── network/             # @pytest.mark.network opt-in real-resolver smoke (never gates)
```

**Structure Decision**: Single-project `src/` layout. Shared cross-cutting concerns live in
`core/` (result envelope, errors, exit codes, config precedence, output rendering, concurrency);
each diagnostic category is a self-contained package (`dns/`) exposing a pure `api.py` and a thin
`cli.py` sub-app registered on the root Typer app in `cli.py`. This is the extensibility pattern for
future net/TLS/AD categories.

## Complexity Tracking

> No constitution violations — section intentionally empty.
