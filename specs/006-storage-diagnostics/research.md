# Phase 0 Research: Storage Diagnostics

Decisions resolving every technical unknown in the plan's Technical Context. Format per speckit:
Decision / Rationale / Alternatives considered.

## R1. Cross-platform volume/partition/filesystem-type/utilization enumeration

**Decision**: Add **`psutil`** (`psutil>=6,<8`) as a new **base runtime dependency** (confirmed
with the requester — same tier as `pyopenssl`/`cryptography` for `tls`, not an extra). Use
`psutil.disk_partitions()` for mounted-volume enumeration (device, mountpoint, fstype, opts) and
`psutil.disk_usage(mountpoint)` for total/used/free/percent (FR-001).

**Rationale**:
- Unlike `tls` (where stdlib almost worked, just not before 3.13), there is **no stdlib API at
  all** for enumerating mounted volumes or filesystem types on any of the three platforms.
  `shutil.disk_usage()` gives capacity but only for a path you already know — it doesn't discover
  *what's mounted*.
- Hand-rolling this per OS means: Linux `/proc/mounts` parsing (genuinely easy, pure stdlib) +
  macOS `getfsstat64` via `ctypes` against the Darwin `statfs64` struct (real fragility — must
  track macOS's APFS firmlink/synthetic-volume model, e.g. the split system/data volumes since
  Catalina) + Windows `GetLogicalDrives`/`GetVolumeInformationW` via `ctypes` (well-trodden but
  still hand-rolled FFI). This is a materially larger, more fragile surface for opskit to own
  than the TLS category ever needed, for the category's flagship P1 story.
- `psutil` is BSD-licensed, one of the most widely deployed Python packages in existence, and
  actively maintained — it satisfies Art. IV (dependency freshness) and Art. III (security
  scanning) the same way `pyopenssl`/`cryptography`/`ldap3` already do. It is an in-process
  library call, not a subprocess/native-tool shell-out, so it does not violate Art. VI ("no
  shelling out to native OS tools" governs *opskit's own* implementation strategy, not whether a
  dependency itself is a compiled extension — `cryptography` and `ldap3` already aren't pure
  Python internally either).
- `psutil.disk_partitions()` already applies its own internal "real filesystem" filter; opskit
  additionally maintains its own explicit, tested pseudo-filesystem blocklist (R4) so the
  exclusion behavior in FR-002 is part of opskit's own contract, not an undocumented side effect
  of whichever `psutil` version happens to be installed.
- `psutil.disk_io_counters(perdisk=True)` further gives a cross-platform way to enumerate
  *physical* disk identifiers (e.g. `sda`, `PhysicalDrive0`, `disk0`) without any extra
  dependency, which seeds the physical-disk inventory in R2.

**Alternatives considered**:
- *Pure stdlib + hand-rolled `ctypes` per OS*: rejected as the higher-risk, higher-maintenance
  path (macOS `statfs64`/APFS complexity in particular) for no material benefit — psutil is
  already a dependency every mainstream ops/monitoring Python tool takes for granted, and the
  project already has precedent (`pyopenssl`, `cryptography`, `ldap3`) for adding a well-audited
  dependency when stdlib genuinely doesn't cover the need.
- *`psutil` as an opt-in extra (`opskit[storage]`), mirroring `ad`'s `ldap3` quarantine*:
  considered and explicitly rejected by the requester — storage's volume-utilization story is as
  core/expected as `tls`/`net`, not a narrower audience-specific category like `ad`, so it ships
  in the base install.
- *`shutil.disk_usage()` alone, with the user required to pass an already-known mount point*:
  rejected — it doesn't satisfy FR-001's "enumerate mounted volumes" requirement at all, only the
  capacity-of-a-known-path half of it.

**As built**: `psutil` ships no `py.typed` marker (unlike `pyopenssl`/`cryptography`), so
`types-psutil` (the community typeshed stub package) was added as a **dev-only** dependency for
`mypy --strict`/pyright — no runtime impact, and it kept `enumerate_.py` fully typed without the
`ldap3`-style `ignore_missing_imports` override.

## R2. Physical disk & partition inventory — scope and per-OS fidelity

**Decision**: Tiered, explicitly-labeled fidelity per platform (FR-004–FR-006 already require
graceful "unavailable" degradation, so this is a spec-conformant, not a compromised, design):

- **Linux**: full fidelity via `/sys/block/<dev>/size` (× 512 for bytes), `/sys/block/<dev>/removable`,
  and `/sys/block/<dev>/device/model` (present for real/virtual disks that expose it; absent on
  some cloud/virtual block devices — reported as unavailable per-field, not per-disk, when
  missing) — all plain pseudo-file reads, pure stdlib, no privilege required. Partitions are
  matched to their disk via the `/sys/block/<dev>/<dev><partN>` subdirectory relationship, then
  cross-referenced to `psutil.disk_partitions()` for mount status/fstype.
