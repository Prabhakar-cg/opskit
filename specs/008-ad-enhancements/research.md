# Phase 0 Research: AD Diagnostics Enhancements

Decisions resolving every technical unknown in the plan's Technical Context. Format per
speckit: Decision / Rationale / Alternatives considered. Numbered independently of
004-ad-diagnostics's R1–R10 (this feature only touches the areas below; everything else in
`ad` — connection security, discovery, status derivation, error normalization for
connect/bind, typing quarantine — is unchanged and not re-litigated here).

## E1. Group-redirect detection — a second, class-scoped query only on the not-found path

**Decision**: `_find_one()` in `api.py` already computes `id_kind`/`value` and the LDAP
equality clause for the requested identifier before running its class-scoped search. When
`kind_filter == "principal"` and that search returns zero matches, run one additional
search using the **same** identifier clause against `_CLASS_FILTERS["group"]` instead of
`_CLASS_FILTERS["principal"]`. If that yields exactly one match, raise the new
`PrincipalIsGroup` error naming the resolved group and suggesting `ad members <name>` and
`ad member <principal> <name>`, instead of `PrincipalNotFound`. If it yields zero or more
than one match, fall through to the existing `PrincipalNotFound`/generic-not-found behavior
unchanged (FR-002) — this feature only handles the exactly-one-group case; a name that
happens to collide with multiple groups is already an unusual directory and gets the plain
not-found message rather than a second layer of ambiguity handling.

**Rationale**: This is the minimal change that satisfies FR-001/FR-002: it costs one extra
LDAP query, and only on the failure path that was already about to fail — no cost is added
to the success path of `ad user`/`ad groups`/`ad member`. Doing it inside `_find_one()`
(rather than duplicating the check at each of the three principal-scoped call sites) means
the fix applies uniformly and can't drift between call sites.

**Alternatives considered**: Always resolving against `_CLASS_FILTERS["any"]` first and then
checking the object type of whatever came back (rejected: changes the shape/cost of the
success path for every call, and reintroduces exactly the "guess across types" behavior R6
of 004 deliberately rejected for object resolution — here we still want group and
user/computer resolution kept structurally separate; we're only adding a diagnostic
second look after failure, not merging the search); a static did-you-mean using
Levenshtein distance over cached names (rejected: requires enumerating the directory,
which Art. X / FR-014 forbid, and doesn't answer the actual question — the identifier
matched exactly, just the wrong class).

## E2. `ad members` traversal — BFS over `member`, mirroring R7's `memberOf` BFS in reverse

**Decision**: A new `_expand_members()` helper walks a group's `member` attribute
breadth-first, exactly mirroring 004's `_expand_nested()` (R7) but in the opposite
direction and over a heterogeneous result set (members can be users, computers, *or*
groups, unlike `memberOf` chains which are always groups):

- seed the visited set with the top-level group's own DN (self-membership guard);
- for each group dequeued, read its `member` values; for every value not already visited,
  mark it visited, record a `GroupMemberEntry` (`via="direct"` for the seed group's own
  members, `via="nested"` otherwise, with the acquisition path of intermediate group names);
- read that member's `objectClass` to classify it (`user`/`computer`/`group` — the same
  `_object_type_of()` helper `_summarize()` already uses); only entries classified `group`
  are enqueued for further expansion;
- the direct-only mode (FR-004) is the same traversal with the queue never fed beyond the
  seed group — equivalently, a fast path that skips the objectClass lookups entirely and
  just lists the seed group's raw `member` values (no need to classify types when nothing
  will be expanded), keeping the common "just show me who's directly in this group" case
  cheap.

Each distinct member (by DN, case-insensitively) is reported exactly once, at the BFS
shortest path — the same guarantee 004 already gives for `ad groups --effective`, now
applied to FR-006's cycle case.

**Rationale**: Reusing the exact shape and cycle-safety mechanism already proven for the
other traversal direction is both the least code and the least risk — it's the same
algorithm with `member`/`memberOf` swapped and one extra per-node classification read. The
extra `objectClass` read per node is the unavoidable cost of a heterogeneous member set;
`ad groups`'s traversal doesn't need it because every node there is already known to be a
group.

