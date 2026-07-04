# Contract: Python API — `opskit.dns`

API-first: the CLI is a client of this. The library **raises typed exceptions**, never `print()`s
or `sys.exit()`s, holds no global state, and ships `py.typed`. Signatures are illustrative
(implementation in `/speckit-implement`); they define the public contract governed by SemVer.

## Public surface (`opskit.dns.__all__`)

```python
from opskit.dns import (
    lookup, reverse, lookup_all, compare, trace, reverse_trace,  # convenience functions
    DnsQuery, DnsRecord, LookupResult, Resolver,                 # models
    ResolverAnswer, ResolverComparison, TraceStep,              # models (compare/trace)
    RecordType, Transport, Outcome,                             # enums
    DnsError, NxDomain, ServerFailure, DnsRefused, DnsTimeout, DnssecError,  # errors
)
```

> The configurable `DnsClient` + `lookup_many()` below are **planned** (task T036) and not yet in
> `__all__`; today the convenience functions above are the shipped public surface.

## Convenience functions

```python
def lookup(
    target: str,
    types: Sequence[RecordType | str] = ("A",),
    *,
    server: str | Sequence[str] | None = None,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
    trace: bool = False,
) -> LookupResult: ...

def reverse(
    ip: str,
    *,
    server: str | Sequence[str] | None = None,
    transport: Transport | str = "auto",
    timeout: float = 5.0,
    retries: int = 2,
    port: int = 53,
) -> LookupResult: ...

def compare(
    target: str,
    servers: Sequence[str],
    types: Sequence[RecordType | str] = ("A",),
    **query_opts: object,
) -> ResolverComparison: ...
```

## Configurable client (reuse / bulk)

```python
class DnsClient:
    def __init__(
        self,
        *,
        server: str | Sequence[str] | None = None,
        transport: Transport | str = "auto",
        timeout: float = 5.0,
        retries: int = 2,
        port: int = 53,
    ) -> None: ...

    def lookup(self, target: str, types: Sequence[RecordType | str] = ("A",),
               *, trace: bool = False) -> LookupResult: ...
    def reverse(self, ip: str) -> LookupResult: ...
    def compare(self, target: str, servers: Sequence[str],
                types: Sequence[RecordType | str] = ("A",)) -> ResolverComparison: ...
    def lookup_many(self, targets: Iterable[str],
                    types: Sequence[RecordType | str] = ("A",)) -> list[LookupResult]: ...
```

## Result behavior

- `LookupResult.ok: bool`; iterating a result yields `DnsRecord`s; `.to_dict()` / `.to_json()`
  produce the versioned envelope (`contracts/json-envelope.md`).
- On failure, functions/methods **raise** the matching `DnsError` subclass (or `UsageError` for
  bad input). Callers catch to branch; the process is never terminated by the library.

## Contract guarantees (SemVer, Art. V)

- Adding params (keyword, with defaults), new functions, or new record types = MINOR.
- Removing/renaming/retyping public symbols or changing defaults = MAJOR.
- Exceptions raised for a given failure class are part of the contract.
