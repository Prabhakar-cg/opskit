# Quickstart & Validation: File Operations

How to run and validate the `file` feature end-to-end. Commands run from the repo root with `uv`
installed.

> **Shell note**: examples use POSIX syntax (`echo $?`); in PowerShell read the exit code with
> `$LASTEXITCODE`. `opskit file` commands themselves are identical on every platform except
> `stat`'s POSIX owner/permissions fields, which read `null`/`—` on Windows — that is expected
> (FR-022), not a bug.

## Setup

```bash
uv sync --extra dev
uv run opskit file --help              # group loads; all 12 commands present
uv run opskit file validate --help
uv run opskit file eol --help
uv run opskit file convert --help
```

## Functional validation (maps to spec user stories)

| # | Scenario | Command | Expected |
|---|----------|---------|----------|
| US1 | valid file | `uv run opskit file validate config.json` | `valid: true`; exit 0 |
| US1 | broken JSON | add a trailing comma, then `uv run opskit file validate broken.json; echo $?` | `valid: false` with line/column of the error; exit 21 |
| US1 | batch, one bad | `uv run opskit file validate good.yaml bad.yaml` | both reported; exit 7 (PARTIAL) |
| US2 | detect mixed endings | create a file mixing `\r\n` and `\n`, then `uv run opskit file lineendings mixed.txt` | reports both counts and `mixed: true` |
| US2 | non-destructive normalize | `uv run opskit file eol mixed.txt --to lf > fixed.txt` | `mixed.txt` unchanged (hash before/after matches); `fixed.txt` is all-LF |
| US2 | in-place with backup | `uv run opskit file eol mixed.txt --to lf --in-place --backup` | `mixed.txt` now all-LF; `mixed.txt.bak` holds the original bytes |
| US3 | round-trip conversion | `uv run opskit file convert config.json --to yaml \| uv run opskit file convert /dev/stdin --format yaml --to json` | final JSON is data-equivalent to the original (`file diff` reports equivalent) |
| US3 | invalid source | `uv run opskit file convert broken.json --to yaml; echo $?` | fails with the same invalid-content shape as `validate`; exit 21 |
| US4 | mislabeled file | rename a JSON file to `.txt`, then `uv run opskit file identify renamed.txt` | `detected_type: json`, `extension: txt`, `extension_matches: false` |
| US4 | checksum | `uv run opskit file hash artifact.tar.gz` | digest matches an independently computed `sha256sum` |
| US5 | encoding detection | `uv run opskit file encoding legacy.txt` | reports the actual encoding (e.g. `iso-8859-1`) and `has_bom: false` |
| US5 | transcode | `uv run opskit file reencode legacy.txt --from iso-8859-1 --to utf-8 --output legacy.utf8.txt` | resulting file decodes correctly as UTF-8 with the same text |
| US6 | structural diff | reformat a YAML file (reorder keys, reindent) with no data change, then `uv run opskit file diff original.yaml reformatted.yaml` | `equivalent: true` |
| US6 | real difference | change one value, then re-run the diff | reports the specific differing key path and both values |
| US6 | duplicates | copy a file twice into a scratch directory, then `uv run opskit file duplicates ./scratch` | one group listing all copies |
| US7 | stat | `uv run opskit file stat config.json` | size/mtime/permissions reported; owner `null` if undeterminable |
| US7 | programmatic | `uv run python -c "from opskit.file import validate; r = validate('config.json'); print(r.path, r.valid)"` | typed result; nothing extra printed |
| US7 | typed error | `uv run python -c "from opskit.file import convert, StructuredFormat; convert('missing.json', to=StructuredFormat.YAML)"` | raises `FileNotFoundOnDisk` |

## Deterministic validation (no external-tool dependency — gates CI)

All format parsing/serialization (`formats.py`), XML mapping (`xml_convert.py`), sniffing
(`sniff.py`), text-stats (`textstats.py`), hashing/duplicates (`hashing.py`), and diffing
(`diffing.py`) are pure-function unit tests over `tmp_path` fixtures — fully deterministic, no
reliance on any pre-existing repo file or real network/OS resource. Atomic-write behavior
(`atomic.py`) is tested against the **real filesystem** (not mocked — `os.replace()` semantics
are what's under test) with fault injection: monkeypatch `os.replace` to raise partway through
and assert the original file's content and mtime are untouched (SC-003).

```bash
uv run pytest tests/unit -k file
uv run pytest -q                                   # full suite, coverage >= 90%
uv run ruff format --check . && uv run ruff check .
uv run mypy && uv run pyright
```

## Acceptance gates

- Every exit class in [contracts/cli.md](contracts/cli.md) (`0`, `2`, `7`, `15`, `16`, `21`, `22`)
  has a test asserting its outcome and exit code.
- A test hashes a source file before and after a non-`--in-place` `eol`/`convert`/`reencode`/
  `pretty` run and asserts byte-for-byte equality (SC-002).
- The fault-injection suite in `test_file_atomic.py` proves an interrupted `--in-place` write
  never leaves the target partially written (SC-003).
- Hypothesis-driven round-trip tests convert JSON→YAML→JSON, JSON→TOML→JSON, and YAML→TOML→YAML
  across generated structures and assert data equality (SC-004).
- A mixed-outcome batch (valid + invalid + missing file in one invocation) proves every target is
  processed and reflected in `--json`/`--jsonl` output (SC-007).
- Docs gate: all 12 commands have help text + `src/opskit/file/README.md`; the root README
  Commands table links it; both API examples in
  [contracts/python-api.md](contracts/python-api.md) run as written.
