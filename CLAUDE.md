# opskit — Claude Code guide

opskit is a cross-platform, pip-installable Python **CLI + library** giving engineers one
consistent set of read-only troubleshooting/diagnostic commands regardless of OS. v1 ships DNS;
network/TLS/AD follow.

**Sources of truth (read these; don't duplicate them):**
- Principles & gates → [`.specify/memory/constitution.md`](.specify/memory/constitution.md) (Arts. I–X)
- Full design & decision log → [`docs/PLAN.md`](docs/PLAN.md)
- Active feature → `specs/<NNN-slug>/` (`spec.md`, `plan.md`, `tasks.md`)

Everything below is a summary of those; if they conflict, **the constitution wins**.

## Golden rules (non-negotiable)

1. **Pure Python, identical on Win/macOS/Linux.** Never shell out to native tools; normalize
   OS-specific errors into the shared exception hierarchy.
2. **API-first; the CLI is a thin client.** All logic lives in the typed API; `cli.py` only parses
   args and renders. Never add logic to the CLI layer.
3. **Library is a good citizen.** The library layer MUST NOT `print()` or call `sys.exit()`, MUST
   NOT hold global mutable state, logs via `logging.getLogger("opskit")` + `NullHandler`, and ships
   `py.typed`. Only the CLI catches exceptions and maps them to exit codes.
4. **Read-only, zero-telemetry, no misuse.** Only perform the diagnostic query the user asked for.
   No network calls except to the chosen/system resolver. No offensive/abuse features (Art. X).
5. **Every command honors the output contract.** Human-readable default + `--json` (versioned
   envelope) + `--jsonl` where batchable; honor `NO_COLOR` and auto-plain-when-piped; structured
   exit codes. Errors are actionable (say what to try next).

## Architecture

```
src/opskit/
  __init__.py     # version + public API re-exports
  __main__.py     # python -m opskit
  cli.py          # root Typer app; registers command groups (zero logic)
  core/           # result models, error hierarchy -> exit-code map, output/render, config
  dns/            # api.py (logic) + models.py (dataclasses) + cli.py (thin sub-app)
```

- Result models are **stdlib dataclasses** (no Pydantic in core) with `to_dict()`/`to_json()`.
- Public API shape: convenience functions (`dns.lookup`, `dns.reverse`) **+** a configurable
  `DnsClient` (requests-style) for reuse/bulk.
- Each new category = a self-contained package registered as a Typer sub-app (in-tree).
- Config precedence: **flags > env (`OPSKIT_*`) > profile > config-file default > built-in.**
  Only the CLI reads env/config; the API takes explicit config only.

## Conventions

- **Types everywhere** (PEP 484/585/604); passes `mypy --strict` **and** `pyright`. No implicit `Any`.
- **Ruff** for format + lint (line length 88; Google-style docstrings via `D`; `PL` rules on). No
  standalone pylint/black/isort/flake8.
- Public modules/classes/functions carry Google-style docstrings (docs gate, Art. II).

## Testing

- `pytest` (+ Hypothesis for parsers); coverage **≥ 90%** (`--cov-fail-under=90`).
- Layers: unit → **injected mock resolver** (cover every rcode/failure class) → **in-process
  loopback DNS server** (real sockets: timeout, REFUSED, TC-bit→TCP, latency, split-horizon).
- Real-network tests are `@pytest.mark.network` and **must never gate CI** (skipped by default).
- Cover the tightened-network edge cases listed in the spec/PLAN.

## Tooling & commands (run via uv, from the repo root)

```bash
uv sync                     # install deps (once pyproject.toml exists)
uv run opskit dns lookup example.com -t MX   # run the CLI
uv run pytest               # tests
uv run ruff format . && uv run ruff check .  # format + lint
uv run mypy src && uv run pyright            # types
uv run nox                  # full local matrix / sessions
```

## Git & workflow

- **Trunk-based:** `main` is the always-releasable trunk (PR-only). Feature branches are Spec-Kit
  numbered: `NNN-slug` (e.g. `001-dns-diagnostics`).
- **Conventional Commits**; **squash-merge**; PR title is a Conventional Commit.
- Versioning/changelog/release are automated (release-please); don't hand-edit versions.
- Spec Kit flow: `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`.
  Every plan/spec passes the Constitution Check.
- Don't implement ahead of an approved spec/plan.

## Security & scope

- Full scanner suite gates every PR (pip-audit, Ruff `S`, bandit, CodeQL, secret scanning,
  dependency review, SonarCloud). Fix root causes; don't add `# noqa`/`# nosec` without written
  justification.
- Redact credentials in all output/logs.
- No dependency on EOL Python or unmaintained libraries.
