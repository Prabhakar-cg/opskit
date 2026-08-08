# Quickstart Validation: AD Diagnostics Enhancements

Prerequisites: `uv sync --extra dev` (installs the `ad` extra's `ldap3`); a directory to
test against — either a real domain controller you're authorized to query (set
`OPSKIT_AD_SERVER`/`OPSKIT_AD_DOMAIN`/`OPSKIT_AD_USER`/`OPSKIT_AD_PASSWORD`), or the
project's offline MOCK_SYNC fixture exercised entirely through `pytest` (no live server
needed for the automated suite below — this guide's manual scenarios assume a real/lab
directory is available; skip them if none is, the automated tests are the CI gate).

## Automated gate (run first; this is what CI checks)

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy src && uv run pyright
uv run pytest -q --cov-fail-under=90
```

Expect: all green, coverage ≥ 90%, and specifically these new/extended test cases passing:
`tests/unit/test_ad_api.py` (group-redirect, UPN/mail matching, `members()` direct/nested/
cycle cases), `tests/unit/test_ad_cli.py` (`ad members` envelope + exit codes),
`tests/unit/test_ad_directory.py` (StartTLS/WSL hint-text assertions),
`tests/unit/test_ad_models.py` (`GroupMemberEntry`/`GroupMembersReport` shapes),
`tests/integration/test_ad_mock_directory.py` (the extended fixture scenarios).

## Manual scenario 1 — group-redirect error (User Story 1)

Against a directory with a group `<name>` and no user/computer account by that name:

```bash
opskit ad groups <name>
```

Expected: exit 16; error text names `<name>` as a group and suggests
`opskit ad members <name>` and `opskit ad member <principal> <name>` — not the generic
"no user or computer account found" message.

```bash
opskit ad groups <a-truly-nonexistent-name>
```

Expected: exit 16; the original generic not-found message, unchanged (regression check).

## Manual scenario 2 — effective group membership (User Story 2)

Against a group `G1` with a direct user member and a nested child group with its own member:

```bash
opskit ad members G1
opskit ad members G1 --direct
opskit ad members G1 --json
```

Expected: the first lists both the direct member and the nested member (with its path); the
second lists only the direct member; the third's JSON envelope has `command: "ad.members"`
and a `result.members[]` array with `via`/`path`/`object_type` per entry. Against a group
with a nested cycle back to itself: the command completes (no hang/crash) and each member
appears once.

## Manual scenario 3 — UPN/mail matching (User Story 3)

Against a user whose `mail` differs from their `userPrincipalName`:

```bash
opskit ad show <their-mail-address>
```

Expected: resolves to that user (previously would have reported not-found). Against two
different accounts where one's `userPrincipalName` and the other's `mail` both equal the
same string: the same lookup now reports the existing ambiguous-match error listing both.

## Manual scenario 4 — TLS hint text (User Story 4)

Against a server presenting a certificate not trusted by the platform store:

```bash
opskit ad check <server> --starttls
```

Expected: the failure's hint text states that `--starttls` does not bypass certificate
verification, and separately notes that WSL doesn't inherit the Windows trust store.
Re-check `src/opskit/ad/README.md` for the new "Trusting a corporate CA" section describing
`--ca-file`.

## Regression check (SC-005)

Run the existing `ad check`/`ad user`/`ad show`/`ad groups`/`ad member` invocations from
`specs/004-ad-diagnostics/quickstart.md` against the same fixtures/server used there; every
one of them must produce identical output/exit codes to before this feature, since none of
them exercise the four new/changed code paths above.
