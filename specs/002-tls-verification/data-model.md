# Phase 1 Data Model: TLS Verification Diagnostics

Conceptual model for the `tls` feature (and the reusable `net` connect primitive). Concrete
types are frozen stdlib `@dataclass`es in `src/opskit/tls/models.py` / `src/opskit/net/tcp.py`
with `to_dict()` for the JSON envelope. No persistence.

## Enumerations

- **TlsOutcome**: `OK`, `EXPIRING_SOON`, `RESOLVE_FAILED`, `CONNECT_REFUSED`,
  `CONNECT_TIMEOUT`, `HANDSHAKE_FAILED`, `CERT_INVALID`, `USAGE_ERROR` — the layered outcome
  classes (spec US3 / FR-005).
- **FindingCode**: `EXPIRED`, `NOT_YET_VALID`, `NAME_MISMATCH`, `SELF_SIGNED`,
  `UNTRUSTED_CHAIN`, `INCOMPLETE_CHAIN`, `NO_SANS`, `EXPIRING_SOON`, `LEGACY_PROTOCOL` — one
  per distinct validation condition (FR-007, FR-009, FR-010).
- **ExitCode** (shared enum, additive members): existing `OK=0`, `ERROR=1`, `USAGE=2`,
  `NXDOMAIN=3` (reused for resolution failure), `TIMEOUT=6`, `PARTIAL=7` **+ new**
  `CONNECT_FAILED=8`, `HANDSHAKE_FAILED=9`, `CERT_INVALID=10`, `CERT_EXPIRING=11` (research R5).

## Entities

### TlsTarget *(what was asked — `tls/models.py`)*
| Field | Type | Notes |
|-------|------|-------|
| `host` | `str` | hostname, IPv4, or IPv6 literal (normalized: trailing dot stripped, brackets removed) |
| `port` | `int` | default 443; from `--port` or `host:port` shorthand (must agree — else UsageError) |
| `server_name` | `str \| None` | SNI actually sent: `--sni` override, else `host` when it is a name, else `None` for IPs |
| `is_ip` | `bool` | drives SNI omission + IP-based name matching |

**Validation**: non-empty host; `1 <= port <= 65535`; shorthand/option port conflict →
`UsageError`; parsing handles `host`, `host:port`, `v6-literal`, `[v6]:port` (bare-v6 colons are
not treated as a port separator).

### TcpConnection *(net primitive result — `net/tcp.py`)*
| Field | Type | Notes |
|-------|------|-------|
| `address` | `str` | IP actually connected to (dual-stack: first success in getaddrinfo order) |
| `family` | `str` | `ipv4` / `ipv6` |
| `port` | `int` | |
| `connect_ms` | `float` | TCP connect duration |

### CertificateInfo *(one parsed certificate — `tls/models.py`)*
| Field | Type | Notes |
|-------|------|-------|
| `subject` | `str` | RFC 4514 string |
| `issuer` | `str` | RFC 4514 string |
| `sans` | `tuple[str, ...]` | DNS + IP SANs, prefixed by type in JSON (`dns:`/`ip:`) |
| `not_before` / `not_after` | `str` | ISO 8601 UTC |
| `days_until_expiry` | `int` | negative when expired |
| `serial` | `str` | hex |
| `signature_algorithm` | `str` | e.g. `sha256WithRSAEncryption` |
| `key_type` / `key_bits` | `str` / `int` | e.g. `RSA`/2048, `EC`/256 |
| `fingerprint_sha256` | `str` | drives the `--watch` change signature (R8) |
| `is_self_signed` | `bool` | subject == issuer && self-verifies |

### ValidationFinding *(one failed/warned condition)*
| Field | Type | Notes |
|-------|------|-------|
| `code` | `FindingCode` | |
| `message` | `str` | human explanation (e.g. requested vs covered names) |
| `hint` | `str \| None` | actionable next step |

### TlsCheckResult *(the full layered report — returned by `opskit.tls.check`)*
| Field | Type | Notes |
|-------|------|-------|
| `target` | `TlsTarget` | |
| `outcome` | `TlsOutcome` | overall verdict class |
| `connection` | `TcpConnection \| None` | populated once TCP succeeds |
| `tls_version` / `cipher` | `str \| None` | negotiated protocol + suite (FR-009) |
| `leaf` | `CertificateInfo \| None` | populated once handshake yields a cert (even if invalid — FR-006) |
| `chain` | `tuple[CertificateInfo, ...]` | as presented by the server, leaf first (FR-011) |
| `findings` | `tuple[ValidationFinding, ...]` | empty on clean pass |
| `elapsed_ms` | `float` | total |
| `.ok` | property | `outcome is OK` |

**State/derivation rules**: `outcome` is the *first failing layer* (resolve → connect →
handshake → validate); certificate findings never mask a connection failure. `EXPIRING_SOON` is
chosen only when validation otherwise passes and `days_until_expiry <= warn_days > 0`.
`findings` may contain multiple certificate conditions (e.g. expired **and** name mismatch) —
all are reported; the exit code is `CERT_INVALID` if any invalid-class finding exists, else
`CERT_EXPIRING` if only the warning exists.

## Error hierarchy (additive)

```
OpskitError (exit ERROR=1)
├── UsageError (exit USAGE=2)                        [existing]
├── DnsError… (exit 3–6)                             [existing]
├── NetError                                         [new — opskit/net/errors.py]
│   ├── ResolutionError   (exit NXDOMAIN=3)
│   ├── ConnectRefused    (exit CONNECT_FAILED=8)    # + unreachable
│   └── ConnectTimeout    (exit TIMEOUT=6)
└── TlsError                                         [new — opskit/tls/errors.py]
    ├── HandshakeError        (exit HANDSHAKE_FAILED=9)   # incl. non-TLS service hint
    ├── CertificateInvalid    (exit CERT_INVALID=10)      # carries findings
    └── CertificateExpiring   (exit CERT_EXPIRING=11)     # warning class
```

Each type owns its `exit_code` (constitution Art. VII); `core` is untouched beyond the additive
enum members. Note: `check()` **returns** a `TlsCheckResult` for completed checks whose outcome
is a certificate condition (details must be reportable, FR-006); it **raises** only when no
report is producible (usage, resolve, connect, handshake) — the CLI maps both paths to codes.
The library-parity wrapper `check(..., raise_on_invalid=True)` raises `CertificateInvalid` /
`CertificateExpiring` for embedding in callers that want exceptions (US7).

## JSON envelope shape (`command: "tls.check"`)

`query` = TlsTarget fields + effective controls (timeout, retries, warn_days, ca_file?);
`result` = TlsCheckResult.to_dict() (outcome, connection, tls_version, cipher, leaf, chain,
findings, elapsed_ms); `error` = envelope error object for raised-path failures (resolve /
connect / handshake), per the batch contract — failed targets always appear (FR-013).
