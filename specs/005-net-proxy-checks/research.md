# Research: Proxy-Aware Reachability Checks

Technical decisions resolving the plan's unknowns. Numbered R1–R11; the plan and contracts
reference these.

## R1 — CONNECT primitive: hand-rolled minimal HTTP over stdlib sockets

**Decision**: New `net/proxy.py` module. Reach the proxy with the existing `tcp.connect()`
(free reuse of refused/timeout normalization, family restriction, and candidate ordering),
then speak the minimal CONNECT exchange by hand on the returned socket: send
`CONNECT host:port HTTP/1.1\r\nHost: host:port\r\n[Proxy-Authorization: Basic …]\r\n\r\n`,
read the status line, drain headers to the blank line under the per-attempt timeout, parse
only the status code and (on 407) the `Proxy-Authenticate` scheme list. The tunnel socket is
closed immediately after the verdict — nothing is ever sent through it (FR-008).

**Rationale**: full control over failure classification (403 vs 502 vs garbage vs silence),
timeouts, and byte hygiene, with zero new dependencies. The exchange is ~30 lines; CONNECT
responses have no body to parse (any body on an error response is drained/ignored).

**Alternatives considered**: `http.client.HTTPConnection.set_tunnel()` — swallows the CONNECT
status into a formatted `OSError` string, making 407/403/502 classification string-scraping;
no per-stage timeout control. `urllib.request`/`requests` — auto-reads proxy env vars inside
the library (violates Art. VII) and `requests` is a new runtime dependency for one request.

## R2 — Proxy specification parsing & redaction-by-construction

**Decision**: `parse_proxy()` in `net/models.py` accepts `host:port`, `http://host:port`, and
`http://user:pass@host:port` (userinfo percent-decoded via `urllib.parse`); a bare spec gets
the implicit `http` scheme. Any other scheme (`https`, `socks5`, …) → `UsageError` naming the
unsupported scheme (FR-006, spec assumption). Result is a frozen `ProxySpec` dataclass whose
`password` field is declared `repr=False`; its `display` property (and `__str__`) render
`user:***@host:port` / `host:port` — the **only** rendering any output path may use. The raw
password is read solely to build the `Proxy-Authorization` header.

**Rationale**: redaction enforced structurally, not by discipline — no call site can
accidentally interpolate the secret because no default rendering contains it (FR-014,
Art. III). `user:***@` (curl convention) discloses that credentials were supplied, satisfying
the spec's "username appears is fixed and documented" note: **username shown, password never**.

**Alternatives considered**: redacting at render time with a scrub helper — every new call
site is a leak risk; rejected. Hiding the username too — hurts diagnosability (which account
was used) with no secrecy gain; rejected.

## R3 — Environment discovery (CLI layer only)

**Decision**: a `resolve_proxy_config()` helper in `net/cli.py` (never the library) applies
the clarified fixed order regardless of target port: `HTTPS_PROXY` → `HTTP_PROXY` →
`ALL_PROXY`, checking the uppercase then lowercase form of each; exemptions from
`NO_PROXY`/`no_proxy` (comma-separated; exact-host or domain-suffix match, case-insensitive,
optional leading dot; `*` exempts everything). The winning variable name is recorded as the
route's `source` (`flag` / `env:HTTPS_PROXY` / …) for disclosure. Precedence: `--proxy` flag
> env > built-in **direct**. `--direct` short-circuits everything.

**Rationale**: matches the Session 2026-07-15 clarification and the curl mental model; the
recorded source makes surprising env routing self-explanatory in output (spec Story 1).
Note: the profile/config-file layers of the constitution's precedence chain do not exist in
the codebase yet (no `core` config module); this feature implements flag > env > built-in and
slots cleanly into the full chain when config support lands — the helper is the single place
to extend.

