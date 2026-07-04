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

## Cross-cutting rules for new categories (hard-won on `dns`)

Apply these from the start of every new category (`net`/`tls`/`ad`); each one cost us rework on `dns`.

- **Typer + Python 3.9:** command modules (`<category>/cli.py`) MUST NOT use
  `from __future__ import annotations`. With deferred (string) annotations, Typer drops the
  `Annotated[..., typer.Argument/Option(...)]` metadata on 3.9 — positional args and short flags
  silently become `--options` and every command exits 2. Keep annotations eager and write
  `Optional[X]` (never `X | None`) in these modules. Every other module keeps future annotations.
- **Type-checker targets:** mypy can only target `>=3.10` (`[tool.mypy] python_version = "3.10"`);
  the real 3.9 floor is enforced by pyright (`pythonVersion = "3.9"`) **and** the 3.9 CI test leg —
  keep both. Don't set mypy to 3.9 (it errors).
- **Escape external strings in rich output:** any resolver/network-derived or user-supplied string
  (record values, hostnames, server addresses, trace referrals, batch headers, **table titles**)
  MUST pass through `rich.markup.escape()` before printing or interpolating into markup — a value
  like `[bold]` otherwise breaks rendering or injects styling. `typer.echo` is plain text — do NOT
  escape there.
- **NO_COLOR:** build consoles via `make_console` and pass `no_color=None` (its default) so rich
  honors the `NO_COLOR` env var and TTY detection; pass `True` only to *force* plain output. Never
  pass `False` — it overrides `NO_COLOR`.
- **Normalize OS/socket errors (Art. VI):** network code MUST catch raw `OSError`
  (refused/unreachable) alongside the library's own timeout type and re-raise a typed error from the
  shared hierarchy with an actionable hint. A raw `OSError` reaching the CLI is a bug.
- **Batch + JSON contract (Art. IX):** commands taking multiple targets (args/`--input-file`/stdin)
  MUST process every target (never abort on first failure); exit `0` only if all succeed, the single
  outcome's code if uniform, else `7` (PARTIAL); and in `--json`/`--jsonl` emit an envelope for
  **every** target including failures (`result: null`, `error: {...}`). Failures go to stderr only in
  human mode — never dropped from JSON.
- **Keep `core` category-agnostic:** `core` must not import a category's models. Each error type owns
  its exit code (`OpskitError.exit_code`; subclasses narrow it) and `exit_code_for` just reads it —
  no isinstance ladders in `core`. Category rendering lives in `<category>/output.py`;
  `core/output.py` holds only the generic `make_console`.
- **Security-fix hygiene:** fix vulnerable deps surgically. Prefer scoping the offending dev tool
  (e.g. `pip-audit`/`nox`/`pre-commit`) with a `; python_version >= '3.10'` marker — their patched
  releases dropped 3.9 and they don't run on the 3.9 test leg — over a blanket `uv lock --upgrade`,
  which drags in unrelated major runtime/tooling bumps. The shipped wheel's runtime deps stay clean;
  dev-only 3.9 residuals with no 3.9-compatible fix (e.g. `pytest`) are acceptable/dismissable.
- **Test gotcha (CodeQL):** don't assert `"<host.tld>" in output` — CodeQL's
  `py/incomplete-url-substring-sanitization` flags it as a URL-host check. Assert on record
  values / IPs / non-hostname substrings instead.

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
