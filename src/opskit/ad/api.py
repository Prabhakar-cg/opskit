"""Public API for Active Directory / LDAP diagnostics.

All category logic lives here (constitution Art. VII): principal resolution, status
derivation, membership traversal, object summaries, and the staged connectivity check —
over the single ldap3 adapter (:mod:`opskit.ad.directory`). Convenience functions build
a throwaway :class:`AdClient`; the client itself holds one reusable authenticated
session. Nothing here prints, exits, or reads environment/config files.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from opskit.ad import attributes as adattr
from opskit.ad import directory, discovery
from opskit.ad.errors import AmbiguousPrincipal, PrincipalIsGroup, PrincipalNotFound
from opskit.ad.models import (
    AccountStatusReport,
    ConnectivityReport,
    DirectoryConfig,
    GroupMemberEntry,
    GroupMembersReport,
    IdentifierKind,
    MembershipEntry,
    MembershipReport,
    MembershipVerdict,
    ObjectSummary,
    Stage,
    classify_identifier,
    escape_filter_value,
    parse_server,
)
from opskit.core.errors import OpskitError, UsageError
from opskit.net.errors import ConnectRefused, ConnectTimeout, ResolutionError

# Injectable session factory (testing seam, like dns's resolver=): mirrors
# directory.connect_session's signature.
SessionFactory = Callable[..., directory.DirectorySession]

_STATUS_ATTRIBUTES = [
    "sAMAccountName",
    "userPrincipalName",
    "userAccountControl",
    "lockoutTime",
    "pwdLastSet",
    "accountExpires",
    "msDS-User-Account-Control-Computed",
    "msDS-UserPasswordExpiryTimeComputed",
]

_MEMBERSHIP_ATTRIBUTES = ["sAMAccountName", "memberOf", "objectSid", "primaryGroupID"]

_SHOW_ATTRIBUTES = [
    "sAMAccountName",
    "userPrincipalName",
    "objectSid",
    "objectClass",
    "whenCreated",
    "whenChanged",
    "description",
    "mail",
    "displayName",
    "title",
    "department",
    "groupType",
    "member",
    "dNSHostName",
    "operatingSystem",
    "operatingSystemVersion",
]

# Object-class scoping per lookup kind (R6). AD computers are a subclass of user, so
# "user" excludes them explicitly and "principal" (status/membership subjects) includes
# both. There is deliberately no unscoped/arbitrary-filter path (FR-020).
_CLASS_FILTERS = {
    "user": "(&(objectClass=user)(!(objectClass=computer)))",
    "computer": "(objectClass=computer)",
    "group": "(objectClass=group)",
    "principal": "(|(objectClass=user)(objectClass=computer))",
    "any": "(|(objectClass=user)(objectClass=group)(objectClass=computer))",
}

OBJECT_TYPES = ("auto", "user", "group", "computer")


def _first_rdn_value(dn: str) -> str:
    """Extract the first RDN's value from a DN (display name for a group DN)."""
    buffer: list[str] = []
    escaped = False
    for char in dn:
        if escaped:
            buffer.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            break
        else:
            buffer.append(char)
    rdn = "".join(buffer)
    if "=" in rdn:
        return rdn.split("=", 1)[1].strip()
    return dn


def _identifier_clause(kind: IdentifierKind, value: str) -> str:
    """Build the escaped equality clause for a non-DN identifier.

    A UPN-shaped identifier (contains ``@``) matches either ``userPrincipalName`` or
    ``mail`` (008-ad-enhancements FR-008) — the same string is often used as both, and
    directories with multiple UPN suffixes can have a user findable only via `mail`. A
    single entry matching both attributes still resolves as one match (FR-010); two
    different entries each matching one attribute still trip the existing ambiguity
    refusal in ``_find_one`` (FR-009) — an LDAP OR-filter search naturally dedups by entry.
    """
    escaped = escape_filter_value(value)
    if kind is IdentifierKind.UPN:
        return f"(|(userPrincipalName={escaped})(mail={escaped}))"
    return f"(|(sAMAccountName={escaped})(cn={escaped}))"


