# CLI Contract Delta: `opskit ad` (008-ad-enhancements)

This is a **delta** over `specs/004-ad-diagnostics/contracts/cli.md`, which remains the
baseline for everything not listed here. All changes are additive (MINOR SemVer). Envelope
`schema_version` stays `"1"`; command names gain one new entry: `ad.members`.

## New command: `opskit ad members GROUP... [-i FILE] [--direct]`

The `member`-direction mirror of `ad groups`: reports every account that ultimately belongs
to a group.

- **Positionals / batch input**: one or more group identifiers (name, `user@domain`-style
  is not meaningful here but DN is accepted), or `-i/--input-file PATH` (`-` = stdin, one
  per line, `#` comments) — **batchable**, matching `ad user`/`ad show`'s contract exactly:
  every target is resolved over one authenticated session, a failure on one target never
  aborts the others, `--jsonl` emits one envelope per group including failures
  (`result: null`, `error` populated). Exit: the standard batch rule — 0 all-ok / uniform
  class / 7 PARTIAL.
- **`--direct`**: report only members listed directly on the group (no nested expansion).
  Default (no flag): nested/effective expansion, cycle-safe, each member reported once at
  its shortest acquisition path. This default is the **inverse** of `ad groups`'s
  direct-by-default / `-e/--effective`-opt-in convention — see data-model.md's note.
- Shared connection options (`-s/--server`, `-d/--domain`, `-U/--user`, `--starttls`,
  `--plaintext`, `--ca-file`, `--base-dn`, `--timeout`, `--json`/`--jsonl`/`--no-color`) are
  identical to every other `ad` command.
- Human output renders as a rich table per group (name, object type, via, and — for
  effective/default mode only, matching `render_membership`'s existing convention of
  showing `path` only under `--effective` — path); `--direct` output omits the path column
  entirely rather than showing it empty. Every directory-derived string is
  `rich.markup.escape()`d.
- Result: `GroupMembersReport`. Errors: same class-scoped resolution errors as
  `ad show --type group` (`PrincipalNotFound`/`AmbiguousPrincipal` when the group identifier
  itself doesn't resolve to exactly one group); connection/auth errors unchanged.

## Changed: principal-scoped commands' not-found error (`ad user`, `ad groups`, `ad member`)

No CLI surface change (no new flags) — the *content* of one error case changes. When the
principal argument to `ad user PRINCIPAL`, `ad groups PRINCIPAL`, or `ad member PRINCIPAL
GROUP` resolves to no user/computer account but resolves to exactly one group, the command
now exits with the new `PrincipalIsGroup` error (still exit 16, `NOT_FOUND` — no exit-code
change) instead of the generic `PrincipalNotFound`. The error's `message`/`hint` name the
group and point at `ad members`/`ad member`. Every other not-found case (identifier matches
nothing at all) is unchanged.

## Changed: UPN-shaped identifier resolution (all commands accepting a principal)

No CLI surface change. Any identifier already treated as UPN-shaped (contains `@`) — as
accepted by `ad user`, `ad groups`, `ad member`'s principal argument, and `ad show` — now
also matches against the directory's `mail` attribute, not `userPrincipalName` alone. The
existing ambiguous-match error (exit 2, usage class) still applies if the widened match
resolves to more than one distinct object.

## Changed: certificate-verification failure hint text

Applies to `ad check` and any other command that connects (e.g. `ad user`/`ad groups`/
`ad member`/`ad show`/`ad members`).

No CLI surface change, no exit-code change (`CertificateInvalid`, exit 10, unchanged). The
hint text accompanying a certificate verification failure now always notes that a WSL/Linux
environment does not inherit the Windows certificate trust store, and additionally notes
that `--starttls` does not bypass certificate verification when `--starttls` was the mode in
use.

## Docs

`src/opskit/ad/README.md` gains: an options-table row for `ad members` (mirroring the
existing `ad groups` row), and a new section, "Trusting a corporate CA," describing how to
obtain the corporate root CA PEM and use it via `--ca-file` (FR-013). No change to the root
README's Commands table is needed — its existing `opskit ad` row and link to
`src/opskit/ad/README.md` already covers the new command.
