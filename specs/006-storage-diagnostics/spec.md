# Feature Specification: Storage Diagnostics

**Feature Branch**: `006-storage-diagnostics`

**Created**: 2026-07-20

**Status**: Draft

**Input**: User description: "Storage diagnostics (opskit storage) — a read-only, cross-platform disk/storage inspection command group for opskit, working identically on Windows, macOS, and Linux (including Ubuntu). Engineers can inspect local storage without reaching for OS-specific tools (diskpart/wmic, diskutil, lsblk/df). Capabilities: disk details, partition details, filesystem details, disk utilization details, directory size (recursive total), child-directory size breakdown at a specified depth, and a separate flag to include or exclude hidden files/directories from size calculations. All existing opskit contracts apply (API-first, JSON envelope, structured exit codes, batch where applicable, actionable errors, no telemetry)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See how full my disks are (Priority: P1)

An engineer suspects a machine is low on disk space — a service is failing, a build is erroring,
or a monitoring alert fired. They run a single command and immediately see every mounted volume
on the machine: where it's mounted, what filesystem it uses, and its total/used/free capacity and
percent-used — the same way on Windows, macOS, and Linux, replacing `df -h`, `diskutil list`, or
opening Disk Management.

**Why this priority**: This is the single most common reason someone reaches for a storage tool —
"is this box out of space?" — and it's the entry point that motivates the rest of the category.

**Independent Test**: Run the command on a machine with multiple mounted volumes and confirm every
mounted volume appears with mount point, filesystem type, total/used/free capacity, and
percent-used; fill a scratch volume close to capacity and confirm the percent-used and free-space
figures reflect it.

**Acceptance Scenarios**:

1. **Given** a machine with one or more mounted volumes, **When** the user requests the storage
   overview, **Then** the output lists each volume's mount point (or drive letter), filesystem
   type, total capacity, used capacity, free capacity, and percent-used, and the process exits 0.
2. **Given** a volume that is nearly full, **When** the user checks it, **Then** the reported
   percent-used and free capacity reflect the actual remaining space.
3. **Given** a volume that is a network-mounted filesystem (e.g., NFS/SMB share), **When** the
   user checks it, **Then** it appears in the listing with the same fields, and the report
   indicates it is a network mount rather than presenting it identically to a local disk.
4. **Given** a pseudo/virtual filesystem that does not represent real storage capacity (e.g.
   `tmpfs`, `proc`, `sysfs` on Linux), **When** the overview is generated, **Then** it is excluded
   by default so the listing stays focused on real capacity.

---

### User Story 2 - Find what's consuming space in a directory (Priority: P2)

Having learned a volume is nearly full, the engineer needs to know *where* the space went. They
point the tool at a directory and get its total size; when they also ask for a breakdown depth,
they get the size of each child directory down to that many levels, so they can drill toward the
actual offender without a full, unbounded recursive listing of every file.

**Why this priority**: This directly answers the question User Story 1 raises and is the
day-to-day "what's eating my disk" task engineers currently solve with `du`, PowerShell
one-liners, or WinDirStat-style GUIs.

**Independent Test**: Point the tool at a directory tree with known file sizes and known nested
subdirectories; confirm the reported total matches the known sum; request depth 1 and depth 2 and
confirm the breakdown lists exactly the child directories at each level with correct sizes;
toggle the hidden-files flag and confirm the total changes only when hidden entries are present.

**Acceptance Scenarios**:

1. **Given** a directory path with no depth specified, **When** the user requests its size,
   **Then** the output reports the total size of all files under that path (recursively) and the
   process exits 0.
2. **Given** a directory path and a requested depth of N, **When** the user requests a breakdown,
   **Then** the output reports the overall total plus, for each child directory down to N levels
   below the given path, that child directory's own total size.
3. **Given** the requested depth is deeper than the tree actually goes, **When** the breakdown
   runs, **Then** it reports whatever levels exist without treating the shortfall as an error.
4. **Given** the hidden-files flag is not supplied, **When** a directory containing hidden files
   or hidden subdirectories is scanned, **Then** those entries are excluded from the reported
   size and count, and the report states that hidden entries were excluded.
5. **Given** the hidden-files flag is supplied, **When** the same directory is scanned, **Then**
   hidden files and subdirectories are included in the reported size, and the report states that
   hidden entries were included.
6. **Given** a subdirectory the current user cannot access, **When** the scan reaches it, **Then**
   the scan continues over the rest of the tree, the inaccessible path is listed separately with
   its reason, and the reported total is annotated as a lower bound rather than silently presented
   as complete.

---

### User Story 3 - Understand the physical disk and partition layout (Priority: P2)

An engineer doing capacity planning, hardware triage, or pre-migration inventory needs to see the
physical disks present on a machine and how they're divided into partitions — separate from the
mounted-volume utilization view, which only shows what's currently mounted and usable.

**Why this priority**: This is a real but less frequent need than "is it full" or "what's using
it" — it matters for inventory and hardware-level troubleshooting (e.g., "is there an unpartitioned
disk we could use") rather than the everyday space-triage flow.