def _find_one(
    session: directory.DirectorySession,
    identifier: str,
    *,
    kind_filter: str,
    attributes: list[str],
    label: str,
) -> directory.DirectoryEntry:
    """Resolve one named object, refusing to guess on ambiguity (R6, FR-014).

    Raises:
        PrincipalNotFound: When nothing matches.
        PrincipalIsGroup: When a principal-scoped lookup finds no user/computer account
            but the identifier matches exactly one group (008-ad-enhancements FR-001).
        AmbiguousPrincipal: When more than one object matches (candidates listed).
    """
    id_kind, value = classify_identifier(identifier)
    if id_kind is IdentifierKind.DN:
        read_attrs = (
            attributes if "objectClass" in attributes else [*attributes, "objectClass"]
        )
        entry = session.read_entry(value, attributes=read_attrs)
        if entry is None:
            raise PrincipalNotFound(
                f"no object found at DN: {value}",
                hint="check the distinguished name",
            )
        if not _matches_kind_filter(entry, kind_filter):
            if kind_filter == "principal" and _object_type_of(entry) == "group":
                group_name = _first_rdn_value(entry.dn)
                raise PrincipalIsGroup(
                    f"'{identifier}' is a group, not a user or computer account",
                    hint=f"see its members with: opskit ad members {group_name}; "
                    f"or test membership with: "
                    f"opskit ad member <principal> {group_name}",
                )
            raise PrincipalNotFound(
                f"the object at this DN is not a {label}: {value}",
                hint="check the distinguished name and expected object type",
            )
        return entry
    identifier_clause = _identifier_clause(id_kind, value)
    ldap_filter = f"(&{_CLASS_FILTERS[kind_filter]}{identifier_clause})"
    entries = session.search(
        base=session.default_base(),
        ldap_filter=ldap_filter,
        attributes=attributes,
    )
    if not entries:
        if kind_filter == "principal":
            group_match = session.search(
                base=session.default_base(),
                ldap_filter=f"(&{_CLASS_FILTERS['group']}{identifier_clause})",
                attributes=["sAMAccountName"],
            )
            if len(group_match) == 1:
                group_name = _first_rdn_value(group_match[0].dn)
                raise PrincipalIsGroup(
                    f"'{identifier}' is a group, not a user or computer account",
                    hint=f"see its members with: opskit ad members {group_name}; "
                    f"or test membership with: opskit ad member <principal> {group_name}",
                )
        raise PrincipalNotFound(
            f"no {label} found matching: {identifier}",
            hint="check the spelling and identifier form (name, user@domain, or DN); "
            "a different --base-dn may be needed",
        )
    if len(entries) > 1:
        candidates = "; ".join(entry.dn for entry in entries)
        raise AmbiguousPrincipal(
            f"'{identifier}' matches more than one object: {candidates}",
            hint="disambiguate by passing the distinguished name",
        )
    return entries[0]


