# Phase 0 Research: TLS Verification Diagnostics

Decisions resolving every technical unknown in the plan's Technical Context. Format per
speckit: Decision / Rationale / Alternatives considered.

## R1. Handshake & chain retrieval library

**Decision**: **pyOpenSSL** (`pyopenssl>=24`) for the TLS handshake, with certificates parsed
via **cryptography** (`cryptography>=42`, pulled in by pyOpenSSL as its own dependency; both are
PyCA-maintained).

**Rationale**:
- FR-011 requires listing the **full chain as presented**. The stdlib only gained
  `SSLSocket.get_unverified_chain()` / `get_verified_chain()` in **Python 3.13**; opskit supports
  3.9+. pyOpenSSL's `Connection.get_peer_cert_chain()` works on every supported version.
- FR-006 requires **retrieving details even when validation fails**. pyOpenSSL supports a custom
  verify callback that *records* each OpenSSL verify error (expired, self-signed, unable to get
  issuer, …) while letting the handshake complete — one connection yields both the certificates
  and the precise validation findings. The stdlib would force a two-handshake dance
  (`CERT_NONE` to fetch + a second connect to validate) with coarser error mapping.
- Each pyOpenSSL cert converts losslessly via `.to_cryptography()`; `cryptography.x509` provides
  every field FR-011 needs (subject, issuer, SANs, validity, serial, signature algorithm,
  key type/size).
- Both libraries are actively maintained by the Python Cryptographic Authority (Art. IV).

**Alternatives considered**:
- *stdlib `ssl` only*: no chain before 3.13; validation failure aborts the handshake before the
  cert is retrievable; `match_hostname` removed in 3.12. Rejected.
- *sslyze/nassl*: heavyweight scanner stack, native wheels, far more capability (and attack
  surface) than a read-only verdict needs. Rejected.
- *shelling out to `openssl s_client`*: violates Art. VI (pure Python, no native tools). Rejected.

## R2. Trust store

**Decision**: Build the verification store from the **stdlib's platform default CAs**: create
`ssl.SSLContext` + `load_default_certs()` (which reads the Windows certificate store, macOS/Linux
OpenSSL paths), export via `get_ca_certs(binary_form=True)`, and load those DER certs into the
pyOpenSSL `X509Store`. `--ca-file` replaces the store entirely (private PKI, FR-008).

**Rationale**: reuses CPython's battle-tested per-platform store discovery (including the Windows
`ROOT`/`CA` system stores) without a new dependency; a trust difference across platforms is then
attributable to the platform store, exactly as FR-016/SC-003 demand.

**Alternatives considered**:
- *`truststore` package*: native-store verification, but requires Python ≥3.10 (we support 3.9)
  and wires into stdlib contexts, not pyOpenSSL's. Rejected for now; revisit when 3.9 is dropped.
- *bundling `certifi`*: ships a CA list that diverges from the operator's platform reality —
  a diagnostic must reflect what the *system* trusts. Rejected (spec assumption: no bundled CAs).

## R3. Name (hostname/SAN) validation

**Decision**: Implement **RFC 6125 matching in-tree** (`_match_hostname`): exact DNS-SAN match,
single left-most-label wildcard (`*.example.com` matches one label, never the bare domain or
multi-label), IP targets matched against IP SANs only, no CN fallback (legacy CN-only
certificates fail with an explanatory finding). Property-test it with Hypothesis.

