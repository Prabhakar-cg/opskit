# Quickstart & Validation: DNS Diagnostics

How to run and validate the DNS feature end-to-end. Assumes a WSL/Unix or Windows shell with `uv`
installed. Commands are run from the repo root. (Implementation is produced by `/speckit-implement`;
this guide defines how we prove it works.)

## Setup

```bash
uv sync                 # create env + install runtime and dev deps
uv run opskit --help    # root CLI loads; dns group present
```

## Functional validation (maps to spec user stories)

| # | Scenario | Command | Expected |
|---|----------|---------|----------|
| US1 | forward lookup | `uv run opskit dns lookup example.com -t A -t MX` | records listed; exit 0 |
| US1 | JSON envelope | `uv run opskit dns lookup example.com --json` | versioned envelope (`schema_version:"1"`) |
| US1 | NXDOMAIN | `uv run opskit dns lookup no-such.invalid; echo $?` | clear "does not exist"; exit 3 |
| US2 | reverse | `uv run opskit dns reverse 8.8.8.8` | hostname(s); exit 0 |
| US3 | custom server + transport | `uv run opskit dns lookup example.com -s 1.1.1.1 --transport tcp --timeout 3` | answer from 1.1.1.1 |
| US4 | multi-resolver diff | `uv run opskit dns lookup example.com -s 1.1.1.1 -s 8.8.8.8 --diff` | consistent, or differences highlighted |
| US5 | batch via stdin (NDJSON) | `printf 'example.com\nexample.org\n' \| uv run opskit dns lookup - --jsonl` | one envelope per line |
| US6 | watch | `uv run opskit dns lookup example.com --watch 5s` | re-runs each interval; surfaces changes |
| US7 | profiles | save a profile, then `uv run opskit dns lookup example.com --profile prod` | profile settings applied |
| US8 | trace | `uv run opskit dns lookup example.com --trace` | resolution path shown |
| US9 | programmatic | `uv run python -c "from opskit.dns import lookup; print(lookup('example.com').ok)"` | `True`; typed result returned |

Exit-code contract (US5/scripting): see `contracts/cli.md` — 0 ok, 2 usage, 3 NXDOMAIN, 4 SERVFAIL,
5 REFUSED, 6 TIMEOUT, 7 partial.

## Tightened-network validation (deterministic, no external network)

These run against the **in-process loopback DNS server** (see `tests/integration/`):

```bash
uv run pytest tests/integration -k "loopback"
```

Covers: UDP blocked → TCP fallback; REFUSED vs silent-drop/timeout; SERVFAIL vs NXDOMAIN;
TC-bit → TCP; injected latency; split-horizon diff.

## Quality gates (must pass)

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy src && uv run pyright
uv run pytest            # coverage >= 90% (--cov-fail-under=90)
```

## Privacy check (Art. VIII)

With a packet capture on the host, run any command and confirm **no** connection is made to any host
other than the specified/system resolver — zero telemetry. Contract test asserts no analytics deps.