def _derive_status(  # noqa: PLR0912, PLR0915 - a flat fact-derivation table (R5); splitting it would obscure the per-fact fallback rules
    principal: str,
    entry: directory.DirectoryEntry,
    *,
    now: datetime | None = None,
) -> AccountStatusReport:
    """Derive the status facts and blockers from one principal's attributes (R5)."""
    moment = now if now is not None else datetime.now(timezone.utc)
    unavailable: list[str] = []

    uac = adattr.coerce_int(entry.first("userAccountControl"))
    enabled = None if uac is None else not bool(uac & adattr.UF_ACCOUNTDISABLE)
    if enabled is None:
        unavailable.append("enabled")

    computed = adattr.coerce_int(entry.first("msDS-User-Account-Control-Computed"))
    lockout_raw = entry.first("lockoutTime")
    lockout_time = adattr.filetime_to_datetime(lockout_raw)
    lockout_stale_possible = False
    locked: bool | None
    if computed is not None:
        locked = bool(computed & adattr.UF_LOCKOUT)
    elif lockout_raw is not None:
        # Raw lockoutTime only: a recorded lockout may already have lapsed by policy —
        # report what the directory recorded, flagged as possibly stale (spec edge case).
        locked = lockout_time is not None
        lockout_stale_possible = bool(locked)
    else:
        locked = None
        unavailable.append("locked")

    never_flag = None if uac is None else bool(uac & adattr.UF_DONT_EXPIRE_PASSWD)
    expiry_raw = entry.first("msDS-UserPasswordExpiryTimeComputed")
    password_never_expires = never_flag
    if expiry_raw is not None and adattr.is_never_filetime(expiry_raw):
        password_never_expires = True
    password_expires_at = (
        None if password_never_expires else adattr.filetime_to_datetime(expiry_raw)
    )
    password_expired: bool | None
    if computed is not None:
        password_expired = bool(computed & adattr.UF_PASSWORD_EXPIRED)
    elif password_never_expires:
        password_expired = False
    elif password_expires_at is not None:
        password_expired = password_expires_at <= moment
    else:
        password_expired = None
        unavailable.append("password_expired")

    last_set_raw = entry.first("pwdLastSet")
    password_last_set = adattr.filetime_to_datetime(last_set_raw)
    must_change: bool | None
    if last_set_raw is None:
        must_change = None
        unavailable.append("must_change_password")
    else:
        must_change = adattr.coerce_int(last_set_raw) == 0

    expires_raw = entry.first("accountExpires")
    account_never_expires: bool | None
    account_expires_at: datetime | None
    account_expired: bool | None
    if expires_raw is None:
        account_never_expires = None
        account_expires_at = None
        account_expired = None
        unavailable.append("account_expired")
    elif adattr.is_never_filetime(expires_raw):
        account_never_expires = True
        account_expires_at = None
        account_expired = False
    else:
        account_never_expires = False
        account_expires_at = adattr.filetime_to_datetime(expires_raw)
        account_expired = (
            account_expires_at <= moment if account_expires_at is not None else None
        )

    blockers: list[str] = []
    if enabled is False:
        blockers.append("disabled")
    if locked:
        blockers.append("locked_out")
    if password_expired:
        blockers.append("password_expired")
    if must_change:
        blockers.append("must_change_password")
    if account_expired:
        blockers.append("account_expired")

    sam = entry.first("sAMAccountName")
    upn = entry.first("userPrincipalName")
    return AccountStatusReport(
        principal=principal,
        dn=entry.dn,
        sam_account_name=str(sam) if sam is not None else None,
        user_principal_name=str(upn) if upn is not None else None,
        enabled=enabled,
        locked=locked,
        lockout_time=lockout_time,
        lockout_stale_possible=lockout_stale_possible,
        password_expired=password_expired,
        password_expires_at=password_expires_at,
        password_never_expires=password_never_expires,
        must_change_password=must_change,
        password_last_set=password_last_set,
        account_expires_at=account_expires_at,
        account_never_expires=account_never_expires,
        account_expired=account_expired,
        blockers=tuple(blockers),
        facts_unavailable=tuple(unavailable),
    )