**Rationale**: `ssl.match_hostname` was removed in Python 3.12, and OpenSSL's built-in check
aborts the handshake rather than *reporting* — we need a finding ("requested `a.example.com`;
certificate covers `example.com`, `*.example.org`"), which requires matching ourselves against
the parsed SANs we already display. The algorithm is small, pure, and highly testable.

**Alternatives considered**:
- *OpenSSL `X509_check_host`*: not cleanly exposed by pyOpenSSL; binary answer, no reporting
  granularity. Rejected.
- *`cryptography.x509.verification` (PolicyBuilder)*: attractive future path (pure-Rust chain +
  name verification) but couples name-match to full chain validation and needs newer
  `cryptography`; our per-finding reporting needs the layers separated. Revisit later.

## R4. Reusable TCP-connect primitive (FR-018)

**Decision**: Create **`src/opskit/net/`** now as a *library-only* package (no CLI registration)
holding the typed connect primitive: `resolve()` (getaddrinfo → ordered candidates),
`connect()` (socket connect honoring timeout/retries, returning a connected socket + the chosen
address + timing), and the net error hierarchy (`ResolutionError`, `ConnectRefused`,
`ConnectTimeout` — each owning its exit code per Art. VII). `opskit.tls` consumes it; the future
`net` CLI category registers on top of it without rework.

**Rationale**: satisfies FR-018 with real reuse (not a doc promise); keeps `core`
category-agnostic (a socket primitive with net-owned errors belongs to the net category, not
core); dual-stack behavior (spec assumption) is implemented once.

**Alternatives considered**: placing it in `opskit/core/` (rejected: core stays free of
category error/exit semantics per constitution VII); leaving it inside `opskit/tls/` and
extracting later (rejected: FR-018 asked for the seam now, extraction later means churn).

## R5. Exit-code allocation

**Decision**: Extend the shared `ExitCode` enum: **8 CONNECT_FAILED** (refused/unreachable),
**9 HANDSHAKE_FAILED**, **10 CERT_INVALID**, **11 CERT_EXPIRING**. Reuse existing classes where
semantics match: **2** usage, **3** resolution failure (name does not exist — same class as DNS
NXDOMAIN), **6** timeout (connect or handshake timeout after retries), **7** partial batch.
Error types own their codes (`OpskitError.exit_code`), so no core changes beyond the enum.

**Rationale**: FR-012 demands distinct classes per layer; reusing 3/6/7 keeps the documented
code space compact and consistent across categories (a timeout is a timeout). `CERT_EXPIRING`
is separate from `CERT_INVALID` so scripts can alert-without-paging (spec assumption).

**Alternatives considered**: per-category code ranges (e.g., 30–39 for tls) — rejected as
over-engineering for a small, documented enum governed by SemVer.

## R6. Deterministic test strategy (no external network in CI)

**Decision**: Test against **in-process TLS servers on loopback** using stdlib
`ssl.SSLContext` servers, with test certificates **generated at runtime** in a session fixture
via `cryptography` (self-signed root → intermediate → leaf; plus expired, not-yet-valid,
wrong-name, no-SAN, and short-lived variants). A plain-TCP listener covers "non-TLS service";
a closed loopback port covers "refused"; connect/handshake timeouts are covered by monkeypatched
socket/handshake layers (loopback cannot produce real filtering). Real-endpoint smoke tests are
`@pytest.mark.network` and never gate CI.

**Rationale**: covers ~all SC-002 failure classes deterministically and cross-platform;
runtime-generated certs avoid committing fixture keys/certs (Art. III — no secrets in repo,
scanners stay quiet) and never expire the test suite.

**Alternatives considered**: committed PEM fixtures (rejected: secret scanners flag keys, and
fixed certs eventually expire or need not-valid-yet gymnastics); `trustme` dev-dependency
(nice, but `cryptography` is already present and gives full control over pathological certs).

## R7. CLI shape & option surface

**Decision**: One command for v1 — **`opskit tls check TARGET`** — with panels matching the dns
style: Query (`TARGET` as `host[:port]` / `[ipv6]:port`), Query controls (`--port` default 443,
`--timeout` 5.0, `--retries` 2, `--sni`, `--ca-file`, `--warn-days` default 30), Modes
(`--watch`), Output (`--json`, `--jsonl`, `--no-color`), batch via `-i/--input-file`. The
report always shows verdict + leaf summary + chain + protocol/cipher (no separate `cert`
subcommand until a real need appears).

**Rationale**: a single rich report satisfies stories 1–5 without flag proliferation; the
`host:port` shorthand (FR-002) is required for mixed-port batch files. Port precedence:
shorthand and `--port` must agree if both given, else usage error (FR-002).

**Alternatives considered**: separate `verify`/`inspect` commands — rejected: same connection,
same data, two names for one report; would violate "one obvious way".

## R8. Watch-change signature

**Decision**: The `--watch` change signature is the tuple of per-target
(outcome class, leaf fingerprint (SHA-256), not-after, negotiated protocol) — certificate
rotation (US5) flips the fingerprint even when everything stays valid; timing jitter never
triggers a change.

**Rationale**: matches the dns `--watch` semantics (flag *meaningful* change, ignore noise).
