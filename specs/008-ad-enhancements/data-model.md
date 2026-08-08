# Phase 1 Data Model: AD Diagnostics Enhancements

This feature adds two new dataclasses and one new error type to `src/opskit/ad/`; it does
not modify any existing dataclass's fields or `to_dict()` shape (SC-005: no observable
change to existing behavior). `AccountStatusReport`, `MembershipReport`, `MembershipEntry`,
`MembershipVerdict`, `ObjectSummary`, `ConnectivityReport`, `ServerInfo`, `Stage`,
`DirectoryConfig`, `IdentifierKind` are all unchanged and not repeated here — see
`specs/004-ad-diagnostics/data-model.md` for their definitions.

## New: `GroupMemberEntry`

One account or nested group ultimately found via a group's membership, and how it was
reached. The `member`-direction mirror of the existing `MembershipEntry`
(`memberOf`-direction), with one addition: `object_type`, needed because a group's members
are heterogeneous (users, computers, *and* groups), unlike a principal's group memberships
which are always groups.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | First-RDN display name |
| `dn` | `str` | Distinguished name |
| `object_type` | `str` | `"user"` \| `"computer"` \| `"group"` |
| `via` | `str` | `"direct"` \| `"nested"` |
| `path` | `tuple[str, ...]` | Intermediate group names for a nested entry (empty for direct) |

`to_dict()`: `{"name", "dn", "object_type", "via", "path": list(path)}` — same shape
convention as `MembershipEntry.to_dict()`.

Validation: `via` is always `"direct"` for entries found directly on the queried group's own
`member` attribute, `"nested"` for anything reached only through an intermediate group;
there is no `"primary"` value here (primary-group membership, R7 of 004, is a `memberOf`-
direction concept with no group-side equivalent — a group has no "primary member").

## New: `GroupMembersReport`

The result of `ad members <group>`. The `member`-direction mirror of `MembershipReport`.

| Field | Type | Notes |
|---|---|---|
| `group` | `str` | The identifier as given by the caller |
| `dn` | `str` | The resolved group's DN |
| `effective` | `bool` | `True` unless `--direct` was passed |
| `members` | `tuple[GroupMemberEntry, ...]` | Deduplicated by DN; empty tuple is a valid (non-error) result |

`to_dict()`: `{"group", "dn", "effective", "members": [m.to_dict() for m in members]}`.

Note the `effective` default is the **inverse** of `MembershipReport.effective`'s default:
`ad groups` defaults to direct-only (`effective=False`) with `-e/--effective` opt-in, while
`ad members` defaults to effective/nested (`effective=True`) with `--direct` opt-out — an
intentional asymmetry from the spec (User Story 2, FR-003/FR-004): the most useful default
answer to "who's in this group" already includes nested membership, whereas "what groups is
this account in" is more often asked at the direct level first.

## New error: `PrincipalIsGroup`

Raised by `_find_one()` in place of `PrincipalNotFound` when a principal-scoped lookup
(`kind_filter="principal"`, used by `ad user`, `ad groups`, and the principal argument of
`ad member`) finds no user/computer match but the identifier matches exactly one group.

| Attribute | Value |
|---|---|
| `code` | `"principal_is_group"` |
| `exit_code` | `ExitCode.NOT_FOUND` (16 — reused; no new exit code) |
| `message` | e.g. `"'sg-gs-test-unix' is a group, not a user or computer account"` |
| `hint` | e.g. `"see its members with: opskit ad members sg-gs-test-unix; or test membership with: opskit ad member <principal> sg-gs-test-unix"` |

Reuses `ExitCode.NOT_FOUND` deliberately (see research E1/plan Constitution Check): the
outcome class from a script's point of view is the same as today's ("the thing you asked
for as an X isn't there as an X"); what changes is only the message/hint clarity, which
doesn't need its own exit branch.

## Changed (behavior only, no shape change): identifier matching

`_identifier_clause()`'s `IdentifierKind.UPN` branch now builds
`(|(userPrincipalName={value})(mail={value}))` instead of `(userPrincipalName={value})`.
`IdentifierKind` itself is unchanged (still `DN` \| `UPN` \| `NAME`, per 004) — this is a
filter-string change, not a classification change.

## Changed (behavior only, no shape change): certificate-verification hint text

`directory.classify_connect_error()` gains an optional `security: str = "ldaps"` parameter;
`CertificateInvalid.hint`'s text gains a WSL trust-store clause unconditionally and a
StartTLS clause when `security == "starttls"`. `CertificateInvalid` itself (class, exit code
10, from `opskit.tls.errors`) is unchanged — only the hint string passed to it changes.
