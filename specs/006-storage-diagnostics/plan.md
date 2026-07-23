# Implementation Plan: Storage Diagnostics

**Branch**: `006-storage-diagnostics` | **Date**: 2026-07-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-storage-diagnostics/spec.md`

## Summary

Add the `opskit storage` category: a read-only, cross-platform disk/storage inspection command
group with three commands — `storage volumes` (every mounted volume's filesystem type and
total/used/free/percent-used utilization, local vs. network tagged), `storage disks` (physical
disks with nested partitions, best-effort per platform), and `storage size PATH...` (recursive
directory-size total, optional depth-limited child-directory breakdown, explicit hidden-files
toggle, permission-denied subdirectories skipped-and-reported rather than aborting). Technical
approach per [research.md](research.md): mounted-volume/filesystem-type enumeration and
utilization via a new base dependency, **`psutil`** (no unified stdlib API exists for this across
Windows/macOS/Linux — confirmed with the requester over hand-rolling per-OS `ctypes`); physical
disk/partition inventory at tiered per-OS fidelity (full on Linux via `/sys/block`, best-effort
elsewhere, explicitly marked unavailable rather than fabricated — FR-006); directory-size
scanning via a pure-stdlib `os.scandir()` walk with no new dependency. Zero new `ExitCode`
members — every outcome reuses an existing class (research R6).

## Technical Context

**Language/Version**: Python 3.9–3.13 (unchanged project floor; `os.scandir()`,
`os.stat().st_file_attributes`, and `psutil`'s public API are all available on 3.9+)

**Primary Dependencies**: existing (typer, rich, platformdirs, dnspython, pyopenssl,
cryptography) **+ new runtime dep: `psutil>=6,<8`** (BSD-licensed, PyPI's most widely deployed
cross-platform system/process-info library). No other additions.

**Storage**: N/A (stateless diagnostics; reads the filesystem/OS, persists nothing)

**Testing**: pytest; volume/disk enumeration tested via a mocked/monkeypatched `psutil` boundary
(deterministic fixture data, no dependency on the CI runner's actual disks); directory-size
scanning tested against real `tmp_path` fixture trees (known sizes, nesting, symlink loop, hidden
entries, a permission-denied subdirectory on POSIX); coverage ≥ 90%

**Target Platform**: Windows / macOS / Linux (CI matrix × 3.9–3.13); per-field platform fidelity
differences are part of the documented contract (research R2), not a gap to hide

**Project Type**: library + CLI (existing single-project `src/` layout)

**Performance Goals**: `volumes`/`disks` complete in well under a second (single `psutil` calls,
no filesystem tree walk); a `size` scan's duration is proportional to tree size (SC-001, SC-002)

**Constraints**: read-only — `size` never modifies/moves/deletes anything it walks; no network
calls of any kind (storage is entirely local-machine diagnostics, stricter than
`dns`/`tls`/`net`/`ad` which each make exactly one user-directed network call); library layer
never prints/exits; `core` receives zero changes (no new `ExitCode` members — research R6)

**Scale/Scope**: three new CLI commands, one new package (`opskit/storage`), one new base
dependency, zero new exit codes, `size` batchable to hundreds of paths like other categories

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 — **PASS**, no violations.*

**Core principles:**

| Principle | Compliance |
|---|---|
| I Conventional Commits/changelog | Standard flow; release-please picks up `feat(storage)` commits. PASS |
| II Documentation completeness | All three commands ship `--help` + `src/opskit/storage/README.md`; public API docstrings (Google style). PASS |
| III Zero security compromise | `psutil` is actively maintained and passes pip-audit/Snyk; no secrets involved (local filesystem/OS queries only). PASS |
| IV Dependency freshness | `psutil>=6,<8` is current-major, Dependabot-covered. PASS |
| V Strict SemVer | New category + zero new `ExitCode` members are **additive** → MINOR. PASS |
| VI Pure-Python parity | No shelling out (no `diskpart`/`wmic`/`diskutil`/`lsblk`/`df` subprocess calls); `psutil` is an in-process library call, same category as `cryptography`/`ldap3`, not a native-tool shell-out; per-platform field gaps are explicitly reported (FR-006), not silently divergent behavior. PASS |
| VII CLI/API parity, typed core | All logic in `opskit.storage`'s typed API; `storage/cli.py` is a thin client; errors own their exit codes; `core` gets **zero changes** (research R6 — no new enum members, no core→storage imports); category rendering lives in `storage/output.py`. PASS |
| VIII Zero telemetry | No network calls at all — strictly stricter than every prior category (local filesystem/OS queries only). PASS |
| IX Output contract | Human + versioned `--json`/`--jsonl`; `NO_COLOR`; `size`'s batch rule (process all paths, per-path failures in JSON, 0/uniform/PARTIAL); `volumes`/`disks` are single-query multi-row reports (no per-target failure mode to lose). PASS |
| X Diagnostic-only scope | Strictly read-only: `size` walks but never modifies/deletes/moves; no scanning of other machines, no enumeration beyond the local machine's own storage. PASS |

**OpenSSF Scorecard & Best-Practices Baseline:**
- [x] No new/edited GitHub Actions (no workflow changes needed).
- [x] Workflow tokens unchanged (least-privilege remains).
- [x] No dangerous-workflow patterns introduced.
- [x] New dependency (`psutil`) is actively maintained, passes pip-audit + Snyk, and lands in `uv.lock`.
- [x] New commands ship tests + docs and preserve the output/exit-code contract (additive only, zero new codes).
- [x] No secrets committed; inputs validated (path existence/type, non-negative `--depth`) before filesystem I/O; read-only, zero-telemetry scope preserved (strictly no network calls at all).
- [x] Release/packaging path untouched (Trusted Publishing + SBOM + attestations intact).
- [x] SECURITY.md, branch protection, Dependabot unchanged.

**New-category cross-cutting checklist** (from CLAUDE.md "Cross-cutting rules for new
categories"):
- [x] `src/opskit/storage/cli.py` will use **eager** annotations + `Optional[X]` — no
      `from __future__ import annotations` — so Typer keeps `Annotated` metadata on Python 3.9.
- [x] Every OS-derived/user-supplied string (device names, mount points, paths, disk models,
      inaccessible-path reasons) is `rich.markup.escape()`d before markup output; consoles built
      via `make_console` (honors `NO_COLOR`).
- [x] Filesystem `OSError`/`PermissionError` raised while listing the *top-level* requested path
      is normalized into `PathNotFound`/`PathPermissionDenied`; errors raised while listing
      *nested* subdirectories are caught per-directory and recorded as `InaccessiblePath` instead
      of propagating (spec FR-011) — `core` stays category-agnostic (zero new members, R6).
- [x] `storage size` (the only batchable command here) processes **every** path, aggregates exit
      codes (0 all-ok / uniform class / else `7` PARTIAL — including the single-path-but-
      incomplete case, research R6), and emits a JSON envelope per path including failures
      (Art. IX). `volumes`/`disks` have no per-target batch shape (no targets — see contracts).
- [ ] Docs-coverage gate: to be satisfied during `/speckit-implement` — `src/opskit/storage/README.md`
      written and linked from the root README's Commands table (tracked as a task, not yet done).
- [x] Cross-OS behavior tested tolerant of platform variance: `psutil` enumeration is exercised
      through a mocked boundary with per-platform fixture data (not real disks) so CI is
      deterministic; best-effort/unavailable fields (research R2) are asserted **per platform**
      rather than skipped, so a regression that silently changes availability is caught.

## Project Structure

### Documentation (this feature)

```text
specs/006-storage-diagnostics/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── cli.md           # Command surface, options, exit codes, envelope
│   └── python-api.md    # opskit.storage public API contract
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/opskit/
├── cli.py                # + register storage sub-app (one line)
└── storage/              # NEW category
    ├── __init__.py       # public API re-exports (list_volumes, list_disks, dir_size, models, errors)
    ├── README.md          # command reference (linked from root README Commands table)
    ├── api.py             # list_volumes(), list_disks(), dir_size() — orchestration
    ├── cli.py             # thin Typer sub-app: volumes/disks/size (no future-annotations; Optional[...])
    ├── enumerate_.py      # psutil-backed volume/disk/partition enumeration + pseudo-fs blocklist (R1/R4)
    ├── linux_block.py     # /sys/block reads for full-fidelity Linux disk/partition detail (R2)
    ├── scan.py            # os.scandir()-based directory-size walk, symlink/hidden handling (R5)
    ├── errors.py          # StorageError base; PathNotFound(16), PathPermissionDenied(15) — reused codes
    ├── models.py          # frozen dataclasses: Volume, Disk, Partition, DirSizeResult, ChildDirSize, InaccessiblePath
    └── output.py          # category-owned rich rendering (escape() on all external strings)

