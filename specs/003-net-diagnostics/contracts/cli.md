# Contract: CLI — `opskit net`

The command surface is public and SemVer-governed (constitution Art. V, IX). Options mirror
the `dns`/`tls` groups' conventions (panels, controls, output flags). Three commands:
`check`, `probe`, `listen`.

## `opskit net check`

```bash
opskit net check [TARGETS]... [OPTIONS]
```

`TARGETS` is **variadic**: each is `host:port`, `[ipv6]:port`, or a bare `host`/IP combined
with `-p`. **A target with no port anywhere is a usage error — there is no default port**
(FR-001). Targets may also come from `--input-file` (or stdin via `-i -`); positionals and
file targets combine, positionals first.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-p, --port` | int | — | port for targets given without shorthand; must agree with any `host:port` shorthand, else usage error |
| `-u, --udp` | flag | off (TCP) | UDP mode: honest open / closed / inconclusive verdicts (FR-008) |
| `-4, --ipv4` / `-6, --ipv6` | flag | off | restrict address family; mutually exclusive; no address in the family → resolution-class failure (FR-003) |
| `--timeout` | float | `5.0` | per-attempt, seconds |
| `--retries` | int | `2` | on timeout/silence only — a refusal (TCP) or unreachable signal (UDP) is definitive and not retried |
| `-i, --input-file` | path | — | one target per line, `#` comments/blank lines ignored; **`-` reads stdin** (FR-014) |
| `--watch` | str | — | re-run every interval (`5s`, `2m`, `250ms`) until Ctrl-C; flags verdict/address/family changes (research R8) |
| `--json` / `--jsonl` | flag | off | versioned envelope / NDJSON, one envelope per target |
| `--no-color` | flag | off | force plain output (`NO_COLOR` and piped output honored automatically) |

**Verdicts** (each with its own exit class and actionable hint — FR-005/FR-008):

| Mode | Verdict | Meaning | Exit |
|------|---------|---------|------|
| TCP | open | connected; address, family, connect time shown | 0 |
| TCP | refused | host answered, nothing listening | 8 |
| TCP | timeout | nothing answered — possibly filtered (hint: firewall) | 6 |
| UDP | open | a reply datagram was received (never claimed otherwise — SC-007) | 0 |
| UDP | closed | host signaled port unreachable | 8 |
| UDP | inconclusive | "no response — open or filtered (inconclusive)"; hint → `net listen` on the service side | 6 |
| both | resolve failed | name didn't resolve / no address in requested family; hint → `opskit dns` | 3 |

## `opskit net probe`

```bash
opskit net probe TARGET [OPTIONS]
```

Single target (batch stability streams don't interleave — fleet checks are `check`'s job).
Ping-style repeated probes; per-attempt lines as they happen, then a summary (attempts,
successes, failures, min/avg/max ms; UDP additionally replies / closed signals / silence).
A failing attempt never aborts the run; Ctrl-C mid-run still prints the summary of
completed attempts (FR-009).

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-c, --count` | int | `4` | number of attempts (ping-like) |
| `--interval` | str | `1s` | delay between attempt starts (`500ms`, `2s`, `1m`) |
| `-p, --port` / `-u, --udp` / `-4` / `-6` / `--timeout` | | | as in `check` |
| `--retries` | int | `0` | within one attempt; the count is the retry story |
| `--json` / `--jsonl` / `--no-color` | flag | off | `--jsonl` streams one envelope per attempt + a summary envelope |

**Exit**: aggregate over attempts — `0` if all succeeded, the uniform class if all failed
identically, else `7` (PARTIAL).

## `opskit net listen`

```bash
opskit net listen PORT [OPTIONS]
```

Temporary diagnostic listener (constitution Art. X's sanctioned example): binds the wildcard
address on both available families, reports each inbound TCP connection or UDP datagram as
**metadata only** (peer address, peer port, timestamp — payload never read/shown/stored),
sends nothing, and always ends with a summary. Ctrl-C stops it cleanly on every platform.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-u, --udp` | flag | off (TCP) | receive datagrams instead of accepting connections |
| `--max-duration` | str | — | stop after this long (`30s`, `5m`) |
| `--max-events` | int | — | stop after N connections/datagrams |
| `--json` / `--jsonl` / `--no-color` | flag | off | `--jsonl` streams one envelope per event + a session summary envelope |

**Exit**: `0` on Ctrl-C or when `--max-events` is reached; `0` when `--max-duration`
expires having received ≥ 1 event; **`6`** (no-response class) when `--max-duration`
expires with zero events — "nothing reached me" is the branchable diagnostic answer;
`12` port already in use; `13` bind permission denied (research R4).

## Exit codes (additive to `core/exit_codes.py`)

