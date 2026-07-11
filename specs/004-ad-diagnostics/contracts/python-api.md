# Python API Contract: `opskit.ad`

Public, typed, SemVer-governed surface (all additive — MINOR). Library rules apply: no
`print`, no `sys.exit`, no global mutable state, no env/config auto-reads; `py.typed`
already ships. `import opskit.ad` succeeds without the extra installed; the first operation
needing ldap3 raises `DependencyMissing`.

## Configuration & session

```python
from opskit import ad

cfg = ad.DirectoryConfig(
    server="dc01.corp.example.com",      # or domain="corp.example.com" for discovery
    security="ldaps",                    # "ldaps" (default) | "starttls" | "plaintext"
    bind_user="ops@corp.example.com",
    password=password,                    # excluded from repr/serialization
    ca_file=None, base_dn=None, timeout=5.0,
    # security="plaintext" plus a password additionally requires allow_cleartext=True
)

with ad.AdClient(cfg) as client:          # one bind, reused for every call
    report = client.check()               # -> ConnectivityReport
    status = client.user_status("jdoe")   # -> AccountStatusReport
    groups = client.membership("jdoe", effective=True)   # -> MembershipReport
    verdict = client.is_member("jdoe", "VPN Users")      # -> MembershipVerdict
    obj = client.show("VPN Users", object_type="group")  # -> ObjectSummary
```

`AdClient` opens the connection lazily on first use, is a context manager (`close()`
otherwise), and is not thread-safe (one client per thread — documented).

## Convenience functions

One-shot equivalents that build a throwaway client (requests-style, mirroring
`dns.lookup` / `tls.check`):

```python
ad.check(server="dc01:636", bind_user=..., password=...) -> ConnectivityReport
ad.user_status(principal, *, server=..., domain=..., ...) -> AccountStatusReport
ad.membership(principal, *, effective=False, ...) -> MembershipReport
ad.is_member(principal, group, ...) -> MembershipVerdict
ad.show(name, *, object_type="auto", ...) -> ObjectSummary
```

Keyword arguments mirror `DirectoryConfig` fields; passing a prebuilt `config=` is also
supported. Exactly one of `server`/`domain` is required (`UsageError` otherwise, before any
I/O). `AdClient` and every convenience function also accept a `session_factory=` testing
seam (mirroring `directory.connect_session`'s signature), the ad analogue of dns's
`resolver=` injection.

## Results

Frozen dataclasses per [data-model.md](../data-model.md): `ConnectivityReport`,
`AccountStatusReport`, `MembershipReport`/`MembershipEntry`, `MembershipVerdict`,
`ObjectSummary` — each with `to_dict()` producing the envelope `result` payload. Datetimes
are aware-UTC; "never" expiries are `None` with the paired `*_never` flag.

## Errors

All raise from the shared hierarchy (`opskit.core.errors.OpskitError`); each owns its exit
code. From `opskit.ad.errors`: `AdError`, `DependencyMissing`, `CleartextRefused`,
`AmbiguousPrincipal`, `DiscoveryError`, `AuthenticationFailed`, `PermissionDenied`,
`PrincipalNotFound`. Reused: `opskit.net.errors.ConnectRefused`/`ConnectTimeout`,
`opskit.tls.errors.HandshakeError`/`CertificateInvalid`, `opskit.core.errors.UsageError`.
`MembershipVerdict(member=False)` is a **return value**, not an exception. No ldap3
exception, raw `OSError`, or ssl error ever escapes (Art. VI).

## Guarantees

- **Read-only**: only bind and search operations are ever sent; nothing in this API can
  modify the directory (Art. X).
- **Redaction**: `password` never appears in `repr()`, `to_dict()`, exception messages, or
  log records (`logging.getLogger("opskit")`).
- **No hidden I/O**: network activity is exactly the requested query plus (when `domain` is
  used) the SRV discovery lookup; nothing else (Art. VIII).
- **Explicit config only**: the API never reads `OPSKIT_*` env vars or config files —
  that's the CLI's job (Art. VII / config precedence).

## Stability

`opskit.ad` public names above are the contract. `ad.directory`, `ad.attributes`, and
`ad.discovery` are internal (no stability promise) — the ldap3 adapter may be replaced
without a MAJOR bump provided these names and behaviors hold.