tests/
├── unit/
│   ├── test_storage_enumerate.py   # volumes/disks against a mocked psutil boundary (fixture data per platform)
│   ├── test_storage_linux_block.py # /sys/block parsing (fixture sysfs trees)
│   ├── test_storage_scan.py        # dir_size(): totals, depth breakdown, hidden toggle, symlinks, Hypothesis on path edge cases
│   ├── test_storage_api.py         # list_volumes/list_disks/dir_size outcomes with injected enumeration
│   ├── test_storage_cli.py         # CLI: options, JSON envelope, exit codes, batch (size)
│   └── test_storage_output.py      # rendering incl. markup escaping
└── integration/
    └── test_storage_scan_fs.py     # real tmp_path trees: known sizes/nesting, chmod-000 subdir (POSIX),
                                     # hidden files/dirs, symlink loop — proves scan.py end-to-end
```

**Structure Decision**: extends the established single-project `src/` layout with one new
category package, `opskit/storage` (full category: api/cli/models/errors/output, mirroring
`opskit/dns`/`opskit/tls`), plus two internal-only modules (`linux_block.py`, `enumerate_.py`)
that are not part of the public API surface. `core` receives **no changes** (zero new `ExitCode`
members — research R6), the strongest form of "core stays category-agnostic" yet achieved by any
category. All cross-cutting rules from CLAUDE.md apply from the start (no future annotations in
`storage/cli.py`, escape external strings, batch+JSON failure contract for `size`, OSError
normalization at the top-level path with per-subdirectory recovery for nested errors).

## Complexity Tracking

No constitutional violations — table not required. The one deliberate, non-default choice (a new
base runtime dependency, `psutil`) was raised explicitly to the requester rather than silently
assumed, and is documented with alternatives considered in [research.md R1](research.md#r1-cross-platform-volumepartitionfilesystem-type-utilization-enumeration).
