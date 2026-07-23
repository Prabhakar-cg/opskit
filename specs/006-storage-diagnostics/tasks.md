# Tasks: Storage Diagnostics

**Input**: Design documents from `/specs/006-storage-diagnostics/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command (Arts. II/III, coverage
≥ 90%); the mocked-`psutil`-boundary + real-`tmp_path`-tree strategy is quickstart.md's
"Deterministic validation" section.

**Organization**: grouped by user story; each phase is an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/`, `tests/` at repo root (per plan.md structure).

---

## Phase 1: Setup

**Purpose**: dependency and package skeleton

- [X] T001 Add runtime dep `psutil>=6,<8` to `[project.dependencies]` in pyproject.toml (research R1 — confirmed with requester as a base dep, not an extra); run `uv lock` + `uv sync --extra dev`; verify `pip-audit` clean
- [X] T002 [P] Create package skeleton with module docstrings: `src/opskit/storage/{__init__,api,cli,enumerate_,linux_block,scan,errors,models,output}.py` — `storage/cli.py` carries the no-future-annotations note (CLAUDE.md rule)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared primitives every story builds on — errors, models, and the shared
`psutil` enumeration boundary (pseudo-filesystem exclusion + network classification)

**⚠️ CRITICAL**: complete before any user-story phase

- [X] T003 [P] Implement storage errors in `src/opskit/storage/errors.py`: `StorageError(OpskitError)`, `PathNotFound` (exit `NOT_FOUND=16`, reused), `PathPermissionDenied` (exit `PERMISSION_DENIED=15`, reused) — zero `core` changes (research R6)
- [X] T004 [P] Implement storage models in `src/opskit/storage/models.py`: frozen dataclasses `Volume`, `Disk`, `Partition`, `InaccessiblePath`, `ChildDirSize`, `DirSizeResult` (with `.incomplete` property, `to_dict()`) per data-model.md
- [X] T005 Implement shared `psutil` enumeration primitives in `src/opskit/storage/enumerate_.py`: `_raw_partitions()` wrapper around `psutil.disk_partitions(all=True)`, the pseudo-filesystem blocklist + `_is_pseudo(fstype)` (research R4), and `_is_network(fstype, opts)` classifier (research R3); unit tests in `tests/unit/test_storage_enumerate.py` using a monkeypatched `psutil.disk_partitions`
- [X] T006 [P] Register the empty `storage` Typer sub-app (no commands yet) in `src/opskit/storage/cli.py` and add it to `src/opskit/cli.py`

**Checkpoint**: shared primitives ready — user stories can begin

---

## Phase 3: User Story 1 - See how full my disks are (Priority: P1) 🎯 MVP

**Goal**: `opskit storage volumes` lists every mounted, non-pseudo volume with mount point,
filesystem type, total/used/free/percent-used, and a local/network tag.

**Independent Test**: quickstart US1 rows — every mounted volume appears; pseudo filesystems
(`tmpfs`/`proc`/`sysfs`) are absent; a network mount is tagged `is_network: true`.

### Implementation for User Story 1

- [X] T007 [US1] Implement `list_volumes()` in `src/opskit/storage/enumerate_.py`: filter `_raw_partitions()` (T005) through `_is_pseudo`, call `psutil.disk_usage(mountpoint)` per remaining partition, classify network via `_is_network`, assemble `Volume` per data-model.md; a single mount whose `disk_usage()` raises `OSError` is logged (`logging.getLogger("opskit")`) and skipped rather than aborting the whole list (depends on T003–T005)
- [X] T008 [P] [US1] `list_volumes()` orchestration wrapper in `src/opskit/storage/api.py` per contracts/python-api.md; Google-style docstrings
- [X] T009 [P] [US1] Category rendering for volumes in `src/opskit/storage/output.py`: table with mountpoint, fstype, total/used/free, %used, local/network tag — `rich.markup.escape()` on every `psutil`-derived string (CLAUDE.md rule)
- [X] T010 [US1] Thin Typer command `volumes` in `src/opskit/storage/cli.py` (Output panel: `--json`/`--jsonl`/`--no-color`; `Optional[...]` annotations, no future import)
- [X] T011 [P] [US1] Unit tests in `tests/unit/test_storage_enumerate.py`: `list_volumes()` against mocked `psutil` fixtures (normal local volume, pseudo-fs excluded, network fs tagged, an unreadable mount skipped without aborting)
- [X] T012 [P] [US1] CLI unit tests in `tests/unit/test_storage_cli.py`: `storage volumes` envelope shape (`command: "storage.volumes"`, `query: {}`, `result.volumes` list), human output smoke, exit 0
- [X] T013 [P] [US1] Rendering tests incl. markup-injection escaping in `tests/unit/test_storage_output.py`

