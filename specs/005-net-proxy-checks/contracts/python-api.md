# Contract: Python API — `opskit.net` proxy surface

Additive extension of the public `opskit.net` API (SemVer MINOR). The library never reads
environment variables or configuration files — the proxy is always an explicit argument
(Art. VII; FR-005/FR-020). Nothing prints or exits; failures are typed exceptions.

## New public names (re-exported from `opskit.net`)

```python
from opskit.net import (
    ProxySpec, parse_proxy, proxy_exempt,          # models
    Route,                                          # attached to results
    ProxyError,                                     # base of the proxy-hop family
    ProxyResolutionError, ProxyConnectRefused, ProxyConnectTimeout,
    ProxyAuthRequired, ProxyTunnelDenied, ProxyGatewayError, ProxyProtocolError,
)
```

## `parse_proxy(raw: str) -> ProxySpec`

Parses/validates `host:port` | `http://host:port` | `http://user:pass@host:port` (userinfo
percent-decoded; bracketed IPv6 accepted). Raises `UsageError` pre-I/O for unsupported
schemes, missing/invalid port, empty host, whitespace. `ProxySpec.display` / `str()` is the
redacted rendering (`user:***@host:port`); `repr()` never contains the password.

## `proxy_exempt(host: str, no_proxy: Sequence[str]) -> bool`

Pure exemption predicate: exact host or domain-suffix match, case-insensitive, tolerant of a
leading dot; `"*"` matches everything. The caller (CLI or embedding code) composes this to
decide per-target routing — the library never sees `NO_PROXY` itself.

## `check(...)` / `probe(...)` — new keyword parameter

```python
def check(
    target: str, *, port: int | None = None, protocol: Protocol = Protocol.TCP,
    family: str | None = None, timeout: float = 5.0, retries: int = 2,
    proxy: ProxySpec | str | None = None,          # NEW — str is parsed via parse_proxy
) -> CheckResult: ...
```

- `proxy=None` (default): exact current behavior, `result.route == Route.direct()`.
- `proxy=` given: HTTP CONNECT tunnel through the proxy; on success `CheckResult` has
  `verdict=OPEN`, `route.via == "http-proxy"`, `address`/`family` describing the **proxy
  hop**, `time_ms` = tunnel establishment time. The tunnel is closed immediately; no
  application data is ever sent (FR-008).
- `proxy=` with `protocol=Protocol.UDP`: raises `UsageError` before any I/O (FR-007).
- `family=` constrains the proxy hop (the connection the library itself makes).

`probe(...)` gains the same parameter: one route per run; the pre-flight resolution check
targets the proxy; each attempt builds a fresh tunnel; attempt verdicts may include the new
`Verdict` members (`AUTH_REQUIRED`, `TUNNEL_DENIED`, `GATEWAY_FAILED`, `NOT_A_PROXY`).

## `connect_via_proxy(...)` — reusable primitive (`opskit.net.proxy`)

```python
def connect_via_proxy(
    proxy: ProxySpec, host: str, port: int, *,
    timeout: float = 5.0, retries: int = 2, family: str | None = None,
) -> tuple[socket.socket, TunnelConnection]: ...
```

The proxied analog of `tcp.connect` — the seam future categories (e.g. `tls` via proxy)
build on. Caller owns and must close the returned tunnel socket. Raises the `ProxyError`
family per the classification table (research R4); retries only on silence.

## Exception contract

| Exception | exit_code | Raised when |
|---|---|---|
| `ProxyResolutionError` | 3 | proxy name unresolvable locally |
| `ProxyConnectRefused` | 8 | proxy hop refused/unreachable |
| `ProxyConnectTimeout` | 6 | proxy silent (TCP connect or CONNECT response), retries exhausted |
| `ProxyAuthRequired` | 14 | 407 — credentials absent/rejected/unsupported scheme (message names advertised schemes) |
| `ProxyTunnelDenied` | 18 | 4xx policy denial |
| `ProxyGatewayError` | 19 | 5xx — proxy healthy, target unreachable from it |
| `ProxyProtocolError` | 20 | response is not an HTTP status line |

All subclass `ProxyError` → `NetError` → `OpskitError`; `except ProxyError` is the
documented "was it the proxy hop?" discriminator (`ProxyGatewayError` is the deliberate
target-side member, separable by type or exit code). Every message and hint uses the
redacted proxy display — raw credentials never appear in exception text.

## Backward compatibility

- All new parameters are keyword-only with `None` defaults; existing calls unchanged.
- `CheckResult`/`ProbeResult` gain `route` with a direct default; `to_dict()` output gains
  the always-present `route` object under unchanged `schema_version "1"`.
- Existing exception types, exit codes, and `Verdict` members are untouched; additions only.
