# Phase 1 Data Model: File Operations

Conceptual model for the `file` category. Concrete types are frozen stdlib `@dataclass`es in
`src/opskit/file/models.py` with `to_dict()` for the JSON envelope. No persistence beyond the
files the operator explicitly targets.

## Enumerations

- **ExitCode**: two new members — `INVALID_CONTENT=21`, `CLOBBER_REFUSED=22` (research R9); every
  other outcome reuses `USAGE=2`, `PERMISSION_DENIED=15`, `NOT_FOUND=16`, `PARTIAL=7`, `OK=0`,
  `ERROR=1`.
- **StructuredFormat**: `json | yaml | toml | xml` — used by `validate`, `convert`, `pretty`,
  `diff` wherever a format must be named or detected (research R1–R3).
- **LineEnding**: `lf | crlf | cr` — the three line-ending styles `lineendings`/`eol` reason
  about; a file may contain a mix (`LineEndingReport.mixed`), but `eol`'s `--to` target is always
  exactly one of these three.

## Entities

### FileTarget *(implicit — the resolved input to every command, not a returned value)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | operator-supplied path, resolved/normalized |
| `format` | `StructuredFormat \| None` | declared via `--format`, else detected (research R5) |
| `encoding` | `str \| None` | declared via `--from`, else detected (research R4) |

### LineEndingReport *(`file/models.py`, returned by `lineendings()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `crlf_count` / `lf_count` / `cr_count` | `int` | occurrences of each style (FR-001) |
| `mixed` | `bool` | `True` when more than one style appears |

### EncodingReport *(`file/models.py`, returned by `encoding()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `encoding` | `str` | best-guess/declared encoding name (research R4) |
| `has_bom` | `bool` | |
| `confidence` | `float \| None` | `None` when a BOM made detection certain; else 0–1 from `charset-normalizer` |
| `invalid_sequences` | `bool` | `True` if byte sequences invalid for the detected encoding were found (FR-002) |

### ValidationResult *(`file/models.py`, returned by `validate()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `format` | `StructuredFormat` | detected or declared (FR-003) |
| `valid` | `bool` | |
| `error_line` / `error_column` | `int \| None` | populated only when `valid is False` and the parser reports a location |
| `error_message` | `str \| None` | populated only when `valid is False` |

### IdentificationResult *(`file/models.py`, returned by `identify()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `detected_type` | `str` | e.g. `"json"`, `"png"`, `"undetermined"` (research R5) |
| `extension` | `str` | the file's actual extension, lowercased, without the dot (`""` if none) |
| `extension_matches` | `bool` | `False` triggers the FR-004 mismatch flag |

### ChecksumResult *(`file/models.py`, returned by `hash_files()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `algorithm` | `str` | `sha256` (default) \| `sha1` \| `md5` |
| `digest` | `str` | lowercase hex |

### DiffEntry *(nested — one differing key path, under `StructuralDiffResult.differences`)*
| Field | Type | Notes |
|-------|------|-------|
| `key_path` | `str` | dotted/bracketed path, e.g. `"server.ports[1]"` (the bare-scalar top-level case, e.g. comparing two non-container documents directly, uses `"$"`, as-built addendum) |
| `left_value` | `object \| MISSING` | the `MISSING` sentinel (`models.MISSING`) when the key is absent on the left (research R6) |
| `right_value` | `object \| MISSING` | same sentinel convention on the right |

`to_dict()` renders each side as `{"present": bool, "value": ...}` (`present: false` and no
`value` key when that side is `MISSING`) rather than emitting the sentinel itself, which isn't
JSON-serializable — as-built addendum. Any `date`/`datetime`/`time` leaf a YAML/TOML source can
produce is coerced to its ISO-8601 string form recursively before serialization, mirroring
`formats.py`'s own JSON-output handling, so a dated document diffed with `--json` doesn't crash
(as-built addendum, discovered implementing `diff()`).