**Checkpoint**: MVP — `opskit storage volumes` fully usable

---

## Phase 4: User Story 2 - Find what's consuming space in a directory (Priority: P2)

**Goal**: `opskit storage size PATH...` reports each path's recursive total, an optional
depth-limited child-directory breakdown, a hidden-files toggle (excluded by default), and
continues past inaccessible subdirectories rather than aborting.

**Independent Test**: quickstart US2 rows — total matches a known-size fixture; `--depth N`
breaks down N levels; hidden toggle changes totals only when hidden entries exist; a
permission-denied subdirectory is skipped-and-reported, not fatal.

### Implementation for User Story 2

- [X] T014 [P] [US2] Implement the iterative `os.scandir()`-based walk in `src/opskit/storage/scan.py`: `_walk(path, include_hidden)` yielding `(DirEntry, depth)`, symlink/junction/reparse-point skip (FR-010), hidden detection (dotfile-name check on POSIX; `stat.FILE_ATTRIBUTE_HIDDEN` via `os.stat(..., follow_symlinks=False).st_file_attributes` on Windows), per-directory `OSError`/`PermissionError` capture → `InaccessiblePath` with the walk continuing into every other branch (research R5)
- [X] T015 [US2] Implement `dir_size()` in `src/opskit/storage/scan.py`: aggregate totals and the depth-limited breakdown (bottom-up child-directory totals) from a single pass of T014's walk; raise `PathNotFound`/`PathPermissionDenied` for top-level failures; return a `DirSizeResult` with `.incomplete` derived from `inaccessible` (depends on T003, T004, T014)
- [X] T016 [P] [US2] `dir_size()` orchestration wrapper in `src/opskit/storage/api.py`: validates `depth >= 0` (negative → `UsageError` before any filesystem I/O), delegates to `scan.dir_size()`
- [X] T017 [P] [US2] Category rendering for size results in `src/opskit/storage/output.py`: total/counts/hidden-mode line, breakdown table, inaccessible-paths list — `rich.markup.escape()` on every path string
- [X] T018 [US2] Thin Typer command `size` in `src/opskit/storage/cli.py`: variadic `PATH...` + `-i/--input-file` via `opskit.core.cliutils.collect_target_list`, `--depth` (default 0), `--include-hidden`, batch wiring via `collect_outcomes`/`emit_envelopes`; a storage-specific exit-code derivation (PARTIAL when any outcome carries a returned `result.incomplete=True`, not only on a raised error, since `core.cliutils.aggregate_outcome_exit` only inspects the error slot — research R6) implemented locally in `storage/cli.py`, not `core`
- [X] T019 [P] [US2] Unit tests (+ Hypothesis on path/depth edge cases) in `tests/unit/test_storage_scan.py`: totals, breakdown at depth 0/1/2/deeper-than-tree, hidden toggle on/off, symlink not followed, a subdirectory `OSError` recorded while the scan continues
- [X] T020 [P] [US2] API unit tests in `tests/unit/test_storage_api.py`: `PathNotFound`/`PathPermissionDenied` raised correctly; negative `--depth` → `UsageError` before any filesystem I/O; delegation to `scan.dir_size`/`enumerate_.list_volumes`
- [X] T021 [P] [US2] CLI unit tests in `tests/unit/test_storage_cli.py`: `storage size` batch envelope with a mixed found/not-found/incomplete set of paths; exit codes 0/2/7/16 (contracts/cli.md)
- [X] T022 [US2] Integration tests against real `tmp_path` trees in `tests/integration/test_storage_scan_fs.py`: known sizes/nesting, a `chmod 000` subdirectory (POSIX only, skipped on Windows), hidden files/dirs, a symlink loop — proves `scan.py` end-to-end (research R5)

