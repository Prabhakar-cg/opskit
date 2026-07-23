# Phase 1 Data Model: Storage Diagnostics

Conceptual model for the `storage` category. Concrete types are frozen stdlib `@dataclass`es in
`src/opskit/storage/models.py` with `to_dict()` for the JSON envelope. No persistence.

## Enumerations

- **ExitCode**: no new members — reuses `USAGE=2`, `PERMISSION_DENIED=15`, `NOT_FOUND=16`,
  `PARTIAL=7`, `OK=0` (research R6).

## Entities

### Volume *(`storage/models.py`, returned by `list_volumes()`)*
| Field | Type | Notes |
|-------|------|-------|
| `mountpoint` | `str` | mount point (POSIX) or drive letter root (`C:\\`) |
| `device` | `str` | device/source string as reported by the platform |
| `fstype` | `str` | e.g. `ext4`, `apfs`, `ntfs` |
| `total_bytes` / `used_bytes` / `free_bytes` | `int` | from `psutil.disk_usage` (research R1) |
| `percent_used` | `float` | 0–100 |
| `is_network` | `bool` | best-effort classification (research R3) |

**Exclusion rule**: pseudo/virtual filesystems (research R4) never appear in `list_volumes()`
output (FR-002).

### Disk *(`storage/models.py`, returned by `list_disks()`)*
| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | platform disk identifier (`sda`, `PhysicalDrive0`, `disk0`) |
| `size_bytes` | `int \| None` | `None` = unavailable on this platform/disk (FR-006) |
| `model` | `str \| None` | `None` = unavailable |
| `removable` | `bool \| None` | `None` = unavailable (macOS in v1; research R2) |
| `partitions` | `tuple[Partition, ...]` | nested — see research R7 |

### Partition *(nested under `Disk.partitions`)*
| Field | Type | Notes |
|-------|------|-------|
| `device` | `str` | |
| `size_bytes` | `int \| None` | `None` when not determinable (e.g. unmounted, non-Linux) |
| `mounted` | `bool` | |
| `mountpoint` | `str \| None` | links to the matching `Volume.mountpoint` when mounted |
| `fstype` | `str \| None` | `None` when unmounted and undeterminable |

**Fidelity note**: on Windows/macOS v1, `Disk` and `Partition` are one-to-one per mounted volume
(research R2) — a `Disk` entry's `partitions` tuple has exactly one member there. Linux reflects
true multi-partition disks; a Linux disk with **no** OS partition table but a direct whole-disk
mount (common on cloud/VM disks) synthesizes a single `Partition` representing that mount rather
than showing an empty `partitions` tuple for a disk that's actually in use (research R2, as-built
addendum).

### InaccessiblePath *(one skipped subdirectory during a size scan)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | the subdirectory that could not be listed |
| `reason` | `str` | human-readable OS error summary (e.g. "permission denied") |

### ChildDirSize *(one entry in a depth-limited breakdown)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | the child directory's path |
| `depth` | `int` | 1..N, levels below the scanned root (research R7 / FR-008) |
| `size_bytes` | `int` | total size of everything under this child (lower bound if `incomplete`) |
| `incomplete` | `bool` | `True` if any subtree under *this* child hit an inaccessible path |

### DirSizeResult *(returned by `dir_size()`, one per requested path)*
| Field | Type | Notes |
|-------|------|-------|
| `path` | `str` | the requested (normalized) path |
| `total_bytes` | `int` | recursive total under `path` (lower bound if `incomplete`) |
| `file_count` / `dir_count` | `int` | entries actually counted |
| `include_hidden` | `bool` | echoes the effective flag used (FR-009) — default `False` |
| `depth_requested` | `int` | 0 = totals only, no breakdown (FR-008) |
| `breakdown` | `tuple[ChildDirSize, ...]` | empty when `depth_requested == 0` |
| `inaccessible` | `tuple[InaccessiblePath, ...]` | empty on a fully complete scan |
| `.incomplete` | property | `bool(inaccessible)` |

**Symlink rule**: symbolic links, junctions, and reparse points are never followed (FR-010);
their own directory-entry size (not their target's) is not descended into, and this is stated in
the report, not silently absorbed into totals.

## Error hierarchy (additive)

```
OpskitError (exit ERROR=1)
├── UsageError (exit USAGE=2)                          [existing — reused: bad depth/path syntax]
└── StorageError                                        [new — opskit/storage/errors.py]
    ├── PathNotFound       (exit NOT_FOUND=16)          # reused class — path missing/not a dir
    └── PathPermissionDenied (exit PERMISSION_DENIED=15) # reused class — top-level path unlistable
```

Each type owns its `exit_code` (constitution Art. VII); `core` receives **no changes at all**
(research R6 — zero new `ExitCode` members). Note the raise/return split, mirroring `tls`: a path
that cannot be scanned **at all** raises (`PathNotFound`, `PathPermissionDenied`); a path that
scans with some inaccessible *subdirectories* **returns** a `DirSizeResult` with `incomplete=True`
so the partial total remains inspectable (spec edge case: "annotated as a lower bound rather than
silently presented as complete"). The CLI maps a returned `incomplete=True` result to the
`PARTIAL=7` exit class same as a raised failure elsewhere in a batch.

## JSON envelope shapes

### `command: "storage.volumes"` / `"storage.disks"`
No target — `query` is `{}` (or reserved for future filter flags). `result` is a **list**:
`{"volumes": [Volume.to_dict(), ...]}` / `{"disks": [Disk.to_dict(), ...]}`. `error` is always
`null` for these two (nothing to fail per-target; a total enumeration failure is a bug, not a
modeled outcome).

### `command: "storage.size"` (one envelope per requested path in `--jsonl`, per batch contract)
`query` = `{"path": ..., "depth": ..., "include_hidden": ...}`; `result` =
`DirSizeResult.to_dict()` on success (including when `incomplete: true`) or `null` when the path
itself raised (`PathNotFound`/`PathPermissionDenied`); `error` populated in the raised case only.
Aggregate exit code follows the standard batch rule (research R6): `0` only if every path is
fully complete and found, `PARTIAL=7` if any path is missing/denied/incomplete while at least one
other succeeds (or a single path is merely incomplete), the uniform failure class if every path
shares one raised failure type, `PERMISSION_DENIED`/`NOT_FOUND` if that's the single uniform
outcome across all paths.
