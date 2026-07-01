# Phase 0 Research: DNS Diagnostics

All technical unknowns were resolved during the project design discussion (see `docs/PLAN.md`).
This document consolidates the decisions in Decision / Rationale / Alternatives form. There are no
open `NEEDS CLARIFICATION` items.

## D1 — DNS implementation library
- **Decision**: `dnspython` for resolution and wire-level control.
- **Rationale**: Pure-Python, cross-platform (reads each OS's resolver config), supports record
  types, custom servers, TCP/UDP, timeouts, TC-bit/TCP fallback, and message inspection needed for
  `--trace`. Satisfies Art. VI (no native shell-out).
- **Alternatives**: Wrapping `nslookup`/`dig`/`Resolve-DnsName` (rejected — inconsistent/absent per
  OS, breaks parity); raw `socket` DNS packet building (rejected — reinventing a mature library).

## D2 — CLI framework
- **Decision**: `typer`.
- **Rationale**: Type-hint driven, auto help, shell completion, sub-command groups map to future
  categories; thin layer over the API. Pairs with `rich`.
- **Alternatives**: `click` (more boilerplate); `argparse` (manual subcommands/completion).

## D3 — Result models
- **Decision**: stdlib `dataclasses`.
- **Rationale**: Zero runtime dependency, fast import, lean for an embeddable library; custom
  `to_dict()`/`to_json()` drive the `--json` envelope. Keeps base install slim.
- **Alternatives**: Pydantic v2 (rejected for core — heavy dependency inherited by every consumer).

## D4 — Output / cross-platform terminal
- **Decision**: `rich` for color/tables/TTY detection and `NO_COLOR`; `platformdirs` for the per-OS
  config path.
- **Rationale**: Erases the biggest cross-platform terminal + path gotchas with small, best-in-class
  deps; auto-plain-when-piped supports Art. IX.
- **Alternatives**: Hand-rolled ANSI + manual per-OS paths (rejected — more code, more edge cases).

## D5 — Config format & precedence
- **Decision**: TOML (`tomllib` ≥3.11, `tomli` <3.11). Precedence: flags > env (`OPSKIT_*`) >
  profile > config-file `[default]` > built-in. Profiles under `[profiles.<name>]`. Sources: user
  config (platformdirs) + optional project-local `./.opskit.toml` + `--config`.
- **Rationale**: TOML is the Python-native config standard; explicit precedence is predictable.
  **The library never auto-reads env/config** — only the CLI resolves precedence and passes explicit
  config into the API (Art. VII, no global state).
- **Alternatives**: YAML/INI (extra dep / less standard); env-only (users retype `--server`).

## D6 — Concurrency model
- **Decision**: Synchronous core, structured to add async later; batch and multi-resolver diff use a
  bounded thread pool (`concurrent.futures`).
- **Rationale**: Simple for a CLI; threads suffice for I/O-bound DNS and enable concurrent
  batch/diff now without an async API surface. Signatures shaped so an async variant can be added
  without breaking callers.
- **Alternatives**: Async-first (more upfront complexity for v1); purely serial (slow for batch/diff).

## D7 — Error model & exit codes
- **Decision**: Typed exception hierarchy (`OpskitError` → `DnsError` → `NxDomain`, `DnsTimeout`,
  `DnsRefused`, `ServerFailure`, `DnssecError`, …) plus a `UsageError`. Central map to an `ExitCode`
  enum. Library raises; only the CLI catches and maps to exit codes.
- **Rationale**: Distinct, scriptable outcomes (SC-004); no raw exceptions reach the user; parity
  between API (exceptions) and CLI (exit codes).
- **Alternatives**: Return-code-only (poor for a library); single generic error (not scriptable).

## D8 — `--json` contract
- **Decision**: Versioned envelope (`schema_version`, `command`, `query`, `result`, `error`,
  `elapsed_ms`); array for batch; `--jsonl` (NDJSON) for streaming. Published JSON Schema validated
  in contract tests. Schema changes governed by SemVer.
- **Rationale**: Stable, scriptable interoperability (Art. IX, Art. V).
- **Alternatives**: Raw per-command result (no consistent metadata/versioning across commands).

## D9 — Testing strategy for network code
- **Decision**: Layered — unit; injected mock resolver (every rcode/failure class); in-process
  loopback DNS server (`dnslib`) on `127.0.0.1:<ephemeral>` for real sockets (timeout, REFUSED,
  TC-bit→TCP, latency, split-horizon); CLI tests via Typer `CliRunner`; Hypothesis for
  parsers/formatters; opt-in `@pytest.mark.network` smoke that never gates.
- **Rationale**: Deterministically reproduces tightened-network scenarios on every OS in CI.
- **Alternatives**: Mock-only (misses real socket/TCP-fallback path); recorded fixtures only (no
  live socket behavior).

## D10 — Packaging & tooling
- **Decision**: `pyproject.toml` (PEP 621), Hatchling backend, `uv` for env/build/lock; category
  extras (`opskit[tls]`, `opskit[ad]`) reserved for future. Ships `py.typed`. Ruff (format+lint incl.
  `PL`), mypy `--strict` + pyright, nox, pre-commit.
- **Rationale**: Standard, pip-compatible wheel; highest-bar quality gates. (Full CI/CD in `PLAN.md`.)
- **Alternatives**: Poetry/PDM/pip-tools (see PLAN.md decision); setup.py (legacy).

## Resolved defaults (from spec Assumptions)
- **Default record type** for a bare forward lookup: `A`.
- **Batch aggregate exit code**: success only if all targets succeed; otherwise a non-success code
  reflecting the most severe per-target outcome.
- **System resolver discovery**: via `dnspython`'s OS configuration reader.
- **IDN handling**: names are IDNA/punycode-encoded consistently before query.
