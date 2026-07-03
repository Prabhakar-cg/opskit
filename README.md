# opskit

Cross-platform diagnostics for engineers — one toolkit, every OS.

[![CI](https://github.com/Prabhakar-cg/opskit/actions/workflows/ci.yml/badge.svg)](https://github.com/Prabhakar-cg/opskit/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Prabhakar-cg/opskit/actions/workflows/codeql.yml/badge.svg)](https://github.com/Prabhakar-cg/opskit/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/Prabhakar-cg/opskit/badge)](https://securityscorecards.dev/viewer/?uri=github.com/Prabhakar-cg/opskit)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13462/badge)](https://www.bestpractices.dev/projects/13462)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=Prabhakar-cg_opskit&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=Prabhakar-cg_opskit)
[![Known Vulnerabilities](https://snyk.io/test/github/Prabhakar-cg/opskit/badge.svg)](https://snyk.io/test/github/Prabhakar-cg/opskit)
![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/Prabhakar-cg/opskit?utm_source=oss&utm_medium=github&utm_campaign=Prabhakar-cg%2Fopskit&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%20|%203.10%20|%203.11%20|%203.12%20|%203.13-blue)](pyproject.toml)

**opskit** gives engineers, developers, and operations teams one consistent set of read-only
troubleshooting commands that behave **identically on Windows, macOS, and Linux** — so you stop
juggling `nslookup` vs `dig` vs PowerShell cmdlets. It's a **CLI *and* an importable Python
library**, is pure-Python (nothing to shell out to), and never phones home.

> ⚠️ **Early development.** v1 ships **DNS diagnostics**; network / TLS / AD categories follow.

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [`opskit dns lookup`](#opskit-dns-lookup)
- [`opskit dns reverse`](#opskit-dns-reverse)
- [Modes](#modes) — `--all`, `--diff`, `--trace`, `--watch`
- [Bulk lookups](#bulk-lookups) — `--input-file`
- [Output & exit codes](#output--exit-codes)
- [Use as a Python library](#use-as-a-python-library)
- [Development](#development)

---

## Install

```bash
pip install opskit            # once published
# or, from source:
git clone https://github.com/Prabhakar-cg/opskit && cd opskit
uv sync                       # or: pip install -e .
```

Everything is available under a single command:

```bash
opskit --help                 # top-level help
opskit dns --help             # the DNS command group
opskit dns lookup --help      # a specific command (grouped options + examples)
```

The help is grouped into panels (**Query**, **Modes**, **Query controls**, **Output**) with
worked examples in the footer — think `man`, but organized and copy-pasteable.

## Quick start

```bash
opskit dns lookup example.com                    # A records, pretty table
opskit dns lookup example.com -t MX -t TXT       # specific record types
opskit dns lookup example.com --all              # every common record type at once
opskit dns reverse 8.8.8.8                       # PTR (IP → hostname)
opskit dns lookup example.com --json             # machine-readable envelope
```

---

## `opskit dns lookup`

Forward DNS lookup for one or more hostnames.

```bash
opskit dns lookup TARGET [OPTIONS]
```

| Option | Description | Default |
|---|---|---|
| `TARGET` | Hostname to resolve (optional if `--input-file` is used) | — |
| `-t, --type` | Record type(s): `A AAAA MX TXT CNAME NS SOA SRV CAA` (repeatable) | `A` |
| `--all` | Query **all** common record types at once | off |
| `--diff` | Query every `--server` and compare/diff answers | off |
| `--trace` | Show the iterative resolution path (root → authoritative) | off |
| `-s, --server` | Resolver(s) to query (repeatable); defaults to the system resolver | system |
| `--transport` | `udp` \| `tcp` \| `auto` (UDP then TCP on truncation) | `auto` |
| `--timeout` | Per-attempt timeout, seconds | `5.0` |
| `--retries` | Retry count on timeout | `2` |
| `--port` | Resolver port | `53` |
| `-i, --input-file` | File of targets, one per line (`#` comments allowed) | — |
| `--json` | Emit the versioned JSON envelope | off |
| `--jsonl` | Emit one JSON envelope per line (NDJSON) | off |
| `--watch` | Re-run every interval (e.g. `5s`, `2m`, `250ms`) until Ctrl-C | off |
| `--no-color` | Disable colored output | off |

## `opskit dns reverse`

Reverse (PTR) lookup for one or more IP addresses. Accepts IPv4 and IPv6.

```bash
opskit dns reverse IP [OPTIONS]
```

Supports the same query controls, output, `--trace`, `--watch`, and `--input-file` options as
`lookup` (record-type options don't apply).

---

## Modes

### `--all` — one-stop lookup

DNS `ANY` queries are deprecated (RFC 8482), so `--all` fans out across every common record type
and aggregates the results. It's **resilient**: if a resolver refuses one type, that type is
skipped rather than failing the whole lookup — you still get everything that exists.

```bash
opskit dns lookup cloudflare.com --all
```

### `--diff` — multi-resolver comparison

Ask the same question of several resolvers and see **who returns what** — the differing resolver
is highlighted. Great for propagation lag, split-horizon/GeoDNS, or spotting a broken/poisoned
resolver.

```bash
opskit dns lookup example.com --diff -s 1.1.1.1 -s 8.8.8.8 -s 9.9.9.9
```

Exit code is `0` when all resolvers agree and `7` when they differ (TTLs are ignored — only real
answer differences count), so you can alert on divergence in scripts.

### `--trace` — the resolution path

An iterative walk from the root servers, following each delegation down to the authoritative
answer — like `dig +trace`, one row per hop.

```bash
opskit dns lookup www.wikipedia.org --trace
opskit dns reverse 8.8.8.8 --trace
```

### `--watch` — live re-run

Re-run on an interval until Ctrl-C, flagging when the **answer changes** (TTL changes are
ignored). Ideal for watching propagation or failover.

```bash
opskit dns lookup example.com --watch 30s
opskit dns lookup example.com --all --watch 10s     # modes compose
```

---

## Bulk lookups

Feed many targets from a file with `-i/--input-file` (one per line; blank lines and `#` comments
are skipped). Combine with a positional target if you like.

```bash
opskit dns lookup -i hosts.txt                 # per-target tables
opskit dns lookup -i hosts.txt --jsonl | jq .  # NDJSON, one object per line
opskit dns reverse -i ips.txt --json           # JSON array
```

For a batch, the exit code is `0` only if **every** target succeeds; otherwise `7` (partial).

---

## Output & exit codes

- **Human** (default): colorized tables, auto-plain when piped; honors `NO_COLOR` and `--no-color`.
- **`--json`**: a stable, versioned envelope (`schema_version`, `command`, `query`, `result`,
  `error`, `elapsed_ms`); an array for batches.
- **`--jsonl`**: NDJSON — one envelope per line, ideal for `jq` / streaming.

```json
{
  "schema_version": "1",
  "command": "dns.lookup",
  "query": { "target": "example.com", "record_types": ["A"], "servers": [], "transport": "auto" },
  "result": { "outcome": "ok", "resolver": "1.1.1.1", "records": [ { "type": "A", "value": "93.184.216.34", "ttl": 300 } ] },
  "error": null,
  "elapsed_ms": 12.3
}
```

Exit codes are documented and scriptable:

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | usage error (bad input; before any network) |
| `3` | NXDOMAIN (name does not exist) |
| `4` | SERVFAIL |
| `5` | REFUSED |
| `6` | TIMEOUT / no response |
| `7` | PARTIAL (batch had a failure, or resolvers disagreed) |

---

## Use as a Python library

opskit is **API-first** — every capability is importable, returns typed results, and raises typed
exceptions (it never prints or calls `sys.exit`). Ships `py.typed`.

```python
from opskit.dns import lookup, reverse, lookup_all, compare, trace
from opskit.dns import NxDomain, DnsTimeout

result = lookup("example.com", ["A", "MX"], server="1.1.1.1", timeout=3)
if result.ok:
    for record in result:            # results are iterable
        print(record.type.value, record.value, record.ttl)

everything = lookup_all("example.com")             # all record types
ptr = reverse("8.8.8.8")                            # PTR
cmp = compare("example.com", ["1.1.1.1", "8.8.8.8"])   # multi-resolver
print("consistent:", cmp.consistent)
hops = trace("www.wikipedia.org")                   # resolution path

try:
    lookup("does-not-exist.invalid")
except NxDomain as exc:
    print(exc.message, "—", exc.hint)
```

Results serialize cleanly: `result.to_dict()` / the envelope shape match the CLI's `--json`.

---

## Development

Built to a high bar: `src/` layout, [uv](https://docs.astral.sh/uv/), Ruff, `mypy --strict` +
pyright, pytest with ≥90% coverage, and a hardened CI/CD pipeline (see `docs/PLAN.md` and the
project constitution in `.specify/memory/constitution.md`).

```bash
uv sync --extra dev
uv run opskit dns lookup example.com     # run the CLI
uv run pytest                            # tests (coverage ≥ 90%)
uv run ruff format . && uv run ruff check .
uv run mypy && uv run pyright
```

Contributions go through PRs into a protected `main` (squash-merge, Conventional Commits). Report
vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © `prabhakar-cg`. Provided as-is, without warranty.
