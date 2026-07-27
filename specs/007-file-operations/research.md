# Phase 0 Research: File Operations

Decisions resolving every technical unknown in the plan's Technical Context. Format per speckit:
Decision / Rationale / Alternatives considered.

## R1. YAML parsing and serialization

**Decision**: Add **`PyYAML`** (`PyYAML>=6,<7`) as a new base runtime dependency. Use
`yaml.safe_load`/`yaml.safe_dump` exclusively — never the full (unsafe) `Loader`/`Dumper`.

**Rationale**: stdlib has no YAML support whatsoever. `PyYAML` is the long-standing de-facto
standard (used by Ansible, Kubernetes tooling, and most of the Python ecosystem), actively
maintained, and satisfies Art. IV the same way `pyopenssl`/`cryptography`/`ldap3`/`psutil`
already do for their categories. `safe_load`/`safe_dump` restrict the loader to plain Python
types (dict/list/str/int/float/bool/None/datetime) and refuse to construct arbitrary Python
objects — YAML's full loader is a well-known code-execution vector (CVE-class issue across many
ecosystems), so using the safe variant is a security decision, not just a style choice (Art. III).

**Alternatives considered**:
- *`ruamel.yaml`*: preserves comments/formatting for round-trip fidelity, which this feature does
  not need (pretty-printing intentionally normalizes formatting — see spec Assumptions); adds a
  heavier, less universally-installed dependency for no required benefit.
- *Hand-rolled YAML parser*: rejected outright — YAML's grammar (anchors, block/flow styles,
  multi-document streams) is not something to reimplement for a diagnostics tool.

## R2. TOML writing (paired with the existing read-only TOML support)

**Decision**: Add **`tomli-w`** (`tomli-w>=1,<2`) as a new base runtime dependency for
serialization. Reading continues to use the project's existing `tomllib` (3.11+ stdlib) /
`tomli` (<3.11) split.

**Rationale**: Python's stdlib `tomllib` (3.11+) is read-only by design (per PEP 680); no stdlib
TOML writer exists at any supported version. `tomli-w` is maintained by the same author as
`tomli` (already a dependency), is a small, focused, pure-Python writer with no transitive
dependencies, and its output is written directly against the same data model `tomllib`/`tomli`
read — no adapter mismatch.

**Alternatives considered**:
- *`tomlkit`*: a combined round-trip read/write library that preserves comments/formatting;
  rejected for the same reason as `ruamel.yaml` in R1 — this feature doesn't need format
  preservation, and `tomlkit` would duplicate the read path the project already has working.
- *Hand-rolled TOML serializer*: TOML's escaping/type-formatting rules (multi-line strings,
  datetime variants, array-of-tables) are non-trivial to get byte-correct; not worth
  reimplementing against a mature, minimal library.

## R3. XML parsing, serialization, and the JSON/YAML mapping convention

**Decision**: Add **`defusedxml`** (`defusedxml>=0.7,<1`) as a new base runtime dependency and
use its `ElementTree` drop-in (`defusedxml.ElementTree`) for all XML parsing; continue using
stdlib `xml.etree.ElementTree` only for *building* output trees (serialization has no XXE
surface — only parsing untrusted input does). Define one fixed, documented element↔dict mapping
in `xml_convert.py`:
- An element's child elements become dict keys (repeated child tags become a list).
- Attributes are collected under a reserved `"@attributes"` key (a dict of attribute name→value).
- Direct text content is stored under a reserved `"#text"` key, present only when the element has
  non-whitespace text alongside attributes/children (an element with only text and no
  attributes/children collapses to that text value directly, matching common tool conventions).
- Namespaces are preserved verbatim in tag/attribute names (Clark notation, `{uri}local`) rather
  than resolved against declared prefixes — deterministic and lossless for the tag name itself,
  at the documented cost of not "prettifying" namespaced names.

**Rationale**: CPython's own documentation flags `xml.etree.ElementTree.parse`/`fromstring` as
unsafe against maliciously crafted XML (entity expansion / XXE); Ruff's Bandit-derived `S314`
rule flags exactly this, and per CLAUDE.md security-fix hygiene, a `# noqa`/`# nosec` suppression
was rejected in favor of the root-cause fix. `defusedxml` is a thin, actively-maintained wrapper
that disables dangerous XML features by default and is API-compatible with `ElementTree`, so it
drops in with no shape change. Mixed content (text interleaved with child elements at arbitrary
positions) and multiple namespace prefixes mapping to the same URI are the two shapes this
convention cannot losslessly round-trip — flagged via the `lossless` field per FR-018 rather than
silently reformatted.

