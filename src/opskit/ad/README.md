# `opskit ad` — Active Directory / LDAP diagnostics

Read-only directory troubleshooting, identical on Windows/macOS/Linux — the answers
`Get-ADUser`, `net user /domain`, and hand-written `ldapsearch` filters give, without
needing any of them installed. Five commands: `check` (staged connectivity/bind
verdict), `user` (why can't this account sign in?), `groups` (direct/effective
membership), `member` (is P in G?), and `show` (key attributes of users, groups,
computers — bidirectional: a user's email and facts, a group's member list).

Strictly diagnostic by design: only bind and search operations are ever sent — **no
writes, no unlocks, no password testing, no wildcard/filter enumeration**. Every query
names its principals explicitly, and one invocation uses exactly one credential.

Requires the category extra:

```bash
pip install "opskit[ad]"      # pulls ldap3 (the base install stays slim)
```

## Contents

- [Quick start](#quick-start)
- [Connection & credentials](#connection--credentials)
- [`opskit ad check`](#opskit-ad-check)
- [`opskit ad user`](#opskit-ad-user)
- [`opskit ad groups`](#opskit-ad-groups)
- [`opskit ad member`](#opskit-ad-member)
- [`opskit ad show`](#opskit-ad-show)
- [Exit codes](#exit-codes)
- [Output](#output)
- [Use as a Python library](#use-as-a-python-library)

## Quick start

```bash
export OPSKIT_AD_DOMAIN=corp.example.com          # DCs found via DNS SRV
export OPSKIT_AD_USER=ops@corp.example.com        # password: prompt or env

opskit ad check                                   # can I reach + bind at all?
opskit ad user jdoe                               # why can't jdoe sign in?
opskit ad groups jdoe --effective                 # what can jdoe access?
opskit ad member jdoe "VPN Users"                 # the pointed question
opskit ad show "VPN Users"                        # who's in this group?
```

## Connection & credentials

Every command takes the same connection options (or their `OPSKIT_AD_*` environment
variables, so you set them once per shell):

| Option | Env | Meaning |
|---|---|---|
| `-s, --server HOST[:PORT]` | `OPSKIT_AD_SERVER` | explicit directory server (wins over domain) |
| `-d, --domain NAME` | `OPSKIT_AD_DOMAIN` | discover DCs from DNS SRV (`_ldap._tcp.dc._msdcs.` then `_ldap._tcp.`) |
| `-U, --user ACCOUNT` | `OPSKIT_AD_USER` | bind account: `user@domain`, `DOMAIN\name`, or DN; omit = anonymous |
| *(password)* | `OPSKIT_AD_PASSWORD` | **there is no `--password` flag** (process lists, shell history) — env var, or a hidden prompt when running interactively |
| `--starttls` | | plain connect on 389, then a mandatory TLS upgrade |
| `--plaintext` | | no TLS (lab use only) — required to send a password unencrypted; output is marked *not encrypted* |
| `--ca-file PEM` | | private-PKI trust bundle (replaces the platform store) |
| `--base-dn DN` | | search base (default: the server's `defaultNamingContext`) |
| `--timeout SECONDS` | | connect/per-operation timeout (default 5) |

Connections are **LDAPS on port 636 by default**, with certificates verified against
the platform trust store. Note for `--domain` discovery: SRV records advertise port
389, so opskit uses the discovered *hostnames* with the security mode's port (636 for
LDAPS) — pass `--server host:port` to override. Certificate problems point you at
`opskit tls check` for deep inspection.

## `opskit ad check`

The first isolation step of any directory incident: reached → secured → authenticated,
each stage timed, so a network problem, a TLS problem, and a credential problem are
never confused.

```bash
opskit ad check dc01.corp.example.com
opskit ad check -d corp.example.com        # discovery: reports which DC answered
opskit ad check dc01 --starttls --ca-file corp-root.pem
```

A rejected bind decodes Active Directory's reason sub-code into the hint —
`account locked out`, `password expired`, `account disabled` — turning the check
itself into a sign-in diagnostic.

## `opskit ad user`

Account-status verdict: enabled/disabled, locked out (with when), password expired /
expires / never expires, must-change-at-next-sign-in, account expiry, password last
set. **Every** active blocker is listed, not just the first.

```bash
opskit ad user jdoe
opskit ad user jdoe asmith svc-backup --jsonl     # batch, one bind for all
opskit ad user -i users.txt --jsonl               # file input (# comments ok)
cat users.txt | opskit ad user -i - --jsonl       # stdin
opskit ad user jdoe --watch 30s                   # watch a lockout clear
```

Batch runs process every principal (failures included in machine output, never
aborting) over a single authenticated session.

## `opskit ad groups`

A principal's memberships: direct by default (always including the **primary group**,
which directories store separately), `--effective`/`-e` to resolve nesting — cycle-safe,
each group once, with the shortest acquisition path shown.

```bash
opskit ad groups jdoe
opskit ad groups jdoe --effective
opskit ad groups wks-042$ --json        # computers are principals too
```

## `opskit ad member`

The explicit yes/no: is this principal in this group, and through what chain?

```bash
opskit ad member jdoe "VPN Users"           # exit 0: member (chain shown)
opskit ad member jdoe "Domain Admins"       # exit 17: not a member
```

Scriptable by exit code: `0` member, `17` not a member, other codes = the query
itself failed.

## `opskit ad show`

Key attributes of named objects, in both directions: a **user's** email address,
display name, title, department; a **group's** kind and complete direct member list
(server paging/ranging followed — never truncated); a **computer's** DNS name and OS.
Batchable with mixed types.

```bash
opskit ad show jdoe
opskit ad show "VPN Users" --type group
opskit ad show jdoe "VPN Users" wks-042$ --jsonl   # mixed batch, one session
printf 'jdoe\nVPN Users\n' | opskit ad show -i -
```

A name matching more than one object is refused with the candidates listed
(disambiguate with a DN) — never a silent first-match.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (including "is a member") |
| 1 | unclassified directory failure |
| 2 | usage error / ambiguous name / missing `opskit[ad]` extra / cleartext refused |
| 3 | DC discovery failed / server name unresolvable |
| 6 | no response before timeout |
| 7 | mixed batch outcomes (partial) |
| 8 | connection refused |
| 9 | TLS handshake / StartTLS upgrade failed |
| 10 | server certificate failed verification |
| 14 | bind rejected (hint decodes the AD reason) |
| 15 | bound, but not authorized to read |
| 16 | principal/group/object not found |
| 17 | membership test verdict: not a member |

## Output

Human output renders list-shaped results as tables; `--json` emits the versioned
envelope (`schema_version`, `query`, `result`, `error`, `elapsed_ms`); `--jsonl`
streams one envelope per name on the batchable commands (`user`, `show`), failures
included. `NO_COLOR` and piped output are honored. **Credentials never appear in any
output, log, or envelope** — the query echo records the bind account name only.

## Use as a Python library

```python
from opskit import ad

cfg = ad.DirectoryConfig(
    server="dc01.corp.example.com",
    bind_user="ops@corp.example.com",
    password=password,                    # excluded from repr; never serialized
)

with ad.AdClient(cfg) as client:          # one bind, reused for every call
    report = client.check()
    status = client.user_status("jdoe")
    if status.blockers:
        print("blocked:", ", ".join(status.blockers))
    verdict = client.is_member("jdoe", "VPN Users")

# or one-shot convenience functions:
groups = ad.membership("jdoe", effective=True, config=cfg)
```

Typed results (`AccountStatusReport`, `MembershipReport`, `MembershipVerdict`,
`ObjectSummary`, `ConnectivityReport`) with `to_dict()`; typed errors
(`AuthenticationFailed`, `PrincipalNotFound`, `PermissionDenied`, …). The library
never prints, never exits, and never reads environment or config files. `import
opskit.ad` works without the extra installed; the first directory operation raises
`DependencyMissing` with the install hint.
