# `opskit storage` — storage diagnostics

Read-only disk/storage inspection that behaves **identically on Windows, macOS, and Linux** —
a single replacement for `df`/`lsblk`/`du` on POSIX and `diskpart`/`wmic`/Explorer's "folder
size" on Windows. Available both as CLI commands and as an importable Python API.

> Part of [**opskit**](../../../README.md). See the root README for install and project-wide docs.

---

## Contents

- [Quick start](#quick-start)
- [`opskit storage volumes`](#opskit-storage-volumes)
- [`opskit storage disks`](#opskit-storage-disks)
- [`opskit storage size`](#opskit-storage-size)
- [Cross-platform fidelity](#cross-platform-fidelity)
- [Output & exit codes](#output--exit-codes)
- [Use as a Python library](#use-as-a-python-library)

---

## Quick start

```bash
opskit storage volumes                          # every mounted volume, utilization + fstype
opskit storage disks                             # physical disks, nested partitions
opskit storage size /var/log                     # recursive total for a directory
opskit storage size /var/log --depth 2           # + a depth-limited child breakdown
opskit storage size /var/log --include-hidden    # include dotfiles/hidden entries
```

## `opskit storage volumes`

```bash
opskit storage volumes [OPTIONS]
```

Lists every mounted, non-pseudo volume: mount point, filesystem type, total/used/free
capacity, percent-used, and whether it's local or network-mounted. Pseudo/virtual
filesystems (`tmpfs`, `proc`, `sysfs`, …) are excluded.

| Option | Description | Default |
|---|---|---|
| `--json` / `--jsonl` | Versioned JSON envelope / one JSON object per volume | off |
| `--no-color` | Disable colored output (`NO_COLOR` honored too) | off |

## `opskit storage disks`

```bash
opskit storage disks [OPTIONS]
```

Lists physical/logical disks, each with its nested partitions (device, size, mounted?, mount
point, filesystem type). See [Cross-platform fidelity](#cross-platform-fidelity) — some
fields are best-effort depending on the OS and are `null`/`—` rather than fabricated when
unavailable.

| Option | Description | Default |
|---|---|---|
| `--json` / `--jsonl` | Versioned JSON envelope / one JSON object per disk | off |
| `--no-color` | Disable colored output | off |

## `opskit storage size`

```bash
opskit storage size PATH... [OPTIONS]
```

Recursive size of one or more directories, with an optional depth-limited child-directory
breakdown. A permission-denied subdirectory is skipped and listed, not fatal — the total
becomes a stated lower bound rather than silently wrong.

| Option | Description | Default |
|---|---|---|
| `PATH` | One or more directory paths (repeatable), and/or `--input-file` | — |
| `--depth` | Child-directory breakdown levels below each path (`0` = total only) | `0` |
| `--include-hidden` | Include hidden files/directories (dotfiles / Windows hidden attribute) | off (excluded) |
| `-i, --input-file` | File of paths, one per line (`#` comments allowed); `-` reads stdin | — |
| `--json` / `--jsonl` | Versioned JSON envelope / NDJSON per path | off |
| `--no-color` | Disable colored output | off |

```bash
opskit storage size /data /var/log /tmp --jsonl | jq .
opskit storage size -i paths.txt --depth 1
```

Every path is processed (one failure never aborts the batch); the exit code is `0` only if
every path is found *and* fully complete, the class code if all outcomes share one class,
else `7` (PARTIAL) — including a single path that succeeded but hit an inaccessible
subdirectory, so an unattended script can tell a verified total from a lower bound without
parsing text.

## Cross-platform fidelity

Volume enumeration and utilization (`volumes`, and the mount-linkage half of `disks`) are
fully reliable on every platform. Physical disk hardware detail is genuinely uneven by
design, not a bug to fix later:

| Field | Linux | Windows | macOS |
|---|---|---|---|
| Volume mount point, fstype, total/used/free/%used | full | full | full |
| Disk size | full (`/sys/block`) | approximated from the volume's own capacity | approximated from the volume's own capacity |
| Disk model | full where exposed | unavailable (`null`) | unavailable (`null`) |
| Disk removable | full | full (decoded from the OS drive type) | unavailable (`null`) |
| Disk↔partition grouping | full (multi-partition disks) | one disk per volume | one disk per volume |

True physical-disk hardware identity and multi-partition grouping need Windows WMI or macOS
IOKit, both deliberately deferred (see `specs/006-storage-diagnostics/research.md` R2) rather
than hand-rolled with the fragility that entails. Every unavailable field is explicit
(`null` in JSON, `—` in the human table) — never fabricated or silently dropped.

## Output & exit codes

- **Human** (default): colorized tables, auto-plain when piped; honors `NO_COLOR` and `--no-color`.
- **`--json`**: a stable, versioned envelope (`schema_version`, `command`, `query`, `result`,
  `error`, `elapsed_ms`).
- **`--jsonl`**: one JSON object per row for `volumes`/`disks`; one envelope per line for
  batched `size` invocations.

```json
{
  "schema_version": "1",
  "command": "storage.size",
  "query": { "path": "/var/log", "depth": 1, "include_hidden": false },
  "result": {
    "path": "/var/log", "total_bytes": 1048576000, "file_count": 4210, "dir_count": 18,
    "include_hidden": false, "depth_requested": 1,
    "breakdown": [{ "path": "/var/log/journal", "depth": 1, "size_bytes": 900000000, "incomplete": false }],
    "inaccessible": [], "incomplete": false
  },
  "error": null,
  "elapsed_ms": 340.1
}
```

No new exit codes were introduced for this category — every outcome reuses an existing class:

| Code | Meaning | Applies to |
|---|---|---|
| `0` | success | all |
| `1` | generic error | all |
| `2` | usage error (e.g. negative `--depth`) — before any filesystem I/O | `size` |
| `7` | PARTIAL — batch with mixed outcomes, or a single path that completed but is incomplete | `size` |
| `15` | permission denied — the requested top-level path itself couldn't be listed at all | `size` |
| `16` | not found — path doesn't exist, or isn't a directory | `size` |

`volumes`/`disks` have no per-target failure mode (a single query, not a batch of targets) —
they exit `0` unless a genuinely unexpected error occurs (exit `1`).

## Use as a Python library

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

`dir_size()` **returns** a result even when nested subdirectories were inaccessible
(`result.incomplete`/`result.inaccessible` name them exactly); it **raises**
`PathNotFound`/`PathPermissionDenied` only when the requested path itself can't be measured
at all. `list_volumes()`/`list_disks()` never raise for a single unreadable mount or
disk — its determinable fields are populated and the rest left `None`.