**Independent Test**: Run the command on a machine with multiple physical disks and multiple
partitions per disk; confirm every disk and every partition on it appears, with partitions linked
to the disk they belong to and, where mounted, to the volume reported in User Story 1.

**Acceptance Scenarios**:

1. **Given** a machine with one or more physical/logical disks, **When** the user requests disk
   details, **Then** each disk appears with its identifying information (size, and model/name and
   type such as fixed vs. removable where the platform exposes it without elevated privileges).
2. **Given** a disk with one or more partitions, **When** the user requests partition details,
   **Then** each partition appears with its size and, if mounted, the mount point/drive letter and
   filesystem type it corresponds to in the volume overview.
3. **Given** a partition that exists but is not currently mounted, **When** the user requests
   partition details, **Then** it is still listed (size and partition identity) with an explicit
   "not mounted" status rather than being silently omitted, on platforms where this is
   determinable; where it is not determinable, the limitation is stated rather than guessed at.
4. **Given** a property of a disk cannot be determined without elevated privileges or is not
   exposed by the platform, **When** the disk is reported, **Then** that field is explicitly
   marked unavailable rather than omitted or fabricated.

---

### User Story 4 - Use it from code (Priority: P3)

A platform engineer embeds the same storage checks in their own tooling — a monitoring script, a
pre-deploy capacity check, a cleanup utility — via a typed programmatic interface that returns
structured results and raises typed errors, without printing or exiting the process.

**Why this priority**: API parity is an opskit constitutional guarantee and enables automation
built on top of the CLI flows above, but it serves those flows rather than standing alone.

**Independent Test**: From a short script, fetch volume utilization and a directory size
breakdown programmatically, read the structured fields, and catch a specific typed error for an
induced failure (e.g., a nonexistent path).

**Acceptance Scenarios**:

1. **Given** the library interface, **When** a volume-utilization or directory-size call
   succeeds, **Then** the caller receives a typed result exposing the same fields as the CLI
   output, and nothing is printed.
2. **Given** an induced failure (e.g., a path that does not exist), **When** the call runs
   programmatically, **Then** a typed exception of the matching failure class is raised with an
   actionable message.

---

### Edge Cases

- **Path does not exist, or is a file rather than a directory**: reported as a distinct usage
  error, not a zero-size result.
- **Directory entirely inaccessible** (no permission to read it at all): reported as a distinct
  failure for that path rather than a silent zero.
- **Symlinks, junctions, and reparse points inside a scanned tree**: not followed by default, to
  avoid double-counting and traversal cycles; this default is stated in the report.
- **Depth of 0 or omitted**: returns the total size only, with no child breakdown.
- **Requested depth exceeds the tree's actual depth**: whatever levels exist are reported; not an
  error.
- **Removable media that disappears mid-scan** (e.g., a USB drive unplugged): normalized into a
  typed, actionable error rather than a raw OS exception or crash.
- **Hidden system entries a normal user cannot access** (e.g., Windows `System Volume
  Information`, `$RECYCLE.BIN`): follow the same skip-and-report rule as any other inaccessible
  path, regardless of the hidden-files flag.
- **Unformatted/raw partitions with no filesystem**: reported as present with no filesystem/
  utilization data, not silently dropped from partition listings.
- **Very large trees** (millions of files): the scan completes without unbounded memory growth,
  even though it may take real wall-clock time proportional to tree size.
- **Zero mounted volumes visible to the current user** (unusual, but possible in constrained
  environments): reported as an empty result, not an error.

## Requirements *(mandatory)*

### Functional Requirements

**Volumes & utilization**

- **FR-001**: The system MUST enumerate mounted volumes visible to the current user and report,
  for each: mount point (or drive letter), filesystem type, total capacity, used capacity, free
  capacity, and percent-used.
- **FR-002**: The system MUST exclude pseudo/virtual filesystems that do not represent real
  storage capacity (e.g. `tmpfs`, `proc`, `sysfs`-style mounts) from the default volume listing.
- **FR-003**: The system MUST distinguish network-mounted filesystems from local filesystems in
  the volume listing rather than presenting them identically.

**Disks & partitions**

- **FR-004**: The system MUST enumerate physical/logical disks present on the machine and report
  each disk's size, and its model/name and fixed-vs-removable type where the platform exposes
  that information without requiring elevated privileges.
- **FR-005**: The system MUST enumerate partitions on each disk and report each partition's size
  and, where determinable, its mount status (mounted — linked to the corresponding volume — or
  not mounted).
- **FR-006**: When a disk or partition property cannot be determined on the current platform
  without elevated privileges, the system MUST report that field as explicitly unavailable rather
  than omitting it silently or fabricating a value.

**Directory size analysis**

- **FR-007**: Users MUST be able to request the total recursive size of all files under a given
  directory path.
- **FR-008**: Users MUST be able to request a child-directory size breakdown to a specified depth
  (number of directory-nesting levels below the given path); each reported level MUST show every
  child directory at that level with its own total size.
