# Quickstart & Validation: Network Connectivity Diagnostics

How to run and validate the `net` feature end-to-end. Commands run from the repo root with
`uv` installed.

> **Shell note**: examples use POSIX syntax (`echo $?`, `&`); in PowerShell read the exit
> code with `$LASTEXITCODE` and run the listener in a second terminal instead of `&`. The
> `opskit` commands themselves are identical on every platform.

## Setup

```bash
uv sync --extra dev
uv run opskit net --help           # group loads; check/probe/listen present
uv run opskit net check --help     # grouped panels + examples
uv run opskit net listen --help
```

## Functional validation (maps to spec user stories)

Rows against local ports are deterministic; rows marked *(net)* need outbound network and
back the `@pytest.mark.network` smoke suite only — CI relies on the loopback suite below.

| # | Scenario | Command | Expected |
|---|----------|---------|----------|
| US1 | open port *(net)* | `uv run opskit net check example.com:443; echo $?` | verdict open; address+family+connect ms; exit 0 |
| US1 | refused | `uv run opskit net check 127.0.0.1:9; echo $?` | "connection refused" + nothing-listening hint; exit 8 (Linux/macOS; Windows loopback may classify as timeout/6 — the CI matrix asserts the class family) |
| US1 | filtered *(net)* | `uv run opskit net check 203.0.113.1:443 --timeout 2; echo $?` | "no response before timeout" + firewall hint, distinct from refused; exit 6 |
| US1 | unresolvable | `uv run opskit net check no-such.invalid:443; echo $?` | resolution failure + `opskit dns` hint; exit 3 |
| US1 | missing port | `uv run opskit net check example.com; echo $?` | usage error before any network I/O; exit 2 |
| US2 | UDP closed | `uv run opskit net check 127.0.0.1:9 --udp; echo $?` | "closed" (port-unreachable signal); exit 8 |
| US2 | UDP inconclusive (paired) | `uv run opskit net listen 5300 --udp --max-events 1 &` then `uv run opskit net check 127.0.0.1:5300 --udp` | listener reports the datagram's peer metadata (definitive service-side answer); `check` stays inconclusive because the receive-only listener never replies — silence alone is never "open" |
| US2 | UDP inconclusive *(net)* | `uv run opskit net check 203.0.113.1:500 --udp --timeout 2; echo $?` | "no response — open or filtered (inconclusive)" + listener hint; exit 6 |
| US3 | probe | `uv run opskit net probe 127.0.0.1:PORT -c 10` (against a local listener) | 10 per-attempt lines + summary with attempts/successes/failures and plausible min/avg/max |
| US3 | watch | `uv run opskit net check 127.0.0.1:PORT --watch 5s` | re-runs; flags open→refused when the listener is stopped mid-watch |
| US4 | batch mixed | `uv run opskit net check -i targets.txt --jsonl; echo $?` (file mixing open/refused/unresolvable) | one envelope per line **including failures**; exit 7 (mixed) |
| US4 | stdin batch | `printf 'web1:443\ndb:5432\n' \| uv run opskit net check -i - --jsonl` | every piped target checked and reported |
| US5 | listener TCP (paired) | `uv run opskit net listen 8080 --max-events 1 &` then `uv run opskit net check 127.0.0.1:8080` | listener prints listening addresses, then the peer address/port/timestamp; stops with summary; exit 0 — the documented smoke test pairing the two commands |
| US5 | port busy | start a listener, then `uv run opskit net listen 8080; echo $?` | "port already in use" + pick-another hint; exit 12 |
| US5 | privileged port | `uv run opskit net listen 80; echo $?` (as a normal user) | permission error + unprivileged-port hint; exit 13 |
| US5 | zero events | `uv run opskit net listen 8081 --max-duration 3s; echo $?` | clean stop, "0 events received" summary; exit 6 (nothing reached me) |
| US6 | programmatic | `uv run python -c "from opskit.net import check; r = check('127.0.0.1:8080'); print(r.verdict.value, r.address, r.time_ms)"` (listener up) | typed result; nothing extra printed |
| US6 | typed errors | see the try/except example in [contracts/python-api.md](contracts/python-api.md) — run it against `127.0.0.1:9` | `ConnectRefused` (or `ConnectTimeout`) is caught specifically, with an actionable hint |

## Deterministic validation (no external network — gates CI)

The suite uses in-process loopback TCP/UDP servers and mocked sockets for the paths
loopback can't produce (filtered/timeout, forced ICMP classification, Windows-style
resets) — see [research.md R6](research.md). Cross-OS variance is asserted as class
families, not exact subclasses.

```bash
uv run pytest tests/unit -k net
uv run pytest tests/integration -k net
uv run pytest -q                                   # full suite, coverage >= 90%
uv run ruff format --check . && uv run ruff check .
uv run mypy src && uv run pyright
```

Real-endpoint smoke tests (optional, never gate CI):

```bash
uv run pytest -m network
```

## Acceptance gates

- Every failure class in SC-002 (usage, unresolvable, refused, timeout, bind-busy,
  bind-permission) has a test asserting its distinct outcome, message/hint, and exit code.
- `--json`/`--jsonl` batch output contains an envelope for **every** input target,
  failures included (FR-014) — verified by a mixed-outcome batch test (SC-004 scale: 50
  targets).
- No test or documented example ever shows a UDP port claimed open without a received
  reply (SC-007).
- The listener⇄check loopback pairing passes in both TCP and UDP modes with exact peer
  metadata (SC-006).
- Docs gate: `net check`/`net probe`/`net listen` each have help text + entries in
  `src/opskit/net/README.md`, linked from the root README Commands table; the API example
  in [contracts/python-api.md](contracts/python-api.md) runs as written (SC-008).
