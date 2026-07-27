# Contract: CLI — `opskit file`

The command surface is public and SemVer-governed (constitution Art. V, IX). Batchable commands
follow the `dns`/`tls`/`net`/`ad`/`storage` convention: positional paths (repeatable) and/or
`-i/--input-file` (one path per line, blank lines/`#` comments ignored) and/or stdin. Guarded
write commands additionally implement the Art. X write-safety contract (research R7).

## Commands

```bash
opskit file lineendings PATH... [OPTIONS]
opskit file encoding PATH... [OPTIONS]
opskit file validate PATH... [OPTIONS]
opskit file identify PATH... [OPTIONS]
opskit file hash PATH... [OPTIONS]
opskit file diff LEFT RIGHT [OPTIONS]
opskit file stat PATH... [OPTIONS]
opskit file duplicates DIRECTORY [OPTIONS]

opskit file convert PATH --to FORMAT [OPTIONS]
opskit file eol PATH... --to {lf,crlf} [OPTIONS]
opskit file reencode PATH --to ENCODING [OPTIONS]
opskit file pretty PATH [OPTIONS]
```

### Read-only diagnostics

#### `file lineendings`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-i, --input-file` | path | — | one file path per line |
| `--json` / `--jsonl` | flag | off | versioned envelope / NDJSON per file |
| `--no-color` | flag | off | force plain output |

**Report content (human default)**: per file — CRLF/LF/lone-CR counts, consistent vs. mixed.

#### `file encoding`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-i, --input-file` | path | — | |
| `--json` / `--jsonl` | flag | off | |
| `--no-color` | flag | off | |

**Report content (human default)**: per file — detected encoding, BOM present?, confidence (when
not BOM-certain), any invalid byte sequences.

#### `file validate`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--format` | choice: `json\|yaml\|toml\|xml` | auto-detect | overrides extension/content detection |
| `-i, --input-file` | path | — | |
| `--json` / `--jsonl` | flag | off | |
| `--no-color` | flag | off | |

**Report content (human default)**: per file — detected/declared format, valid?, and on failure
the line/column and message of the parse error.

#### `file identify`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-i, --input-file` | path | — | |
| `--json` / `--jsonl` | flag | off | |
| `--no-color` | flag | off | |

**Report content (human default)**: per file — detected type, extension, match/mismatch flag
(mismatches are visually highlighted in human mode).

#### `file hash`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--algo` | choice: `sha256\|sha1\|md5` | `sha256` | |
| `-i, --input-file` | path | — | |
| `--json` / `--jsonl` | flag | off | |
| `--no-color` | flag | off | |

**Report content (human default)**: per file — algorithm used, hex digest.

#### `file diff LEFT RIGHT`

Exactly two positional paths — not batchable (data-model.md batch summary).

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--format` | choice: `json\|yaml` | auto-detect | applies to both sides |
| `--json` | flag | off | versioned envelope (no `--jsonl` — single result) |
| `--no-color` | flag | off | |

**Report content (human default)**: equivalent, or a table of differing key paths with the value
on each side.

#### `file stat`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `-i, --input-file` | path | — | |
| `--json` / `--jsonl` | flag | off | |
| `--no-color` | flag | off | |

**Report content (human default)**: per file — size, permissions (`—` on Windows), owner (`—`
when undeterminable), last-modified time, symlink target (if applicable).

#### `file duplicates DIRECTORY`

Single directory root — not batchable.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--recursive` | flag | off | descend into subdirectories |
| `--json` | flag | off | versioned envelope (no `--jsonl` — grouped result) |
| `--no-color` | flag | off | |

**Report content (human default)**: one group per set of duplicate files (digest, size, member
paths); "no duplicates found" when none exist.

### Guarded write commands