**Alternatives considered**: AD's transitive `member:1.2.840.113556.1.4.1941:=<DN>` matching
rule in one server-side query (rejected for the same reason 004's R7 rejected it for the
reverse direction: it returns a flat set with no acquisition path, so "via nested group X"
in FR-005 couldn't be reported, and it's AD-only); skipping the per-node `objectClass`
lookup and instead inferring "is this a group" from whether a later `member` read on it
succeeds (rejected: an empty group and "not a group" are indistinguishable that way, and it
still costs a read either way — an explicit classification read is clearer and no more
expensive); returning intermediate groups themselves filtered out of the final list, showing
only leaf user/computer accounts (rejected: FR-005's "membership path" requirement is best
satisfied by keeping the intermediate group as a first-class entry too — matching how
`ad groups --effective` keeps every intermediate group in its own output — and it avoids a
"why is this member missing" surprise if a directly-listed nested group happens to be empty).

## E3. UPN/mail matching — widen one filter clause; ambiguity rule unchanged

**Decision**: `_identifier_clause()` in `api.py` changes its `IdentifierKind.UPN` branch
from `(userPrincipalName={value})` to `(|(userPrincipalName={value})(mail={value}))`. No
other part of identifier classification changes — an `@`-containing identifier is still
classified as UPN-shaped by the same rule as today (R6 of 004); only the filter built for
that shape widens. `_find_one()`'s existing zero/one/many handling is untouched: a single
LDAP search naturally returns each matching directory entry once even when it satisfies
both clauses of an OR filter, so a user whose `userPrincipalName` and `mail` are the same
string is still resolved as a single, unambiguous match (FR-010); two different accounts
each matching one clause still trip the existing `AmbiguousPrincipal` path unchanged
(FR-009).

**Rationale**: The narrowest possible change that satisfies FR-008 — one filter string,
zero new control flow, and the existing ambiguity refusal (R6) is reused as-is rather than
re-implemented, because an LDAP OR-filter search already has the right dedup-by-entry
semantics for FR-010's "matched via both attributes is not ambiguous" requirement.

