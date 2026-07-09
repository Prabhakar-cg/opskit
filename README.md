# opskit

Cross-platform diagnostics for engineers — one toolkit, every OS.

[![CI](https://github.com/Prabhakar-cg/opskit/actions/workflows/ci.yml/badge.svg)](https://github.com/Prabhakar-cg/opskit/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Prabhakar-cg/opskit/actions/workflows/codeql.yml/badge.svg)](https://github.com/Prabhakar-cg/opskit/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/Prabhakar-cg/opskit/badge)](https://securityscorecards.dev/viewer/?uri=github.com/Prabhakar-cg/opskit)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13462/badge)](https://www.bestpractices.dev/projects/13462)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=Prabhakar-cg_opskit&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Prabhakar-cg_opskit)
[![Known Vulnerabilities](https://snyk.io/test/github/Prabhakar-cg/opskit/badge.svg)](https://snyk.io/test/github/Prabhakar-cg/opskit)
[![Reviewed by CodeRabbit](https://img.shields.io/badge/reviewed%20by-CodeRabbit-FF570A?labelColor=171717)](https://coderabbit.ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/Prabhakar-cg/opskit/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%20|%203.10%20|%203.11%20|%203.12%20|%203.13-blue)](https://github.com/Prabhakar-cg/opskit/blob/main/pyproject.toml)

**opskit** gives engineers, developers, and operations teams one consistent set of read-only
troubleshooting commands that behave **identically on Windows, macOS, and Linux** — so you stop
juggling `nslookup` vs `dig` vs PowerShell cmdlets. It's a **CLI *and* an importable Python
library**, is pure-Python (nothing to shell out to), and never phones home.

> ⚠️ **Early development.** Ships **DNS** and **TLS** diagnostics today; network / AD categories follow.

---

## Contents

- [Install](#install)
- [Commands](#commands)
- [Why opskit](#why-opskit)
- [Output & exit codes](#output--exit-codes)
- [Development](#development)
- [Security](#security)
- [License](#license)

---

## Install

```bash
pip install opskit            # once published
# or, from source:
git clone https://github.com/Prabhakar-cg/opskit && cd opskit
uv sync                       # or: pip install -e .
```

Everything lives under a single command; each category is a sub-command group:

```bash
opskit --help                 # top-level help
opskit dns --help             # the DNS command group
opskit dns lookup --help      # a specific command (grouped options + examples)
```

## Commands

Each command group is self-contained and documented in its own README next to the code:

| Command group | What it does | Docs |
|---|---|---|
| `opskit dns` | Read-only DNS diagnostics — forward/reverse lookups, multi-resolver diff, iterative trace, watch | [dns/README.md](https://github.com/Prabhakar-cg/opskit/blob/main/src/opskit/dns/README.md) |
| `opskit tls` | TLS verification — layered endpoint checks, certificate/chain inspection, expiry warnings, private-PKI trust | [tls/README.md](https://github.com/Prabhakar-cg/opskit/blob/main/src/opskit/tls/README.md) |
| `opskit net` | Network reachability — TCP/UDP port checks, ping-style probes, temporary metadata-only listener | [net/README.md](https://github.com/Prabhakar-cg/opskit/blob/main/src/opskit/net/README.md) |
| `opskit ad` | Directory (LDAP/AD) queries | _planned_ |

Quick taste of the DNS group:

```bash
opskit dns lookup example.com                    # A records, pretty table
opskit dns lookup example.com --all              # every common record type at once
opskit dns reverse 8.8.8.8                       # PTR (IP → hostname)
opskit dns lookup example.com --diff -s 1.1.1.1 -s 8.8.8.8   # compare resolvers
```

See **[the DNS command reference](https://github.com/Prabhakar-cg/opskit/blob/main/src/opskit/dns/README.md)** for every option, mode, and the
importable Python API.

## Why opskit

- **One tool, every OS.** Identical behavior and output on Windows, macOS, and Linux — no more
  remembering which flag `nslookup` uses versus `dig` versus `Resolve-DnsName`.
- **CLI *and* library.** Every capability is importable, typed, and API-first; the CLI is a thin
  client over it. Ships `py.typed`.
- **Scriptable by design.** A stable, versioned `--json` envelope, NDJSON `--jsonl`, and
  structured exit codes make it safe to build automation on.
- **Read-only & private.** Only performs the diagnostic you asked for; no telemetry, no phoning
  home, no offensive/abuse features.

## Output & exit codes

Every command shares one output contract:

- **Human** (default): colorized tables, auto-plain when piped; honors `NO_COLOR` and `--no-color`.
- **`--json`**: a stable, versioned envelope (`schema_version`, `command`, `query`, `result`,
  `error`, `elapsed_ms`); an array for batches.
- **`--jsonl`**: NDJSON — one envelope per line, ideal for `jq` / streaming.
- **Exit codes** are documented and scriptable (`0` success, `2` usage error, `7` partial, plus
  category-specific codes). See each command's README for the full table.

## Development

Built to a high bar: `src/` layout, [uv](https://docs.astral.sh/uv/), Ruff, `mypy --strict` +
pyright, pytest with ≥90% coverage, and a hardened CI/CD pipeline (see [`docs/PLAN.md`](https://github.com/Prabhakar-cg/opskit/blob/main/docs/PLAN.md)
and the project constitution in [`.specify/memory/constitution.md`](https://github.com/Prabhakar-cg/opskit/blob/main/.specify/memory/constitution.md)).

```bash
uv sync --extra dev
uv run opskit dns lookup example.com     # run the CLI
uv run ruff format --check . && uv run ruff check .   # format + lint
uv run mypy && uv run pyright                         # types
uv run pytest                            # tests (coverage ≥ 90%)
```

These are exactly the gates CI runs on every pull request — see
[`.github/workflows/ci.yml`](https://github.com/Prabhakar-cg/opskit/blob/main/.github/workflows/ci.yml).

Contributions go through PRs into a protected `main` (squash-merge, Conventional Commits).

## Security

Report vulnerabilities privately — see [SECURITY.md](https://github.com/Prabhakar-cg/opskit/blob/main/SECURITY.md). Every PR is gated by a full
scanner suite (pip-audit, Ruff `S`, CodeQL, secret scanning, dependency review, SonarCloud, Snyk).

## License

[MIT](https://github.com/Prabhakar-cg/opskit/blob/main/LICENSE) © `prabhakar-cg`. Provided as-is, without warranty.
