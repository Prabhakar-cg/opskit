# `opskit file` — local file inspection & safe conversion

Read-only file diagnostics (line-ending/encoding detection, JSON/YAML/TOML/XML validation,
type sniffing, checksums, structural diff, cross-platform stat, duplicate-file finder) plus a
narrow, guarded set of opt-in local write commands (format conversion, line-ending
normalization, encoding transcoding, pretty-printing) — under constitution Art. X's
file-utility exception. Available both as CLI commands and as an importable Python API.

> Part of [**opskit**](../../../README.md). See the root README for install and project-wide docs.

---

## Contents

- [Quick start](#quick-start)
- [Read-only diagnostics](#read-only-diagnostics)
  - [`validate`](#opskit-file-validate)
  - [`lineendings`](#opskit-file-lineendings)
  - [`encoding`](#opskit-file-encoding)
  - [`identify`](#opskit-file-identify)
  - [`hash`](#opskit-file-hash)
  - [`diff`](#opskit-file-diff)
  - [`duplicates`](#opskit-file-duplicates)
  - [`stat`](#opskit-file-stat)
- [Guarded write commands](#guarded-write-commands)
  - [`convert`](#opskit-file-convert)
  - [`pretty`](#opskit-file-pretty)
  - [`eol`](#opskit-file-eol)
  - [`reencode`](#opskit-file-reencode)
- [The XML ↔ dict mapping](#the-xml--dict-mapping)
- [Output & exit codes](#output--exit-codes)
- [Use as a Python library](#use-as-a-python-library)

---

## Quick start

```bash
opskit file validate config.json config.yaml settings.toml   # syntax-check, line:column on error
opskit file lineendings *.sh                                 # CRLF/LF/mixed detection
opskit file identify downloaded_file                         # content-sniffed type vs. extension
opskit file hash *.tar.gz --algo sha256                      # checksum
opskit file diff config.old.yaml config.new.yaml              # structural (semantic) diff
opskit file duplicates ./vendor --recursive                   # find identical-content files
opskit file stat config.json                                  # size/perms/owner/mtime/symlink

opskit file convert config.json --to yaml > config.yaml       # non-destructive, stdout
opskit file eol script.sh --to lf --in-place --backup         # opt-in, atomic, non-clobbering
opskit file pretty data.json --sort-keys --indent 4
opskit file reencode legacy.txt --from latin-1 --to utf-8 --output legacy.utf8.txt
```

Every command supports human-readable output (default), `--json` (a versioned envelope), and
`--jsonl` where the command is batchable; `NO_COLOR`/`--no-color` and piped-output auto-plain
are honored everywhere.

## Read-only diagnostics

None of these commands modify, move, delete, or create any file, or perform any network call
(constitution Art. X). The batchable ones (`validate`, `lineendings`, `encoding`, `identify`,
`hash`, `stat`) accept multiple paths, `-i/--input-file`, or stdin, and process every target —
one failure never aborts the rest (Art. IX).

### `opskit file validate`

```bash
opskit file validate PATH... [OPTIONS]
```

Syntax-checks each path as JSON/YAML/TOML/XML, reporting the line/column of any parse error.
Format is auto-detected from the extension, falling back to content sniffing; `--format`
forces it. An undetermined format is reported as `valid=false`, not an error.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `--format` | Force `json`\|`yaml`\|`toml`\|`xml` instead of auto-detecting | auto-detect |
| `-i, --input-file` | File of paths, one per line (`#` comments allowed); `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file validate config.json config.yaml settings.toml
opskit file validate config.txt --format json
opskit file validate -i paths.txt --jsonl
```

### `opskit file lineendings`

```bash
opskit file lineendings PATH... [OPTIONS]
```

Streams each file counting CRLF/LF/lone-CR occurrences (bounded memory regardless of file
size) and flags mixed-style files.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file lineendings script.sh
opskit file lineendings *.sh --json
```

### `opskit file encoding`

```bash
opskit file encoding PATH... [OPTIONS]
```

Detects each file's text encoding: a BOM check first (UTF-8/UTF-16-LE/UTF-16-BE/UTF-32),
falling back to `charset-normalizer` with a confidence score; flags invalid byte sequences.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file encoding legacy.txt
opskit file encoding *.txt --json
```

### `opskit file identify`

```bash
opskit file identify PATH... [OPTIONS]
```

Content-sniffed type (binary magic bytes first — PNG/GIF/PDF/ZIP-family/gzip/ELF and similar
— then structural sniffing for JSON/XML/YAML/TOML) vs. the file's extension, visually flagging
a mismatch. Falls back to `"undetermined"` for files too short/ambiguous to classify.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file identify downloaded_file
opskit file identify *.bin --json
```

### `opskit file hash`

```bash
opskit file hash PATH... [OPTIONS]
```

Streaming checksum of each file (bounded memory regardless of file size).

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `--algo` | `sha256`\|`sha1`\|`md5` | `sha256` |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file hash artifact.tar.gz
opskit file hash *.tar.gz --algo sha256 --jsonl
```

### `opskit file diff`

```bash
opskit file diff LEFT RIGHT [OPTIONS]
```

Structural (semantic) comparison of two JSON/YAML files: reports equivalence when only
formatting/key-order differs, and pinpoints the differing key path(s) and value on each side
when data actually differs (a hand-rolled recursive walk, not a line-based text diff — the
whole point is to ignore reformatting noise). Exactly two positional paths — not batchable;
two identical paths compare as equivalent, not an error.

| Option | Description | Default |
|---|---|---|
| `LEFT`, `RIGHT` | The two files to compare | — |
| `--format` | Force `json`\|`yaml` instead of auto-detecting; applies to both sides | auto-detect |
| `--json` | Versioned JSON envelope (no `--jsonl` — single result) | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file diff config.old.yaml config.new.yaml
opskit file diff a.json b.json --json
```

### `opskit file duplicates`

```bash
opskit file duplicates DIRECTORY [OPTIONS]
```

Groups files under `DIRECTORY` with byte-identical content (grouped by size first, then by
streaming SHA-256 content hash within each size group). Symlinks are never followed. A single
directory root — not batchable; an empty directory or one with no duplicates reports no
groups, not an error.

| Option | Description | Default |
|---|---|---|
| `DIRECTORY` | The directory to search | — |
| `--recursive` | Descend into subdirectories | off (top-level only) |
| `--json` | Versioned JSON envelope (no `--jsonl` — grouped result) | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file duplicates ./vendor
opskit file duplicates ./vendor --recursive --json
```

### `opskit file stat`

```bash
opskit file stat PATH... [OPTIONS]
```

Cross-platform-normalized metadata: size, POSIX permissions/owner where determinable,
last-modified time, and symlink target (if applicable) — a portable replacement for
`stat`/`Get-Item`. `permissions`/`owner` are `null`/`—` on Windows (POSIX mode bits and
numeric-uid ownership don't apply there), not fabricated.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit file stat config.json
opskit file stat *.json --json
```

## Guarded write commands

Per constitution Art. X: named-target only, non-destructive by default (stdout or an explicit
`--output` path), atomic + non-clobbering `--in-place` opt-in only, and file content is only
ever parsed/transformed as data — never executed. All four share this write-destination
contract:

| Option | Description | Default |
|---|---|---|
| `--output` | Write the result to this new file instead of stdout | — |
| `--in-place` | Overwrite the source file atomically; mutually exclusive with `--output` | off |
| `--backup` | Only with `--in-place`: write `<path>.bak` before replacing | off |
| `--force` | Only with `--in-place --backup`: overwrite an existing `.bak` | off |
| `--json` | Versioned JSON envelope | off |
| `--no-color` | Disable colored output | off |

Without `--in-place`, the source file is never opened for writing — only read.

### `opskit file convert`

```bash
opskit file convert PATH --to FORMAT [OPTIONS]
```

Converts a JSON/YAML/TOML/XML file's content to another of those formats, per the [documented
XML ↔ dict mapping](#the-xml--dict-mapping). `--to` must differ from the detected/declared
source format. When a conversion can't preserve the source data exactly (e.g. XML mixed
content), the command still succeeds but reports `lossless: false` with a reason — never
silently.

| Option | Description | Default |
|---|---|---|
| `PATH` | File to convert | — |
| `--to` | Target format: `json`\|`yaml`\|`toml`\|`xml` *(required)* | — |
| `--format` | Force the source format instead of auto-detecting | auto-detect |
| *(write options above)* | | |

```bash
opskit file convert config.json --to yaml > config.yaml
opskit file convert config.json --to yaml --output config.yaml
opskit file convert config.json --to yaml --in-place --backup
```

### `opskit file pretty`

```bash
opskit file pretty PATH [OPTIONS]
```

Reformats a JSON/YAML/XML file's layout (indentation, optional key sorting) without changing
its data — never its format or its line endings, unlike `convert`/`eol`. TOML is rejected
(its layout is already canonical).

| Option | Description | Default |
|---|---|---|
| `PATH` | File to reformat | — |
| `--format` | Force `json`\|`yaml`\|`xml` instead of auto-detecting | auto-detect |
| `--indent` | Indent width | `2` |
| `--sort-keys` | Sort mapping keys | off |
| *(write options above)* | | |

```bash
opskit file pretty data.json --sort-keys --indent 4
opskit file pretty data.json --in-place --backup
```

### `opskit file eol`

```bash
opskit file eol PATH... --to STYLE [OPTIONS]
```

Normalizes line endings to a target style — a portable `dos2unix`/`unix2dos`. Batchable (each
file converts independently); `--output` is only valid with a single target.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more file paths (repeatable), and/or `--input-file` | — |
| `--to` | Target style: `lf`\|`crlf`\|`cr` *(required)* | — |
| `-i, --input-file` | File of paths, one per line; `-` reads stdin | — |
| *(write options above; `--jsonl` also available)* | | |

```bash
opskit file eol script.sh --to lf > fixed.sh
opskit file eol script.sh --to lf --in-place --backup
opskit file eol *.sh --to lf --in-place --jsonl
```

### `opskit file reencode`

```bash
opskit file reencode PATH --to ENCODING [OPTIONS]
```

Transcodes a text file from one encoding to another. `--from` overrides auto-detection
(BOM-first, then `charset-normalizer`); a character the target encoding can't represent is
reported as an actionable error, not silently mangled.

| Option | Description | Default |
|---|---|---|
| `PATH` | File to transcode | — |
| `--to` | Target encoding name *(required)* | — |
| `--from` | Source encoding, overriding auto-detection | auto-detect |
| *(write options above)* | | |

```bash
opskit file reencode legacy.txt --from iso-8859-1 --to utf-8 --output legacy.utf8.txt
opskit file reencode legacy.txt --to utf-8 --in-place --backup
```

## The XML ↔ dict mapping

`convert`/`pretty`/`validate`/`diff` treat XML through one fixed, documented mapping onto the
same dict/list shape JSON and YAML use, so a file can round-trip between all four structured
formats:

- An element's child elements become dict keys; repeated child tags become a list.
- Attributes are collected under a reserved `"@attributes"` key (a dict of attribute name → value).
- Direct text content is stored under a reserved `"#text"` key, present only when the element
  has non-whitespace text *alongside* attributes/children — an element with only text and no
  attributes/children collapses to that text value directly.
- Namespaces are preserved verbatim in tag/attribute names using Clark notation (`{uri}local`)
  rather than resolved against declared prefixes.

Mixed content (text interleaved with child elements at arbitrary positions) and multiple
namespace prefixes mapping to the same URI are the two shapes this convention can't losslessly
round-trip — flagged via `lossless: false` and a `lossy_reason`, per FR-018, rather than
silently reformatted. XML is parsed via `defusedxml` (no XXE surface); output is built with
the stdlib `ElementTree` writer, which has no parsing-of-untrusted-input surface.

## Output & exit codes

- **Human** (default): colorized, auto-plain when piped; honors `NO_COLOR` and `--no-color`.
- **`--json`**: a stable, versioned envelope (`schema_version`, `command`, `query`, `result`,
  `error`, `elapsed_ms`).
- **`--jsonl`**: one envelope per line for every batchable command.

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

Two exit codes are new to this category; every other outcome reuses an existing class:

| Code | Meaning | Applies to |
|---|---|---|
| `0` | success | all |
| `1` | generic error | all |
| `2` | usage error (bad flag combination, e.g. `--output` with `--in-place`, or `--to` equal to the source format) — before any file I/O | all |
| `7` | PARTIAL — batch with mixed outcomes | batchable commands |
| `15` | permission denied — reading the source, or writing (`--in-place`/`--output`/`--backup`) | all |
| `16` | not found — a named path does not exist, or is a directory where a file is required | all |
| **`21`** | **NEW** — invalid content: source fails to parse for its (detected/declared) format, or a binary file was given to a text-oriented command | `validate`, `convert`, `pretty`, `diff`, `reencode` |
| **`22`** | **NEW** — clobber refused: an in-place write would overwrite a pre-existing, unrelated file (typically an existing `.bak`) without `--force` | any write command with `--backup` |

Batch rule (constitution Art. IX) for `validate`/`lineendings`/`encoding`/`identify`/`hash`/
`stat`/`eol`: every target is processed; exit `0` only if every target succeeded, the shared
outcome's code if uniform, else `7` (PARTIAL). Failures always appear in `--json`/`--jsonl`
output (`result: null` + populated `error`).

## Use as a Python library

```python
from opskit.file import (
    validate, eol, convert, InvalidContent, ClobberRefused, LineEnding, StructuredFormat,
)

for path in ("config.json", "config.yaml"):
    result = validate(path)
    status = "OK" if result.valid else f"INVALID @ {result.error_line}:{result.error_column}"
    print(result.path, result.format, status)

eol_outcome = eol("script.sh", to=LineEnding.LF, in_place=True, backup=True)
print(eol_outcome.result.backup_path, eol_outcome.result.lossless)

try:
    convert("config.json", to=StructuredFormat.YAML, output="config.yaml")
except InvalidContent as exc:
    print(exc.message, "—", exc.hint)

try:
    convert("config.json", to=StructuredFormat.YAML, in_place=True, backup=True)
except ClobberRefused as exc:
    print(exc.message, "—", exc.hint)  # e.g. "pass --force to overwrite it"
```

Every function in `opskit.file` takes a **single** target path and raises directly on failure
(`FileNotFoundOnDisk`, `FilePermissionDenied`, `InvalidContent`, `ClobberRefused`) — matching
`opskit.storage.dir_size`/`opskit.net.probe` elsewhere in the library. `validate()` is the one
documented exception: a syntax error, or an undetermined format, is a *returned*
`valid=False` result, not a raised error, since detecting that is the function's whole
purpose. Batching over multiple targets (what the CLI's `PATH...`/`--input-file`/stdin do) is
entirely the CLI layer's job — call the single-target function once per target yourself.

Every guarded write function (`convert`, `eol`, `reencode`, `pretty`) returns a `WriteOutcome`
— `(result: ConversionResult, stdout_content: bytes | None)`. `stdout_content` is populated
only when neither `output=` nor `in_place=True` was passed; the library itself never prints
it — printing (or writing it further) is the caller's job. `ConversionResult.lossless=False`
is a **successful** outcome with a caveat, not an error — inspect it rather than assuming any
returned result is fully faithful to the source. `StatResult.permissions`/`.owner` being
`None` on Windows is part of the documented contract, not a bug.

See [`contracts/python-api.md`](../../../specs/007-file-operations/contracts/python-api.md)
for the full signature reference.
