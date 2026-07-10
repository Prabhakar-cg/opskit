# Contract: CLI surface — `opskit dns`

Thin Typer sub-app. The CLI parses/validates args and renders results; all logic is in `dns/api.py`.

## Commands

```
opskit dns lookup  TARGET... [options]     # forward lookup
opskit dns reverse TARGET... [options]     # reverse (PTR) lookup
```

`TARGET...` may be given as positional args, from `--file`, or from stdin (`-`). Multiple targets ⇒
batch. `lookup` accepts hostnames; `reverse` accepts IPv4/IPv6 addresses.

## Options (both commands)

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-t, --type` | repeatable enum | `A` (lookup) | A/AAAA/MX/TXT/CNAME/NS/SOA/SRV/CAA; ignored for `reverse` |
| `-s, --server` | repeatable str | system | resolver(s); ≥2 enables compare/diff |
| `--transport` | enum | `auto` | `auto`\|`udp`\|`tcp` |
| `--timeout` | float | `5.0` | per-attempt seconds |
| `--retries` | int | `2` | |
| `--port` | int | `53` | |
| `--diff` | flag | off | with ≥2 servers, highlight differences |
| `--trace` | flag | off | show resolution path |
| `--watch` | duration | off | re-run every interval (e.g. `5s`) |
| `--file` | path | — | read targets (one per line) |
| `--profile` | str | — | apply a saved profile's settings |
| `--config` | path | — | use an explicit config file |
| `--json` | flag | off | machine-readable envelope (array for batch) |
| `--jsonl` | flag | off | NDJSON, one envelope per line |
| `--quiet / --verbose` | flag | — | verbosity |
| `--no-color` | flag | off | also honors `NO_COLOR` env |

Human output is colorized/tabular on a TTY and auto-plain when piped.

## Exit codes (see `core/exit_codes.py`)

| Code | Meaning |
|------|---------|
| 0 | success |
| 1 | generic error (uncategorized failure) |
| 2 | usage error (bad args/input; before network) |
| 3 | NXDOMAIN (name does not exist) |
| 4 | SERVFAIL |
| 5 | REFUSED |
| 6 | TIMEOUT / no response |
| 7 | PARTIAL (batch: at least one target failed) |
| 8 | connection failed *(shared enum; used by tls/net categories)* |
| 9 | TLS handshake failed *(tls)* |
| 10 | certificate invalid *(tls)* |
| 11 | certificate expiring soon *(tls)* |
| 12 | port already in use *(net listener bind)* |
| 13 | bind permission denied *(net listener bind)* |
| 14 | authentication failed *(ad bind rejected)* |
| 15 | permission denied *(ad: bound but not authorized)* |
| 16 | not found *(ad principal/group/object)* |
| 17 | not a member *(ad membership-test verdict)* |

Batch rule: exit 0 only if every target succeeds; otherwise the most severe outcome's code (or 7
when outcomes are mixed).

## Examples

```
opskit dns lookup example.com -t MX -t TXT
opskit dns lookup example.com -s 1.1.1.1 -s 8.8.8.8 --diff
opskit dns lookup api.example.com --transport tcp --timeout 3 --json
opskit dns reverse 8.8.8.8
cat hosts.txt | opskit dns lookup - --jsonl
opskit dns lookup example.com --watch 5s
```
