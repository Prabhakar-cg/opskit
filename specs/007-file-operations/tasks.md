# Tasks: File Operations

**Input**: Design documents from `/specs/007-file-operations/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution mandates tests with every command (Arts. II/III, coverage
≥ 90%); the `tmp_path`-fixture + real-filesystem-atomic-write strategy is quickstart.md's
"Deterministic validation" section.

**Organization**: grouped by user story; each phase is an independently testable increment. The
`pretty` command (FR-013) has no standalone user story in spec.md — it shares `formats.py` and
`atomic.py` with `convert` and is delivered alongside it in Phase 5 (User Story 3).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US7 from spec.md (user-story phases only)

## Path Conventions

Single project: `src/opskit/`, `tests/` at repo root (per plan.md structure).

---

## Phase 1: Setup

**Purpose**: dependencies and package skeleton

- [X] T001 Add four new runtime deps to `[project.dependencies]` in pyproject.toml — `PyYAML>=6,<7` (research R1), `tomli-w>=1,<2` (R2), `defusedxml>=0.7,<1` (R3), `charset-normalizer>=3,<4` (R4); run `uv lock` + `uv sync --extra dev`; verify `pip-audit` clean
- [X] T002 [P] Create package skeleton with module docstrings: `src/opskit/file/{__init__,api,cli,formats,xml_convert,sniff,textstats,hashing,diffing,atomic,errors,models,output}.py` — `file/cli.py` carries the no-future-annotations note (CLAUDE.md rule)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the shared primitives every story builds on — new exit codes, errors, models, the
format load/dump adapter, and the atomic-write helper every guarded write command routes through

**⚠️ CRITICAL**: complete before any user-story phase

- [X] T003 [P] Add two new `ExitCode` members to `src/opskit/core/exit_codes.py`: `INVALID_CONTENT = 21`, `CLOBBER_REFUSED = 22` (research R9) — the only `core` change this feature makes
- [X] T004 [P] Implement file errors in `src/opskit/file/errors.py`: `FileError(OpskitError)` base; `FileNotFoundOnDisk` (exit `NOT_FOUND=16`, reused), `FilePermissionDenied` (exit `PERMISSION_DENIED=15`, reused), `InvalidContent` (exit `INVALID_CONTENT=21`, new), `ClobberRefused` (exit `CLOBBER_REFUSED=22`, new) (depends on T003)
- [X] T005 [P] Implement file models in `src/opskit/file/models.py`: frozen dataclasses `LineEndingReport`, `EncodingReport`, `ValidationResult`, `IdentificationResult`, `ChecksumResult`, `DiffEntry` (with the `_Missing` sentinel), `StructuralDiffResult`, `StatResult`, `DuplicateGroup`, `ConversionResult`, plus the `StructuredFormat`/`LineEnding` enums — all with `to_dict()` per data-model.md
- [X] T006 Implement the documented XML↔dict mapping in `src/opskit/file/xml_convert.py`: `@attributes`/`#text` reserved keys, Clark-notation namespace passthrough, a `lossless` flag for mixed-content/multi-prefix cases (research R3); unit tests in `tests/unit/test_file_xml_convert.py`
- [X] T007 Implement `src/opskit/file/formats.py`: `StructuredFormat` load()/dump() adapter — JSON via stdlib `json`; YAML via `PyYAML` `safe_load`/`safe_dump` only; TOML via `tomllib`/`tomli` (read) + `tomli_w` (write); XML via `defusedxml.ElementTree` (read) + stdlib `xml.etree.ElementTree` (build, no XXE surface on output) + `xml_convert` (T006); format auto-detection by extension, falling back to content sniffing; raises `InvalidContent` with line/column when the underlying parser exposes one (depends on T001, T004, T006)
- [X] T008 [P] Implement `src/opskit/file/atomic.py`: shared write-safety helper — build the replacement content, write it to a `tempfile.NamedTemporaryFile` in the **same directory** as the target, `--backup` copies the original to `<path>.bak` via `shutil.copy2` first (raising `ClobberRefused` if `<path>.bak` already exists and `--force` was not passed), then `os.replace()`; a plain (non-atomic) write path for the no-`--in-place` stdout/`--output` case (research R7) (depends on T004)
- [X] T009 [P] Register the empty `file` Typer sub-app (no commands yet) in `src/opskit/file/cli.py` and add it to `src/opskit/cli.py`
- [X] T010 [P] Unit tests for `formats.py` in `tests/unit/test_file_formats.py`: JSON/YAML/TOML/XML load/dump; Hypothesis round-trip property tests JSON⇄YAML⇄TOML and YAML⇄TOML⇄YAML across generated structures (SC-004); format auto-detection from extension and from content; `InvalidContent` raised with line/column where the parser provides one
- [X] T011 [P] Unit tests for `atomic.py` in `tests/unit/test_file_atomic.py`: successful in-place write; `--backup` creates the sibling `.bak`; `ClobberRefused` when a `.bak` already exists without `--force`; fault-injected `os.replace`/`shutil.copy2` failure (monkeypatched to raise mid-operation) leaves the original file's bytes and mtime untouched (SC-003)

