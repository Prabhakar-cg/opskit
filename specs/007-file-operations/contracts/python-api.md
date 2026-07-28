# Contract: Python API — `opskit.file`

API-first (constitution Art. VII): the CLI is a client of this. The library raises typed
exceptions, never prints or exits, holds no global state, ships `py.typed`. Signatures are
illustrative; they define the SemVer-governed public contract.

**As-built note**: every function below takes a **single** target path, not `*paths` —
matching the `opskit.storage.dir_size` / `opskit.net.probe` precedent elsewhere in the
codebase. Batching over multiple targets (arguments, `--input-file`, stdin) is entirely the
CLI layer's job (`opskit.core.cliutils.collect_outcomes`), which keeps this module free of
batch/output concerns and lets every diagnostic function have one uniform raise-on-failure
shape. This deviates from the feature's original plan (which specified `*paths` varargs
returning a tuple); recorded here per the `storage` feature's as-built-deviation precedent.

## Public surface — `opskit.file.__all__`

```python
from opskit.file import (
    lineendings, encoding, validate, identify, hash_files,   # diagnostics
    diff, stat_files, find_duplicates,                        # diagnostics
    convert, eol, reencode, pretty,                           # guarded write commands
    StructuredFormat, LineEnding,                              # enums
    LineEndingReport, EncodingReport, ValidationResult,        # models
    IdentificationResult, ChecksumResult, DiffEntry,           # models
    StructuralDiffResult, StatResult, DuplicateGroup,          # models
    ConversionResult,                                          # models
    FileError, FileNotFoundOnDisk, FilePermissionDenied,       # errors
    InvalidContent, ClobberRefused,                            # errors
)
```

## Read-only diagnostic functions

```python
def lineendings(path: str | Path) -> LineEndingReport:
    """CRLF/LF/lone-CR counts and mixed-style detection for `path` (FR-001).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: `path` could not be read.
    """

def encoding(path: str | Path) -> EncodingReport:
    """Detected encoding, BOM presence, and invalid-sequence flag for `path` (FR-002)."""

def validate(
    path: str | Path,
    *,
    format: StructuredFormat | None = None,  # None = auto-detect
) -> ValidationResult:
    """Syntax-check `path` as JSON/YAML/TOML/XML (FR-003).

    A syntax error, or a format that can't be determined at all, is reported as
    `valid=False` — never raised, since that's precisely what this function detects.
    """

def identify(path: str | Path) -> IdentificationResult:
    """Content-sniffed type vs. extension for `path` (FR-004)."""

def hash_files(path: str | Path, *, algo: str = "sha256") -> ChecksumResult:
    """Checksum of `path` using `algo` (FR-005)."""

def diff(
    left: str | Path,
    right: str | Path,
    *,
    format: StructuredFormat | None = None,  # None = auto-detect; applies to both sides
) -> StructuralDiffResult:
    """Structural (semantic) comparison of two JSON/YAML files (FR-006).

    Raises:
        FileNotFoundOnDisk: either path does not exist, or is not a file.
        InvalidContent: either file fails to parse as the (detected/declared) format.
    """

def stat_files(path: str | Path) -> StatResult:
    """Cross-platform-normalized metadata for `path` (FR-007)."""

def find_duplicates(
    directory: str | Path,
    *,
    recursive: bool = False,
) -> tuple[DuplicateGroup, ...]:
    """Groups of files under `directory` sharing identical content (FR-008).

    Raises:
        FileNotFoundOnDisk: `directory` does not exist, or is not a directory.
        FilePermissionDenied: `directory` itself cannot be listed.
    """
```

## Guarded write functions

Every function below shares the write-safety parameters and guarantees in
[data-model.md](../data-model.md#write-safety-invariants) (research R7), and returns a
`WriteOutcome` — a `NamedTuple` of `(result: ConversionResult, stdout_content: bytes | None)`.
`stdout_content` is populated only when neither `output` nor `in_place` was requested; the
library itself never prints it (Art. III) — the CLI does, on the caller's behalf:

```python
class WriteOutcome(NamedTuple):
    result: ConversionResult
    stdout_content: bytes | None

def convert(
    path: str | Path,
    *,
    to: StructuredFormat,
    format: StructuredFormat | None = None,  # None = auto-detect source format
    output: str | Path | None = None,        # write here instead of stdout
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Convert a JSON/YAML/TOML/XML file to another of those formats (FR-010).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source failed.
        InvalidContent: the source fails to parse as its (detected/declared) format.
        ClobberRefused: `--backup` was requested but `<path>.bak` already exists (and
            `force` is False).
        UsageError: `to` equals the detected/declared source format — raised before any
            file I/O when `format` is passed explicitly (matching the CLI's "usage errors
            before any file I/O" contract); when `format` is `None`, the source format can
            only be known by reading and parsing the file first, so the check happens after
            that unavoidable read in the auto-detect case.
    """

def eol(
    path: str | Path,
    *,
    to: LineEnding,
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Normalize line endings in `path` to `to` (FR-011)."""

def reencode(
    path: str | Path,
    *,
    to: str,
    from_: str | None = None,  # None = auto-detect source encoding
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Transcode a text file from one encoding to another (FR-012).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source failed.
        InvalidContent: the source cannot be decoded using the source encoding, or
            contains a character the target encoding cannot represent.
        ClobberRefused: as above.
    """

def pretty(
    path: str | Path,
    *,
    format: StructuredFormat | None = None,  # None = auto-detect; TOML unsupported (already canonical)
    indent: int = 2,
    sort_keys: bool = False,
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> WriteOutcome:
    """Reformat a JSON/YAML/XML file's layout without changing its data (FR-013)."""
```

## Raise/return split

Every function documented above takes a single target and raises directly on failure
(`FileNotFoundOnDisk`, `FilePermissionDenied`, `InvalidContent`, `ClobberRefused`,
`UsageError`) — like `dir_size`/`probe` elsewhere in opskit. `validate`'s "is this file
syntactically valid" question is the one documented exception: a syntax error, or an
undetermined format, is a *returned* `valid=False` result, not a raised error, since
detecting that is the function's whole purpose.

The batch behavior described by FR-020 (process every target; a failure on one doesn't abort
the rest; a per-target `result=None` + typed `error` in `--json`/`--jsonl`) is implemented
once, in the CLI layer (`opskit.core.cliutils.collect_outcomes`), by calling each of these
single-target functions once per target — it is not a property of the library functions
themselves.

## Usage example (documented in `file/README.md`; must run as written — SC-006)

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
    print(exc.message, "—", exc.hint)  # e.g. "pass --force to overwrite config.json.bak"
```

## Compatibility rules

- Two new `ExitCode` members (`INVALID_CONTENT=21`, `CLOBBER_REFUSED=22`, research R9) plus one
  new additive package → **MINOR** release.
- `*Result.to_dict()` matches the CLI envelope's `result` object exactly for every model in
  [data-model.md](../data-model.md).
- `ConversionResult.lossless=False` is a **successful** outcome with a caveat, not an error —
  callers must inspect it rather than assume any returned `ConversionResult` is fully faithful to
  the source (FR-018).
- `StatResult.permissions`/`.owner` being `None` on Windows is part of the documented contract
  (FR-022), not a bug — callers MUST handle `None` for those two fields.