- **Windows**: one Disk-equivalent entry is derived per volume from `psutil.disk_partitions()`
  (Windows does not cleanly expose physical-disk→volume grouping without WMI). Fixed vs.
  removable vs. network **is** cheaply and reliably available: `psutil` encodes the result of the
  Win32 `GetDriveTypeW` call into each partition's `opts` string (e.g. `rw,fixed`, `rw,removable`,
  `rw,remote`, `rw,cdrom`) — no extra dependency or hand-rolled FFI needed. Model/serial and true
  multi-partition-per-physical-disk grouping are marked explicitly unavailable in v1.
- **macOS**: same one-entry-per-volume approach. Fixed/removable and model are marked explicitly
  unavailable in v1 (no cheap, reliable syscall-level signal is available without IOKit, which —
  like Windows WMI — is deferred; see below).

**Rationale**: true physical-disk hardware identity (model/serial) and physical→partition
grouping require Windows WMI (`Win32_DiskDrive`) or macOS IOKit — both meaningfully heavier,
platform-specific integrations (COM/CoreFoundation bridging) than the volume-utilization story
needs, and neither is exposed by `psutil`. Linux's `/sys/block` is uniquely cheap and reliable, so
it gets full fidelity; Windows/macOS degrade honestly per FR-006 rather than opskit guessing or
silently omitting fields. This keeps the category shippable without a second, riskier dependency
(`pywin32`/`wmi`) for a P2 story.

**Alternatives considered**:
- *`pywin32`/`wmi` extra for Windows physical-disk detail*: rejected for v1 — a second,
  Windows-only heavy dependency (COM automation) for a P2 inventory nice-to-have; revisit only if
  real user demand appears.
- *Shelling out to `diskutil`/`wmic`/`lsblk`*: rejected outright (Art. VI).
- *Refusing to report disks/partitions at all on Windows/macOS until full fidelity is available*:
  rejected — a degraded-but-honest report (size + fstype + mount linkage, fixed/removable on
  Windows) is more useful than nothing, and the spec's own acceptance scenario (US3, scenario 4)
  requires exactly this "mark unavailable, don't omit or fabricate" behavior.

**As built — whole-disk-mount fallback (discovered during manual smoke-testing)**: on Linux,
some disks (common on cloud/VM/container hosts, and confirmed against this project's own dev
container) have no OS-level partition table at all — the whole block device is mounted directly
(e.g. `/dev/sdd` mounted at `/`, not `/dev/sdd1`). `linux_block.partition_names()` correctly
reports zero partitions for such a disk, which would otherwise render a used, mounted disk with
an empty partitions table. `enumerate_._list_disks_linux()` now checks, only when a disk has no
sysfs partitions, whether the whole-disk device path itself appears in the mounted-partitions map
and, if so, synthesizes a single `Partition` entry (the disk's own device path, size, mountpoint,
fstype) representing that direct mount — never fabricated data, just surfacing the mount that
`psutil` already reported under the disk's own device name instead of a partition's. This wasn't
anticipated in the original R2 design (which assumed partitions are always the mount unit) and is
recorded here rather than silently left as an undocumented code path.

## R3. Network vs. local volume classification (FR-003)

**Decision**: Classify by filesystem-type name against a small in-tree allowlist of known network
filesystem types (`nfs`, `nfs4`, `cifs`, `smbfs`, `afpfs`, `fuse.sshfs`, and similar on POSIX) plus,
on Windows, `psutil`'s `opts` containing `remote` (from `GetDriveTypeW`'s `DRIVE_REMOTE`).
Anything not matched is treated as local.

**Rationale**: no platform exposes a single universal "is this mount local or remote" flag across
all three OSes; a filesystem-type/opts heuristic is the same category of approach every `df`-like
tool uses and covers the overwhelming majority of real-world cases. Documented as best-effort in
the same spirit as R2.

**Alternatives considered**: per-OS syscall flags — only Windows has one cheaply (`DRIVE_REMOTE`,
already covered); POSIX has no equivalent, so a heuristic is the only broadly portable option.

## R4. Pseudo/virtual filesystem exclusion (FR-002)

**Decision**: Maintain an explicit, unit-tested in-tree blocklist of fstype names (`tmpfs`,
`devtmpfs`, `proc`, `sysfs`, `cgroup`, `cgroup2`, `overlay`, `squashfs`, `autofs`, `rpc_pipefs`,
and similar Linux pseudo-filesystems) applied on top of whatever `psutil.disk_partitions()`
already returns, rather than relying solely on `psutil`'s internal `all=False` filtering.

**Rationale**: opskit's own exclusion contract (FR-002, tested by opskit's own test suite) should
not silently drift if a future `psutil` release changes its internal filtering. The list is small,
Linux-specific (Windows/macOS rarely surface pseudo-filesystems as `disk_partitions()` entries in
the first place), and independently testable.

**Alternatives considered**: trust `psutil`'s own `all=False` filtering completely — rejected as
an untested, version-coupled implicit dependency for a requirement (FR-002) that's part of
opskit's own documented contract.

## R5. Directory size scanning strategy

