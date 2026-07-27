# Contract: Python API — `opskit.file`

API-first (constitution Art. VII): the CLI is a client of this. The library raises typed
exceptions, never prints or exits, holds no global state, ships `py.typed`. Signatures are
illustrative; they define the SemVer-governed public contract.

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
def lineendings(*paths: str | Path) -> tuple[LineEndingReport, ...]:
    """CRLF/LF/lone-CR counts and mixed-style detection for each path (FR-001).

    Never raises for an individual bad path — see "Raise/return split" below.
    """

def encoding(*paths: str | Path) -> tuple[EncodingReport, ...]:
    """Detected encoding, BOM presence, and invalid-sequence flag for each path (FR-002)."""

def validate(
    *paths: str | Path,
    format: StructuredFormat | None = None,  # None = auto-detect
) -> tuple[ValidationResult, ...]:
    """Syntax-check each path as JSON/YAML/TOML/XML (FR-003)."""

def identify(*paths: str | Path) -> tuple[IdentificationResult, ...]:
    """Content-sniffed type vs. extension for each path (FR-004)."""

def hash_files(
    *paths: str | Path,
    algo: str = "sha256",
) -> tuple[ChecksumResult, ...]:
    """Checksum of each path using `algo` (FR-005)."""

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

def stat_files(*paths: str | Path) -> tuple[StatResult, ...]:
    """Cross-platform-normalized metadata for each path (FR-007)."""

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
[data-model.md](../data-model.md#write-safety-invariants) (research R7):

```python
def convert(
    path: str | Path,
    *,
    to: StructuredFormat,
    format: StructuredFormat | None = None,  # None = auto-detect source format
    output: str | Path | None = None,        # write here instead of stdout
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> ConversionResult:
    """Convert a JSON/YAML/TOML/XML file to another of those formats (FR-010).

    Raises:
        FileNotFoundOnDisk / FilePermissionDenied: reading the source failed.
        InvalidContent: the source fails to parse as its (detected/declared) format.
        ClobberRefused: `--backup` was requested but `<path>.bak` already exists (and
            `force` is False).
    """

def eol(
    *paths: str | Path,
    to: LineEnding,
    output: str | Path | None = None,  # only valid for a single path
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> tuple[ConversionResult, ...]:
    """Normalize line endings across one or more files (FR-011). Never raises for an
    individual bad path when called with multiple paths — see "Raise/return split"."""

def reencode(
    path: str | Path,
    *,
    to: str,
    from_: str | None = None,  # None = auto-detect source encoding
    output: str | Path | None = None,
    in_place: bool = False,
    backup: bool = False,
    force: bool = False,
) -> ConversionResult:
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
) -> ConversionResult:
    """Reformat a JSON/YAML/XML file's layout without changing its data (FR-013)."""
```

## Raise/return split

Failures that preclude any report at all for a *single-target* call **raise**
(`FileNotFoundOnDisk`, `FilePermissionDenied`, `InvalidContent`, `ClobberRefused`). For the
*batchable* functions (`lineendings`, `encoding`, `validate`, `identify`, `hash_files`,
`stat_files`, `eol` called with multiple paths), a failure on one target does **not** raise and
does **not** stop the rest — it is represented as `result=None` alongside the corresponding typed
error for that target in the batch's own result shape (mirrored 1:1 by the CLI's per-target JSON
envelope, FR-020). `diff` and `find_duplicates` are single-target calls and raise directly, like
`dir_size` in `opskit.storage`.

## Usage example (documented in `file/README.md`; must run as written — SC-006)

```python
from opskit.file import validate, eol, convert, InvalidContent, ClobberRefused

for result in validate("config.json", "config.yaml"):
    status = "OK" if result.valid else f"INVALID @ {result.error_line}:{result.error_column}"
    print(result.path, result.format, status)

eol_result, = eol("script.sh", to="lf", in_place=True, backup=True)
print(eol_result.backup_path, eol_result.lossless)

try:
    convert("config.json", to="yaml", output="config.yaml")
except InvalidContent as exc:
    print(exc.message, "—", exc.hint)

try:
    convert("config.json", to="yaml", in_place=True, backup=True)
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