**Alternatives considered**: two sequential searches (UPN first, then `mail` if empty)
(rejected: reintroduces the exact "cascade risks matching the wrong object silently" problem
R6 rejected for the three-form identifier detection, and doesn't naturally give FR-010's
single-match behavior when both attributes match the same entry — two separate one-result
searches would need explicit entry-DN dedup logic to reconstruct what the OR-filter gives
for free); also matching `proxyAddresses` (Exchange's multi-valued alias attribute)
(rejected: out of scope — the backlog and spec both name `mail` specifically; broadening to
`proxyAddresses` risks matching stale/shared aliases and is a larger, separate decision
better left for its own backlog item if it's ever needed).

## E4. Certificate-verification hint text — thread the security mode through classification

**Decision**: `classify_connect_error()` in `directory.py` gains an optional
`security: str = "ldaps"` keyword parameter. Both places it constructs `CertificateInvalid`
(the exception-chain branch matching `ssl.SSLCertVerificationError`, and the string-fallback
branch matching `"certificate" in text`) build their hint from a shared helper that always
appends the WSL trust-store note (FR-012, unconditional — a WSL user hitting this failure
under LDAPS is just as likely to be confused by it as one using StartTLS) and additionally
appends the "`--starttls` does not skip certificate verification" clause only when
`security == "starttls"` (FR-011 — scoped, so an LDAPS user isn't told something irrelevant
to the flag they didn't pass). The three call sites inside `connect_session()` (initial
`conn.open()`, the StartTLS `conn.start_tls()` upgrade, and the bind-stage exception
handler) and the one inside `DirectorySession.search()` all pass `security=config.security`
(the latter via `self.config.security`) instead of relying on the new parameter's default.

**Rationale**: `classify_connect_error()` is the single normalization choke point for every
connect/TLS-stage failure (both `use_ssl` LDAPS opens and the separate `start_tls()` upgrade
call it), so threading the security mode through it — rather than duplicating hint text at
each of the four call sites — keeps the wording change in one place and impossible to drift.
Gating the StartTLS-specific sentence keeps the message accurate to what the user actually
ran.

**Alternatives considered**: raising a StartTLS-specific `CertificateInvalid` subtype
instead of varying the hint string (rejected: no code branches on the distinction — the
exit code and error class are identical either way; a hint-text difference doesn't earn a
new error type per Art. VII's "each error type owns its exit code," which isn't in play
here); putting the WSL note only behind a runtime WSL-detection check, e.g. reading
`/proc/version` (rejected: adds a platform-sniffing code path for a hint that is harmless
and still useful advice on non-WSL Linux/macOS/Windows too — a certificate genuinely not
trusted by the local store has the same fix everywhere: `--ca-file`; WSL is called out
because it's the specific "but I *did* install the corporate CA!" confusion the backlog
recorded, not because the advice is WSL-exclusive).

## E5. Test strategy — extend the existing 004 mock-directory and directory-classification layers

**Decision**: No new test layer. Extend the two layers 004 already established (R8):

1. **Offline mock directory** (`tests/integration/test_ad_mock_directory.py` and its shared
   conftest builder): add (a) a group entry whose name collides with no user/computer, to
   exercise E1's redirect path from `ad groups`/`ad member`; (b) a user whose `mail` differs
   from their `userPrincipalName`, plus a second, unrelated user/group pair where two
   *different* entries each match one of the two attributes for the same input string, to
   exercise both the success and the ambiguity side of E3; (c) reuse the existing nested-
   group-with-a-cycle fixture (already built for `ad groups --effective`'s tests) as the
   `member`-direction fixture for E2's `ad members` traversal — the same topology exercises
   both directions, just walked the other way.
2. **Directory-classification unit tests** (`tests/unit/test_ad_directory.py`, which already
   calls `classify_connect_error()` directly with an injected exception): add cases passing
   `security="starttls"` vs. the default, asserting the StartTLS clause's presence/absence
   and the WSL note's unconditional presence in the resulting `CertificateInvalid.hint`.

**Rationale**: All four changes are refinements of code paths 004's test layers were
already built to exercise (identifier resolution, membership traversal, connect-error
classification) — no new kind of fake, mock, or loopback server is needed, matching the
plan's "no new OS-sensitive code path" scope note.

**Alternatives considered**: a real-LDAP `@pytest.mark.network` smoke test for the redirect/
mail-matching behavior (rejected: same reasoning as 004's R8 — these are directory-semantics
branches the offline MOCK_SYNC layer already pins deterministically and cross-platform; a
real-network test would be the *only* thing gating this, which the constitution's CI rules
forbid for anything but opt-in smoke coverage).

## Addenda (as-built)

- **E1–E4 implemented exactly as designed** — no deviations from the decisions above were
  needed during implementation. One correction made along the way: the first draft of
  `AdClient.members()` requested only `["sAMAccountName"]` when resolving the top-level
  group via `_find_one()`, which silently dropped the `member` attribute needed for the
  traversal (caught immediately by T007's unit tests failing with an empty member list);
  fixed by requesting `["sAMAccountName", "member"]`.
- **Pre-existing, unrelated test-suite issue discovered during the Phase 7 full-gate run**
  (T023): running the complete `uv run pytest` suite (not just the `ad`-scoped files this
  feature touches) surfaces 15 pre-existing failures in `tests/unit/test_ad_output.py`,
  `tests/unit/test_net_cli.py`, `tests/unit/test_net_output.py`, and
  `tests/unit/test_storage_output.py`. All 15 are the same root cause: those test files'
  own `_console()` helpers build a raw `rich.console.Console(...)` directly instead of via
  `opskit.core.output.make_console` (which already sets `highlight=False`), so rich's
  default `ReprHighlighter` now bolds numbers/paths/parentheses in the captured plain-text
  output — likely surfaced by the `rich>=13,<16` range resolving to a newer 14.x release.
  This is **not caused by this feature**: `pyproject.toml`/`uv.lock` are byte-identical to
  `origin/main` on this branch, none of the four affected test files were touched by
  008-ad-enhancements, and the two affected `ad` cases (`TestRenderStatus`,
  `TestRenderObject`) exercise `render_status`/`render_object`/`_summarize`, none of which
  this feature modifies (`render_group_members` — the one function this feature adds to
  `output.py` — has no failing test). Every `ad`-scoped test this feature added or touched
  passes; the 15 failures are pre-existing on `main` and are a separate, follow-up fix (add
  `highlight=False` to each affected test file's `_console()` helper, or standardize them
  on `make_console`) — out of scope for this feature to fix.