### StructuralDiffResult *(`file/models.py`, returned by `diff()`)*
| Field | Type | Notes |
|-------|------|-------|
| `left_path` / `right_path` | `str` | |
| `equivalent` | `bool` | `True` iff `differences` is empty (FR-006) |
| `differences` | `tuple[DiffEntry, ...]` | empty when `equivalent` |

### StatResult *(`file/models.py`, returned by `stat_files()`, one per file)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | |
| `size_bytes` | `int` | |
| `modified_at` | `str` | ISO-8601, UTC |
| `permissions` | `str \| None` | POSIX octal mode string; `None` on Windows (FR-022) |
| `owner` | `str \| None` | `None` when not determinable without elevated privileges |
| `is_symlink` | `bool` | |
| `symlink_target` | `str \| None` | populated only when `is_symlink` |

### DuplicateGroup *(`file/models.py`, one element of `find_duplicates()`'s result list)*
| Field | Type | Notes |
|-------|------|-------|
| `digest` | `str` | the shared content hash (sha256) grouping these files (research R6) |
| `size_bytes` | `int` | shared file size |
| `paths` | `tuple[str, ...]` | every file sharing this digest — always length ≥ 2 |

### ConversionResult *(`file/models.py`, returned by `convert()`/`reencode()`/`eol()`/`pretty()`)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | the source file operated on |
| `destination` | `str` | `"-"` for stdout (as-built; originally spec'd `"stdout"`), an explicit `--output` path, or the source path itself when `in_place` |
| `in_place` | `bool` | |
| `backup_path` | `str \| None` | populated only when `--backup` was used |
| `lossless` | `bool` | `False` when the conversion could not exactly preserve source data/characters (FR-018) — the command still succeeds; this is a reported fact, not a failure |
| `lossy_reason` | `str \| None` | populated only when `lossless is False` |

**Write-safety invariants** (apply to every `ConversionResult`-producing command, research R7):
- `in_place is False` ⇒ the source file at `path` is guaranteed byte-for-byte unchanged.
- `in_place is True` ⇒ the write that produced the current content of `path` was atomic
  (`atomic.py`); `path` never observably contains partially-written content.
- `backup_path is not None` ⇒ that path contains the pre-conversion content of `path`.

**As-built addendum**: each guarded write function actually returns a `WriteOutcome` — a
`NamedTuple` of `(result: ConversionResult, stdout_content: bytes | None)`, not a bare
`ConversionResult` — because the library layer must never `print()` (Art. III). When neither
`output` nor `in_place` was requested, `result.destination == "-"` and the transformed bytes
come back via `stdout_content` for the *caller* (the CLI, in practice) to print; otherwise
`stdout_content is None`. See `contracts/python-api.md`.

## Batch semantics summary

| Command | Batchable? | Multiple-target shape |
|---|---|---|
| `lineendings`, `encoding`, `validate`, `identify`, `hash`, `stat` | Yes | args / `--input-file` / stdin; one `*Result` per target; failures never abort the batch (FR-020) |
| `eol` | Yes | same as above — line-ending normalization doesn't change a file's format/identity, so batching multiple files to the same `--to` target is unambiguous |
| `convert`, `reencode`, `pretty` | No — one source per invocation | changing format/encoding needs a per-file output destination decision (`--output`/`--in-place`); batching is deferred (spec Assumptions) |
| `diff` | No — exactly two paths | pairwise by definition |
| `duplicates` | No — one directory root (with `--recursive`) | not a list-of-targets shape |

**As-built addendum**: "batchable" above describes the *CLI's* behavior. Every function in
`opskit.file`'s Python API — including the batchable ones — takes a single target path and
raises directly on failure; there is no `*paths` varargs form. The CLI implements FR-020's
batch semantics once, generically, in `opskit.core.cliutils.collect_outcomes`, by calling the
single-target function once per target (matching the `opskit.storage.dir_size`/
`opskit.net.probe` precedent). See `contracts/python-api.md`'s "As-built note" and "Raise/return
split" for the full rationale.
