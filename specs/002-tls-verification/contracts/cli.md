# Contract: CLI — `opskit tls`

The command surface is public and SemVer-governed (constitution Art. V, IX). Options mirror the
`dns` group's conventions (panels, controls, output flags).

## Command

```bash
opskit tls check [TARGET] [OPTIONS]
```

`TARGET` accepts `host`, `host:port`, an IPv4/IPv6 literal, or `[ipv6]:port`. Optional when
`--input-file` supplies targets.

## Options

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-p, --port` | int | `443` | must agree with a `host:port` shorthand if both given, else usage error |
| `--sni` | str | target hostname | server name sent in the handshake; omitted automatically for IP targets |
| `--ca-file` | path | platform trust store | PEM bundle replacing the system store (private PKI) |
| `--warn-days` | int | `30` | expiring-soon threshold; `0` disables the warning class |
| `--timeout` | float | `5.0` | per-attempt (connect and handshake each) |
| `--retries` | int | `2` | on timeout |
| `-i, --input-file` | path | — | one target per line (`host[:port]`), `#` comments/blank lines ignored |
| `--watch` | str | — | re-run every interval (`5s`, `2m`, `250ms`) until Ctrl-C; flags outcome/cert changes |
| `--json` / `--jsonl` | flag | off | versioned envelope / NDJSON per target |
| `--no-color` | flag | off | force plain output (NO_COLOR honored too) |

## Report content (human default)

Verdict line (outcome + one-line reason) → leaf summary table (subject, issuer, SANs, validity,
days to expiry, serial, sig alg, key) → chain table (one row per presented cert) → negotiated
protocol/cipher (+ legacy-protocol warning < TLS 1.2) → findings with hints. Certificate details
are shown **even when validation fails** (FR-006). Batch mode prefixes each target's section.

## Exit codes (additive to `core/exit_codes.py`)

| Code | Meaning | Layer |
|------|---------|-------|
| 0 | success — TLS healthy | |
| 1 | generic error | |
| 2 | usage error (bad target/controls; before any network) | pre-flight |
| 3 | name resolution failure (shared class with DNS NXDOMAIN) | resolve |
| 6 | timeout (connect or handshake; after retries) | connect/handshake |
| 7 | PARTIAL (batch with mixed outcomes) | aggregate |
| **8** | **connection failed** (refused / unreachable) | connect |
| **9** | **handshake failed** (incl. non-TLS service on port; STARTTLS hint) | handshake |
| **10** | **certificate invalid** (expired, not-yet-valid, name mismatch, self-signed, untrusted/incomplete chain) | validate |
| **11** | **certificate expiring soon** (valid, within `--warn-days`) | validate |
| 12 | port already in use *(net listener bind — shared enum)* | — |
| 13 | bind permission denied *(net listener bind — shared enum)* | — |
| 14–17 | ad category: auth failed / permission denied / not found / not a member *(shared enum)* | — |

Batch rule (constitution Art. IX): every target processed; exit 0 only if all pass; the uniform
class if all failures share one class; else 7. Failed targets always appear in `--json`/`--jsonl`
output with `result: null` (or the completed result) and a populated `error`.

## JSON envelope

`schema_version "1"`, `command "tls.check"`; `query` echoes the target + effective controls;
`result` per [data-model.md](../data-model.md). Example (elided):

```json
{
  "schema_version": "1",
  "command": "tls.check",
  "query": {"host": "example.com", "port": 443, "server_name": "example.com",
             "timeout": 5.0, "retries": 2, "warn_days": 30},
  "result": {
    "outcome": "ok",
    "connection": {"address": "93.184.216.34", "family": "ipv4", "port": 443, "connect_ms": 18.2},
    "tls_version": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384",
    "leaf": {"subject": "CN=example.com", "issuer": "CN=DigiCert…", "sans": ["dns:example.com"],
              "not_before": "…", "not_after": "…", "days_until_expiry": 187, "serial": "0A…",
              "signature_algorithm": "sha256WithRSAEncryption", "key_type": "EC", "key_bits": 256,
              "fingerprint_sha256": "…", "is_self_signed": false},
    "chain": ["…leaf…", "…intermediate…"],
    "findings": [],
    "elapsed_ms": 231.4
  },
  "error": null,
  "elapsed_ms": 231.4
}
```

## Examples (epilog)

```bash
opskit tls check example.com
opskit tls check example.com:8443
opskit tls check 192.0.2.10 -p 8443 --sni internal.example.com
opskit tls check ldap.corp.example:636 --ca-file corp-root.pem
opskit tls check example.com --warn-days 14
opskit tls check -i endpoints.txt --jsonl
opskit tls check example.com --watch 30s
```
