# Contract: Python API — `opskit.tls` and `opskit.net`

API-first (constitution Art. VII): the CLI is a client of these. The library raises typed
exceptions, never prints or exits, holds no global state, ships `py.typed`. Signatures are
illustrative; they define the SemVer-governed public contract.

## Public surface — `opskit.tls.__all__`

```python
from opskit.tls import (
    check,                                            # convenience function
    TlsTarget, TlsCheckResult, CertificateInfo,       # models
    ValidationFinding, TlsOutcome, FindingCode,       # models / enums
    TlsError, HandshakeError,                         # errors
    CertificateInvalid, CertificateExpiring,
)
```

## Public surface — `opskit.net.__all__` (library-only for now; FR-018)

```python
from opskit.net import (
    resolve, connect,                # primitives the future net category builds on
    TcpConnection,                   # model
    NetError, ResolutionError, ConnectRefused, ConnectTimeout,   # errors
)
```

## Convenience function

```python
def check(
    target: str,                       # "host", "host:port", IP, "[v6]:port"
    *,
    port: int | None = None,           # default 443; conflict with shorthand -> UsageError
    server_name: str | None = None,    # SNI override; None -> hostname (omitted for IPs)
    ca_file: str | Path | None = None, # PEM bundle replacing the platform trust store
    warn_days: int = 30,               # 0 disables the expiring-soon class
    timeout: float = 5.0,
    retries: int = 2,
    raise_on_invalid: bool = False,    # True: raise CertificateInvalid/CertificateExpiring
) -> TlsCheckResult: ...
```

**Raise/return split** (per [data-model.md](../data-model.md)): failures that preclude a report
**raise** (`UsageError`, `ResolutionError`, `ConnectRefused`, `ConnectTimeout`,
`HandshakeError`); completed handshakes **return** a `TlsCheckResult` whose `outcome`/`findings`
carry certificate conditions — so an expired cert's full details are inspectable (FR-006).
`raise_on_invalid=True` opts into exceptions for those too (US7 embedding).

**SNI & validation identity** (as built): when `server_name` is given it is both sent as SNI
*and* used as the identity for name validation — `check("192.0.2.10", server_name="a.corp")`
verifies the `a.corp` identity on that IP. Without it, validation targets the host (IP SANs
for IP targets), and SNI is omitted for IPs.

## net primitives

```python
def resolve(host: str, port: int, *, timeout: float = 5.0) -> list[AddressCandidate]: ...
def connect(host: str, port: int, *, timeout: float = 5.0, retries: int = 2)
    -> tuple[socket.socket, TcpConnection]: ...   # caller owns/closes the socket
```

`connect` tries candidates in `getaddrinfo` order (dual-stack), normalizes `OSError` into the
net error hierarchy, and reports the address actually used.

## Usage example (documented in tls/README.md; must run as written — SC-006)

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

## Compatibility rules

- New exit codes (8–11) and both packages are **additive** → MINOR release.
- `TlsCheckResult.to_dict()` matches the CLI envelope's `result` object exactly.
- `opskit.net` is public from day one but documented as "primitive layer"; the future net
  category adds commands without breaking it.