**Checkpoint**: `opskit storage size` fully usable

---

## Phase 5: User Story 3 - Understand the physical disk and partition layout (Priority: P2)

**Goal**: `opskit storage disks` lists physical/logical disks with nested partitions, at
tiered per-platform fidelity (full on Linux, best-effort elsewhere, unavailable fields
explicit rather than fabricated or omitted — FR-006).

**Independent Test**: quickstart US3 rows — every disk and its partitions appear; on
Linux, size/model/removable are populated; on Windows, fixed/removable/network are
populated from `psutil`'s `opts`; unavailable fields are `null` in JSON, never absent.

### Implementation for User Story 3

- [X] T023 [P] [US3] Implement the Linux `/sys/block` reader in `src/opskit/storage/linux_block.py`: enumerate block devices, size (`× 512` from the `size` pseudo-file), `removable` flag, best-effort `model`, and the disk↔partition relationship via the `/sys/block/<dev>/<dev><partN>` subdirectory listing (research R2); unit tests in `tests/unit/test_storage_linux_block.py` using fixture sysfs trees built under `tmp_path`
- [X] T024 [US3] Implement `list_disks()` in `src/opskit/storage/enumerate_.py`: on Linux, merge T023's disk/partition data with T005's raw partitions (mount status/fstype linkage to the matching `Volume`), plus a whole-disk-mount fallback (synthesizes one partition entry when a disk has no OS partition table but is mounted directly — common on cloud/VM disks, discovered via manual smoke-testing against this container's real `/sys/block`); on Windows/macOS, derive one `Disk` entry per partition from T005 (one-to-one per research R2), with Windows `removable`/network parsed from `psutil`'s `opts` (`GetDriveTypeW` encoding: `fixed`/`removable`/`remote`/`cdrom`) and macOS `model`/`removable` left `None` (depends on T003–T005, T023)
- [X] T025 [P] [US3] `list_disks()` orchestration wrapper in `src/opskit/storage/api.py`
- [X] T026 [P] [US3] Category rendering for disks in `src/opskit/storage/output.py`: one panel per disk (id/size/model/removable, `—` where unavailable) with a nested partitions table (device, size, mounted?, mount point, fstype)
- [X] T027 [US3] Thin Typer command `disks` in `src/opskit/storage/cli.py`
- [X] T028 [P] [US3] Unit tests in `tests/unit/test_storage_enumerate.py`: `list_disks()` against mocked Linux fixtures (multi-partition disk, full fidelity, unmounted partition, whole-disk-mount synthesis) and mocked Windows/macOS fixtures (one-to-one, `removable`/network parsed from `opts` on Windows, `model`/`removable` `None` on macOS) — asserts per-platform field availability exactly matches research R2
- [X] T029 [P] [US3] CLI unit tests in `tests/unit/test_storage_cli.py`: `storage disks` envelope shape (`command: "storage.disks"`), nested partitions present in JSON, unavailable fields serialize as `null` (never omitted); rendering tests added to `tests/unit/test_storage_output.py` for the same escaping/placeholder guarantees as `volumes`

**Checkpoint**: all three commands independently usable — full category functional

---

## Phase 6: User Story 4 - Use it from code (Priority: P3)

**Goal**: `opskit.storage` is public, typed, and documented; the contract example runs
unmodified.

**Independent Test**: quickstart US4 rows + SC-005 — the python-api.md example executes
as written; `dir_size()` raises `PathNotFound` for a nonexistent path when called directly.

- [X] T030 [US4] Finalize `src/opskit/storage/__init__.py` `__all__` (`list_volumes`, `list_disks`, `dir_size`, models, errors) and add a test executing the contracts/python-api.md usage example in `tests/unit/test_storage_api.py`

**Checkpoint**: API parity delivered

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T031 [P] Write `src/opskit/storage/README.md` (command reference mirroring `tls/README.md`: options tables for `volumes`/`disks`/`size`, exit codes, JSON samples, library section, per-platform fidelity notes from research R2) and add the `opskit storage` row + link in the root `README.md` Commands table (docs gate, Art. II)
- [X] T032 Run the full quickstart validation matrix + all gates on default (3.9 import-compatibility spot-checked directly against the venv-free `python3.9` interpreter, since the dev container's default `uv` toolchain is 3.13): `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pyright && uv run pytest` — 801 passed, 12 deselected (network-marked), coverage 94.19% (gate ≥ 90%)
- [X] T033 Reconciled design docs with as-built reality: research.md R1 (types-psutil dev dep) and R2 (whole-disk-mount partition synthesis, discovered via manual `opskit storage disks` smoke-testing against this container's real `/sys/block`) plus the matching data-model.md fidelity note

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US1 (Phase 3)** depends only on Foundational; it is the MVP.
- **US2 (Phase 4)** depends only on Foundational (T003, T004) — independent of US1's file (`enumerate_.py` vs `scan.py`), can proceed in parallel with US1 after Phase 2.
- **US3 (Phase 5)** depends on Foundational (T003–T005) and reuses US1's `enumerate_.py` primitives (T007's `_raw_partitions`/`_is_network`), so start after T007 lands even though it's a separate story.
- **US4 (Phase 6)** depends on US1 + US2 + US3 (finalizes the combined public surface).
- **Polish (Phase 7)** last; T031 can start once all three commands are stable.

### Key task-level dependencies

- T005 needs T003 (errors) only indirectly (no direct call), but T007/T024 need T003–T005.
- T007 needs T003, T004, T005. T008 needs T007. T009 independent of T007/T008 (different file). T010 needs T008 + T009 + T006.
- T015 needs T003, T004, T014. T016 needs T015. T018 needs T016 + T017 + T006.
- T024 needs T005 (US1's T007) and T023. T025 needs T024. T027 needs T025 + T026 + T006.
- T030 needs T010, T018, T027 (the finalized command surface).

### Parallel Opportunities

- Phase 2: T003, T004, T006 fully parallel; T005 can start alongside them (touches a different file) but its tests depend on T003.
- Phase 3: T009 parallel with T007/T008 (different file); T011/T012/T013 parallel once their targets exist.
- Phase 4: T014 and T017 parallel (different files); T019/T020/T021 parallel once their targets exist.
- Phase 5: T023 parallel with everything in Phase 3/4 (different file, only needs Phase 2); T026 parallel with T024 (different file); T028/T029 parallel once their targets exist.
- **US1, US2, and (after T007) US3 can be worked on concurrently by different contributors** — they touch largely disjoint files after Foundational.

## Parallel Example: User Story 1

```bash
# After T005 completes, run in parallel:
Task: "T007 list_volumes() in src/opskit/storage/enumerate_.py"
Task: "T009 volumes rendering in src/opskit/storage/output.py"
# After T007/T008/T009/T010:
Task: "T011 enumerate unit tests"  |  Task: "T012 CLI unit tests"  |  Task: "T013 output tests"
```

## Implementation Strategy

**MVP first**: Phases 1–3 (T001–T013) deliver a fully usable `opskit storage volumes` — stop,
validate against quickstart US1, demo. **Incremental**: each subsequent phase is an
independently testable increment ending in a checkpoint; commit per task or logical group with
Conventional Commits; run the four gates before each commit batch (CLAUDE.md). US2 and US3 are
both P2 and mutually independent — treat Phases 3+4+5 together as the release-worthy core if
demoing the full category at once.
