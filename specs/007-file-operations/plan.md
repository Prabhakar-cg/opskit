# Implementation Plan: File Operations

**Branch**: `007-file-operations` | **Date**: 2026-07-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-file-operations/spec.md`

## Summary

Add the `opskit file` category: eight read-only diagnostics — `lineendings`, `encoding`,
`validate`, `identify`, `hash`, `diff`, `stat`, `duplicates` — plus four guarded, opt-in write
commands — `convert`, `eol`, `reencode`, `pretty` — under the new Art. X (v1.3.0) exception for
local file utilities. Technical approach per [research.md](research.md): parsing/dumping for
JSON/YAML/TOML/XML behind one `formats.py` adapter (stdlib `json`; new deps `PyYAML` and
`tomli_w` paired with the existing `tomli`/`tomllib` reader; `defusedxml` instead of raw
`xml.etree.ElementTree` to avoid XXE and the associated Bandit `S314` finding); a documented,
fixed XML⇄dict mapping convention (R3); file-type sniffing via an in-house magic-byte/structural
signature table rather than `libmagic` (R5, preserves pure-Python OS parity); encoding detection
via the new dep `charset-normalizer` (R4); a single shared `atomic.py` helper (temp file +
`os.replace()`, pre-flight no-clobber check, `--backup`/`--force` gate) used by all four write
commands so the Art. X write-safety guarantees are implemented exactly once (R7). Two new
`ExitCode` members are added: `INVALID_CONTENT` (syntax/format errors) and `CLOBBER_REFUSED`
(an in-place write would silently overwrite an unrelated existing file) — every other outcome
reuses an existing class (research R9).

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor; every new dependency below
supports 3.9+)

**Primary Dependencies**: existing (typer, rich, platformdirs, tomli/tomllib) **+ four new
runtime deps**: `PyYAML>=6,<7` (YAML load/dump — R1), `tomli-w>=1,<2` (TOML *writing*; pairs with
the existing read-only `tomli`/`tomllib` — R2), `defusedxml>=0.7,<1` (XXE-safe XML parsing,
replacing raw `xml.etree.ElementTree` — R3), `charset-normalizer>=3,<4` (text-encoding detection
— R4). No `libmagic`/`python-magic` (native C dependency, unreliable cross-platform install story
— R5 uses an in-house signature table instead). No structural-diff library (`diffing.py` is a
small hand-rolled recursive comparator — R6, no third dependency needed for the scope required).

**Storage**: N/A (operates only on files the operator explicitly names; keeps no state of its
own beyond an optional `.bak` sibling file the operator opted into)

**Testing**: pytest; Hypothesis property tests for JSON⇄YAML⇄TOML round-tripping and the
structural diff; atomic-write fault-injection tests (monkeypatch `os.replace`/`shutil.copy2` to
raise mid-operation, assert the original file is byte-for-byte intact — SC-003); all file I/O
exercised against `tmp_path` fixtures (no reliance on any pre-existing repo file); coverage ≥ 90%

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13); atomic-write and
line-ending semantics must be identical across all three (`os.replace()` is atomic on all three
since Python 3.3+; the one intentional per-OS gap is POSIX owner/permission bits being
unavailable on Windows for `stat`, reported explicitly per FR-022)

**Project Type**: library + CLI (existing single-project `src/` layout)

**Performance Goals**: hashing and line-ending/encoding detection stream files in fixed-size
chunks so memory stays bounded regardless of file size (spec edge case); no other latency target
— this category is not interactive/real-time

**Constraints**: zero network calls (tied with `storage` as the strictest category); guarded
write commands default to non-destructive output (stdout/`--output`) and only mutate the named
source file behind an explicit `--in-place`; `--in-place` writes are atomic and refuse to clobber
an unrelated existing file without `--backup`/`--force`; file content is only ever parsed/
transformed as data, never executed or evaluated (FR-017)

**Scale/Scope**: 12 new CLI commands (8 diagnostics + 4 guarded writers), one new package
(`opskit/file`), four new base runtime dependencies, two new `ExitCode` members

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, no violations.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(file)` commits. PASS |
| II Documentation completeness | All 12 commands ship `--help` + `src/opskit/file/README.md`, linked from the root README Commands table; public API carries Google-style docstrings. PASS |
| III Zero security compromise | `defusedxml` is chosen specifically *because* raw `xml.etree.ElementTree` is XXE-unsafe (Bandit `S314`) — this is a security-motivated dependency choice, not a workaround. All four new deps are actively maintained and pass pip-audit/Snyk. No secrets ever touch this category. PASS |
| IV Dependency freshness | `PyYAML`, `tomli-w`, `defusedxml`, `charset-normalizer` are all current-major, Dependabot-covered, non-EOL. PASS |
| V Strict SemVer | New category + two new additive `ExitCode` members → MINOR. PASS |
| VI Pure-Python parity | No shelling out (no `dos2unix`/`iconv`/`xmllint`/`jq` subprocess calls, no `libmagic` native dependency); all four new deps are in-process pure-Python (or pure-Python-with-optional-C-speedup, same tier as `cryptography`) libraries, not native-tool shell-outs. Atomic-write semantics (`os.replace`) are identical across OSes. PASS |
| VII CLI/API parity, typed core | All logic lives in `opskit.file`'s typed API; `file/cli.py` is a thin client; each error type owns its exit code; `core` gains only two new `ExitCode` enum members (still category-agnostic — no `core`→`file` model imports); category rendering lives in `file/output.py`. PASS |
| VIII Zero telemetry | No network calls at all — matches `storage`'s strictest-yet bar. PASS |
| IX Output contract | Human + versioned `--json`/`--jsonl`; `NO_COLOR`; every batchable command (`lineendings`, `encoding`, `validate`, `identify`, `hash`, `stat`, `eol`) processes every target and aggregates exit codes (0 all-ok / uniform class / else `7` PARTIAL); `diff`/`duplicates`/`convert`/`reencode`/`pretty` are single-target commands (contracts/cli.md documents why per-command). PASS |
| X Diagnostic-only scope & guarded file utilities | Read-only commands never write. The four guarded write commands satisfy every new Art. X guarantee: named-target only (no globbing beyond what the shell itself expands, no network calls — R8), non-destructive by default (stdout/`--output` unless `--in-place` — R7), atomic + non-clobbering in-place writes (`atomic.py`, `--backup`/`--force` required to overwrite — R7), never execute file content (parsed as data only via `formats.py`/`defusedxml`, never `eval`/`exec`/template-rendered). PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes needed).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] New dependencies (`PyYAML`, `tomli-w`, `defusedxml`, `charset-normalizer`) are all actively
      maintained, pass pip-audit + Snyk, and land in `uv.lock`.