**Alternatives considered**: `urllib.request.getproxies()` — scheme-keyed dict with platform
magic (Windows registry, macOS SystemConfiguration), unpredictable across the CI matrix and
impossible to reconcile with the clarified fixed order; rejected. Typer's `envvar=[…]` — can
express ordered vars but not lowercase pairs, NO_PROXY semantics, or source provenance;
rejected.

## R4 — CONNECT status classification

**Decision**: mapping owned by `net/proxy.py`:

| Proxy behavior | Typed error (R5) | Retried? |
|---|---|---|
| TCP refused / unreachable at proxy | `ProxyConnectRefused` | no (definitive) |
| TCP timeout at proxy / silence after CONNECT sent | `ProxyConnectTimeout` | yes |
| 2xx status | success — tunnel established | — |
| 407 | `ProxyAuthRequired` (message names the advertised schemes; if none supported — e.g. only `Negotiate` — says so per FR-015) | no |
| 403 and any other 4xx | `ProxyTunnelDenied` | no |
| 504 | `ProxyGatewayError` — "target did not answer the proxy" flavor | no |
| 502/503 and any other 5xx | `ProxyGatewayError` — "unreachable/unresolvable from the proxy" flavor | no |
| Response that is not `HTTP/x.y NNN …` | `ProxyProtocolError` ("does not behave like an HTTP proxy") | no |

**Rationale**: implements FR-009's six outcomes and FR-011's retry rule (only silence is
retried; every answered outcome is definitive). The 504-vs-other split gives the clarified
gateway-flavor wording without inventing sub-verdicts.

## R5 — Error types and exit codes

**Decision**: new subtree in `net/errors.py`, each type owning its exit code (Art. VII):

```
ProxyError(NetError)                      # base; never raised directly
├── ProxyResolutionError    → NXDOMAIN (3)        # proxy name didn't resolve locally
├── ProxyConnectRefused     → CONNECT_FAILED (8)  # proxy hop refused/unreachable
├── ProxyConnectTimeout     → TIMEOUT (6)         # proxy hop silent (TCP or CONNECT)
├── ProxyAuthRequired       → AUTH_FAILED (14)    # reused class (established by ad)
├── ProxyTunnelDenied       → TUNNEL_DENIED (18)  # NEW ExitCode member
├── ProxyGatewayError       → PROXY_GATEWAY (19)  # NEW — per clarification: dedicated class
└── ProxyProtocolError      → NOT_A_PROXY (20)    # NEW — nominated endpoint isn't a proxy
```

Three additive `ExitCode` members (18–20); `core/exit_codes.py` gains only enum members — no
category knowledge (Art. VII). Proxy-unreachable deliberately reuses 8/6 per the spec: within
a proxied run those codes are unambiguous (a direct-style target refusal cannot occur — the
proxy reports it as a gateway failure instead), and route + wording carry the attribution.

**Rationale**: matches spec FR-009 and the Q4 clarification (dedicated gateway class);
follows the established "each error owns its code" pattern; reusing `AUTH_FAILED` mirrors how
`net` already reuses `NXDOMAIN` for resolution failures.

## R6 — Route disclosure in models and envelopes

**Decision**: new frozen `Route` dataclass (`via`: `"direct"` | `"http-proxy"`; `proxy`:
redacted display string or `None`; `source`: `"flag"` / `"env:HTTPS_PROXY"` / `"default"` /
`"no-proxy-exemption"`). `CheckResult` (and `ProbeResult`) gain a `route` field defaulting to
direct. Envelope: a `route` object is **always present** in `--json`/`--jsonl` (Q3
clarification) — `{"via": "direct", "proxy": null, "source": "default"}` for today's
behavior. `schema_version` stays `"1"` (purely additive per Art. V → package MINOR). The
watch-mode change signature includes `via` + `proxy` so a route flip flags as a change
(FR-019). Human output: direct checks unchanged; proxied checks add a `via <proxy>` line and
label timing as tunnel-establishment time.

