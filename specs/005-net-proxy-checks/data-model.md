# Data Model: Proxy-Aware Reachability Checks

Extends the `net` category's existing frozen-dataclass model (`net/models.py`). All additions
are additive; existing fields and semantics are unchanged. Decisions referenced: R2, R4–R8.

## ProxySpec (new, `net/models.py`)

The parsed, validated proxy the user nominated. Frozen dataclass.

| Field | Type | Notes |
|---|---|---|
| `host` | `str` | proxy hostname or IP literal (normalized like targets; IPv6 accepted bracketed) |
| `port` | `int` | 1–65535; **required** — a proxy spec without a port is a usage error |
| `username` | `Optional[str]` | percent-decoded from userinfo; `None` when absent |
| `password` | `Optional[str]` | percent-decoded; **`repr=False`** — never appears in repr/logs |

**Derived**:
- `display` (property, also `__str__`): the **only** rendering output paths may use —
  `host:port`, or `user:***@host:port` when credentials were supplied (username shown,
  password never — R2).
- `authorization` (property): the `Basic` header value, built from raw credentials at send
  time only; `None` without credentials.

**Validation** (`parse_proxy(raw: str) -> ProxySpec`, raises `UsageError` pre-I/O):
- Accepted forms: `host:port`, `http://host:port[/]`, `http://user:pass@host:port[/]`,
  bracketed IPv6 host in any form.
- Scheme other than `http` (e.g. `https`, `socks5`) → usage error naming the unsupported
  scheme; empty host, missing/invalid/out-of-range port, embedded whitespace → usage error.

## Route (new, `net/models.py`)

How a given target was actually checked. Frozen dataclass, attached to every result.

| Field | Type | Notes |
|---|---|---|
| `via` | `str` | `"direct"` or `"http-proxy"` |
| `proxy` | `Optional[str]` | the proxy's **redacted display** string; `None` when direct |
| `source` | `str` | provenance: `"default"`, `"flag"`, `"env:HTTPS_PROXY"` (winning variable name), `"config"` (reserved), `"no-proxy-exemption"` |

**Constructors**: `Route.direct(source="default")`, `Route.via_proxy(spec, source)`.

**Envelope shape** (always present in every check/probe envelope — Q3 clarification):

```json
"route": {"via": "direct", "proxy": null, "source": "default"}
"route": {"via": "http-proxy", "proxy": "svc:***@proxy.corp.example:3128", "source": "env:HTTPS_PROXY"}
```

`schema_version` stays `"1"` (additive field).

## TunnelConnection (new, `net/proxy.py`)

Facts about an established tunnel — the proxied analog of `TcpConnection`.

| Field | Type | Notes |
|---|---|---|
| `proxy_address` | `str` | proxy IP actually connected to |
| `family` | `str` | `"ipv4"` / `"ipv6"` — the family of the **proxy hop** (the `-4`/`-6` flags constrain this hop) |
| `port` | `int` | target port requested in CONNECT |
| `tunnel_ms` | `float` | proxy TCP connect + CONNECT exchange, wall-clock (labeled tunnel time — R8) |

## CheckResult (extended)

| New field | Type | Notes |
|---|---|---|
| `route` | `Route` | defaults to `Route.direct()` — existing constructions unchanged |

On a proxied OPEN: `address`/`family` describe the **proxy hop** (the connection the tool
itself made); `time_ms` is tunnel establishment time. Human rendering adds a `via <proxy>`
line and labels the timing accordingly (direct rendering byte-identical to today).

## ProbeAttempt / ProbeResult (extended)

- `ProbeResult` gains `route: Route` (one route per run — the proxy decision is made once
  per target, before the first attempt).
- `ProbeAttempt` is unchanged in shape; per-attempt `time_ms` on proxied runs is tunnel
  establishment time; attempt verdicts use the extended `Verdict` set below.
- Pre-flight for proxied probes resolves **the proxy** (not the target — the proxy resolves
  the target; spec edge case), so an unresolvable proxy fails before the first attempt,
  matching the direct pre-flight rule.

## Verdict (extended enum)

Existing members unchanged (`OPEN`, `REFUSED`, `TIMEOUT`, `CLOSED`, `INCONCLUSIVE`,
`RESOLVE_FAILED`). Additive members for proxied outcomes:

| New member | Produced by | Meaning |
|---|---|---|
| `AUTH_REQUIRED` | `ProxyAuthRequired` | proxy demanded/rejected credentials (or unsupported scheme) |
| `TUNNEL_DENIED` | `ProxyTunnelDenied` | proxy refused to tunnel (policy) |
| `GATEWAY_FAILED` | `ProxyGatewayError` | proxy healthy but could not reach the target |
| `NOT_A_PROXY` | `ProxyProtocolError` | nominated endpoint doesn't speak HTTP proxy |

Proxy-hop refusal/timeout/resolution reuse `REFUSED`/`TIMEOUT`/`RESOLVE_FAILED` — the route
plus message wording carry the proxy attribution (R5); `verdict_for()` is extended
accordingly.

## Error hierarchy (extended, `net/errors.py`)

```
ProxyError(NetError)                                  code="proxy_error" (base, never raised)
├── ProxyResolutionError   exit NXDOMAIN (3)          code="proxy_resolve_failed"
├── ProxyConnectRefused    exit CONNECT_FAILED (8)    code="proxy_connect_refused"
├── ProxyConnectTimeout    exit TIMEOUT (6)           code="proxy_connect_timeout"
├── ProxyAuthRequired      exit AUTH_FAILED (14)      code="proxy_auth_required"
├── ProxyTunnelDenied      exit TUNNEL_DENIED (18)    code="proxy_tunnel_denied"
├── ProxyGatewayError      exit PROXY_GATEWAY (19)    code="proxy_gateway_failed"
└── ProxyProtocolError     exit NOT_A_PROXY (20)      code="not_a_proxy"
```

Every message/hint is constructed from `ProxySpec.display` — the raw password cannot reach an
error string (R2). All are catchable as `ProxyError` (the "proxy-hop failure" family the spec
requires to be distinct from target-side failures); `ProxyGatewayError` is the deliberate
target-side member of the subtree, distinguished by its dedicated exit class (Q4).

`core/exit_codes.py` gains `TUNNEL_DENIED = 18`, `PROXY_GATEWAY = 19`, `NOT_A_PROXY = 20`
(enum members only — `core` stays category-agnostic).

## State transitions (proxied check attempt)

```
parse_target ──UDP+proxy──▶ UsageError (pre-I/O)
     │
resolve proxy host ──fail──▶ ProxyResolutionError
     │
tcp.connect(proxy) ──refused──▶ ProxyConnectRefused      ──timeout──▶ retry loop (R8)
     │                                                        └─exhausted─▶ ProxyConnectTimeout
send CONNECT + read status ──silence──▶ retry loop ─exhausted─▶ ProxyConnectTimeout
     │                      ──garbage──▶ ProxyProtocolError
     ├── 2xx ──▶ OPEN (close tunnel immediately; nothing sent through it)
     ├── 407 ──▶ ProxyAuthRequired (schemes parsed from Proxy-Authenticate)
     ├── other 4xx ──▶ ProxyTunnelDenied
     └── 5xx ──▶ ProxyGatewayError (504 = "target silent"; else "unreachable from proxy")
```

Definitive answers (refused, 4xx, 5xx, garbage) are never retried; only silence retries
(FR-011, R4).
