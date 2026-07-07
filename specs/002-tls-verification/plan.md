# Implementation Plan: TLS Verification Diagnostics

**Branch**: `002-tls-verification` | **Date**: 2026-07-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-tls-verification/spec.md`

## Summary

Add the `opskit tls` category: a read-only, cross-platform TLS endpoint verifier
(`opskit tls check host[:port]`, default port 443) with layered outcomes (resolve → connect →
handshake → validate), full certificate/chain inspection even when validation fails, RFC 6125
name matching, platform-trust-store (or `--ca-file`) chain validation, expiry warnings with a
distinct exit class, SNI control, and all established opskit contracts (typed `opskit.tls` API,
thin Typer CLI, JSON envelope, batch with per-target tolerance, `--watch`). The TCP-connect step
ships as a reusable `opskit.net` library primitive (FR-018). Technical approach per
[research.md](research.md): pyOpenSSL handshake with a recording verify callback + `cryptography`
parsing; stdlib-sourced platform trust store; in-tree RFC 6125 matcher; loopback TLS servers with
runtime-generated certs for deterministic tests.

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor; chain retrieval must not rely on
3.13-only stdlib APIs — see R1)

**Primary Dependencies**: existing (typer, rich, platformdirs) **+ new runtime deps:
`pyopenssl>=24`, `cryptography>=42`** (PyCA-maintained; cryptography is pyOpenSSL's own core
dependency). No other additions.

**Storage**: N/A (stateless diagnostics)

**Testing**: pytest (+ Hypothesis for the RFC 6125 matcher and target parser); in-process
loopback TLS servers with certificates generated at runtime (R6); `@pytest.mark.network`
smoke tests excluded from CI; coverage ≥ 90%

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13)

**Project Type**: library + CLI (existing single-project `src/` layout)

**Performance Goals**: single check completes in < 10 s against a reachable endpoint (SC-001);
default per-attempt timeout 5 s, retries 2 (consistent with dns)

**Constraints**: read-only/zero-telemetry — exactly one connection per check attempt, to the
user-specified endpoint only; no application data sent; no OCSP/CRL fetches (spec assumption);
library layer never prints/exits; `core` stays category-agnostic

**Scale/Scope**: one new CLI command (`tls check`), one new library-only package (`opskit.net`
primitive), ~5 new exit-code/outcome classes, batch files of ~hundreds of targets

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, no violations.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(tls)` commits. PASS |
| II Documentation completeness | `tls check` ships `--help` + `src/opskit/tls/README.md`; all public API docstrings (Google style). PASS |
| III Zero security compromise | New deps are PyCA-maintained and pass pip-audit/Snyk; test certs generated at runtime — no keys committed (R6). PASS |
| IV Dependency freshness | pyOpenSSL/cryptography current majors, Dependabot-covered. PASS |
| V Strict SemVer | New exit codes 8–11 and the `opskit.tls`/`opskit.net` APIs are **additive** → MINOR. PASS |
| VI Pure-Python parity | No shelling out (no `openssl` binary); trust store sourced via stdlib per platform; OS socket errors normalized into the shared hierarchy. PASS |
| VII CLI/API parity, typed core | All logic in `opskit.tls`/`opskit.net` typed APIs; `tls/cli.py` is a thin client; errors own their exit codes; `core` untouched except the additive ExitCode members; category rendering in `tls/output.py`. PASS |
| VIII Zero telemetry | Connects only to the specified endpoint; explicitly no OCSP/CRL callouts. PASS |
| IX Output contract | Human + versioned `--json`/`--jsonl`; NO_COLOR; batch rule (process all, per-item failures in JSON, 0/uniform/PARTIAL). PASS |
| X Diagnostic-only scope | Anonymous handshake, no app data, no scanning/enumeration features; single user-specified target per check. PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes needed).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] New dependencies (pyopenssl, cryptography) are actively maintained, pass pip-audit + Snyk, and land in `uv.lock`.
- [x] New command ships tests + docs and preserves the output/exit-code contract (additive only).
- [x] No secrets committed — test certificates/keys are generated at runtime in fixtures (R6); inputs validated before network I/O; read-only zero-telemetry scope preserved.
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact).
- [x] SECURITY.md, branch protection, Dependabot unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/002-tls-verification/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # Command surface, options, exit codes, envelope
│   └── python-api.md    # opskit.tls + opskit.net public API contract
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/
├── cli.py               # + register tls sub-app (one line)
├── core/
│   └── exit_codes.py    # + CONNECT_FAILED=8, HANDSHAKE_FAILED=9, CERT_INVALID=10, CERT_EXPIRING=11 (additive)
├── net/                 # NEW — library-only reusable primitive (FR-018); no CLI yet
│   ├── __init__.py      # public: resolve, connect, TcpConnection, net errors
│   ├── errors.py        # ResolutionError(3), ConnectRefused(8), ConnectTimeout(6) — own exit codes
│   └── tcp.py           # resolve() via getaddrinfo; connect() with timeout/retries; dual-stack order
└── tls/                 # NEW category
    ├── __init__.py      # public API re-exports (check, models, errors)
    ├── README.md        # command reference (linked from root README Commands table)
    ├── api.py           # check() — orchestrates resolve→connect→handshake→validate
    ├── cli.py           # thin Typer sub-app: `tls check` (no future-annotations; Optional[...])
    ├── errors.py        # TlsError base; HandshakeError(9), CertificateInvalid(10), CertificateExpiring(11)
    ├── handshake.py     # pyOpenSSL connection, recording verify callback, chain extraction
    ├── inspect.py       # cryptography-based cert parsing → CertificateInfo; RFC 6125 _match_hostname
    ├── models.py        # frozen dataclasses: TlsTarget, CertificateInfo, ValidationFinding, TlsCheckResult
    └── output.py        # category-owned rich rendering (escape() on all external strings)

tests/
├── unit/
│   ├── test_net_tcp.py          # resolve/connect primitive (mocked sockets + loopback)
│   ├── test_tls_target.py       # host[:port]/[v6]:port parsing (+ Hypothesis)
│   ├── test_tls_match.py        # RFC 6125 matcher (+ Hypothesis)
│   ├── test_tls_inspect.py      # cert field extraction from generated certs
│   ├── test_tls_api.py          # check() outcomes with injected handshake
│   ├── test_tls_cli.py          # CLI: options, JSON envelope, exit codes, batch, watch
│   └── test_tls_output.py       # rendering incl. markup escaping
└── integration/
    └── test_tls_loopback.py     # in-process TLS servers: valid/expired/wrong-name/self-signed/
                                 # untrusted/no-SAN/non-TLS-port/refused (R6)
```

**Structure Decision**: extends the established single-project `src/` layout with two packages:
`opskit/tls` (full category: api/cli/models/errors/output, mirroring `opskit/dns`) and
`opskit/net` (library-only connect primitive per FR-018, becoming the future net category's
foundation). `core` receives only additive `ExitCode` members — no category imports, per
constitution Art. VII. All cross-cutting rules from CLAUDE.md apply from the start (no future
annotations in `tls/cli.py`, escape external strings, batch+JSON failure contract, OSError
normalization).

## Complexity Tracking

No constitutional violations — table not required.
