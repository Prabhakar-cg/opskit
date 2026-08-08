# Python API Contract Delta: `opskit.ad` (008-ad-enhancements)

Delta over `specs/004-ad-diagnostics/contracts/python-api.md`, which remains the baseline.
All additions are additive (MINOR SemVer). Library rules unchanged: no `print`, no
`sys.exit`, no global mutable state, no env/config auto-reads.

## New: `AdClient.members()` / `ad.members()`

```python
with ad.AdClient(cfg) as client:
    report = client.members("VPN Users", effective=True)   # -> GroupMembersReport (default)
    direct = client.members("VPN Users", effective=False)  # direct members only
```

```python
ad.members(group, *, effective=True, server=..., domain=..., ...) -> GroupMembersReport
```

Signature mirrors `AdClient.membership()`/`ad.membership()` exactly, with the direction
reversed (a group in, its members out) and `effective` **defaulting to `True`** — the
inverse of `membership()`'s `effective=False` default (see data-model.md). Same keyword
conventions as every other convenience function: mirrors `DirectoryConfig` fields, accepts a
prebuilt `config=`, accepts `session_factory=` for test injection.

Raises the same class of resolution errors as `show()`/`membership()`:
`PrincipalNotFound`/`AmbiguousPrincipal` if `group` doesn't resolve to exactly one group
object; connection/auth errors from the shared hierarchy otherwise. Never raises for an
empty (zero-member) result — that's a valid `GroupMembersReport` with `members=()`.

## New types: `GroupMemberEntry`, `GroupMembersReport`

See `data-model.md` for full field tables. Both are frozen dataclasses with `to_dict()`,
exported from `opskit.ad.__init__.__all__` alongside the existing model exports.

## New error: `PrincipalIsGroup`

```python
from opskit.ad import PrincipalIsGroup  # subclass of AdError, exit_code = ExitCode.NOT_FOUND
```

Raised instead of `PrincipalNotFound` by `AdClient.user_status()`, `AdClient.membership()`,
and `AdClient.is_member()`'s principal-argument resolution, specifically when the given
identifier resolves to exactly one group and no user/computer account. Carries the resolved
group's name/DN and a hint pointing at `members()`/`is_member()` (the API-level equivalents
of the CLI's `ad members`/`ad member`).

## Changed (no signature change): UPN-shaped identifier resolution

`AdClient.user_status()`, `AdClient.membership()`, `AdClient.is_member()` (principal
argument), and `AdClient.show()` now resolve any `@`-containing identifier against either
`userPrincipalName` or `mail`, not `userPrincipalName` alone. `AmbiguousPrincipal` is raised
under the same condition as before (more than one distinct object matches); a single object
matching via either or both attributes still resolves without raising.

## Changed (no signature change): certificate-verification error hint text

No public signature changes. `CertificateInvalid.hint` (raised from the connection layer,
surfaced through `AdClient.check()` and any operation that opens a connection) now includes
the WSL trust-store note unconditionally and the StartTLS clarification when the client was
configured with `security="starttls"`.
