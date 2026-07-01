# opskit — Working Plan

> **Purpose:** Single source of truth shared between planning (Claude desktop app) and
> implementation (VS Code + Claude Code plugin). Desktop writes decisions here; the
> plugin reads them, implements, and checks tasks off. Keep this file committed.
>
> **Note:** This repo is driven by **Spec Kit**. Once `specify init` is run, the
> authoritative artifacts become `constitution.md`, `spec.md`, `plan.md`, and `tasks.md`.
> This file then shrinks to the cross-app handoff notes only (reachability + who-edits-when).

---

## How to use this file

- **Desktop (planning):** capture goals, decisions, and open questions. Break work into
  a task checklist under **Current Iteration**. Don't write code here — describe intent.
- **Plugin (implementation):** start each session with *"Read docs/PLAN.md and continue."*
  As you implement, check off tasks, log deviations under **Decision Log**, and move
  anything discovered to **Open Questions** or **Backlog**.
- **Sync rule:** the last section, **Handoff**, always reflects the most recent state so
  the other side can pick up cold.

---

## Project

- **Name (repo):** opskit
- **PyPI dist name:** `opskit` (`pip install opskit`) — confirmed available on PyPI
- **Command:** `opskit` (single entry point), optional short alias `ops`
- **License:** MIT (permissive, no warranty/liability — solo author, community give-away)
  — copyright line: `Copyright (c) 2026 prabhakar-cg` (GitHub handle)
- **Status:** greenfield — repo initialized, no code yet; Spec Kit not yet initialized
- **One-liner:** A cross-platform Python CLI + library that gives engineers, developers,
  and operations teams one consistent set of troubleshooting/query commands (DNS, and
  later network connectivity, TLS certs, AD/LDAP) regardless of operating system —
  installable with pip.

## Goals

- Eliminate per-OS command juggling (`nslookup` vs `dig`, `telnet` vs `Test-NetConnection`,
  Windows AD cmdlets vs `ldapsearch`, etc.) with one uniform interface.
- Identical behavior and output on Windows, macOS, and Linux.
- Usable both interactively (CLI) and in automation (Python API + JSON output + exit codes).

## Constraints & Decisions

| Area | Decision |
|---|---|
| Implementation | **Pure Python** (dnspython, socket, cryptography, ldap3…) — no shelling out to native tools |
| CLI framework | **Typer** (subcommand groups = one group per category) |
| CLI structure | Single command + functional subcommands (git/kubectl model) |
| Consumption | CLI is a thin layer over an importable Python API |
| Output | Human-readable by default + `--json` for automation |
| Exit codes | **Structured** — 0 = success; distinct non-zero codes per failure class; errors also in `--json` |
| Python support | 3.9+ |
| Distribution | Public PyPI |
| Extensibility | Each category = self-contained package registered as a Typer sub-app, sharing a core output/exit-code/error contract |

### Engineering standards (the "beast" bar)

| Area | Decision |
|---|---|
| Layout | `src/` layout (PyPA standard); tests outside the package |
| Packaging | `pyproject.toml` only (PEP 621); build backend Hatchling |
| Dep/env tooling | **uv** (venv/install/lock/build); `uv.lock` for reproducible dev/CI |
| Runtime deps | Permissive ranges (library); category extras (`opskit[ad]`, `opskit[tls]`) keep base install slim |
| Lint + format | **Ruff** (format + lint, incl. security `S` rules) — replaces black/isort/flake8/pyupgrade |
| Type checking | **mypy `--strict` + pyright**, both in CI; no implicit `Any` |
| Testing | pytest + coverage (≥90%), Hypothesis for parser property tests; **nox** drives matrix |
| CI matrix | Windows/macOS/Linux × Python 3.9–3.13 (proves cross-platform promise) |
| CI/CD platform | **GitHub Actions** (matrix, lint/type/test/security jobs, Trusted Publishing) |
| Quality gate | **SonarCloud** (coverage, complexity, duplication, maintainability/reliability, security hotspots) — fed `coverage.xml`; blocks merge |
| Platform security | GitHub **CodeQL** code scanning, **secret scanning + push protection**, **dependency review**, **Dependabot** (security + version updates), private vuln reporting |
| AI code review | **CodeRabbit** on every PR (summaries + line-by-line suggestions) — complements the static gates, does not replace them; config `.coderabbit.yaml` |
| Pre-commit | pre-commit hooks (ruff, type checks) gate every commit |
| Concurrency | Sync core for v1, **structured to add async later** without breaking callers |
| Extensibility | In-tree sub-app registration now; entry-point plugin discovery deferred |
| Security | pip-audit + Dependabot/Renovate; PyPI **Trusted Publishing (OIDC)** + PEP 740 attestations + SBOM; redact credentials in all output |
| Docs/versioning | Google-style docstrings (Ruff `D`); SemVer + Keep-a-Changelog |

