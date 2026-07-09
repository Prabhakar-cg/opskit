# `opskit net` — network reachability

Read-only TCP/UDP connectivity diagnostics, identical on Windows/macOS/Linux — the
answers `telnet`, `nc`, and `Test-NetConnection` give, without needing any of them
installed. Three commands: `check` (single-shot port verdict), `probe` (ping-style
repeated probes with statistics), and `listen` (temporary metadata-only listener for
"is anything reaching me?").

Targets are always explicit, user-listed endpoints — there are **no port ranges, no
CIDR expansion, no host discovery**. Outbound traffic is exactly the requested
connection attempt: TCP sends no application data; UDP sends one zero-byte probe
datagram; the listener binds only your port and never sends.

## Contents

- [Quick start](#quick-start)
- [`opskit net check`](#opskit-net-check)
- [`opskit net probe`](#opskit-net-probe)
- [`opskit net listen`](#opskit-net-listen)
- [Verdicts & exit codes](#verdicts--exit-codes)
- [UDP honesty](#udp-honesty)
- [Bulk checks](#bulk-checks)
- [Output](#output)
- [Use as a Python library](#use-as-a-python-library)

## Quick start

```bash
opskit net check db.example.com:5432          # is this TCP port open?
opskit net check 10.0.0.5 -p 22               # bare host + --port
opskit net check ntp.example.com:123 --udp    # honest UDP verdict
opskit net probe api.example.com:443 -c 20    # latency/stability statistics
opskit net listen 8080                        # is anything reaching me?
```

A target is `host:port`, `[ipv6]:port`, or a bare host/IP combined with `-p`. **A
target with no port anywhere is a usage error — there is no default port**: the port
is the thing being diagnosed.

## `opskit net check`

Single-shot reachability verdict for one or more targets, batchable and watchable.

| Option | Description | Default |
|---|---|---|
| `-p, --port` | Port for targets given without `:port` (must agree with any shorthand) | — |
| `-u, --udp` | UDP mode: honest open / closed / inconclusive verdicts | TCP |
| `-4, --ipv4` / `-6, --ipv6` | Restrict the address family (mutually exclusive) | both |
| `--timeout` | Per-attempt timeout, seconds | `5.0` |
| `--retries` | Retries on timeout/silence (a refusal/unreachable is definitive) | `2` |
| `-i, --input-file` | File of targets, one per line (`#` comments allowed); `-` reads stdin | — |
| `--watch` | Re-run every interval (e.g. `30s`, `2m`) until Ctrl-C; flags verdict/address changes | off |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per target | off |
| `--no-color` | Disable colored output (`NO_COLOR` honored too) | off |

```bash
opskit net check web1:443 web2:443 db:5432 --jsonl
cat endpoints.txt | opskit net check -i - --jsonl
opskit net check api.example.com:443 --watch 30s
```

## `opskit net probe`

Ping-style repeated probes of **one** target: a per-attempt line as each completes,
then a summary (attempts, successes, failures, min/avg/max ms; UDP additionally
replies / closed signals / silence). A failing attempt never aborts the run, and
Ctrl-C mid-run still prints the summary of completed attempts.

| Option | Description | Default |
|---|---|---|
| `-c, --count` | Number of attempts | `4` |
| `--interval` | Delay between attempt starts (`500ms`, `2s`, `1m`) | `1s` |
| `-p` / `-u` / `-4` / `-6` / `--timeout` | As in `check` | |
| `--retries` | Retries within one attempt (the count is the retry story) | `0` |
| `--json` / `--jsonl` | One envelope for the run / one envelope per attempt + a summary envelope | off |
| `--no-color` | Disable colored output | off |

```bash
opskit net probe api.example.com:443 -c 20 --interval 500ms
opskit net probe dns.example.com:53 --udp -c 10
```

**Exit**: aggregate over attempts — `0` if all succeeded, the uniform failure class if
all failed identically, else `7` (PARTIAL).

## `opskit net listen`

A temporary diagnostic listener for the service side of "is it the network or me?":
binds the wildcard address on both available families and reports each inbound TCP
connection or UDP datagram as **metadata only** — peer address, peer port, timestamp.
Payload bytes are never read, shown, or stored; the listener never sends anything; it
always ends with a summary. Ctrl-C stops it cleanly on every platform.

| Option | Description | Default |
|---|---|---|
| `-u, --udp` | Receive datagrams instead of accepting connections | TCP |
| `--max-duration` | Stop after this long (`30s`, `5m`) | until Ctrl-C |
| `--max-events` | Stop after N connections/datagrams | — |
| `--json` / `--jsonl` | One envelope for the session / one per event + a session envelope | off |
| `--no-color` | Disable colored output | off |

```bash
opskit net listen 8080
opskit net listen 514 --udp --max-duration 5m
opskit net listen 9000 --max-events 1 --json
```

**Exit**: `0` on Ctrl-C or when `--max-events` is reached; `0` when `--max-duration`
expires having received at least one event; `6` when it expires with **zero** events
("nothing reached me" — the branchable answer); `12` port already in use; `13` bind
permission denied (hint: pick an unprivileged port ≥ 1024).

## Verdicts & exit codes

| Code | Meaning | Used by |
|---|---|---|
| `0` | success — open / all probes succeeded / listener clean stop | all |
| `1` | generic error | all |
| `2` | usage error (missing/conflicting port, bad flags; before any network I/O) | all |
| `3` | name resolution failure (no address, or none in the requested family) | check, probe |
| `6` | timeout / no response (TCP possibly filtered; UDP inconclusive; listener zero-event expiry) | all |
| `7` | PARTIAL — mixed batch or mixed probe attempts | check, probe |
| `8` | connection refused (TCP) / port closed (UDP unreachable signal) | check, probe |
| `12` | port already in use (listener bind) | listen |
| `13` | bind permission denied (listener bind) | listen |

TCP verdicts: **open** (connected; address/family/connect-time shown), **refused**
(host answered, nothing listening — exit 8), **timeout** (nothing answered, possibly
firewall-filtered — exit 6), **resolve failed** (exit 3).

## UDP honesty

UDP has no handshake, so `--udp` reports exactly three states and never guesses:

- **open** — a reply datagram was received (the only way UDP is ever called open)
- **closed** — the host signaled ICMP *port unreachable* (definitive, like a TCP refusal)
- **inconclusive** — no response: the port is **open or filtered**. Silence is never
  reported as closed. The hint points at the service side (`opskit net listen <port>
  --udp`) and at protocol-aware tooling (e.g. `opskit dns` for DNS ports) — many UDP
  services simply don't reply to an empty probe datagram.

## Bulk checks

`check` processes **every** target (positionals + `--input-file`/stdin, first-appearance
order) and never aborts on the first failure. In `--json`/`--jsonl` every target gets an
envelope — failures carry `result: null` and a populated `error` object, never dropped.
Human-mode failures go to stderr. `-p` applies to any listed target that has no port of
its own.

```bash
opskit net check -i endpoints.txt --jsonl   # one NDJSON line per endpoint
```

## Output

Human-readable by default; `--json` emits the versioned envelope (`schema_version "1"`,
commands `net.check` / `net.probe` / `net.listen`); `--jsonl` streams NDJSON — per
target (check), per attempt then a `"kind": "summary"` envelope (probe), per event then
a `"kind": "session"` envelope (listen). `NO_COLOR` and piped output disable styling
automatically.

```json
{"schema_version": "1", "command": "net.check",
 "query": {"host": "db.example.com", "port": 5432, "protocol": "tcp", "family": null,
            "timeout": 5.0, "retries": 2},
 "result": {"verdict": "open", "address": "192.0.2.10", "family": "ipv4",
             "port": 5432, "time_ms": 12.4},
 "error": null, "elapsed_ms": 12.4}
```

## Use as a Python library

The CLI is a thin client of the typed `opskit.net` API — same verdicts, no printing,
typed exceptions (each owning its exit code):

```python
from opskit.net import check, probe, Listener, Protocol, ConnectRefused, ConnectTimeout

result = check("db.example.com:5432")
print(result.verdict.value, result.address, result.family, result.time_ms)

try:
    check("db.example.com", port=5433)
except ConnectRefused as exc:
    print(exc.message, "—", exc.hint)
except ConnectTimeout as exc:
    print("filtered?", exc.message)

stats = probe("api.example.com:443", count=10, interval=0.5)
print(stats.successes, "/", stats.completed, "min/avg/max:",
      stats.min_ms, stats.avg_ms, stats.max_ms)

with Listener(8080, protocol=Protocol.TCP, max_events=1) as listener:
    for event in listener.events():
        print("inbound:", event.peer_address, event.peer_port, event.timestamp)
print(listener.session.stop_reason.value, listener.session.events_received)
```

`check` returns only the OPEN verdict; every other single-shot outcome raises the
matching typed error (`ConnectRefused`, `ConnectTimeout`, `UdpClosed`,
`UdpInconclusive`, `ResolutionError`, `UsageError`). `probe` never raises for
per-attempt failures — attempts are data (`ProbeAttempt.verdict`). `Listener` raises
`PortInUse` / `BindPermissionDenied` at bind time.
