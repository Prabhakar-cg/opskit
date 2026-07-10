# Phase 1 Data Model: Active Directory / LDAP Diagnostics

All result models are frozen stdlib dataclasses in `src/opskit/ad/models.py` with
`to_dict()` (JSON-envelope `result` payloads) following the established category pattern.
Times are timezone-aware UTC `datetime`s in Python, ISO-8601 strings in JSON; "never"
sentinels serialize as JSON `null` with a paired `*_never: true` flag where the distinction
matters. Field names below are the JSON names.

## DirectoryConfig (input model, not serialized)

How to reach and authenticate to the directory. Built only by the CLI (from flags/env) or
by the API caller explicitly — never from auto-read config (Art. VII).

| Field | Type | Notes |
|---|---|---|
| `server` | `str \| None` | explicit `host` / `host:port`; wins over `domain` |
| `domain` | `str \| None` | SRV discovery input; exactly one of server/domain required |
| `security` | `"ldaps" \| "starttls" \| "plaintext"` | default `ldaps` (R2) |
| `port` | `int \| None` | default by security mode: 636 / 389 / 389 |
| `bind_user` | `str \| None` | UPN, `DOMAIN\name`, or DN; `None` = anonymous bind |
| `allow_cleartext` | `bool` | explicit opt-in required to combine a password with `security="plaintext"` (the CLI's `--plaintext` sets both); otherwise `CleartextRefused` |
| `password` | `str \| None` | **excluded from `repr()` and every serialization** (dataclass `repr=False`; no `to_dict`) |
| `ca_file` | `path \| None` | PEM bundle replacing the platform trust store |
| `base_dn` | `str \| None` | overrides rootDSE `defaultNamingContext` |
| `timeout` | `float` | default 5.0 (connect + per-operation) |

Validation: server/domain mutual requirement; `password` without TLS requires
`security == "plaintext"` having been *explicitly* selected (else `CleartextRefused`);
timeout > 0. All checked before any network I/O.

## ConnectivityReport (`ad check`)

Staged verdict. `stages` is ordered; a failed run still reports the stages that completed
(the error carries the failing stage's class).

| Field | Type | Notes |
|---|---|---|
| `server_used` | `str` | host actually connected (discovery-resolved or explicit) |
| `port` | `int` | |
| `security` | `str` | mode actually in effect |
| `discovered` | `bool` | true when the server came from SRV discovery |
| `candidates_tried` | `list[str]` | discovery candidates attempted before success |
| `stages` | `list[Stage]` | see below; order: `reached`, `secured`, `authenticated` |
| `bind_user` | `str \| None` | account name echo; `null` for anonymous |
| `server_info` | `ServerInfo` | rootDSE basics: `default_naming_context`, `dns_host_name`, `supports_starttls`, `vendor` (each `str \| None`) |

**Stage**: `{"name": str, "ok": bool, "elapsed_ms": float}`. In plaintext mode the stage
list contains `reached`, `authenticated` only (there is no `secured` stage) and the report
carries `"encrypted": false`, rendered prominently (FR-002). Under LDAPS the `reached`
stage is proven with a plain connect (closed immediately) before the TLS open, so the
three stages are individually attributable.

## AccountStatusReport (`ad user`)

One principal's status facts. Every fact is tri-state: a value, `null` +
`facts_unavailable` listing (non-AD degradation, FR-009).

| Field | Type | Notes |
|---|---|---|
| `principal` | `str` | as the user supplied it |
| `dn` | `str` | resolved distinguished name |
| `sam_account_name` / `user_principal_name` | `str \| None` | |
| `enabled` | `bool \| None` | UAC 0x2 inverted |
| `locked` | `bool \| None` | computed UAC 0x10 preferred (R5) |
| `lockout_time` | `datetime \| None` | recorded lockout instant |
| `lockout_stale_possible` | `bool` | true when only raw `lockoutTime` was available |
| `password_expired` | `bool \| None` | computed UAC 0x800000 |
| `password_expires_at` | `datetime \| None` | `null` + `password_never_expires: true` for never |
| `password_never_expires` | `bool \| None` | |
| `must_change_password` | `bool \| None` | `pwdLastSet == 0` |
| `password_last_set` | `datetime \| None` | |
| `account_expires_at` | `datetime \| None` | `null` + `account_never_expires: true` for never |
| `account_never_expires` | `bool \| None` | |
| `account_expired` | `bool \| None` | derived: expiry in the past |
| `blockers` | `list[str]` | `disabled`, `locked_out`, `password_expired`, `must_change_password`, `account_expired` — all that apply (spec US1 sc.5); empty = signs in clean |
| `facts_unavailable` | `list[str]` | field names degraded on this server |

Watch signature (`ad user --watch`): JSON of `(principal, blockers, enabled, locked,
password_expired, account_expired)` — expiry *instants* and timings excluded.

## MembershipReport / MembershipEntry (`ad groups`)

| Field | Type | Notes |
|---|---|---|
| `principal` / `dn` | `str` | as above |
| `effective` | `bool` | whether nesting was resolved |
| `groups` | `list[MembershipEntry]` | sorted: direct+primary first, then nested by path length |

**MembershipEntry**: `{"name": str, "dn": str, "via": "direct" | "nested" | "primary",
"path": list[str]}` — `path` is the shortest granting chain of group names starting at a
direct group (empty for `direct`/`primary`). Each group appears once (cycle rule).

## MembershipVerdict (`ad member`)

| Field | Type | Notes |
|---|---|---|
| `principal` / `principal_dn` | `str` | |
| `group` / `group_dn` | `str` | |
| `member` | `bool` | drives exit 0 vs 17 |
| `via` | `"direct" \| "nested" \| "primary" \| None` | `None` when not a member |
| `path` | `list[str]` | granting chain (empty for direct/primary) |

## ObjectSummary (`ad show`)

| Field | Type | Notes |
|---|---|---|
| `name` / `dn` | `str` | |
| `object_type` | `"user" \| "group" \| "computer"` | resolved type (auto or requested) |
| `identifiers` | dict | `sam_account_name`, `user_principal_name`, `sid` (each `str \| None`) |
| `created` / `changed` | `datetime \| None` | `whenCreated` / `whenChanged` |
| `description` | `str \| None` | |
| `type_facts` | dict | per type — user: `display_name`, `mail`, `title`, `department`; group: `group_kind` (security/distribution + scope), `members` (`list[{name, dn}]`, complete via paging/ranging); computer: `dns_host_name`, `operating_system`, `os_version` |

## Error model (ad/errors.py) & exit codes

Additive `ExitCode` members: `AUTH_FAILED = 14`, `PERMISSION_DENIED = 15`,
`NOT_FOUND = 16`, `NOT_MEMBER = 17` (17 is a verdict code used by the CLI, not an error's).

| Error | code | exit | Raised when |
|---|---|---|---|
| `AdError` (base) | `ad_error` | 1 | unclassified directory failure (normalized ldap3/OSError) |
| `DependencyMissing` | `dependency_missing` | 2 | ldap3 not installed; hint `pip install "opskit[ad]"` |
| `CleartextRefused` | `cleartext_refused` | 2 | password given without TLS and without `--plaintext` |
| `AmbiguousPrincipal` | `ambiguous_principal` | 2 | >1 match; message lists candidate DNs |
| `DiscoveryError` | `discovery_failed` | 3 | no SRV records / domain unresolvable |
| `AuthenticationFailed` | `auth_failed` | 14 | bind rejected; hint decodes AD `data` sub-code (R3) |
| `PermissionDenied` | `permission_denied` | 15 | bound but not authorized for the query |
| `PrincipalNotFound` | `principal_not_found` | 16 | zero matches |
| (reused) `net.ConnectRefused` / `net.ConnectTimeout` | | 8 / 6 | reach stage |
| (reused) `tls.HandshakeError` / `tls.CertificateInvalid` | | 9 / 10 | secure stage |

## Relationships & state

Stateless throughout; the only stateful object is the API-level `AdClient` (holds a
`DirectoryConfig` and a lazily opened, reusable authenticated connection; context manager;
never global — Art. VII). All reports are pure values derived from one query's responses.