### Code style & conventions

- **PEP 8** enforced by Ruff (`pycodestyle` E/W + `pyflakes` F) — no separate flake8/pycodestyle.
- **Formatting:** `ruff format` (Black-style), line length **88**.
- **Ruff rule selection:** `E,W,F` (PEP 8/pyflakes), `I` (isort import order), `N` (PEP 8
  naming), `D` (**PEP 257** docstrings, Google convention), `UP` (pyupgrade), `B` (bugbear),
  `S` (bandit security), `ANN` (require annotations), `PL` (**pylint-derived** checks), `C4`,
  `SIM`, `PTH`, `RUF`.
- **pylint:** intentionally **not** run standalone — redundant with Ruff `PL` + mypy/pyright +
  SonarCloud; its high-value checks are captured via Ruff's `PL` ruleset without the extra
  process, config, and CI cost.
- **Type hints:** **PEP 484/585/604** everywhere; checked by mypy `--strict` + pyright.
- **Docstrings:** Google style on every public module/class/function (ties to Art. II docs gate).
- **Zen of Python (PEP 20)** as the tie-breaker for judgment calls.

### Proposed module layout

```
opskit/
├── src/opskit/
│   ├── __init__.py      # version + public API re-exports
│   ├── __main__.py      # `python -m opskit`
│   ├── cli.py           # root Typer app; registers command groups
│   ├── core/            # result models, error hierarchy → exit-code map, renderers
│   └── dns/             # api.py (logic) + models.py + cli.py (thin sub-app)
├── tests/
├── docs/
├── pyproject.toml
└── noxfile.py
```

**Iron rules:** `cli.py` holds zero logic (parses + delegates to `api.py`); typed result
models render to both human and `--json`; a central exception hierarchy maps to structured
exit codes so no raw exception ever reaches the user.

---

## Public API design (API-first — CLI is just one client)

Design the API first; the CLI is a thin presentation client over it. Every capability
(incl. multi-resolver diff, timing, bulk) is available programmatically too.

- **Shape:** module-level convenience functions **+** a configurable client
  (`requests` model): `dns.lookup()/reverse()` for one-offs, `DnsClient(...)` for shared
  defaults, reuse, and bulk/concurrent calls (`client.lookup_many([...])`).
- **Result models:** **stdlib dataclasses** (zero runtime dep, fast import) with
  `to_dict()`/`to_json()`, iterable, truthy `.ok`. No Pydantic in core.
- **Errors:** typed exception hierarchy (`DnsError → NxDomain/DnsTimeout/...`). In library
  use, failures **raise**; only the CLI catches them and maps to exit codes.
- **Good-citizen rules:** library layer **never** `print()`s or calls `sys.exit()`;
  logging via `logging.getLogger("opskit")` + `NullHandler`; **no global mutable state**
  (config passed explicitly or on a client); ships **`py.typed`** (PEP 561); `__all__` +
  underscore-private define a SemVer-stable public surface; namespaced (`opskit.dns.*`).

