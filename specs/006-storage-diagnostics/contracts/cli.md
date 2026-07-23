# Contract: CLI — `opskit storage`

The command surface is public and SemVer-governed (constitution Art. V, IX). Options mirror the
`dns`/`tls`/`net`/`ad` groups' conventions (panels, output flags, batch behavior).

## Commands

```bash
opskit storage volumes [OPTIONS]
opskit storage disks [OPTIONS]
opskit storage size PATH... [OPTIONS]
```

### `storage volumes`

No arguments. Lists every mounted, non-pseudo volume visible to the current user.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--json` / `--jsonl` | flag | off | versioned envelope / one JSON object per volume |
| `--no-color` | flag | off | force plain output (`NO_COLOR` honored too) |

**Report content (human default)**: one row per volume — mount point/drive letter, filesystem
type, total/used/free, percent-used, local/network tag.

### `storage disks`

No arguments. Lists every physical/logical disk, each with its nested partitions.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--json` / `--jsonl` | flag | off | versioned envelope / one JSON object per disk |
| `--no-color` | flag | off | force plain output |

**Report content (human default)**: one panel per disk (id, size, model, fixed/removable — `—`
where unavailable) with a nested table of its partitions (device, size, mounted?, mount point,
filesystem type).

### `storage size`

`PATH` accepts one or more directory paths (positional, repeatable) and/or `-i/--input-file`
(one path per line, blank lines/`#` comments ignored) — same batch convention as `dns`/`tls`/`net`/`ad`.

| Option | Type | Default | Notes |
|--------|------|---------|-------|
| `--depth` | int | `0` | child-directory breakdown levels below each path; `0` = total only |
| `--include-hidden` | flag | off | include hidden files/directories in the size calculation |
| `-i, --input-file` | path | — | one directory path per line |
| `--json` / `--jsonl` | flag | off | versioned envelope / NDJSON per path |
| `--no-color` | flag | off | force plain output |

`--depth` must be `>= 0`; a negative value is a usage error before any filesystem I/O.

**Report content (human default)**: per path — total size, file/directory counts, whether hidden
entries were included, the breakdown table (if `--depth > 0`, one row per child directory with its
level and size), and a list of any inaccessible subdirectories with their reason. A path whose
scan hit inaccessible subdirectories is marked incomplete/lower-bound in the output, not presented
as a clean total. Batch mode prefixes each path's section.

## Exit codes (all reused — no new members; research R6)

| Code | Meaning | Applies to |
|------|---------|------------|
| 0 | success | all |
| 1 | generic error | all |
| 2 | usage error (e.g. negative `--depth`) — before any filesystem I/O | `size` |
| 7 | PARTIAL — batch with mixed outcomes, **or** a single `size` path that completed but hit inaccessible subdirectories (`incomplete: true`) | `size` |
| 15 | permission denied — the requested top-level path itself could not be listed at all | `size` |
| 16 | not found — path does not exist, or is not a directory | `size` |

`volumes`/`disks` always exit `0` (nothing per-target to fail — a total enumeration failure is a
bug, not a modeled outcome) unless a genuinely unexpected error occurs (exit `1`).

Batch rule for `size` (constitution Art. IX): every path processed; exit `0` only if every path is
found and fully complete; the uniform failure class if all paths share one outcome; else `7`
(PARTIAL). Failed/incomplete paths always appear in `--json`/`--jsonl` output (`result: null` +
populated `error` for raised failures; a populated `result` with `incomplete: true` for partial
scans).

## JSON envelopes

`schema_version "1"`. Examples (elided):

```json
{
  "schema_version": "1",
  "command": "storage.volumes",
  "query": {},
  "result": {
    "volumes": [
      {"mountpoint": "/", "device": "/dev/sda1", "fstype": "ext4",
       "total_bytes": 512110190592, "used_bytes": 210110190592, "free_bytes": 302000000000,
       "percent_used": 41.0, "is_network": false}
    ]
  },
  "error": null,
  "elapsed_ms": 12.4
}
```

```json
{
  "schema_version": "1",
  "command": "storage.size",
  "query": {"path": "/var/log", "depth": 1, "include_hidden": false},
  "result": {
    "path": "/var/log", "total_bytes": 1048576000, "file_count": 4210, "dir_count": 18,
    "include_hidden": false, "depth_requested": 1,
    "breakdown": [
      {"path": "/var/log/journal", "depth": 1, "size_bytes": 900000000, "incomplete": false},
      {"path": "/var/log/private", "depth": 1, "size_bytes": 0, "incomplete": true}
    ],
    "inaccessible": [{"path": "/var/log/private", "reason": "permission denied"}]
  },
  "error": null,
  "elapsed_ms": 340.1
}
```

## Examples (epilog)

```bash
opskit storage volumes
opskit storage volumes --json
opskit storage disks
opskit storage size /var/log
opskit storage size /var/log --depth 2
opskit storage size C:\Users\me\Downloads --include-hidden
opskit storage size /data /var/log /tmp --jsonl
opskit storage size -i paths.txt --depth 1
```