class AdClient:
    """A reusable directory diagnostics session (requests-style client).

    Opens its connection lazily on first use and reuses it for every query; use it as
    a context manager (or call :meth:`close`). Not thread-safe — use one client per
    thread. The optional ``session_factory`` is a testing seam mirroring
    :func:`opskit.ad.directory.connect_session`.
    """

    def __init__(
        self,
        config: DirectoryConfig,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        """Store the configuration; no I/O happens until the first query."""
        self._config = config
        self._session_factory: SessionFactory = (
            session_factory
            if session_factory is not None
            else directory.connect_session
        )
        self._session: directory.DirectorySession | None = None

    def __enter__(self) -> AdClient:
        """Return self; the connection opens lazily on first use."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the underlying session, if one was opened."""
        self.close()

    def close(self) -> None:
        """Unbind and drop the session (idempotent)."""
        if self._session is not None:
            self._session.close()
            self._session = None

    @property
    def config(self) -> DirectoryConfig:
        """The configuration this client was built with."""
        return self._config

    @property
    def connected_server(self) -> tuple[str, int] | None:
        """The (host, port) actually connected, or ``None`` before the first query."""
        if self._session is None:
            return None
        return self._session.host, self._session.port

    # -- connection ------------------------------------------------------------

    def _candidates(self) -> tuple[list[tuple[str, int]], bool]:
        """Resolve the candidate (host, port) list: explicit server or discovery (R4)."""
        config = self._config
        if config.server:
            host, shorthand = parse_server(config.server)
            port = shorthand if shorthand is not None else config.effective_port
            return [(host, port)], False
        hosts = discovery.discover_dcs(str(config.domain), timeout=config.timeout)
        return [(host, config.effective_port) for host in hosts], True

    def _connect_any(
        self, *, stages: list[Stage] | None = None
    ) -> tuple[directory.DirectorySession, list[str], bool]:
        """Connect to the first answering candidate; rotate only on reach failures."""
        candidates, discovered = self._candidates()
        tried: list[str] = []
        last_error: OpskitError | None = None
        for host, port in candidates:
            tried.append(host)
            if stages is not None:
                stages.clear()
            try:
                session = self._session_factory(
                    self._config, host=host, port=port, stages=stages
                )
            except (ConnectRefused, ConnectTimeout, ResolutionError) as exc:
                last_error = exc  # reach-class failure: try the next candidate
                continue
            return session, tried, discovered
        assert last_error is not None  # noqa: S101 - candidates is never empty
        raise last_error

    def connect(self) -> None:
        """Open and bind the session now (otherwise it opens on first use)."""
        self._ensure()

    def _ensure(self) -> directory.DirectorySession:
        """Return the open session, connecting on first use."""
        if self._session is None:
            session, _, _ = self._connect_any()
            self._session = session
        return self._session

    # -- capabilities ------------------------------------------------------------

    def check(self) -> ConnectivityReport:
        """Run the staged connectivity/bind check (US3).

        Returns:
            The :class:`ConnectivityReport` with per-stage timings, the server used
            (and discovery candidates tried), and rootDSE identity basics.
        """
        stages: list[Stage] = []
        session, tried, discovered = self._connect_any(stages=stages)
        if self._session is None:
            self._session = session  # adopt for subsequent queries on this client
        elif session is not self._session:
            session.close()
            session = self._session
        info = session.server_info()
        return ConnectivityReport(
            server_used=session.host,
            port=session.port,
            security=self._config.security,
            encrypted=self._config.encrypted,
            discovered=discovered,
            candidates_tried=tuple(tried),
            stages=tuple(stages),
            bind_user=self._config.bind_user,
            server_info=info,
        )

    def user_status(self, principal: str) -> AccountStatusReport:
        """Report a principal's sign-in status facts and active blockers (US1)."""
        session = self._ensure()
        entry = _find_one(
            session,
            principal,
            kind_filter="principal",
            attributes=_STATUS_ATTRIBUTES,
            label="user or computer account",
        )
        return _derive_status(principal, entry)

    def membership(
        self, principal: str, *, effective: bool = False
    ) -> MembershipReport:
        """List a principal's group memberships, optionally with nesting resolved (US2)."""
        session = self._ensure()
        entry = _find_one(
            session,
            principal,
            kind_filter="principal",
            attributes=_MEMBERSHIP_ATTRIBUTES,
            label="user or computer account",
        )
        groups = self._direct_groups(session, entry)
        if effective:
            groups = _expand_nested(session, groups)
        return MembershipReport(
            principal=principal,
            dn=entry.dn,
            effective=effective,
            groups=tuple(groups),
        )

    def is_member(self, principal: str, group: str) -> MembershipVerdict:
        """Answer "is principal P in group G?" with the granting chain (US2)."""
        session = self._ensure()
        entry = _find_one(
            session,
            principal,
            kind_filter="principal",
            attributes=_MEMBERSHIP_ATTRIBUTES,
            label="user or computer account",
        )
        group_entry = _find_one(
            session,
            group,
            kind_filter="group",
            attributes=["sAMAccountName"],
            label="group",
        )
        target_dn = group_entry.dn.lower()
        effective = _expand_nested(session, self._direct_groups(session, entry))
        for candidate in effective:
            if candidate.dn.lower() == target_dn:
                return MembershipVerdict(
                    principal=principal,
                    principal_dn=entry.dn,
                    group=group,
                    group_dn=group_entry.dn,
                    member=True,
                    via=candidate.via,
                    path=candidate.path,
                )
        return MembershipVerdict(
            principal=principal,
            principal_dn=entry.dn,
            group=group,
            group_dn=group_entry.dn,
            member=False,
        )

    def members(self, group: str, *, effective: bool = True) -> GroupMembersReport:
        """Report a group's members, optionally with nesting resolved (US2, default: yes).

        The ``member``-direction mirror of :meth:`membership`. Unlike ``membership()``,
        ``effective`` defaults to ``True`` here (see :class:`GroupMembersReport`).
        """
        session = self._ensure()
        entry = _find_one(
            session,
            group,
            kind_filter="group",
            attributes=["sAMAccountName", "member"],
            label="group",
        )
        if effective:
            direct = _direct_members(session, entry, classify=True)
            members = _expand_members(session, entry.dn, direct)
        else:
            members = _direct_members(session, entry, classify=False)
        return GroupMembersReport(
            group=group,
            dn=entry.dn,
            effective=effective,
            members=tuple(members),
        )

    def show(self, name: str, *, object_type: str = "auto") -> ObjectSummary:
        """Report one named user/group/computer object's key attributes (US5)."""
        if object_type not in OBJECT_TYPES:
            raise UsageError(
                f"unknown object type: {object_type}",
                hint="use one of: " + ", ".join(OBJECT_TYPES),
            )
        session = self._ensure()
        kind_filter = "any" if object_type == "auto" else object_type
        entry = _find_one(
            session,
            name,
            kind_filter=kind_filter,
            attributes=_SHOW_ATTRIBUTES,
            label="object" if object_type == "auto" else object_type,
        )
        return _summarize(entry)

    # -- membership helpers ------------------------------------------------------

    def _direct_groups(
        self,
        session: directory.DirectorySession,
        entry: directory.DirectoryEntry,
    ) -> list[MembershipEntry]:
        """The principal's direct memberships plus its primary group (R7)."""
        groups = [
            MembershipEntry(name=_first_rdn_value(str(dn)), dn=str(dn), via="direct")
            for dn in entry.values("memberOf")
        ]
        primary = self._primary_group(session, entry)
        if primary is not None:
            groups.append(primary)
        return groups

    def _primary_group(
        self,
        session: directory.DirectorySession,
        entry: directory.DirectoryEntry,
    ) -> MembershipEntry | None:
        """Resolve the primary group (not present in ``memberOf``) via its SID (R7)."""
        sid = adattr.primary_group_sid(
            entry.first("objectSid"), entry.first("primaryGroupID")
        )
        if sid is None:
            return None
        matches = session.search(
            base=session.default_base(),
            ldap_filter=(
                f"(&(objectClass=group)(objectSid={escape_filter_value(sid)}))"
            ),
            attributes=["sAMAccountName"],
        )
        if not matches:
            return None
        group = matches[0]
        return MembershipEntry(
            name=_first_rdn_value(group.dn), dn=group.dn, via="primary"
        )


def _direct_members(
    session: directory.DirectorySession,
    entry: directory.DirectoryEntry,
    *,
    classify: bool = True,
) -> list[GroupMemberEntry]:
    """The group's direct members (008-ad-enhancements US2).

    When ``classify`` is false (the ``--direct``-only fast path), skips the per-member
    ``objectClass`` read entirely — cheap even for a very large group — and reports
    ``object_type="unknown"`` for every entry, since nothing will be recursed into anyway.
    """
    members: list[GroupMemberEntry] = []
    for raw in entry.values("member"):
        member_dn = str(raw)
        object_type = "unknown"
        if classify:
            member_entry = session.read_entry(member_dn, attributes=["objectClass"])
            if member_entry is not None:
                object_type = _object_type_of(member_entry)
        members.append(
            GroupMemberEntry(
                name=_first_rdn_value(member_dn),
                dn=member_dn,
                object_type=object_type,
                via="direct",
            )
        )
    return members


def _expand_members(
    session: directory.DirectorySession,
    top_dn: str,
    direct: list[GroupMemberEntry],
) -> list[GroupMemberEntry]:
    """Resolve effective (nested) group membership: BFS over ``member`` (008 US2).

    The ``member``-direction mirror of :func:`_expand_nested` (R7 of 004-ad-diagnostics):
    breadth-first order guarantees each member is recorded at its **shortest** acquisition
    path; the visited set — seeded with the top-level group's own DN, guarding against a
    cycle looping back to it — terminates cycles and reports each distinct member once
    (FR-006). Only members already classified ``"group"`` are recursed into.
    """
    results = list(direct)
    visited = {top_dn.lower()}
    visited.update(entry.dn.lower() for entry in direct)
    queue: deque[tuple[str, str, tuple[str, ...]]] = deque(
        (entry.dn, entry.name, ()) for entry in direct if entry.object_type == "group"
    )
    while queue:
        dn, name, path = queue.popleft()
        group_entry = session.read_entry(dn, attributes=["member"])
        if group_entry is None:  # e.g. a referral outside this directory's view
            continue
        chain = (*path, name)
        for raw in group_entry.values("member"):
            member_dn = str(raw)
            key = member_dn.lower()
            if key in visited:
                continue
            visited.add(key)
            member_name = _first_rdn_value(member_dn)
            member_read = session.read_entry(member_dn, attributes=["objectClass"])
            object_type = (
                _object_type_of(member_read) if member_read is not None else "unknown"
            )
            results.append(
                GroupMemberEntry(
                    name=member_name,
                    dn=member_dn,
                    object_type=object_type,
                    via="nested",
                    path=chain,
                )
            )
            if object_type == "group":
                queue.append((member_dn, member_name, chain))
    return results


def _expand_nested(
    session: directory.DirectorySession, direct: list[MembershipEntry]
) -> list[MembershipEntry]:
    """Resolve effective membership: BFS over group ``memberOf`` with cycle safety (R7).

    Breadth-first order guarantees each group is recorded with its **shortest**
    acquisition path; the visited set terminates cycles and reports each group once.
    """
    results = list(direct)
    visited = {entry.dn.lower() for entry in direct}
    queue: deque[tuple[str, str, tuple[str, ...]]] = deque(
        (entry.dn, entry.name, ()) for entry in direct
    )
    while queue:
        dn, name, path = queue.popleft()
        group_entry = session.read_entry(dn, attributes=["memberOf"])
        if group_entry is None:  # e.g. a referral outside this directory's view
            continue
        chain = (*path, name)
        for parent in group_entry.values("memberOf"):
            parent_dn = str(parent)
            key = parent_dn.lower()
            if key in visited:
                continue
            visited.add(key)
            parent_name = _first_rdn_value(parent_dn)
            results.append(
                MembershipEntry(
                    name=parent_name, dn=parent_dn, via="nested", path=chain
                )
            )
            queue.append((parent_dn, parent_name, chain))
    return results


def _object_type_of(entry: directory.DirectoryEntry) -> str:
    """Classify an entry as user/group/computer from its objectClass values."""
    classes = {str(value).lower() for value in entry.values("objectClass")}
    if "computer" in classes:
        return "computer"
    if "group" in classes:
        return "group"
    return "user"


def _matches_kind_filter(entry: directory.DirectoryEntry, kind_filter: str) -> bool:
    """Whether a DN-resolved entry's actual class satisfies the requested scope.

    Mirrors ``_CLASS_FILTERS``' semantics for the DN lookup path (008-ad-enhancements):
    a DN is an exact address, so unlike the search path it can't be narrowed by an LDAP
    filter — the class has to be checked after the read.
    """
    if kind_filter == "any":
        return True
    object_type = _object_type_of(entry)
    if kind_filter == "principal":
        return object_type in ("user", "computer")
    return object_type == kind_filter


def _summarize(entry: directory.DirectoryEntry) -> ObjectSummary:
    """Build the key-attribute summary for one resolved object (US5)."""
    object_type = _object_type_of(entry)
    type_facts: dict[str, Any] = {}
    if object_type == "user":
        for fact, attribute in (
            ("mail", "mail"),
            ("display_name", "displayName"),
            ("title", "title"),
            ("department", "department"),
        ):
            value = entry.first(attribute)
            type_facts[fact] = str(value) if value is not None else None
    elif object_type == "group":
        type_facts["group_kind"] = adattr.group_kind(entry.first("groupType"))
        type_facts["members"] = [
            {"name": _first_rdn_value(str(dn)), "dn": str(dn)}
            for dn in entry.values("member")
        ]
    else:  # computer
        for fact, attribute in (
            ("dns_host_name", "dNSHostName"),
            ("operating_system", "operatingSystem"),
            ("os_version", "operatingSystemVersion"),
        ):
            value = entry.first(attribute)
            type_facts[fact] = str(value) if value is not None else None

    sam = entry.first("sAMAccountName")
    upn = entry.first("userPrincipalName")
    description = entry.first("description")
    return ObjectSummary(
        name=_first_rdn_value(entry.dn),
        dn=entry.dn,
        object_type=object_type,
        identifiers={
            "sam_account_name": str(sam) if sam is not None else None,
            "user_principal_name": str(upn) if upn is not None else None,
            "sid": adattr.sid_to_string(entry.first("objectSid")),
        },
        created=adattr.parse_generalized_time(entry.first("whenCreated")),
        changed=adattr.parse_generalized_time(entry.first("whenChanged")),
        description=str(description) if description is not None else None,
        type_facts=type_facts,
    )


# -- convenience functions (one-shot, requests-style) -----------------------------


def _make_config(
    config: DirectoryConfig | None,
    *,
    server: str | None,
    domain: str | None,
    security: str,
    port: int | None,
    bind_user: str | None,
    password: str | None,
    allow_cleartext: bool,
    ca_file: Path | None,
    base_dn: str | None,
    timeout: float,
) -> DirectoryConfig:
    """Return the prebuilt config, or build one from the keyword mirror."""
    if config is not None:
        return config
    return DirectoryConfig(
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )


def check(
    *,
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> ConnectivityReport:
    """One-shot staged connectivity/bind check (see :meth:`AdClient.check`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.check()


def user_status(
    principal: str,
    *,
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> AccountStatusReport:
    """One-shot account-status report (see :meth:`AdClient.user_status`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.user_status(principal)


def membership(
    principal: str,
    *,
    effective: bool = False,
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> MembershipReport:
    """One-shot membership report (see :meth:`AdClient.membership`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.membership(principal, effective=effective)


def is_member(
    principal: str,
    group: str,
    *,
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> MembershipVerdict:
    """One-shot membership test (see :meth:`AdClient.is_member`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.is_member(principal, group)


def members(
    group: str,
    *,
    effective: bool = True,
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> GroupMembersReport:
    """One-shot group-members report (see :meth:`AdClient.members`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.members(group, effective=effective)


def show(
    name: str,
    *,
    object_type: str = "auto",
    server: str | None = None,
    domain: str | None = None,
    security: str = "ldaps",
    port: int | None = None,
    bind_user: str | None = None,
    password: str | None = None,
    allow_cleartext: bool = False,
    ca_file: Path | None = None,
    base_dn: str | None = None,
    timeout: float = 5.0,
    config: DirectoryConfig | None = None,
    session_factory: SessionFactory | None = None,
) -> ObjectSummary:
    """One-shot object-summary lookup (see :meth:`AdClient.show`)."""
    built = _make_config(
        config,
        server=server,
        domain=domain,
        security=security,
        port=port,
        bind_user=bind_user,
        password=password,
        allow_cleartext=allow_cleartext,
        ca_file=ca_file,
        base_dn=base_dn,
        timeout=timeout,
    )
    with AdClient(built, session_factory=session_factory) as client:
        return client.show(name, object_type=object_type)
