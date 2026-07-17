# Contract: CLI — proxy mode of `opskit net check` / `opskit net probe`

Additive extension of the `net` command surface (SemVer MINOR — Art. V, IX). No new
commands; `net listen` unchanged. All existing options and behavior are preserved;
direct-check human output is byte-identical to today (SC-006).

## New options (both `check` and `probe`)

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--proxy` | str | — | HTTP proxy to tunnel through: `host:port`, `http://host:port`, or `http://user:pass@host:port`. Any other scheme (socks5, https) → usage error naming it. Overrides env/config. |
| `--no-proxy` | str | — | comma-separated exemptions (exact host or domain suffix, case-insensitive, optional leading dot; `*` = all). When given, **replaces** the `NO_PROXY` env value entirely. |
| `--direct` | flag | off | force a direct check even when env/config nominate a proxy. Combining with `--proxy` → usage error. |

**Proxy resolution order** (when neither `--proxy` nor `--direct` given; CLI layer only):
`HTTPS_PROXY` → `HTTP_PROXY` → `ALL_PROXY`, each uppercase-then-lowercase; first non-empty
wins, regardless of target port (clarification 2026-07-15). Built-in default: **direct**.
Exemptions from `--no-proxy`, else `NO_PROXY`/`no_proxy`. Per-target: an exempt target in a
proxied run is checked directly and its route says so.

**Usage errors (pre-I/O, exit 2)**: `--udp` with a proxy from any source (HTTP proxies
cannot tunnel UDP; hint says to use `--direct` if the env nominates a proxy);
`--proxy` + `--direct`; malformed/unsupported proxy spec.

## Verdicts & exit codes (proxied run)

| Verdict | Meaning / wording anchor | Exit |
|---------|--------------------------|------|
| open (via proxy) | tunnel established; proxy named; tunnel time labeled | 0 |
| proxy resolve failed | the **proxy's** name didn't resolve locally; hint → `opskit dns lookup <proxy>` | 3 |
| proxy refused | connecting **to the proxy** was refused; hint → check proxy address/port | 8 |
| proxy timeout | proxy silent (TCP or after CONNECT); hint → proxy may be down/filtered; retried per `--retries` | 6 |
| proxy auth required | 407; hint → supply `user:pass@` credentials; if only unsupported schemes advertised (e.g. Negotiate), says so honestly | 14 |
| tunnel denied | 403/other 4xx; hint → destination/port may be disallowed by proxy policy | 18 |
| target unreachable via proxy | 5xx; **proxy hop is healthy** — wording distinguishes 504 "target did not answer the proxy" vs 502/503 "unreachable or unresolvable from the proxy" | 19 |
| not an HTTP proxy | endpoint answered but not with an HTTP response | 20 |

Codes 18–20 are new (`TUNNEL_DENIED`, `PROXY_GATEWAY`, `NOT_A_PROXY`); 14 reuses
`AUTH_FAILED`. Batch aggregation is unchanged: 0 all-pass / uniform class / else 7 PARTIAL,
with the new codes participating in uniformity. Every target still gets an envelope,
failures included.

## Envelope: the `route` field (always present — clarification 2026-07-15)

Every `check`/`probe` envelope (JSON and JSONL, success and failure) carries:

```json
"route": {"via": "direct",     "proxy": null,                                  "source": "default"}
"route": {"via": "http-proxy", "proxy": "svc:***@proxy.corp.example:3128",     "source": "env:HTTPS_PROXY"}
"route": {"via": "direct",     "proxy": null,                                  "source": "no-proxy-exemption"}
```

- `source` ∈ `default | flag | env:<VARIABLE_NAME> | config (reserved) | no-proxy-exemption`.
- `proxy` is always the **redacted display** (`user:***@host:port`); the password appears in
  zero bytes of any output, any format, any verdict (SC-004).
- `schema_version` remains `"1"` (additive field).
- The `--proxy` value echoed in the envelope's `query` block is likewise redacted.

## Human output

- Direct runs: unchanged, byte-for-byte.
- Proxied runs add one line naming the (redacted, `escape()`d) proxy and label timing as
  tunnel establishment time, e.g. `via proxy.corp.example:3128 (HTTPS_PROXY) — tunnel 41.2 ms`.
- Failures go to stderr in human mode as today, with the hint from the verdict table.

## Watch mode

`--watch` change detection includes `via` + `proxy`: a route flip (open via proxy → proxy
refused, or exemption change) flags as a change exactly like a verdict flip (FR-019).

## `probe` specifics

- One route decision per run (before the first attempt); pre-flight resolves the **proxy**
  (the proxy resolves the target).
- Each attempt establishes a fresh tunnel (FR-012); per-attempt and min/avg/max timings are
  tunnel establishment times; summary and JSONL stream carry the run's `route`.

## Timeout & retry semantics

`--timeout` applies per stage (proxy TCP connect; CONNECT exchange). `--retries` fires only
on silence (proxy connect timeout / no CONNECT response); definitive answers (refusal, 4xx,
5xx, non-HTTP) are never retried. Worst case per target ≈ `2 × timeout × (retries + 1)`,
documented in `--help`.