- [x] New commands ship tests + docs and preserve the output/exit-code contract (additive only:
      two new `ExitCode` members, both documented in `contracts/cli.md`).
- [x] No secrets committed; inputs validated (path existence/type, format/encoding names,
      non-empty `--to` values) before any file I/O; read-only default preserved for diagnostics,
      and the Art. X write-safety guarantees (named-target only, non-destructive default, atomic
      non-clobbering `--in-place`) preserved for the four guarded commands; zero-telemetry scope
      preserved (no network calls anywhere in this category).
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact).
- [x] `SECURITY.md`, branch protection, Dependabot unchanged.

**New-category cross-cutting checklist** (from CLAUDE.md "Cross-cutting rules for new
categories"):
- [x] `src/opskit/file/cli.py` will use **eager** annotations + `Optional[X]` — no
      `from __future__ import annotations` — so Typer keeps `Annotated` metadata on Python 3.9.
- [x] Every file-derived/user-supplied string (paths, parse-error messages, detected format/type
      names, differing-key paths) is `rich.markup.escape()`d before markup output; consoles built
      via `make_console` (honors `NO_COLOR`).
- [x] Raw `OSError`/`PermissionError`/`UnicodeDecodeError` raised while opening/writing a named
      target is normalized into the typed hierarchy (`FileNotFoundOnDisk`→`NOT_FOUND`,
      `FilePermissionDenied`→`PERMISSION_DENIED`, parse/decode failures→`InvalidContent`→
      `INVALID_CONTENT`) with an actionable hint; `core` stays category-agnostic (two new enum
      members only, research R9).
- [x] Batchable commands (`lineendings`, `encoding`, `validate`, `identify`, `hash`, `stat`,
      `eol`) process **every** target, aggregate exit codes (0 all-ok / uniform class / else `7`
      PARTIAL), and emit a JSON envelope per target including failures (Art. IX).
- [ ] Docs-coverage gate: to be satisfied during `/speckit-implement` — `src/opskit/file/README.md`
      written and linked from the root README's Commands table (tracked as a task, not yet done).
- [x] Cross-OS behavior tested tolerant of platform variance: atomic-write tests run on the real
      filesystem (not mocked) since `os.replace()` behavior is what's under test; the one
      genuinely platform-divergent field (POSIX owner/permission bits, absent on Windows) is
      asserted per-platform as explicitly unavailable rather than skipped.

## Project Structure

### Documentation (this feature)

```text
specs/007-file-operations/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # Command surface, options, exit codes, envelope
│   └── python-api.md    # opskit.file public API contract
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/
├── cli.py                # + register file sub-app (one line)
└── file/                  # NEW category
    ├── __init__.py        # public API re-exports (functions, models, errors)
    ├── README.md           # command reference (linked from root README Commands table)
    ├── api.py              # orchestration: one function per command, calling the modules below
    ├── cli.py              # thin Typer sub-app: 12 commands (no future-annotations; Optional[...])
    ├── formats.py          # Format enum + load()/dump() adapter over json/yaml/tomli(-w)/defusedxml (R1/R2/R3)
    ├── xml_convert.py      # documented XML<->dict convention: @attributes / #text (R3)
    ├── sniff.py            # magic-byte + structural file-type signature table (R5)
    ├── textstats.py        # line-ending detection/normalization, encoding detection/transcoding (R4)
    ├── hashing.py          # streaming checksum + size-then-hash duplicate grouping (R6)
    ├── diffing.py          # structural diff of two parsed JSON/YAML trees (R6)
    ├── atomic.py           # shared write-safety helper: temp file + os.replace, --backup/--force gate (R7)
    ├── errors.py           # FileError base; new InvalidContent(21), ClobberRefused(22); reuse NotFound(16)/PermissionDenied(15)
    ├── models.py           # frozen dataclasses for every *Result entity in data-model.md
    └── output.py           # category-owned rich rendering (escape() on all file-derived strings)

tests/
├── unit/
│   ├── test_file_formats.py        # JSON/YAML/TOML/XML load/dump; Hypothesis round-trip properties
│   ├── test_file_xml_convert.py    # documented mapping convention, lossy-case flagging (FR-018)
│   ├── test_file_sniff.py          # signature table, extension/content mismatch, undetermined-short-file
│   ├── test_file_textstats.py      # CRLF/LF/mixed detection + eol normalization; encoding/BOM + reencode
│   ├── test_file_hashing.py        # checksum algorithms, streaming on a large fixture, duplicate grouping
│   ├── test_file_diffing.py        # equivalence/differing-path cases; Hypothesis on nested structures
│   ├── test_file_atomic.py         # success path; fault-injected interruption leaves original intact (SC-003); clobber gate
│   ├── test_file_api.py            # every api.py function's outcomes against tmp_path fixtures
│   ├── test_file_cli.py            # CLI: options, JSON/--jsonl envelope, exit codes incl. PARTIAL, batch semantics
│   └── test_file_output.py         # rendering incl. markup escaping of file-derived strings
└── integration/
    └── test_file_roundtrip_fs.py   # real tmp_path files: full CLI round-trips for convert/eol/reencode/pretty,
                                     # --in-place + --backup end-to-end, interrupted-write fault injection
```

**Structure Decision**: extends the established single-project `src/` layout with one new
category package, `opskit/file` (full category: api/cli/models/errors/output, mirroring
`opskit/dns`/`opskit/tls`/`opskit/storage`), plus internal-only modules (`formats.py`,
`xml_convert.py`, `sniff.py`, `textstats.py`, `hashing.py`, `diffing.py`, `atomic.py`) that are
not part of the public API surface — `api.py` is the only import boundary other categories or
external callers use. `atomic.py` is deliberately the single choke point every guarded write
command routes through, so the Art. X write-safety guarantees are implemented and tested exactly
once rather than reimplemented per command. `core` gains only two new `ExitCode` members
(`INVALID_CONTENT`, `CLOBBER_REFUSED`) — no `core`→`file` model imports, preserving
category-agnosticism.

## Complexity Tracking

No constitutional violations — the table below documents the one non-default choice (four new
runtime dependencies) transparently, mirroring how `storage`'s plan flagged `psutil`, rather than
silently assuming it.

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `PyYAML` + `tomli-w` (2 deps) | FR-003/FR-010 require YAML and TOML read *and write*; stdlib has no YAML support at all, and `tomli`/`tomllib` (already a dep) is read-only | Hand-rolling a YAML or TOML serializer is a correctness/security minefield (YAML in particular — arbitrary-tag deserialization); both libraries are the de-facto standard, actively maintained, and `PyYAML`'s `safe_load`/`safe_dump` avoids the unsafe-tag pitfall by construction |
| `defusedxml` | FR-003/FR-010 require XML parsing; raw `xml.etree.ElementTree` is documented by CPython itself as unsafe against maliciously crafted XML (billion-laughs, XXE) and is flagged by Bandit `S314` | Suppressing the Bandit finding with `# nosec` on raw `ElementTree` was rejected — it treats a real vulnerability class as a lint annoyance instead of fixing it; `defusedxml` is a drop-in, actively maintained hardening wrapper with no functional downside for this feature's read/write needs |
| `charset-normalizer` | FR-002 requires detecting a file's encoding when no BOM is present (Latin-1 vs. UTF-8 vs. others is not distinguishable by inspection alone) | A hand-rolled heuristic would re-implement a well-solved, security-adjacent problem (mis-detection on adversarial input) worse than a purpose-built, actively maintained library (already a transitive dependency of `requests` across most of the ecosystem) |
| In-house signature table instead of `libmagic`/`python-magic` | FR-004 requires content-based file-type identification | `python-magic` wraps the native `libmagic` C library, which is not reliably installable on Windows without bundling a DLL — this would break Art. VI's "identical on Win/macOS/Linux" for a feature whose scope (JSON/YAML/XML/TOML + a short list of common binary signatures) does not need `libmagic`'s full MIME database |
| Hand-rolled `diffing.py` instead of `deepdiff` | FR-006 requires structural JSON/YAML comparison | The required output shape (equivalent, or a list of differing key-paths + values) is narrow enough that a ~50-line recursive comparator is simpler to reason about, test, and keep in opskit's own envelope format than adopting and wrapping a general-purpose diff library's own output model |