All four share this write-destination contract (research R7):

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--output` | path | — | write result to this new file instead of stdout |
| `--in-place` | flag | off | overwrite the source file atomically; mutually exclusive with `--output` |
| `--backup` | flag | off | only with `--in-place`: write `<path>.bak` before replacing |
| `--force` | flag | off | only with `--in-place --backup`: overwrite an existing `.bak` |
| `--json` | flag | off | versioned envelope |
| `--no-color` | flag | off | |

Without `--in-place`, the source file is never opened for writing — only read (FR-015).

#### `file convert PATH --to FORMAT`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--to` | choice: `json\|yaml\|toml\|xml` | *(required)* | must differ from the detected source format |
| `--format` | choice: `json\|yaml\|toml\|xml` | auto-detect | overrides source-format detection |
| *(write options above)* | | | |

#### `file eol PATH... --to {lf,crlf}`

Batchable (data-model.md batch summary) — all files convert to the same `--to` target.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--to` | choice: `lf\|crlf` | *(required)* | |
| `-i, --input-file` | path | — | |
| `--jsonl` | flag | off | NDJSON per file (batch) |
| *(write options above)* | | | `--output` only valid with a single file target |

#### `file reencode PATH --to ENCODING`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--to` | text | *(required)* | target encoding name (e.g. `utf-8`) |
| `--from` | text | auto-detect | overrides source-encoding detection |
| *(write options above)* | | | |

#### `file pretty PATH`

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--format` | choice: `json\|yaml\|xml` | auto-detect | TOML has no free-form pretty option (its layout is already canonical) |
| `--indent` | int | `2` | |
| `--sort-keys` | flag | off | |
| *(write options above)* | | | |

## Exit codes

| Code | Meaning | Applies to |
|------|---------|------------|
| 0 | success | all |
| 1 | generic error | all |
| 2 | usage error (bad flag combination, e.g. `--output` with `--in-place`, or `--to` equal to the source format) — before any file I/O | all |
| 7 | PARTIAL — batch with mixed outcomes | batchable commands |
| 15 | permission denied — reading the source, or writing (`--in-place`/`--output`/`--backup`) | all |
| 16 | not found — a named path does not exist, or is a directory where a file is required | all |
| **21** | **NEW** — invalid content: source fails to parse for its (detected/declared) format, or a binary file was given to a text-oriented command | `validate`, `convert`, `pretty`, `diff`, `reencode` |
| **22** | **NEW** — clobber refused: an in-place write would overwrite a pre-existing, unrelated file (typically an existing `.bak`) without `--force` | any write command with `--backup` |

Batch rule (constitution Art. IX) for `lineendings`/`encoding`/`validate`/`identify`/`hash`/`stat`/
`eol`: every target processed; exit `0` only if every target succeeded, the shared outcome's code
if uniform, else `7` (PARTIAL). Failures always appear in `--json`/`--jsonl` output (`result:
null` + populated `error`).

## JSON envelopes

`schema_version "1"`. Examples (elided):

```json
{
  "schema_version": "1",
  "command": "file.validate",
  "query": {"path": "config.yaml", "format": null},
  "result": {
    "path": "config.yaml", "format": "yaml", "valid": false,
    "error_line": 12, "error_column": 3,
    "error_message": "mapping values are not allowed here"
  },
  "error": null,
  "elapsed_ms": 4.2
}
```

```json
{
  "schema_version": "1",
  "command": "file.eol",
  "query": {"path": "script.sh", "to": "lf", "in_place": true, "backup": true},
  "result": {
    "path": "script.sh", "destination": "script.sh", "in_place": true,
    "backup_path": "script.sh.bak", "lossless": true, "lossy_reason": null
  },
  "error": null,
  "elapsed_ms": 1.8
}
```

## Examples (epilog)

```bash
opskit file validate config.json config.yaml settings.toml
opskit file lineendings *.sh --json
opskit file eol script.sh --to lf --in-place --backup
opskit file convert config.json --to yaml > config.yaml
opskit file convert config.json --to yaml --in-place --backup
opskit file identify downloaded_file --json
opskit file hash *.tar.gz --algo sha256 --jsonl
opskit file diff config.old.yaml config.new.yaml
opskit file duplicates ./vendor --recursive
opskit file reencode legacy.txt --from latin-1 --to utf-8 --output legacy.utf8.txt
opskit file pretty data.json --sort-keys --indent 4
```