**Decision**: An iterative (explicit stack, not recursive function calls — avoids Python's
recursion limit on very deep trees) walk using `os.scandir()`, which yields `os.DirEntry` objects
with cached `stat`/`is_dir`/`is_symlink` for one syscall per entry instead of two. Symlinks,
junctions, and reparse points are detected via `DirEntry.is_symlink()` and never descended into
(FR-010). Hidden-entry detection: dotfile-name check (`name.startswith(".")`) on Linux/macOS;
`stat.FILE_ATTRIBUTE_HIDDEN` from `os.stat(..., follow_symlinks=False).st_file_attributes` (a
Windows-only attribute CPython's `os.stat` already exposes — no `ctypes` needed) on Windows.
Permission errors (`OSError`/`PermissionError`) raised while listing a subdirectory are caught
per-directory, recorded as an `InaccessiblePath`, and the walk continues into every other branch
(FR-011). The depth-limited breakdown (FR-008) is computed during the same single walk by
tracking each entry's depth from the root and aggregating child-directory totals bottom-up — no
separate pass per depth level.

**Rationale**: pure stdlib, identical code path on all three OSes (only the hidden-attribute check
branches, and that branch is one `os.name == "nt"` check using an already-stdlib-exposed field —
no new dependency or `ctypes`). `os.scandir()` over `os.walk()`/`pathlib.rglob()` because it gives
direct, cheap access to `is_symlink()`/`is_dir()` without a second `stat` call per entry and
because the depth-tracking and single-error-per-directory recovery are simpler to control
explicitly than folding them into `os.walk()`'s callback/`onerror` shape.

**Alternatives considered**: `os.walk(path, onerror=...)` — workable, but its `topdown`/`onerror`
callback shape makes per-branch depth tracking and the bottom-up child-total aggregation more
convoluted than an explicit stack; `pathlib.Path.rglob("*")` — simplest to read but slowest (no
cached `stat`) and offers no clean per-entry error isolation.

## R6. Exit-code allocation

**Decision**: **No new `ExitCode` members are needed.** Reuse existing classes exactly as their
semantics already match: `USAGE=2` for invalid input (negative `--depth`, malformed path
argument) before any filesystem I/O; `NOT_FOUND=16` (existing, from `ad`) for a path that doesn't
exist or isn't a directory; `PERMISSION_DENIED=15` (existing, from `ad`) when the *requested* path
itself cannot be listed at all; `PARTIAL=7` (existing batch-aggregate class) both for (a) a
multi-path `storage size` invocation where some paths failed entirely while others succeeded, and
(b) a single path that scanned successfully but hit permission errors on nested subdirectories
(the result is returned, not raised, with `incomplete: true` and the `inaccessible` list
populated — mirroring `tls`'s "certificate details returned even on failure" raise/return split).

**Rationale**: matches the project's established pattern (`tls`'s R5: "reuse existing classes
where semantics match") and keeps the documented exit-code enum from growing per category when it
doesn't need to. Making "partial/incomplete" a **non-zero** exit (rather than a silent `0`) is a
deliberate choice, same reasoning as `tls`'s `EXPIRING_SOON`: an unattended script trusting a
directory-size number should be able to tell, via exit code alone, whether that number is a
verified total or a lower bound.

**Alternatives considered**: minting `PATH_NOT_FOUND`/`PATH_PERMISSION_DENIED`/`SCAN_INCOMPLETE`
as new, storage-specific codes — rejected as unnecessary growth of the shared enum when existing
classes already carry the right semantics and scripts can already branch on them from other
categories.

## R7. CLI command surface

**Decision**: Three subcommands under `opskit storage`:
- `opskit storage volumes` — User Story 1 (P1): every mounted volume, no path argument, single
  query → multi-row report (mountpoint, fstype, total/used/free/percent, local/network).
- `opskit storage disks` — User Story 3 (P2): physical disks, each with its nested partitions
  (linked to the corresponding volume where mounted), best-effort fields per R2.
- `opskit storage size PATH... [--depth N] [--include-hidden]` — User Story 2 (P2): recursive
  total (and depth-limited breakdown) for one or more directory paths; batchable like
  `dns`/`tls`/`net`/`ad` via repeated positional args and/or `-i/--input-file`.

**Rationale**: `volumes` and `disks` are naturally single-shot, multi-row reports (there's nothing
to target — "what's on this machine" — so no batch/target-list semantics apply, same shape as
e.g. `dns lookup`'s multi-record response), while `size` is inherently per-target and gets full
batch treatment (FR-013-equivalent: process every path, aggregate exit code, JSON envelope per
target including failures) since checking several directories in one invocation is a real,
low-cost, high-value addition consistent with every other category's batch support. Presenting
disks and their partitions together (rather than two separate flat commands) mirrors how engineers
actually reason about the hierarchy (`lsblk`-style) and avoids forcing a second lookup to relate
one to the other.

**Alternatives considered**: a single `opskit storage report` command bundling all three views —
rejected, the three questions ("is it full", "what's using it", "what disks exist") are
independently useful and have different argument shapes (`size` takes paths, the other two don't);
forcing them into one command would mean either always paying for all three or a pile of
mode-selection flags. Separate `disks` and `partitions` commands — rejected per R7 rationale above
(nested is more useful than forcing a second lookup to relate them).
