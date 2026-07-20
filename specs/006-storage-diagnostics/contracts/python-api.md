# Contract: Python API — `opskit.storage`

API-first (constitution Art. VII): the CLI is a client of this. The library raises typed
exceptions, never prints or exits, holds no global state, ships `py.typed`. Signatures are
illustrative; they define the SemVer-governed public contract.

## Public surface — `opskit.storage.__all__`

```python
from opskit.storage import (
    list_volumes, list_disks, dir_size,              # convenience functions
    Volume, Disk, Partition,                          # models
    DirSizeResult, ChildDirSize, InaccessiblePath,     # models
    StorageError, PathNotFound, PathPermissionDenied,  # errors
)
```

## Convenience functions

```python
def list_volumes() -> tuple[Volume, ...]:
    """Every mounted, non-pseudo volume visible to the current user (FR-001, FR-002, FR-003)."""

def list_disks() -> tuple[Disk, ...]:
    """Every physical/logical disk, each with its nested partitions (FR-004, FR-005, FR-006)."""

def dir_size(
    path: str | Path,
    *,
    depth: int = 0,               # 0 = total only; N = breakdown N levels below `path`
    include_hidden: bool = False, # FR-009 default: hidden entries excluded
) -> DirSizeResult:
    """Recursive size of `path`, optionally with a depth-limited child breakdown.

    Raises:
        PathNotFound: `path` does not exist, or is not a directory.
        PathPermissionDenied: `path` itself cannot be listed at all.

    Returns a `DirSizeResult` even when nested subdirectories were inaccessible —
    `result.incomplete` and `result.inaccessible` report exactly which ones (FR-011),
    matching `tls.check`'s raise/return split for reportable-but-degraded outcomes.
    """
```

**Raise/return split** (per [data-model.md](../data-model.md)): failures that preclude any report
**raise** (`PathNotFound`, `PathPermissionDenied`); a scan that completes with some inaccessible
subdirectories **returns** a `DirSizeResult` with `incomplete=True` — the partial total and the
list of skipped paths are both inspectable, never silently swallowed (spec FR-011).

`list_volumes()`/`list_disks()` do not raise for individual entries — a single unreadable mount or
disk is represented with its determinable fields populated and the rest `None`/best-effort
(research R2), never dropped from the returned tuple.

## Usage example (documented in `storage/README.md`; must run as written — SC-005)

```python
from opskit.storage import list_volumes, list_disks, dir_size, PathNotFound

for vol in list_volumes():
    print(vol.mountpoint, vol.fstype, f"{vol.percent_used:.1f}%")

for disk in list_disks():
    print(disk.id, disk.size_bytes, disk.model or "(model unavailable)")
    for part in disk.partitions:
        print(" ", part.device, part.mountpoint or "(not mounted)")

result = dir_size("/var/log", depth=1)
print(result.total_bytes, "incomplete:", result.incomplete)
for child in result.breakdown:
    print(" ", child.path, child.size_bytes)

try:
    dir_size("/no/such/path")
except PathNotFound as exc:
    print(exc.message, "—", exc.hint)
```

## Compatibility rules

- No new `ExitCode` members (research R6) — the shared enum is untouched, so no core release
  implications beyond the additive `opskit.storage` package itself → **MINOR** release.
- `Volume.to_dict()`/`Disk.to_dict()`/`DirSizeResult.to_dict()` match the CLI envelope's `result`
  objects exactly (per [data-model.md](../data-model.md)).
- Fields that are `None`/best-effort on a given platform (research R2) are part of the documented
  contract, not a bug — callers MUST handle `None` for `Disk.size_bytes`/`model`/`removable` and
  `Partition.size_bytes`/`fstype`.