- **FR-009**: Users MUST be able to explicitly include or exclude hidden files and directories
  (dotfiles on Linux/macOS; the hidden file attribute on Windows) from size calculations via a
  dedicated flag; the report MUST state which mode was used. Hidden entries MUST be excluded by
  default.
- **FR-010**: The system MUST NOT follow symbolic links, junctions, or reparse points while
  computing directory sizes.
- **FR-011**: When a subdirectory is inaccessible during a size scan, the system MUST skip it,
  continue scanning the rest of the tree, list the inaccessible path with its reason, and
  annotate the resulting total as incomplete rather than presenting it as a complete result.

**Contracts (per constitution)**

- **FR-012**: The command group MUST honor the opskit output contract: human-readable default,
  versioned JSON envelope, `NO_COLOR`/auto-plain behavior, and structured exit codes with distinct
  classes for usage error, path-not-found, permission-denied/partial-result, and success.
- **FR-013**: Every capability MUST be available programmatically with typed results and typed
  errors; the programmatic layer never prints or terminates the process.
- **FR-014**: Behavior MUST be identical on Windows, macOS, and Linux except where a platform
  genuinely does not expose a given piece of information, and such gaps MUST be attributable from
  the output (per FR-006) rather than silently inconsistent.
- **FR-015**: The feature MUST be read-only: it MUST NOT modify, move, delete, or create any file,
  directory, or volume, and MUST perform no network calls.

### Key Entities

- **Disk**: a physical or logical storage device — size, and best-effort model/name and
  fixed-vs-removable type.
- **Partition**: a division of a Disk — size, the Disk it belongs to, and (if mounted) a link to
  its Volume.
- **Volume**: a mounted, usable filesystem — mount point/drive letter, filesystem type, total/
  used/free capacity, percent-used, and whether it's local or network-mounted.
- **Directory Size Result**: the outcome of a size scan — the requested path, total size, file/
  directory counts, whether hidden entries were included, the child-directory breakdown (if a
  depth was requested), and the list of any inaccessible paths encountered.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any machine, an engineer can see every mounted volume's mount point, filesystem
  type, and total/used/free/percent-used capacity with a single command in under 10 seconds.
- **SC-002**: An engineer pointed at a directory gets its total size, and can request a
  depth-limited child-directory breakdown, without the command aborting due to a subset of
  inaccessible subdirectories — verified by scanning a tree that includes at least one
  permission-denied subdirectory.
- **SC-003**: The same commands produce structurally identical reports (same fields, same units,
  same behavior) on Windows, macOS, and Linux, verified by the CI matrix, with any
  platform-specific unavailable field explicitly named rather than silently omitted.
- **SC-004**: Toggling the hidden-files flag changes the reported total only for paths that
  actually contain hidden entries, and the report always states which mode was used — verified by
  a directory fixture containing both hidden and non-hidden files.
- **SC-005**: All capabilities are usable programmatically with typed results; the documented
  examples run as written.

## Assumptions

- **Hidden files/directories are excluded from size calculations by default**, with an explicit
  flag to include them — the user-requested "separate switch to include hidden files" implies the
  default state is exclusion; this avoids surprising size jumps from caches and dotfiles (e.g.
  `.git`, browser profiles) unless the engineer opts in.
- **"Depth" for the child-directory breakdown means directory-nesting levels below the given
  path** (equivalent to a `du --max-depth`-style report), not a count of directories to list —
  chosen because it's the well-established convention for this kind of tool and gives the
  engineer control over report size regardless of how many subdirectories exist at any one level.
- **Symbolic links/junctions are not followed during size scans**, matching common `du`-family
  default behavior and avoiding double-counting or cycles; this may undercount trees that rely on
  symlinked data and is stated in the report.
- **Only mounted volumes are covered by the utilization view (User Story 1)**; unmounted/raw
  partitions are covered separately by the disk/partition inventory (User Story 3) on platforms
  where they're determinable, and are explicitly marked as such rather than guessed at where they
  are not.
- **Physical disk hardware identity (model/name, fixed-vs-removable) is best-effort and may be
  partially unavailable** on some platforms/permission levels without elevated privileges;
  volume-level utilization (User Story 1) remains the fully reliable core of the category across
  all three OSes.
- **No client-side timeout is imposed on directory scans or volume enumeration** in v1; a stalled
  network-mounted filesystem can make a scan take as long as the OS/filesystem layer takes. This
  matches the read-only, no-extra-network-activity stance and may be revisited if it proves to be
  a real-world pain point.
- **No `--watch` mode in v1**: unlike `net`/`tls`, storage state changes are driven by local
  activity rather than remote endpoints going up/down, so repeated on-demand runs cover the need;
  this may be revisited later.
- **Free/used/total capacity figures reflect the filesystem's own accounting** (matching what
  `df`/Disk Management/`diskutil` would show), including any filesystem-level reservations; no
  attempt is made to reconcile discrepancies between filesystem-reported and application-level
  usage.