**Checkpoint**: shared primitives ready — user stories can begin

---

## Phase 3: User Story 1 - Catch a broken config file before it breaks something (Priority: P1) 🎯 MVP

**Goal**: `opskit file validate PATH...` reports valid/invalid per file, with the line/column of
any syntax error, and never aborts a batch on the first invalid file.

**Independent Test**: quickstart US1 rows — a valid file of each format reports `valid: true`
and exits 0; a file with a deliberate syntax error reports `valid: false` with a location and
exits 21; a batch of good + bad files reports both and exits 7.

### Implementation for User Story 1

- [X] T012 [US1] Implement `validate()` in `src/opskit/file/api.py`: calls `formats.load()` (T007) per target; a target that raises `InvalidContent`/`FileNotFoundOnDisk`/`FilePermissionDenied` is captured and represented as `result=None` + the typed error for that target rather than aborting the batch, per data-model.md batch semantics (depends on T005, T007)
- [X] T013 [P] [US1] Category rendering for `validate` in `src/opskit/file/output.py`: per-file valid/invalid line with line:column on failure; `rich.markup.escape()` on every path and parser-derived message
- [X] T014 [US1] Thin Typer command `validate` in `src/opskit/file/cli.py`: `PATH...` + `-i/--input-file` via `opskit.core.cliutils` batch helpers, `--format` override, `--json`/`--jsonl`/`--no-color`
- [X] T015 [P] [US1] Unit tests in `tests/unit/test_file_api.py`: `validate()` against `tmp_path` fixtures — valid/invalid file per format, auto-detected vs. `--format`-overridden
- [X] T016 [P] [US1] CLI unit tests in `tests/unit/test_file_cli.py`: envelope shape (`command: "file.validate"`), exit 0 (all valid), exit 21 (single invalid), exit 7 (mixed batch)
- [X] T017 [P] [US1] Rendering tests incl. markup-injection escaping in `tests/unit/test_file_output.py`

**Checkpoint**: MVP — `opskit file validate` fully usable

---

## Phase 4: User Story 2 - Normalize line endings without guessing what changed (Priority: P1)

**Goal**: `opskit file lineendings PATH...` reports CRLF/LF/mixed per file; `opskit file eol
PATH... --to {lf,crlf}` normalizes them, non-destructively by default, atomically and
non-clobbering when `--in-place` is used.

**Independent Test**: quickstart US2 rows — a mixed-ending fixture is correctly reported; a
non-`--in-place` `eol` run leaves the source byte-identical (hash before/after); `--in-place
--backup` rewrites the source and leaves a `.bak` with the original bytes.

### Implementation for User Story 2

- [X] T018 [P] [US2] Implement line-ending detection and normalization in `src/opskit/file/textstats.py`: `detect_line_endings(path)` — a streaming byte scan counting CRLF/LF/lone-CR occurrences without loading the whole file into memory (spec edge case); `normalize_line_endings(data, to)` — a pure transform over bytes/text
- [X] T019 [US2] Implement `lineendings()` in `src/opskit/file/api.py` using T018, batch-safe like T012 (depends on T005, T018)
- [X] T020 [US2] Implement `eol()` in `src/opskit/file/api.py`: read the source, normalize via T018, write the result through `atomic.py` (T008) honoring `output`/`in_place`/`backup`/`force`, return a `ConversionResult` per target; batchable — each file converts independently (depends on T005, T008, T018)
- [X] T021 [P] [US2] Category rendering for `lineendings`/`eol` results in `src/opskit/file/output.py`
- [X] T022 [US2] Thin Typer command `lineendings` in `src/opskit/file/cli.py` (batch, read-only)
- [X] T023 [US2] Thin Typer command `eol` in `src/opskit/file/cli.py`: `PATH...` + `-i/--input-file`, `--to` (required), `--output`/`--in-place`/`--backup`/`--force` (mutual-exclusion and dependency validation as `UsageError` before any file I/O), `--jsonl` for batch
- [X] T024 [P] [US2] Unit tests in `tests/unit/test_file_textstats.py`: CRLF/LF/mixed detection incl. a large-file streaming case; normalization to `lf`/`crlf`
- [X] T025 [P] [US2] API unit tests in `tests/unit/test_file_api.py`: `lineendings()`/`eol()` batch behavior; non-destructive default proven by hashing the source before/after (SC-002); `--in-place --backup` produces the expected `.bak`
- [X] T026 [P] [US2] CLI unit tests in `tests/unit/test_file_cli.py`: `eol` batch envelope; exit codes 0/2/7/15/16/22; `--output` with `--in-place` rejected as a usage error