**Alternatives considered**:
- *`xmltodict`*: a popular library implementing a similar element↔dict convention; rejected in
  favor of hand-rolling in `xml_convert.py` because opskit needs to control the exact convention
  (and its documentation) precisely, and the mapping logic itself is small — the security-relevant
  part (safe parsing) still comes from `defusedxml`, so `xmltodict` would only be replacing ~80
  lines of straightforward tree-walking code with a new dependency.
- *`lxml`*: faster and more feature-complete (XPath, schema validation) but ships a compiled
  `libxml2` binding — heavier install footprint across three OSes for capabilities this feature
  doesn't need.
- *Suppressing `S314` with a documented `# nosec`*: rejected per CLAUDE.md's explicit guidance to
  fix security findings at the root rather than annotate around them.

## R4. Text-encoding detection

**Decision**: Add **`charset-normalizer`** (`charset-normalizer>=3,<4`) as a new base runtime
dependency. BOM detection (UTF-8/UTF-16-LE/UTF-16-BE/UTF-32 BOM byte sequences) is done directly
via a small stdlib byte-prefix check — no library needed for that half. When no BOM is present,
`charset_normalizer.from_bytes(...).best()` supplies the best-guess encoding and a confidence
signal.

**Rationale**: distinguishing, say, Latin-1 from UTF-8 in a BOM-less file is a genuinely hard,
well-studied heuristic problem (mis-detection is common on short/ambiguous byte sequences) —
reimplementing it would both take real effort and likely be worse than a purpose-built library.
`charset-normalizer` is pure Python, MIT-licensed, and already a transitive dependency of
`requests` across most of the Python ecosystem, making it a well-audited, low-risk addition.

**Alternatives considered**:
- *`chardet`*: the older, historically standard alternative; `charset-normalizer` was created
  specifically to be a faster, more actively maintained, dependency-free replacement and is what
  `requests` itself migrated to — chosen for that reason.
- *Assume UTF-8 always, report a decode failure otherwise*: rejected — it would leave User Story
  5's "what encoding is this actually in" question unanswered for the exact files that motivate
  the feature (non-UTF-8 legacy files).

## R5. File-type identification (content sniffing)

**Decision**: Implement `sniff.py` as a small, in-house, ordered signature table — no new
dependency. Check (in order): known binary magic-byte prefixes (PNG, GIF, PDF, ZIP/JAR/DOCX-family,
gzip, ELF, a handful of other common ones), then attempt structural sniffing for text-based
formats already supported elsewhere in this feature: JSON (leading non-whitespace byte is `{` or
`[`, and it actually parses), XML (`<?xml` prologue or a leading `<`), TOML/YAML disambiguated by
attempting each parser via `formats.py` and reporting whichever succeeds unambiguously (a file
valid as both is reported as both, not arbitrarily resolved). A file too short to contain any
matched signature and unparseable by any structural check is reported as `"undetermined"`.

**Rationale**: `python-magic`/`libmagic` (the natural off-the-shelf choice) wraps a native C
library that is not reliably installable on Windows without bundling a DLL alongside the Python
package — this would compromise Art. VI's "identical on Win/macOS/Linux" guarantee for a
category whose actual scope (the four structured formats this feature already parses, plus a
short list of common binary signatures so `identify` can say "this is actually a PNG, not JSON")
does not need `libmagic`'s thousands-of-signatures MIME database.

**Alternatives considered**:
- *`python-magic`/`libmagic`*: rejected for the cross-platform-install reason above.
- *`filetype` (pure-Python magic-byte library)*: covers binary signatures well but has no
  awareness of the structured text formats (JSON/YAML/TOML/XML) this feature centers on, so
  `formats.py`-based structural sniffing would still need to be written regardless — the binary
  signature table itself is short enough (a dozen entries) not to justify a dependency just for
  that slice.
- *Extension-only detection*: rejected outright — it's the exact failure mode FR-004 exists to
  catch (a mislabeled file).

## R6. Structural diff and duplicate-file detection

**Decision**: `diffing.py` — a small hand-rolled recursive comparator over two already-parsed
(via `formats.py`) JSON/YAML trees: walks dicts by key and lists by index, emitting a
`(key_path, left_value, right_value)` tuple for every leaf-level difference; a completely absent
key on one side is reported the same way with the missing side represented as absent (not
`None`, to distinguish "missing" from "present but null"). `hashing.py`'s duplicate finder groups
files by size first (cheap pre-filter), then by streaming SHA-256 content hash within each
size group — only files sharing both size and hash are reported as duplicates.

**Rationale**: the required output shape (equivalent, or a list of differing key-paths) is narrow
and specific to opskit's own envelope format; a ~50-line recursive walk is simpler to test and
reason about than adopting a general-purpose diff library (`deepdiff`) and re-shaping its richer,
more general output model into this feature's contract. The size-then-hash duplicate strategy is
the standard, efficient approach (avoids hashing every file pair or every full file when sizes
already disqualify most candidates).

