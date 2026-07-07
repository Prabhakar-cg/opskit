# Quickstart & Validation: TLS Verification Diagnostics

How to run and validate the `tls` feature end-to-end. Commands run from the repo root with `uv`
installed.

> **Shell note**: examples use POSIX syntax (`echo $?`); in PowerShell read the exit code with
> `$LASTEXITCODE`. The `opskit` commands themselves are identical on every platform.

## Setup

```bash
uv sync --extra dev
uv run opskit tls --help          # group loads; check command present
uv run opskit tls check --help    # grouped panels + examples
```

## Functional validation (maps to spec user stories)

Real-endpoint rows use public hosts (badssl.com et al.) — they are for **manual** validation and
the `@pytest.mark.network` smoke suite only; CI relies on the deterministic loopback suite below.

| # | Scenario | Command | Expected |
|---|----------|---------|----------|
| US1 | healthy endpoint | `uv run opskit tls check example.com` | verdict OK; leaf+chain+protocol shown; exit 0 |
| US1 | expired cert | `uv run opskit tls check expired.badssl.com; echo $?` | "expired" finding with dates; details still shown; exit 10 |
| US1 | wrong host | `uv run opskit tls check wrong.host.badssl.com` | name-mismatch finding (requested vs covered); exit 10 |
| US1 | self-signed | `uv run opskit tls check self-signed.badssl.com` | self-signed finding (distinct from untrusted); exit 10 |
| US2 | custom port | `uv run opskit tls check example.com:8443` (or `-p 8443`) | check runs against 8443 |
| US2 | IP target | `uv run opskit tls check 93.184.216.34` | runs without SNI; report notes IP matching |
| US3 | unresolvable | `uv run opskit tls check no-such.invalid; echo $?` | resolution failure; exit 3 |
| US3 | refused | `uv run opskit tls check 127.0.0.1:9` | connection refused; exit 8 |
| US3 | non-TLS port | `uv run opskit tls check neverssl.com:80` | handshake failure + STARTTLS/non-TLS hint; exit 9 |
| US4 | chain details | `uv run opskit tls check example.com --json \| jq .result.chain` | one object per presented cert |
| US5 | expiring soon | `uv run opskit tls check example.com --warn-days 3650; echo $?` | "expiring soon" with days remaining; exit 11 |
| US5 | watch | `uv run opskit tls check example.com --watch 30s` | re-runs; flags outcome/cert changes |
| US6 | batch | `uv run opskit tls check -i endpoints.txt --jsonl` | one envelope per line incl. failures; batch exit rule |
| US7 | programmatic | `uv run python -c "from opskit.tls import check; r = check('example.com'); print(r.outcome.value, r.leaf.days_until_expiry)"` | typed result; nothing extra printed |

## Deterministic validation (no external network — gates CI)

The integration suite spins up **in-process loopback TLS servers** with certificates generated
at runtime (valid / expired / not-yet-valid / wrong-name / self-signed / untrusted chain /
no-SAN) plus a plain-TCP listener and a closed port ([research.md R6](research.md)):

```bash
uv run pytest tests/integration -k tls
uv run pytest -q                                   # full suite, coverage >= 90%
uv run ruff format --check . && uv run ruff check .
uv run mypy && uv run pyright
```

Real-endpoint smoke tests (optional, never gate CI):

```bash
uv run pytest -m network
```

## Acceptance gates

- Every failure class in SC-002 has a loopback (or mocked-timeout) test asserting its distinct
  outcome, message, and exit code.
- `--json` batch output contains an entry for **every** input target (FR-013) — verified by a
  mixed-outcome batch test.
- Docs gate: `tls check` has help text + `src/opskit/tls/README.md`; the root README Commands
  table links it; the API example in [contracts/python-api.md](contracts/python-api.md) runs as
  written.