| Code | Meaning | Used by |
|------|---------|---------|
| 0 | success (open / probe all-success / listener clean stop) | all |
| 1 | generic error | all |
| 2 | usage error (missing/conflicting port, bad flags/controls — before any network I/O) | all |
| 3 | name resolution failure (shared class with DNS/tls) | check, probe |
| 6 | timeout / no response (TCP filtered; UDP inconclusive; listener zero-event expiry) | all |
| 7 | PARTIAL (mixed batch / mixed probe attempts) | check, probe |
| 8 | connection refused (TCP) / port closed (UDP unreachable signal) | check, probe |
| **12** | **port already in use** (listener bind) | listen |
| **13** | **bind permission denied** (listener bind; hint: unprivileged port) | listen |

Batch rule (Art. IX): every target processed; exit `0` only if all pass; the uniform class
if all failures share one class; else `7`. Failed targets always appear in
`--json`/`--jsonl` output with `result: null` and a populated `error`.

## JSON envelopes

`schema_version "1"`; commands `net.check`, `net.probe`, `net.listen`. `query` echoes the
parsed target + effective controls. Shapes per [data-model.md](../data-model.md).

`net check --json` (open):

```json
{
  "schema_version": "1",
  "command": "net.check",
  "query": {"host": "db.example.com", "port": 5432, "protocol": "tcp", "family": null,
             "timeout": 5.0, "retries": 2},
  "result": {"verdict": "open", "address": "192.0.2.10", "family": "ipv4",
              "port": 5432, "time_ms": 12.4},
  "error": null,
  "elapsed_ms": 12.4
}
```

`net check --jsonl` failure line (UDP inconclusive — never dropped):

```json
{"schema_version": "1", "command": "net.check",
 "query": {"host": "vpn.example.com", "port": 500, "protocol": "udp", "family": null,
            "timeout": 5.0, "retries": 2},
 "result": null,
 "error": {"code": "udp_inconclusive",
            "message": "no response from vpn.example.com:500 — open or filtered (inconclusive)",
            "hint": "silence does not mean closed: check from the service side with 'opskit net listen 500 --udp', or use protocol-aware tooling (e.g. 'opskit dns' for DNS ports)"},
 "elapsed_ms": 15012.0}
```

`net probe --jsonl` stream (`result.kind` discriminates attempt vs summary):

```json
{"schema_version": "1", "command": "net.probe", "query": {"host": "api.example.com", "port": 443, "...": "..."},
 "result": {"kind": "attempt", "index": 1, "verdict": "open", "address": "203.0.113.7",
             "family": "ipv4", "time_ms": 18.1, "error": null}, "error": null, "elapsed_ms": 18.1}
{"schema_version": "1", "command": "net.probe", "query": {"...": "..."},
 "result": {"kind": "summary", "requested": 4, "completed": 4, "successes": 3, "failures": 1,
             "replies": 0, "closed_signals": 0, "silent": 0,
             "min_ms": 17.2, "avg_ms": 19.0, "max_ms": 21.3}, "error": null, "elapsed_ms": 3095.2}
```

`net listen --jsonl` stream:

```json
{"schema_version": "1", "command": "net.listen", "query": {"port": 8080, "protocol": "tcp"},
 "result": {"kind": "event", "index": 1, "peer_address": "198.51.100.23", "peer_port": 52114,
             "family": "ipv4", "timestamp": "2026-07-09T10:15:02.114Z"}, "error": null, "elapsed_ms": 4211.0}
{"schema_version": "1", "command": "net.listen", "query": {"port": 8080, "protocol": "tcp"},
 "result": {"kind": "session", "bound_addresses": ["0.0.0.0", "::"], "started_at": "…",
             "stopped_at": "…", "stop_reason": "interrupt", "events_received": 1,
             "max_duration_s": null, "max_events": null}, "error": null, "elapsed_ms": 30412.0}
```

## Scope guarantees (FR-018/FR-019)

No port ranges, no CIDR/address-range expansion, no host discovery — targets are always
explicit, user-listed endpoints. Outbound traffic is exactly the requested connection
attempt (TCP: no application data; UDP: one zero-byte probe datagram). The listener binds
only the user's port and never sends.

## Examples (epilogs)

```bash
opskit net check db.example.com:5432
opskit net check 10.0.0.5 -p 22
opskit net check [2001:db8::7]:443 -6
opskit net check ntp.example.com:123 --udp
opskit net check web1:443 web2:443 db:5432 --jsonl
opskit net check -i endpoints.txt --jsonl
cat endpoints.txt | opskit net check -i - --jsonl
opskit net check api.example.com:443 --watch 30s

opskit net probe api.example.com:443 -c 20 --interval 500ms
opskit net probe dns.example.com:53 --udp -c 10

opskit net listen 8080
opskit net listen 514 --udp --max-duration 5m
opskit net listen 9000 --max-events 1 --json
```
