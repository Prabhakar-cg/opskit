# CLI Contract: `opskit ad`

Public, SemVer-governed surface. All additions are MINOR. Envelope `schema_version` stays
`"1"`; command names `ad.check`, `ad.user`, `ad.groups`, `ad.member`, `ad.show`.

## Shared connection options (every `ad` command)

| Option | Env | Default | Notes |
|---|---|---|---|
| `-s, --server HOST[:PORT]` | `OPSKIT_AD_SERVER` | — | explicit server; wins over `--domain` |
| `-d, --domain NAME` | `OPSKIT_AD_DOMAIN` | — | SRV DC discovery (`_ldap._tcp.dc._msdcs.` then `_ldap._tcp.`); exactly one of server/domain must be given (for `ad check` the positional counts as `--server`) |
| `-U, --user ACCOUNT` | `OPSKIT_AD_USER` | anonymous | bind account: UPN, `DOMAIN\name`, or DN |
| *(password)* | `OPSKIT_AD_PASSWORD` | prompt | **no flag exists**; env, else hidden interactive prompt (only when `--user` given and stdin is a TTY); piped stdin without the env var → usage error naming `OPSKIT_AD_PASSWORD` |
| `--starttls` | | off | plain connect then mandatory TLS upgrade (port default 389) |
| `--plaintext` | | off | no TLS (port default 389); required to send a password unencrypted; output carries `"encrypted": false` and a visible warning |
| `--ca-file PATH` | | platform store | PEM bundle replacing the trust store (private PKI) |
| `--base-dn DN` | | rootDSE `defaultNamingContext` | search-base override |
| `--timeout SECONDS` | | `5.0` | connect + per-operation |
| `--json` / `--no-color` | | | standard output contract; `--jsonl` on the batchable `ad user`/`ad show` |

Human output renders list-shaped results (status facts, membership lists, group members,
batch results) as rich **tables** (FR-015); every directory-derived string markup-escaped.

Default security mode is LDAPS on port 636. Explicit `:port` on the server always wins.

## Commands

### `opskit ad check [SERVER]`

Staged connectivity/bind verdict (reached → secured → authenticated) with per-stage timing,
server identity basics, and — under discovery — which candidate DC was used.
Result: `ConnectivityReport`. Exit: 0 all stages ok; else the failing stage's class
(8 refused / 6 timeout / 9 TLS handshake / 10 certificate / 14 auth / 3 discovery).

### `opskit ad user [PRINCIPALS]... [-i FILE] [--watch SPEC]`

Account-status verdict per principal (enabled, lockout, password expiry, account expiry,
password-last-set, all active blockers). **Batchable**: variadic positionals +
`-i/--input-file` (one per line, `#` comments, `-` = stdin), all processed over one
authenticated session, never aborting on failure. `--jsonl` emits one envelope per
principal; failures included (`result: null`, `error` populated). Exit: batch rule —
0 all-ok / uniform class / 7 PARTIAL. `--watch` re-runs on the interval grammar
(`30s`, `2m`), flagging blocker/fact changes. Result: `AccountStatusReport`.

### `opskit ad groups PRINCIPAL [-e/--effective]`

Direct memberships (plus primary group, marked); `--effective` resolves nesting with
cycle-safe traversal, marking each entry `direct`/`nested`/`primary` with the shortest
granting path. Complete under server paging/ranging. Result: `MembershipReport`. Exit: 0 on
success (an empty list is a successful answer); error classes otherwise.

### `opskit ad member PRINCIPAL GROUP`

Explicit membership test. Human output: verdict + granting chain. Result:
`MembershipVerdict`. Exit: **0 member / 17 not-a-member**; error classes otherwise.

### `opskit ad show [NAMES]... [-i FILE] [--type user|group|computer|auto]`

Key attributes per named object (identifiers, location, created/changed, description,
type-specific facts — user: email/`mail`, display name, title, department; group: kind +
complete direct member list; computer: dNS name, OS). Default `--type auto` searches the
three types (applies to every name in the run); cross-type ambiguity → the standard
ambiguity error. **Batchable** exactly like `ad user`: variadic positionals +
`-i/--input-file` (`-` = stdin), users and groups mixable in one run, all processed over one
authenticated session; `--jsonl` emits one envelope per name, failures included; exit
follows the batch rule. Human output renders one attribute table per object (group members
as a nested table). Result: `ObjectSummary`.

## Exit codes (category view)

| Code | Class | Meaning here |
|---|---|---|
| 0 | OK | success (incl. "is a member") |
| 1 | ERROR | unclassified directory failure |
| 2 | USAGE | bad input; ambiguous principal; cleartext refused; missing extra |
| 3 | NXDOMAIN | DC discovery failed / server name unresolvable |
| 6 | TIMEOUT | server didn't answer |
| 7 | PARTIAL | mixed batch outcomes |
| 8 | CONNECT_FAILED | connection refused |
| 9 | HANDSHAKE_FAILED | TLS establishment/StartTLS upgrade failed |
| 10 | CERT_INVALID | server certificate failed verification |
| **14** | **AUTH_FAILED** | bind rejected (hint decodes AD reason sub-code) |
| **15** | **PERMISSION_DENIED** | bound but not authorized to read |
| **16** | **NOT_FOUND** | principal/group/object does not exist |
| **17** | **NOT_MEMBER** | membership test verdict: not a member |

Codes 14–17 are new (additive). Failures go to stderr in human mode; in `--json`/`--jsonl`
every input's envelope appears on stdout including failures.

## Envelope examples

```json
{"schema_version": "1", "command": "ad.user",
 "query": {"principal": "jdoe", "server": "dc01.corp.example.com", "port": 636,
           "security": "ldaps", "bind_user": "ops@corp.example.com"},
 "result": {"dn": "CN=J Doe,OU=Staff,DC=corp,DC=example,DC=com", "enabled": true,
            "locked": true, "lockout_time": "2026-07-10T08:12:33Z",
            "password_expired": false, "password_expires_at": "2026-09-01T00:00:00Z",
            "blockers": ["locked_out"], "...": "..."},
 "error": null, "elapsed_ms": 84.2}
```

The `query` object **never contains a password field** (guaranteed by construction:
`DirectoryConfig.password` has no serialization path; verified by the suite-wide redaction
scan).

Human output follows the standard contract: rich tables/lines, `NO_COLOR` and pipe
detection via `make_console`, every directory-derived string markup-escaped.