**Rationale**: always-present field per clarification; structured object beats a bare string
because scripts get `via` to branch on without parsing; `source` makes env-sourced routing
self-diagnosing.

## R7 — API shape: explicit proxy, route decided by the caller

**Decision**: `api.check()`/`api.probe()` gain `proxy: Optional[ProxySpec] = None` (str also
accepted and parsed). The library never sees NO_PROXY: exemption matching is a pure, exported
helper (`proxy_exempt(host, no_proxy) -> bool` in `net/models.py`), and the **CLI** composes
it per target — deciding, for each batch target, whether to pass the proxy or `None`, and
stamping the route source. `parse_target` gains a `proxy` guard: UDP + proxy →
`UsageError` before any I/O (FR-007).

**Rationale**: keeps the library env-free and explicit (Art. VII, FR-005/FR-020) while making
the exemption logic unit-testable in isolation; per-target route decisions land in the one
layer that already owns batching.

## R8 — Timeout and retry budgeting

**Decision**: the per-attempt `--timeout` applies **per stage**: once to the proxy TCP
connect (inside `tcp.connect`) and once to the CONNECT exchange (socket timeout on the
response read). `--retries` wraps the whole tunnel attempt and fires only on timeout-family
failures (R4 table); worst case per attempt ≈ `2 × timeout × (retries + 1)` — documented in
the CLI help. Definitive answers are never retried.

**Rationale**: mirrors the direct check's per-attempt semantics (no new budget concept);
per-stage application keeps each socket operation bounded without inventing a deadline
scheduler for v1.

## R9 — Testing strategy: in-process stand-in proxy

**Decision**: a threaded loopback stand-in proxy in `tests/` (pattern of the existing
loopback DNS/TCP servers) scriptable per scenario: `200` (tunnel then close), `407` with
configurable `Proxy-Authenticate` schemes (Basic; Negotiate-only for FR-015), `403`, `502`,
`504`, garbage banner, accept-then-silence, accept-then-close, and credential capture (to
assert the Basic header). Plus: Hypothesis property tests for `parse_proxy` and
`proxy_exempt`; CLI env-fallback tests via monkeypatched `os.environ` covering the full
variable order and lowercase forms; redaction tests asserting the password bytes appear in
zero outputs across every verdict × format; proxy-hop refused-vs-timeout asserted as the
`ProxyError` class family (the canonical cross-OS lesson). Real-proxy smoke tests are
`@pytest.mark.network`, never gating CI.

**Rationale**: every FR-009 outcome inducible deterministically on loopback across the whole
CI matrix (SC-002, SC-007); redaction is a test matrix, not a spot check (SC-004).

## R10 — Command surface

**Decision**: `check` and `probe` gain three options (Query controls panel):
`--proxy TEXT` (spec per R2), `--no-proxy TEXT` (comma-separated exemptions; overrides
env `NO_PROXY` entirely when given), `--direct` (force direct; combining `--direct` with
`--proxy` is a usage error). `--udp` + proxy (from any source) is a usage error naming the
UDP/CONNECT mismatch. `listen` is untouched. Env fallback per R3 applies only when neither
`--proxy` nor `--direct` is given.

**Rationale**: smallest surface that satisfies FR-001–FR-004; names follow curl familiarity
(`--proxy`, NO_PROXY) while `--direct` is more discoverable than curl's `--noproxy '*'`
idiom.

## R11 — Docs & category placement

**Decision**: everything lives in the existing `net` category (no new category): `proxy.py`
primitive beside `tcp.py`/`udp.py`; models/errors/api/cli/output extended in place.
`src/opskit/net/README.md` gains a "Checking through an HTTP proxy" section + option rows +
the three new exit codes; root README Commands table already links the net README (docs
gate satisfied by updating the category README). The JSON schema addition (`route`) is
documented in the README's envelope section.

**Rationale**: the feature is a mode of existing commands, not a new command group; the
docs-coverage gate keys on commands, which are unchanged in name.
