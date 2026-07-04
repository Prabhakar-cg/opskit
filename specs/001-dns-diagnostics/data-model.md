# Phase 1 Data Model: DNS Diagnostics

Conceptual model for the DNS feature. Concrete types are stdlib `@dataclass`es (frozen where
practical) in `src/opskit/dns/models.py` and shared envelope/error types in `src/opskit/core/`.
No persistence beyond the TOML profile store.

## Enumerations

- **RecordType**: `A`, `AAAA`, `MX`, `TXT`, `CNAME`, `NS`, `SOA`, `SRV`, `CAA`, `PTR`.
- **Transport**: `AUTO` (UDP then TCP on truncation), `UDP`, `TCP`.
- **Outcome**: `OK`, `NXDOMAIN`, `SERVFAIL`, `REFUSED`, `TIMEOUT`, `USAGE_ERROR`.
- **ExitCode** (`core/exit_codes.py`): `OK=0`, `ERROR=1` (generic/uncategorized), `USAGE=2`,
  `NXDOMAIN=3`, `SERVFAIL=4`, `REFUSED=5`, `TIMEOUT=6`, `PARTIAL=7` (batch: some targets failed).
  Each error type owns its code (`OpskitError.exit_code`), so `core` stays category-agnostic.

## Entities

### DnsQuery  *(what was asked)*
| Field | Type | Notes |
|-------|------|-------|
| `target` | `str` | hostname (forward) or IP (reverse) |
| `record_types` | `tuple[RecordType, ...]` | defaults to `(A,)` for forward |
| `servers` | `tuple[str, ...]` | empty ⇒ system resolver; >1 ⇒ multi-resolver compare |
| `transport` | `Transport` | default `AUTO` |
| `timeout_s` | `float` | per-attempt; default 5.0 |
| `retries` | `int` | default 2 |
| `port` | `int` | default 53 |
| `trace` | `bool` | capture resolution path |

**Validation**: target non-empty and well-formed (hostname or IP as appropriate); record types
recognized; `timeout_s > 0`; `retries >= 0`; `1 <= port <= 65535`. Failures → `UsageError`
(`ExitCode.USAGE`) **before** any network activity.

### DnsRecord  *(one returned datum)*
| Field | Type | Notes |
|-------|------|-------|
| `type` | `RecordType` | |
| `value` | `str` | rendered value (e.g. IP, `10 mail.example.com.` for MX) |
| `ttl` | `int` | seconds |

### LookupResult  *(outcome of a single query against a single resolver)*
| Field | Type | Notes |
|-------|------|-------|
| `query` | `DnsQuery` | echoes the request |
| `resolver` | `Resolver` | which server answered (or system) |
| `outcome` | `Outcome` | |
| `records` | `tuple[DnsRecord, ...]` | empty unless `OK` |
| `elapsed_ms` | `float` | measured latency |
| `trace` | `tuple[TraceStep, ...] | None` | present when `trace=True` |
| `error` | `ResultError | None` | populated for non-`OK` |

Properties: `ok: bool` (`outcome is OK`); iterating a result yields its `records`.

### Resolver
| Field | Type | Notes |
|-------|------|-------|
| `address` | `str` | IP/host of the resolver; `"system"` sentinel for default |
| `label` | `str | None` | optional friendly name (e.g. from a profile) |

### ResolverComparison  *(multi-resolver diff)*
| Field | Type | Notes |
|-------|------|-------|
| `target` | `str` | |
| `results` | `tuple[LookupResult, ...]` | one per resolver |
| `consistent` | `bool` | True when all `OK` results agree on the record set |
| `differences` | `Mapping[str, tuple[DnsRecord, ...]]` | per-resolver differing records when not consistent |

### TraceStep
| Field | Type | Notes |
|-------|------|-------|
| `server` | `str` | resolver/authority queried at this hop |
| `query` | `str` | question asked |
| `outcome` | `Outcome` | |
| `elapsed_ms` | `float` | |

### Profile  *(persisted; TOML `[profiles.<name>]`)*
| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | profile key |
| `servers` | `tuple[str, ...]` | default resolver(s) |
| `transport` | `Transport | None` | |
| `timeout_s` | `float | None` | |
| `retries` | `int | None` | |
| `port` | `int | None` | |

Precedence at resolution time: explicit flag > env > profile value > config `[default]` > built-in.

## Envelope & errors (core)

### ResultError
| Field | Type | Notes |
|-------|------|-------|
| `code` | `str` | stable machine code (e.g. `nxdomain`, `timeout`) |
| `message` | `str` | human summary |
| `hint` | `str | None` | actionable next step (Art. IX / FR-016) |

### JSON envelope (see `contracts/json-envelope.md`)
`{ schema_version, command, query, result, error, elapsed_ms }`. Batch ⇒ top-level array (or one
envelope per line with `--jsonl`).

### Exception hierarchy (`core/errors.py`, `dns/errors.py`)
```
OpskitError
├── UsageError                 # bad input; ExitCode.USAGE; raised before network I/O
└── DnsError                   # base for resolution failures
    ├── NxDomain               # ExitCode.NXDOMAIN
    ├── ServerFailure          # SERVFAIL → ExitCode.SERVFAIL
    ├── DnsRefused             # REFUSED → ExitCode.REFUSED
    ├── DnsTimeout             # no response → ExitCode.TIMEOUT
    └── DnssecError            # validation failure (distinct signal)
```
The library **raises** these; the CLI catches them and maps to `ExitCode` via `core/exit_codes.py`.
