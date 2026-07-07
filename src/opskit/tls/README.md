# `opskit tls` — TLS verification

Read-only TLS/certificate verification that behaves **identically on Windows, macOS, and
Linux** — a single replacement for `openssl s_client` incantations. Available both as a CLI
command and as an importable Python API.

> Part of [**opskit**](../../../README.md). See the root README for install and project-wide docs.

---

## Contents

- [Quick start](#quick-start)
- [`opskit tls check`](#opskit-tls-check)
- [Layered outcomes & exit codes](#layered-outcomes--exit-codes)
- [Trust & name validation](#trust--name-validation)
- [Expiry warnings & watch](#expiry-warnings--watch)
- [Bulk checks](#bulk-checks)
- [Output](#output)
- [Use as a Python library](#use-as-a-python-library)

---

## Quick start

```bash
opskit tls check example.com                     # full verdict, port 443
opskit tls check example.com:8443                # non-standard port (or -p 8443)
opskit tls check 192.0.2.10 --sni internal.corp  # IP endpoint, verify a vhost identity
opskit tls check ldap.corp.example:636 --ca-file corp-root.pem   # private PKI
opskit tls check example.com --json              # machine-readable envelope
```

The report always shows the verdict, the leaf certificate (subject, issuer, SANs, validity,
days to expiry, serial, signature algorithm, key), the presented chain, the negotiated
protocol/cipher, and every failed condition with an actionable hint — **certificate details
are shown even when validation fails**, so you can inspect the bad certificate instead of
just being told it's bad.

## `opskit tls check`

```bash
opskit tls check [TARGET] [OPTIONS]
```

`TARGET` accepts `host`, `host:port`, IPv4/IPv6 literals, or `[ipv6]:port`.

| Option | Description | Default |
|---|---|---|
| `-p, --port` | Port to check (must agree with a `host:port` shorthand) | `443` |
| `--sni` | Server name to send & verify (see below) | target hostname |
| `--ca-file` | PEM bundle replacing the platform trust store (private PKI) | platform store |
| `--warn-days` | Expiring-soon threshold in days (`0` disables) | `30` |
| `--timeout` | Per-attempt timeout (connect and handshake each), seconds | `5.0` |
| `--retries` | Retries on timeout | `2` |
| `-i, --input-file` | File of targets, one `host[:port]` per line (`#` comments allowed) | — |
| `--watch` | Re-run every interval (e.g. `30s`, `2m`) until Ctrl-C | off |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per target | off |
| `--no-color` | Disable colored output (`NO_COLOR` honored too) | off |

## Layered outcomes & exit codes

The check walks resolve → connect → handshake → validate and reports **which layer broke**:

| Code | Meaning | Layer |
|---|---|---|
| `0` | TLS healthy | |
| `1` | generic error | |
| `2` | usage error (bad target/controls; before any network) | pre-flight |
| `3` | name resolution failure | resolve |
| `6` | timeout (connect or handshake, after retries) | connect/handshake |
| `7` | PARTIAL (batch with mixed outcomes) | aggregate |
| `8` | connection failed (refused / unreachable) | connect |
| `9` | handshake failed (incl. non-TLS service; STARTTLS is out of scope) | handshake |
| `10` | certificate invalid (expired, not-yet-valid, name mismatch, self-signed, untrusted/incomplete chain) | validate |
| `11` | certificate valid but expiring soon (within `--warn-days`) | validate |

Distinct certificate conditions are reported separately: **expired** vs **not yet valid**,
**self-signed** vs **untrusted chain** vs **incomplete chain** (server forgot the
intermediate), **name mismatch** (shows requested vs covered names), **no SANs** (legacy
CN-only certificates), and a **legacy protocol** warning below TLS 1.2.

## Trust & name validation

- Chain validation uses the **platform trust store** by default (Windows certificate stores,
  macOS/Linux OpenSSL paths); `--ca-file` replaces it entirely for private PKI.
- Name matching follows RFC 6125: exact DNS-SAN match, a wildcard covers exactly one
  left-most label (`*.example.com` matches `a.example.com`, never `example.com` or
  `a.b.example.com`), IP targets match IP SANs, and there is no CN fallback.
- **SNI**: the target hostname is sent by default and omitted for IP targets. With `--sni`,
  the given name is both sent *and* used for name validation — so
  `opskit tls check 192.0.2.10 --sni internal.corp` verifies the `internal.corp` identity on
  that IP (split-horizon / pre-DNS setups).
- Revocation (OCSP/CRL) is not checked: opskit connects **only** to the endpoint you name.

## Expiry warnings & watch

```bash
opskit tls check example.com --warn-days 14      # exit 11 if < 14 days remain
opskit tls check example.com --watch 30s         # flag outcome/certificate changes
```

`--watch` flags a change when the outcome class, the leaf fingerprint (rotation!), the
expiry date, or the negotiated protocol changes — timing jitter is ignored.

## Bulk checks

```bash
opskit tls check -i endpoints.txt --jsonl | jq .
```

Every target is processed (one failure never aborts the batch); failed targets appear in the
JSON output with their error; the exit code is `0` only if all pass, the class code if all
failures share one class, else `7`.

## Output

`--json` emits the versioned envelope (`schema_version`, `command: "tls.check"`, `query`,
`result`, `error`, `elapsed_ms`); `--jsonl` emits one envelope per line. The `result` object
carries the outcome, connection (address/family/timing), `tls_version`, `cipher`, `leaf`,
`chain`, and `findings`.

## Use as a Python library

```python
from opskit.tls import check, CertificateInvalid

result = check("example.com:443", warn_days=14)
print(result.outcome.value, result.tls_version, result.cipher)
print(result.leaf.subject, result.leaf.days_until_expiry)
for cert in result.chain:
    print(" ", cert.subject, "->", cert.issuer)
for finding in result.findings:
    print("!", finding.code.value, finding.message)

try:
    check("expired.badssl.com", raise_on_invalid=True)
except CertificateInvalid as exc:
    print(exc.message, "—", exc.hint)
```

Completed handshakes **return** a result (even for bad certificates — details stay
inspectable); failures that preclude a report (resolve/connect/handshake) **raise** typed
errors from `opskit.net` / `opskit.tls`. `raise_on_invalid=True` opts into exceptions for
certificate conditions too. The TCP primitive is importable from `opskit.net`
(`resolve`/`connect`) for building your own network tooling.
