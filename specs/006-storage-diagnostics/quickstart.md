# Quickstart & Validation: Storage Diagnostics

How to run and validate the `storage` feature end-to-end. Commands run from the repo root with
`uv` installed.

> **Shell note**: examples use POSIX syntax (`echo $?`); in PowerShell read the exit code with
> `$LASTEXITCODE` and use a Windows path for `size`. The `opskit` commands themselves are
> identical on every platform (fields marked best-effort in [data-model.md](data-model.md) may
> read `null`/`—` on Windows/macOS — that is expected, not a bug).

## Setup

```bash
uv sync --extra dev
uv run opskit storage --help          # group loads; volumes/disks/size present
uv run opskit storage volumes --help
uv run opskit storage disks --help
uv run opskit storage size --help
```

## Functional validation (maps to spec user stories)

| # | Scenario | Command | Expected |
|---|----------|---------|----------|
| US1 | volume overview | `uv run opskit storage volumes` | every mounted volume: mountpoint, fstype, total/used/free, %used; exit 0 |
| US1 | pseudo-fs excluded | `uv run opskit storage volumes --json \| jq '.result.volumes[].fstype'` (Linux) | no `tmpfs`/`proc`/`sysfs` entries |
| US1 | network mount tagged | mount an NFS/SMB share, then `uv run opskit storage volumes` | that row's `is_network` is `true`, others `false` |
| US2 | directory total | `uv run opskit storage size /var/log` (or any known-size dir) | `total_bytes` matches an independent check (e.g. sum via a file manager) |
| US2 | depth breakdown | `uv run opskit storage size /var/log --depth 2` | breakdown rows for levels 1 and 2 under the path |
| US2 | hidden default excluded | create a dir with a hidden file, run `size` with and without `--include-hidden` | totals differ only when hidden entries are present; report states which mode ran |
| US2 | permission-denied subtree | scan a tree containing one unreadable subdirectory | scan completes (doesn't abort); that path appears in `inaccessible`; `incomplete: true`; exit 7 |
| US2 | path missing | `uv run opskit storage size /no/such/path; echo $?` | not-found error; exit 16 |
| US3 | disk + partition inventory | `uv run opskit storage disks` | every disk listed with nested partitions; Linux shows size/model/removable, Windows shows fixed/removable, macOS shows size only for those fields best-effort |
| US3 | unavailable fields marked | `uv run opskit storage disks --json \| jq '.result.disks[].model'` (macOS/Windows) | `null`, never fabricated or silently omitted from the JSON shape |
| US4 | programmatic | `uv run python -c "from opskit.storage import list_volumes; [print(v.mountpoint, v.percent_used) for v in list_volumes()]"` | typed results; nothing extra printed |
| US4 | typed error | `uv run python -c "from opskit.storage import dir_size, PathNotFound; dir_size('/no/such/path')"` | raises `PathNotFound` |

## Deterministic validation (no OS-hardware dependency — gates CI)

Volume/disk enumeration is exercised against **`psutil`, mocked/monkeypatched** at the
`opskit.storage` boundary (injecting fixture `sdiskpart`/`sdiskusage` tuples covering: a normal
local volume, a pseudo-filesystem to be excluded, a network filesystem, an unmounted partition
represented in `/sys/block` fixtures on Linux) so results are deterministic across CI runners
regardless of their actual disks. Directory-size scanning is exercised against **real temp
directories** built per-test (`tmp_path`) with known file sizes, nested depths, a symlink loop, a
hidden file/dir, and (on POSIX) a `chmod 000` subdirectory to exercise the inaccessible-path path;
Windows CI covers the hidden-attribute branch via `FILE_ATTRIBUTE_HIDDEN` set through `os.stat`.

```bash
uv run pytest tests/unit -k storage
uv run pytest -q                                   # full suite, coverage >= 90%
uv run ruff format --check . && uv run ruff check .
uv run mypy && uv run pyright
```

## Acceptance gates

- Every exit class in [contracts/cli.md](contracts/cli.md) (`0`, `2`, `7`, `15`, `16`) has a test
  asserting its outcome and exit code (SC-002).
- `size` batch output contains an entry for **every** requested path (spec FR — batch contract),
  verified by a mixed-outcome batch test (found + not-found + incomplete in one invocation).
- A fixture directory containing both hidden and non-hidden files proves the `--include-hidden`
  toggle changes `total_bytes` only for that fixture (SC-004).
- A fixture tree with one inaccessible subdirectory proves the scan completes, lists the path in
  `inaccessible`, and does not abort (SC-002).
- Cross-OS field-availability differences (research R2) are asserted per-platform in the mocked
  unit suite rather than skipped, so a platform regression (e.g. a field that should be `None` on
  macOS suddenly isn't, or vice versa) is caught.
- Docs gate: all three commands have help text + `src/opskit/storage/README.md`; the root README
  Commands table links it; the API example in
  [contracts/python-api.md](contracts/python-api.md) runs as written.