**Checkpoint**: the dos2unix workflow (`lineendings` + `eol`) fully usable

---

## Phase 5: User Story 3 - Convert a file between structured data formats (Priority: P2)

**Goal**: `opskit file convert PATH --to FORMAT` converts JSON/YAML/TOML/XML data losslessly
where possible (flagging when it can't); `opskit file pretty PATH` reformats a file's layout
without changing its data — both guarded write commands, sharing `formats.py`/`atomic.py`.

**Independent Test**: quickstart US3 rows — JSON→YAML→JSON round-trips to equivalent data
(`file diff` reports equivalent); an invalid source fails the same way `validate` would; `pretty
--sort-keys` changes formatting only.

### Implementation for User Story 3

- [X] T027 [US3] Implement `convert()` in `src/opskit/file/api.py`: `formats.load(source)` → `formats.dump(data, to)` → `atomic.py` write; rejects `--to` equal to the detected/declared source format as a `UsageError`; propagates the `lossless`/`lossy_reason` fields from the XML mapping (T006) into the returned `ConversionResult` (depends on T005, T007, T008)
- [X] T028 [US3] Implement `pretty()` in `src/opskit/file/api.py`: `formats.load()` → re-`dump()` the **same** format with `indent`/`sort_keys` → `atomic.py` write; TOML is rejected with a `UsageError` (already-canonical layout, per spec Assumptions) (depends on T005, T007, T008)
- [X] T029 [P] [US3] Category rendering for `convert`/`pretty` results in `src/opskit/file/output.py`, including a visible note when `lossless: false`
- [X] T030 [US3] Thin Typer command `convert` in `src/opskit/file/cli.py`: `PATH --to FORMAT [--format]` + write options
- [X] T031 [US3] Thin Typer command `pretty` in `src/opskit/file/cli.py`: `PATH [--format] [--indent] [--sort-keys]` + write options
- [X] T032 [P] [US3] Unit tests in `tests/unit/test_file_api.py`: `convert()` round-trips JSON⇄YAML⇄TOML (Hypothesis, SC-004); an XML fixture with mixed content converts with `lossless: false` and a reason; an invalid source raises `InvalidContent`; `pretty()` indent/sort-keys change formatting only, non-destructive by default
- [X] T033 [P] [US3] CLI unit tests in `tests/unit/test_file_cli.py`: `convert`/`pretty` envelopes; exit codes 0/2/15/16/21/22
- [X] T034 [P] [US3] Integration test in `tests/integration/test_file_roundtrip_fs.py`: full CLI invocation round-trip `convert` JSON→YAML→JSON against real `tmp_path` files; `--in-place --backup` end-to-end

**Checkpoint**: `convert` + `pretty` fully usable

---

## Phase 6: User Story 4 - Verify and identify a file of unknown or unverified provenance (Priority: P2)

**Goal**: `opskit file identify PATH...` reports a file's actual type independent of its
extension and flags mismatches; `opskit file hash PATH...` reports checksums.

**Independent Test**: quickstart US4 rows — a JSON file renamed to `.txt` is identified as JSON
with `extension_matches: false`; a computed checksum matches an independently computed one.

### Implementation for User Story 4

- [X] T035 [P] [US4] Implement `src/opskit/file/sniff.py`: an ordered signature table — binary magic-byte prefixes (PNG/GIF/PDF/ZIP-family/gzip/ELF and similar) checked first, then structural sniffing for JSON/XML/YAML/TOML via `formats.py` (T007), falling back to `"undetermined"` for files too short/ambiguous to classify (research R5)
- [X] T036 [US4] Implement `identify()` in `src/opskit/file/api.py` using T035, batch-safe (depends on T005, T007, T035)
- [X] T037 [P] [US4] Implement streaming checksum computation in `src/opskit/file/hashing.py`: chunked reads for `sha256`/`sha1`/`md5` so memory stays bounded regardless of file size
- [X] T038 [US4] Implement `hash_files()` in `src/opskit/file/api.py` using T037, batch-safe (depends on T005, T037)
- [X] T039 [P] [US4] Category rendering for `identify`/`hash` in `src/opskit/file/output.py`, visually flagging extension/content mismatches
- [X] T040 [US4] Thin Typer command `identify` in `src/opskit/file/cli.py`
- [X] T041 [US4] Thin Typer command `hash` in `src/opskit/file/cli.py`: `--algo` (default `sha256`)
- [X] T042 [P] [US4] Unit tests in `tests/unit/test_file_sniff.py`: signature table hits, extension/content mismatch, an undetermined short file
- [X] T043 [P] [US4] Unit tests in `tests/unit/test_file_hashing.py`: checksum correctness per algorithm, streaming on a large fixture
- [X] T044 [P] [US4] CLI unit tests in `tests/unit/test_file_cli.py`: `identify`/`hash` envelopes and batch mixed-outcome handling

**Checkpoint**: `identify` + `hash` fully usable

---

## Phase 7: User Story 5 - Diagnose and fix a text-encoding problem (Priority: P3)

**Goal**: `opskit file encoding PATH...` reports detected encoding/BOM/invalid sequences;
`opskit file reencode PATH --to ENCODING` transcodes safely.

**Independent Test**: quickstart US5 rows — UTF-8-with-BOM, UTF-16, and Latin-1 fixtures are each
correctly detected; a Latin-1→UTF-8 transcode round-trips the original text.

### Implementation for User Story 5

- [X] T045 [P] [US5] Implement encoding detection in `src/opskit/file/textstats.py`: BOM check via a stdlib byte-prefix table (UTF-8/UTF-16-LE/UTF-16-BE/UTF-32); no-BOM fallback via `charset_normalizer.from_bytes(...).best()`; invalid-byte-sequence flagging (research R4)
- [X] T046 [US5] Implement `encoding()` in `src/opskit/file/api.py`, batch-safe (depends on T005, T045)
- [X] T047 [US5] Implement transcoding in `src/opskit/file/textstats.py` and `reencode()` in `src/opskit/file/api.py`: decode with the source encoding (declared via `--from` or detected), re-encode with the target, raising `InvalidContent` naming the offending character when the target encoding can't represent it; write via `atomic.py` (T008) (depends on T005, T008, T045)
- [X] T048 [P] [US5] Category rendering for `encoding`/`reencode` in `src/opskit/file/output.py`
- [X] T049 [US5] Thin Typer command `encoding` in `src/opskit/file/cli.py`
- [X] T050 [US5] Thin Typer command `reencode` in `src/opskit/file/cli.py`: `--to` (required), `--from` (optional) + write options
- [X] T051 [P] [US5] Unit tests in `tests/unit/test_file_textstats.py`: BOM detection for each variant, no-BOM detection via `charset-normalizer`, invalid-sequence flagging, a transcode round-trip, an unencodable-character failure
- [X] T052 [P] [US5] CLI unit tests in `tests/unit/test_file_cli.py`: `encoding`/`reencode` envelopes and exit codes

**Checkpoint**: encoding diagnostics + transcoding fully usable

---

## Phase 8: User Story 6 - Compare structured files and find duplicates (Priority: P3)

**Goal**: `opskit file diff LEFT RIGHT` reports structural equivalence or the differing key
paths; `opskit file duplicates DIRECTORY` groups files with identical content.

**Independent Test**: quickstart US6 rows — a reformatted-but-equivalent YAML pair reports
`equivalent: true`; a real value change is pinpointed by key path; duplicate copies in a scratch
directory are grouped.

### Implementation for User Story 6

- [X] T053 [US6] Implement the recursive structural comparator in `src/opskit/file/diffing.py`: walks two parsed trees by key/index, emits `(key_path, left_value, right_value)` per leaf difference using the `_Missing` sentinel for absent keys (research R6)
- [X] T054 [US6] Implement `diff()` in `src/opskit/file/api.py`: `formats.load()` (T007) both sides, run T053; raises `FileNotFoundOnDisk`/`InvalidContent` directly (single-target, not batch) (depends on T005, T007, T053)
- [X] T055 [US6] Implement size-then-hash duplicate grouping in `src/opskit/file/hashing.py`: group by size first, then by streaming content hash (**depends on T037, US4** — build T037 first if US4 hasn't landed yet) within each size group; honors a `recursive` flag over the directory walk
- [X] T056 [US6] Implement `find_duplicates()` in `src/opskit/file/api.py` using T055 (depends on T005, T037, T055)
- [X] T057 [P] [US6] Category rendering for `diff`/`duplicates` in `src/opskit/file/output.py`
- [X] T058 [US6] Thin Typer command `diff LEFT RIGHT` in `src/opskit/file/cli.py`: `--format` override applying to both sides
- [X] T059 [US6] Thin Typer command `duplicates DIRECTORY` in `src/opskit/file/cli.py`: `--recursive`
- [X] T060 [P] [US6] Unit tests in `tests/unit/test_file_diffing.py`: equivalence and differing-path cases, Hypothesis over generated nested structures, the missing-key sentinel behavior
- [X] T061 [P] [US6] Unit tests in `tests/unit/test_file_hashing.py`: duplicate grouping (size pre-filter, `recursive` on/off, empty-directory/no-duplicates case)
- [X] T062 [P] [US6] CLI unit tests in `tests/unit/test_file_cli.py`: `diff`/`duplicates` envelopes and exit codes

**Checkpoint**: `diff` + `duplicates` fully usable

---

## Phase 9: User Story 7 - Inspect file metadata and use everything from code (Priority: P3)

**Goal**: `opskit file stat PATH...` reports cross-platform-normalized metadata; the full
`opskit.file` public API surface is finalized and its documented examples run as written.

**Independent Test**: quickstart US7 rows — size/mtime/permissions/symlink-target reported
correctly; both `contracts/python-api.md` usage examples execute unmodified (SC-006).

### Implementation for User Story 7

- [X] T063 [US7] Implement `stat_files()` in `src/opskit/file/api.py`: `os.stat`/`os.lstat`-based cross-platform-normalized metadata (POSIX octal permissions and owner via `pwd`, both `None` on Windows per FR-022; symlink target via `os.readlink`), batch-safe (depends on T005)
- [X] T064 [P] [US7] Category rendering for `stat` in `src/opskit/file/output.py`
- [X] T065 [US7] Thin Typer command `stat` in `src/opskit/file/cli.py`
- [X] T066 [P] [US7] Unit tests in `tests/unit/test_file_api.py`: `stat_files()` incl. a symlink fixture; cross-platform field-availability assertions per platform (permissions/owner `None` on Windows, populated on POSIX)
- [X] T067 [P] [US7] CLI unit tests in `tests/unit/test_file_cli.py`: `stat` envelope and batch behavior
- [X] T068 [US7] Finalize `src/opskit/file/__init__.py` `__all__` (all functions, models, enums, errors per contracts/python-api.md) and add a test executing **both** `contracts/python-api.md` usage examples verbatim in `tests/unit/test_file_api.py` (SC-006)

**Checkpoint**: full category functional — API parity delivered

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T069 [P] Write `src/opskit/file/README.md` (command reference mirroring `storage/README.md`: options tables for all 12 commands, the two new exit codes, JSON samples, the documented XML↔dict mapping convention, a library section) and add the `opskit file` row + link in the root `README.md` Commands table (docs gate, Art. II)
- [X] T070 [P] Additional end-to-end scenarios in `tests/integration/test_file_roundtrip_fs.py`: `eol`/`reencode`/`pretty` `--in-place --backup` end-to-end; an interrupted-write fault injection exercised through the full CLI (not just `atomic.py` unit-level)
- [X] T071 Run the full quickstart validation matrix + all gates: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pyright && uv run pytest -q` (coverage ≥ 90%)
- [X] T072 Reconcile design docs with as-built reality: append any research.md/data-model.md addenda for discoveries made during implementation (matching the `storage` feature's precedent of recording as-built deviations rather than silently diverging from the plan)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories (T007's `formats.py` and
  T008's `atomic.py` are load-bearing for nearly every later phase)
- **User Stories (Phase 3–9)**: all depend on Foundational; can proceed in priority order
  (P1 → P1 → P2 → P2 → P3 → P3 → P3) or in parallel if staffed — see per-story dependencies below
- **Polish (Phase 10)**: depends on all desired user stories being complete

### User Story Dependencies

- **US1 (validate, P1)**: needs `formats.py` (T007) only — no write path, no other story
- **US2 (lineendings/eol, P1)**: needs `atomic.py` (T008) in addition to T005 — independent of US1
- **US3 (convert/pretty, P2)**: needs `formats.py` (T007) + `atomic.py` (T008) + `xml_convert.py`
  (T006) — independent of US1/US2, though it reuses the same `InvalidContent` failure shape US1
  exercises first
- **US4 (identify/hash, P2)**: needs `formats.py` (T007, for structural sniffing) — independent
- **US5 (encoding/reencode, P3)**: needs `atomic.py` (T008) — independent
- **US6 (diff/duplicates, P3)**: needs `formats.py` (T007) for `diff`; `duplicates` (T055/T056)
  additionally reuses the streaming-checksum primitive built in **US4's T037** (`hashing.py`) — so
  unlike every other story pair here, US6 is not fully independent of US4. Under the documented
  priority-order execution (P2 before P3) this is never a problem; a parallel team should either
  build US4 first or have whoever picks up US6 implement T037 as part of their own work if US4
  hasn't landed yet.
- **US7 (stat/API parity, P3)**: needs only T005; T068 additionally exercises every prior story's
  public function, so it's naturally last

### Within Each User Story

- Detection/transform primitives before the `api.py` orchestration function
- `api.py` before `cli.py`
- Implementation before its unit tests' assertions are meaningful (tests may be written first if
  practicing TDD — both orders satisfy this task list)
- Story complete (checkpoint) before moving to the next priority tier

### Parallel Opportunities

- T001/T002 (Setup) can run in parallel
- T003–T005 and T008–T009 (Foundational, different files) can run in parallel; T006 and T007 are
  sequential (T007 imports T006); T010/T011 (foundational tests) can run once their targets exist
- Once Foundational completes, **US1, US2, US4, US7's read half, and US6's `diff` half** can
  start in parallel immediately; US3 and US5 additionally need `atomic.py` (already in
  Foundational, so they too can start immediately). US6's `duplicates` half is the one exception —
  it depends on US4's T037 (streaming checksum), so a fully parallel team should sequence that
  slice after US4 or have its owner implement T037 directly
- Within any story, `output.py` rendering, unit test files, and (where noted `[P]`) model/helper
  modules touching different files can run in parallel

---

## Parallel Example: User Story 2 (the dos2unix workflow)

```bash
# Foundational primitives for this story (after Phase 2 completes):
Task: "Implement line-ending detection/normalization in src/opskit/file/textstats.py"

# Once T018 lands, api.py and rendering can proceed in parallel:
Task: "Implement lineendings() in src/opskit/file/api.py"
Task: "Category rendering for lineendings/eol results in src/opskit/file/output.py"

# Tests for this story, in parallel with each other:
Task: "Unit tests in tests/unit/test_file_textstats.py"
Task: "API unit tests in tests/unit/test_file_api.py"
Task: "CLI unit tests in tests/unit/test_file_cli.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — `formats.py`/`atomic.py` block everything else)
3. Complete Phase 3: User Story 1 (`validate`)
4. **STOP and VALIDATE**: run quickstart's US1 rows independently
5. Ship if ready — `validate` alone is a complete, useful diagnostic

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 (`validate`) → validate independently → ship (MVP)
3. US2 (`lineendings`/`eol`) → the dos2unix use case that motivated this feature → ship
4. US3 (`convert`/`pretty`) → the format-conversion use case → ship
5. US4 (`identify`/`hash`) → ship
6. US5 (`encoding`/`reencode`) → ship
7. US6 (`diff`/`duplicates`) → ship
8. US7 (`stat` + finalized API) → ship
9. Each story adds value without breaking previously-shipped stories

### Parallel Team Strategy

With multiple developers, after Setup + Foundational: one developer per story (US1–US7); the two
guarded-write-heavy stories (US2, US3) are the ones most worth prioritizing together since they
exercise `atomic.py` most thoroughly and would surface any shared-helper bug earliest.

---

## Notes

- [P] tasks = different files, no dependency on incomplete work
- [Story] label maps task to specific user story for traceability
- `formats.py` (T007) and `atomic.py` (T008) are the two foundational modules nearly everything
  else depends on — get these right and well-tested (T010/T011) before starting story work
- Every guarded write command MUST route through `atomic.py` — no command should hand-roll its
  own write logic (this is the whole point of R7's shared-helper decision)
- Commit after each task or logical group
- Stop at any checkpoint to validate a story independently
- Avoid: vague tasks, same-file conflicts, cross-story dependencies that break independence