## UX & DX principles (apply to every command — the "daily driver" bar)

- **Output ergonomics:** color/tables when a TTY, auto-plain when piped; honor `NO_COLOR`
  and `--no-color`; `--quiet`/`--verbose`; `--json` everywhere for automation.
- **Composability:** accept targets from args, a file, or **stdin**; batch mode; exit codes
  designed for `&&`/scripting.
- **DNS power moves:** query **multiple resolvers at once and diff** the answers; per-query
  **latency/timing**; `--trace` showing the resolution path.
- **Config/profiles:** saved named resolvers/environments (`--profile prod`) so users stop
  retyping `--server`.
- **Watch mode:** `--watch 5s` re-runs and shows changes live (propagation/failover).
- **Error empathy:** failures state *what to try next*, never a raw stack trace.
- **Shell completion:** ship completion (Typer) for a big daily QoL win.
- **Privacy:** explicitly **zero telemetry / no phone-home** — never.

---

## Testing strategy (real-world, incl. tightened networks)

**Scenario matrix — every one has a test:**

| Scenario | Expected behavior |
|---|---|
| UDP/53 blocked | Auto-fallback to DNS-over-TCP; clear message if both blocked |
| Resolver REFUSED | Distinct error + exit code (≠ timeout) |
| Firewall silently drops | Timeout after N + retries; hint "no response — filtered?" |
| SERVFAIL / NXDOMAIN | Distinct handling for each rcode |
| Truncated UDP (TC bit) | Automatic retry over TCP |
| Split-horizon (internal ≠ external) | Multi-resolver **diff** surfaces the difference |
| Corp-resolver-only (can't reach public) | Graceful timeout + hint to use `--server`/profile |
| `/etc/hosts` override | Respect/bypass explicitly, documented |
| IPv4-only / IPv6-only / dual-stack | A vs AAAA consistent, no crash on missing family |
| Slow/flaky resolver | Timeout + retry tuning works |
| DNSSEC validation failure | Clear, distinct signal |
| Empty answer / CNAME chains | Follow/report correctly |

**Test layers (all run in the cross-platform CI matrix):**

- **Unit** — parsing, result models, error mapping, config precedence. No network, instant.
- **Injected mock resolver** — dependency inversion feeds canned dnspython messages / raises
  specific exceptions to cover every rcode + failure class deterministically.
- **Loopback DNS server** — in-process server on `127.0.0.1:<ephemeral>` exercising the real
  socket path: drop packets (timeout), REFUSED, set TC bit (force TCP), inject latency, serve
  different answers per resolver (split-horizon diff). Simulates tightened networks with no
  external access. (dev-dep: `dnslib` or a small dnspython-based stub.)
- **CLI** — Typer `CliRunner`: output, exit codes, `--json` shape, `NO_COLOR`/plain-when-piped,
  stdin/file input.
- **Property tests (Hypothesis)** — round-trip invariants on formatters/parsers.
- **Opt-in real-network smoke** — `@pytest.mark.network`, **skipped by default and in gating
  CI**; run nightly/manually to catch upstream drift. Never gates.

## Cross-platform support

- **System resolver discovery** differs (Linux/mac `/etc/resolv.conf`; Windows registry/API) —
  rely on **dnspython**; document the difference.
- **Socket error codes** differ (`WSAECONNREFUSED` vs `ECONNREFUSED`) — normalized into the
  unified exception hierarchy so messages/behavior are identical everywhere.
- **Color/tables/TTY/`NO_COLOR`** via **rich** (pairs with Typer).
- **Config/hosts paths** via **platformdirs** (per-OS config dir); never hardcode paths.
- **`--watch` Ctrl+C** + console encoding (Windows cp1252 vs utf-8) handled explicitly.
- **Privileged ops** (ICMP later) avoided — reachability uses TCP connect, not raw ICMP.

## Config precedence (highest wins)

1. Explicit CLI flag (`--server`)
2. Env var (`OPSKIT_SERVER`, `OPSKIT_TIMEOUT`, …)
3. Active profile (`--profile prod` / `OPSKIT_PROFILE`) from config file
4. Config file `[default]`
5. Built-in defaults

- **Format:** TOML (`tomllib` on 3.11+, `tomli` backport on 3.9/3.10).
- **Sources:** user config at platformdirs path (overridable via `--config`) **+** optional
  project-local `./.opskit.toml` **+** env **+** flags. Profiles as `[profiles.<name>]`.
- **Library rule:** only the **CLI** resolves env/file precedence; the **API never auto-reads
  env or config files** — callers pass explicit config. Preserves "no global state / no surprises."

## `--json` output contract

- **Consistent, versioned envelope** on every command:
  ```json
  {
    "schema_version": "1",
    "ok": true,
    "command": "dns.lookup",
    "query": { "name": "example.com", "types": ["A"], "server": "1.1.1.1" },
    "result": { "records": [ { "type": "A", "value": "…", "ttl": 300 } ] },
    "error": null,
    "elapsed_ms": 12.3
  }
  ```
- **Errors serialized too** (`"error": {"code","message","hint"}`) — parity with exit codes.
- **Batch:** `--json` emits an array for multiple targets; **`--jsonl`** (NDJSON) streams one
  object per line for large batches / `jq`.
- **Compatibility (tied to SemVer, Art. V):** adding fields = non-breaking; rename/remove/retype
  = major bump. A **published JSON Schema** ships and is validated against sample outputs in tests.

## Dependencies (v1)

- **Runtime (base):** `dnspython`, `typer`, `rich`, `platformdirs`, `tomli` (only Python < 3.11).
- **Dev:** `pytest`, `pytest-cov`, `hypothesis`, `dnslib` (loopback test server), `ruff`,
  `mypy`, `pyright`, `nox`, `pip-audit`, `pre-commit`.
- **Future extras:** `opskit[tls]` → `cryptography`; `opskit[ad]` → `ldap3`.

---

## CI/CD pipeline (GitHub Actions — Continuous Delivery)

> **Model:** Continuous **Delivery** — fully automated pipeline up to a **human-approved**
> publish to PyPI (not Continuous Deployment, which would auto-publish on merge).

**Triggers & matrix**

- **PR:** reduced matrix (Linux × oldest+newest Python) for fast feedback.
- **Merge to `main` + nightly (scheduled):** full matrix Win/mac/Linux × Python 3.9–3.13.
- **Nightly also** runs the opt-in real-network smoke tests (`@pytest.mark.network`).

**Per-PR jobs (all must pass; branch protection enforces)**

- `ruff check` + `ruff format --check`
- `mypy --strict` + `pyright`
- `pytest` + coverage (`--cov-fail-under=90`) → upload `coverage.xml`
- `pip-audit` + `bandit`
- CodeQL, dependency review, secret scanning + push protection
- SonarCloud quality gate
- CodeRabbit AI review (advisory)

**Merge strategy**

- **Squash-merge**; PR title must follow **Conventional Commits** (enforced) — clean linear
  history that drives versioning.

**Release (Continuous Delivery — human-approved publish)**

- **release-please** runs on push to `main` via a **GitHub App token** (via
  `actions/create-github-app-token`, *not* `GITHUB_TOKEN`) so **full CI + the full matrix +
  the security suite re-run on the release PR itself** — catching last-minute issues / zero-days
  (e.g. dependency CVEs disclosed after the feature merged).
- release-please maintains a release PR accumulating changelog + version bump; merging the
  approved PR creates the git tag + GitHub Release.
- **Publish is gated on a protected GitHub Environment `pypi` with required reviewers** — the
  publish job **pauses for manual approval** before anything ships (this is the "CD" gate).
- **Final security re-scan before upload** (`pip-audit` + dependency review) as a hard gate —
  nothing publishes if a CVE dropped in the interim.
- On approval: build (Hatchling) → SBOM (CycloneDX) + PEP 740 attestations → publish to PyPI
  via **Trusted Publishing (OIDC)** (no tokens).
- **Nightly scheduled security scan** on `main` flags newly-disclosed CVEs between releases.
- *Caveat:* the tag/GitHub Release is created at release-PR merge; if publish is rejected at the
  approval gate, fix forward with the next patch release.

**Supply-chain hardening (full)**

- All GitHub Actions **pinned to commit SHAs**.
- **step-security/harden-runner** (egress control) on every job.
- **OpenSSF Scorecard** workflow.
- uv cache for speed; `concurrency` with cancel-in-progress for superseded runs.

---

## Constitution — draft articles (for Spec Kit)

Non-negotiable principles; every feature is checked against these ("Constitution Check").
Each is phrased as an **automated gate**, not an aspiration.

- **Art. I — Conventional Commits + auto-changelog.** All commits follow Conventional
  Commits (`feat`/`fix`/`feat!`…). *Gate:* commit-lint in CI rejects non-conforming
  messages; the changelog (Keep a Changelog) is generated automatically at release from
  commit history. No user-facing change ships without a changelog entry.
- **Art. II — Every command is documented.** A command is not "done" without (a) Typer
  help text/docstring and (b) a docs page. *Gate:* an automated test enumerates every
  registered Typer command and asserts each has help text **and** a matching docs entry —
  undocumented commands fail CI.
- **Art. III — Zero security compromise (full suite).** *Gate:* `pip-audit` + Ruff `S` +
  bandit + secrets scan + **GitHub CodeQL code scanning** + **secret scanning + push
  protection** + **dependency review** + **SonarCloud quality gate** (incl. security
  hotspots); any high/critical blocks merge. Security suppressions (`# noqa`/`# nosec`)
  require written justification + review. Credentials are always redacted in output/logs.
- **Art. IV — Dependencies stay current (pragmatic hard-gate).** No EOL Python, no
  unmaintained/deprecated libraries, no known-vulnerable deps. *Gate:* Renovate/Dependabot
  auto-PRs keep deps fresh; each must pass the full CI matrix before merge. Always current,
  always green.
- **Art. V — Strict SemVer (automated).** Breaking → major, feature → minor, fix → patch,
  single-sourced version. *Gate:* Conventional-Commits-driven release tooling
  (release-please / python-semantic-release) computes the bump, tag, and changelog together;
  a `feat!`/`BREAKING CHANGE` without a major bump fails the release check.
- **Art. VI — Pure Python, cross-platform parity.** No shelling out to native tools;
  identical behavior/output on Windows/macOS/Linux. *Gate:* CI matrix Win/mac/Linux ×
  Python 3.9–3.13 must pass.
- **Art. VII — CLI/API parity via typed core.** `cli.py` holds zero logic; typed result
  models render to both human and `--json`; a central exception hierarchy maps to structured
  exit codes so no raw exception reaches the user.
- **Art. VIII — Privacy: zero telemetry.** opskit makes **no** network connection except the
  diagnostic query the user explicitly requested. No analytics, phone-home, auto-update checks,
  or third-party crash reporting — ever. *Gate:* CI audit asserts no outbound calls to any host
  other than the user-specified target, and forbids any analytics/telemetry dependency.
- **Art. IX — Output & interoperability contract.** Every command must provide: human-readable
  default, `--json` (versioned envelope), `--jsonl` where batchable, honor `NO_COLOR` +
  auto-plain-when-piped, and structured exit codes. *Gate:* a test walks all registered commands
  and asserts these flags/behaviors exist and conform.
- **Art. X — Diagnostic-only scope (no misuse; not a hacking tool).** opskit is a **read-only
  diagnostic/troubleshooting** tool for operators working on **their own, authorized**
  environments. It will **not** ship offensive or abuse features — no exploitation, no
  credential brute-forcing/guessing, no mass/range port scanning, no traffic
  interception/spoofing, no detection-evasion. Legitimate operator diagnostics (explicit TCP
  connect checks, a temporary listener for one's own troubleshooting, read-only AD queries with
  the operator's own credentials) are in scope; anything enabling attack or misuse is out.
  *Gate:* every feature spec is checked against this boundary in the Constitution Check; misuse-
  enabling capabilities are rejected. Credentials are always redacted (see Art. III).

---

## Current Iteration

**Focus:** v1 — framework + DNS command group

### Tasks

- [ ] Initialize Spec Kit (`specify init`)
- [ ] Write `constitution.md` (principles: pure-Python cross-platform, output/exit-code
      contract, JSON parity, extensibility pattern, testing requirements)
- [ ] Write v1 DNS `spec.md` (scope below)
- [ ] `/plan` and `/tasks` for the DNS feature
- [ ] (implementation — only after specs are approved)

**v1 DNS scope:**
- Forward lookup with record types: A, AAAA, MX, TXT, CNAME, NS, SOA, SRV
- Reverse lookup (PTR from IP)
- Custom resolver/server (`--server`, dig `@server` style)
- Query controls: timeout, retries, TCP vs UDP, custom port

### Open Questions

- Confirm short alias `ops` is wanted (vs `opskit` only).

---

## Decision Log

_Append-only. Date + one line per decision, so both sides know why things are the way they are._

- 2026-07-01 — Adopted `docs/PLAN.md` as the cross-app handoff artifact.
- 2026-07-01 — Repo driven by Spec Kit; spec/requirements/constitution live in Spec Kit artifacts.
- 2026-07-01 — Name settled on `opskit` (was briefly `devkit` then `python_buddy`); `devkit` taken on PyPI, `opskit` available. Repo renamed devkit → opskit.
- 2026-07-01 — Locked: pure Python, Typer, single-command+subcommands, CLI+API, human+`--json`, structured exit codes, Python 3.9+, public PyPI.
- 2026-07-01 — v1 scope narrowed to DNS; connectivity/TLS/AD deferred to backlog.
- 2026-07-01 — Engineering standards locked: src/ layout, uv, Ruff, mypy `--strict` + pyright, pytest+Hypothesis+nox, Win/mac/Linux × 3.9–3.13 CI matrix, sync-but-async-ready, in-tree extensibility, Trusted Publishing + SBOM, category extras.
- 2026-07-01 — Confirmed pip stays first-class: uv is dev/build only; published wheel is standard, `pip install opskit` and `pip install -e .[dev]` both supported.
- 2026-07-01 — Governance locked: Conventional Commits driving automated versioning + changelog; dependency freshness = pragmatic hard-gate (block vulnerable/EOL + auto-update PRs); security gate = full suite (pip-audit + Ruff S + bandit + gitleaks + CodeQL + dependency review). Captured as constitution Arts. I–VII.
- 2026-07-01 — License = MIT (solo author, community give-away, no warranty/liability). SPDX `MIT`. Copyright holder = `prabhakar-cg` (GitHub handle); LICENSE line: `Copyright (c) 2026 prabhakar-cg`.
- 2026-07-01 — API-first confirmed as co-equal to CLI: shape = functions + configurable client (requests model); result models = stdlib dataclasses; typed exceptions; library never prints/exits; py.typed shipped.
- 2026-07-01 — Adopted full "daily driver" UX/DX principle set (output ergonomics, stdin/batch composability, multi-resolver diff, timing, --trace, profiles, watch mode, error empathy, shell completion, zero telemetry). "Take it all."
- 2026-07-01 — Testing = layered (unit + injected mock resolver + in-process loopback DNS server + opt-in real-network smoke); real-world scenario matrix defined (blocked UDP, REFUSED, drops, SERVFAIL/NXDOMAIN, TC-bit→TCP, split-horizon, hosts override, IP family, DNSSEC…).
- 2026-07-01 — Cross-platform via rich (output/TTY/NO_COLOR) + platformdirs (paths) + dnspython (resolver discovery); OS socket errors normalized into the exception hierarchy.
- 2026-07-01 — Config: TOML, precedence flags > env > profile > file default > built-in; user (platformdirs) + project-local `./.opskit.toml` + `--config`; API is explicit-only (never auto-reads env/files).
- 2026-07-01 — `--json` = versioned envelope (schema_version, query/result/error/elapsed_ms), array for batch, `--jsonl` NDJSON for streaming; published JSON Schema validated in tests; schema changes governed by SemVer.
- 2026-07-01 — v1 deps set: runtime dnspython/typer/rich/platformdirs (+tomli<3.11); dev pytest/pytest-cov/hypothesis/dnslib/ruff/mypy/pyright/nox/pip-audit/pre-commit.
- 2026-07-01 — Promoted to constitution: Art. VIII (privacy/zero telemetry), Art. IX (output & interoperability contract). Added Art. X (diagnostic-only scope — no misuse; opskit is not a hacking tool; read-only diagnostics on authorized environments only).
- 2026-07-01 — CI/quality tooling added: GitHub Actions (runner), SonarCloud quality gate (coverage/complexity/hotspots), GitHub CodeQL + secret scanning + push protection + dependency review + Dependabot, and CodeRabbit AI PR review (`.coderabbit.yaml`). All free for public repos. (Note: user meant CodeRabbit, not RabbitMQ.)
- 2026-07-01 — Confirmed pytest is the test framework and PEP 8 is enforced via Ruff (not a separate tool). Recorded explicit code-style spec: Ruff rule selection (E/W/F/I/N/D/UP/B/S/ANN/PL/C4/SIM/PTH/RUF), line length 88, PEP 257 Google docstrings, PEP 484/585/604 typing, PEP 20 as tie-breaker.
- 2026-07-01 — Standalone pylint considered and declined as redundant; instead enabled Ruff's `PL` (pylint-derived) ruleset to capture its high-value checks without a second linter/config/CI cost.
- 2026-07-01 — CI mechanics settled: reduced matrix on PR / full matrix on main + nightly (+ nightly real-network smoke); release-please for versioning/changelog/release; full supply-chain hardening (SHA-pinned actions, harden-runner egress control, OpenSSF Scorecard); squash-merge with Conventional-Commit PR titles; coverage gate --cov-fail-under=90.
- 2026-07-01 — Delivery model = Continuous Delivery (CI/CD). GitHub App token so full CI + security suite run on the release PR (last-minute/zero-day catch). Publish gated behind protected `pypi` Environment with required reviewers (manual approval) + a final pre-publish pip-audit/dependency re-scan + nightly CVE scan on main. Publish via Trusted Publishing on approval. Accepted caveat: tag/Release exists at release-PR merge even if publish is later rejected (fix forward).

## Backlog

_Not now, but don't lose it._

- Network connectivity: TCP connect (telnet-style), nc-style temporary port listener, ping/reachability
- TLS/SSL certs: fetch + inspect expiry, chain, SANs, issuer for host:port
- Active Directory / LDAP: user status (enabled/locked/expiry), group membership

---

## Handoff

**Last updated by:** plugin (VS Code) — design discussion captured
**Date:** 2026-07-01
**State:** Design discussion substantially complete — identity, engineering standards,
constitution articles I–X, API-first design, UX/DX principles, testing strategy,
cross-platform plan, config precedence, `--json` contract, and v1 dependencies all locked
(see sections + decision log). Repo renamed to `opskit`. No code or Spec Kit scaffolding yet.
Open: none — design discussion complete; ready for the Spec Kit phase when you are.
**Next step:** Initialize Spec Kit, then draft `constitution.md` and the v1 DNS `spec.md`
from the decisions above. Do not write implementation code until specs are approved.