**Alternatives considered**:
- *`deepdiff`*: more feature-rich (ignores order, type coercion options) than this feature needs;
  rejected to avoid a mid-size dependency for a narrowly-scoped requirement.
- *Line-based text diff (`difflib`) on the raw file content*: explicitly what FR-006/User Story 6
  reject — it's noisy for reformatted-but-equivalent files, which is the whole point of a
  *structural* diff.

## R7. Atomic, non-clobbering in-place writes (the shared write-safety mechanism)

**Decision**: One shared helper, `atomic.py`, used by all four guarded write commands
(`convert`, `eol`, `reencode`, `pretty`):
1. Compute the new content in memory (or via a streaming write to a `tempfile.NamedTemporaryFile`
   created in the **same directory** as the target, so the final `os.replace()` is guaranteed to
   be same-filesystem and therefore atomic on every supported OS).
2. If `--backup` was requested, copy the *original* file to `<path>.bak` via `shutil.copy2`
   first — refusing (raising `ClobberRefused`) if `<path>.bak` already exists, unless `--force`
   is also passed.
3. `os.replace(tmp_path, original_path)` — atomic rename on POSIX and Windows alike (Windows
   `MoveFileEx` with the replace flag, wrapped by `os.replace` since Python 3.3).
4. Without `--in-place` at all, none of the above runs — output goes to stdout or an explicit
   `--output` path (a plain write, since there is no "original" being replaced).

**Rationale**: doing this once in a single module — rather than once per command — is what makes
the Art. X write-safety guarantees ("atomic", "non-clobbering") a property of the mechanism
rather than a discipline each command author has to remember. Creating the temp file in the same
directory (not a system temp dir) is the detail that makes `os.replace()` atomic in the first
place — a cross-filesystem rename is not guaranteed atomic on any OS.

**Alternatives considered**:
- *Write-then-rename using a system temp directory*: rejected — a rename across filesystems
  (e.g., `/tmp` on a different mount than the target) silently degrades to copy+delete on some
  platforms, reintroducing the partial-write window Art. X exists to close.
- *Per-command bespoke write logic*: rejected — exactly the kind of repeated, easy-to-get-subtly-
  wrong safety code a shared helper (and one shared test suite, `test_file_atomic.py`) exists to
  prevent.

## R8. Named-target-only scope (no network, no unbounded traversal)

**Decision**: Every guarded write command takes an explicit file path (or, where batchable,
paths/`--input-file`/stdin — see R2 in the plan's Technical Context and the data-model batch
notes). None of them accept a directory to walk and mutate recursively; `duplicates`' own
`--recursive` flag is a read-only traversal knob and lives only on that diagnostic command, never
on a write command. No command in this category opens a socket.

**Rationale**: directly implements Art. X's "named-target only... no unbounded directory-tree
mutation beyond an explicit recursive flag the operator opted into" — and since no write command
here needs directory mutation at all (each acts on files the operator names), the simplest
compliant design is to never offer recursive mutation, rather than offer and gate it.

**Alternatives considered**: a hypothetical `file convert --recursive <dir>` batch-converting
every file in a tree was considered and rejected for v1 — it multiplies the blast radius of a
mistake (wrong `--to`, wrong glob) well beyond what any current user story asks for; single- or
explicitly-listed-file scope is deliberately more conservative than the constitution strictly
requires.

## R9. Exit codes

**Decision**: Add two new `ExitCode` members:
- `INVALID_CONTENT = 21` — a file is not valid for its (detected or declared) format: a
  `validate` failure, a `convert`/`pretty`/`diff` source that fails to parse, or a text-oriented
  command given a binary file.
- `CLOBBER_REFUSED = 22` — an in-place write would silently overwrite a pre-existing, unrelated
  file (most commonly an existing `.bak`) without `--backup`/`--force`.

Every other outcome reuses an existing member: `NOT_FOUND` (16) for a missing target,
`PERMISSION_DENIED` (15) for a read/write permission failure, `USAGE` (2) for bad flag
combinations (e.g. `--to` naming the source's own format), `PARTIAL` (7) for a mixed-result
batch, `OK`/`ERROR` as the success/generic-failure fallback.

**Rationale**: "invalid content" and "would-clobber" are both outcome classes no existing code
represents (they're neither a usage error, a not-found, nor a permission failure), and scripts
consuming opskit's exit-code contract need to distinguish "your input file is broken" from "you
asked for something that would have silently destroyed data" from every other failure class —
collapsing either into generic `ERROR` (1) would make the contract less useful for exactly the
kind of guarded write commands Art. X was written for.

**Alternatives considered**: reusing `ERROR` (1) for both cases — rejected because it would make
`echo $?` scripting unable to distinguish "fix your file" from "add `--backup`" from an
unanticipated failure, undermining the point of a structured exit-code contract (Art. IX).
