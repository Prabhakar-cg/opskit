"""Active Directory attribute semantics as pure functions (no I/O, no ldap3).

FILETIME/GeneralizedTime conversion with sentinel handling, ``userAccountControl`` bit
readers, SID parsing, and primary-group SID derivation — the boundary where directory
wire values become typed Python values, so no ``Any`` leaks past the adapter (R5, R9).
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from typing import Union

# Directory wire forms a value can arrive in (ldap3 may pre-format some of these).
WireValue = Union[str, int, bytes, datetime, None]

# FILETIME: 100 ns ticks since 1601-01-01 UTC. Both sentinels mean "never".
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
FILETIME_NEVER_LOW = 0
FILETIME_NEVER_HIGH = 0x7FFFFFFFFFFFFFFF

# userAccountControl flag bits (raw attribute).
UF_ACCOUNTDISABLE = 0x2
UF_DONT_EXPIRE_PASSWD = 0x10000
# msDS-User-Account-Control-Computed flag bits (server-computed attribute, R5).
UF_LOCKOUT = 0x10
UF_PASSWORD_EXPIRED = 0x800000

# Binary SID header: revision + count + 48-bit authority.
_SID_HEADER_LEN = 8

# groupType flag bits.
_GROUP_SECURITY = 0x80000000
_GROUP_SCOPES = ((0x2, "global"), (0x4, "domain-local"), (0x8, "universal"))


def coerce_int(value: WireValue) -> int | None:
    """Coerce a directory wire value to ``int``; ``None`` when absent/unparseable."""
    if isinstance(
        value, bool
    ):  # bools are ints; a bool here is not a numeric attribute
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def coerce_str(value: WireValue) -> str | None:
    """Coerce a directory wire value to ``str``; ``None`` when absent."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        return value
    return str(value)


def is_never_filetime(value: WireValue) -> bool:
    """Return True when a FILETIME value is one of the "never" sentinels."""
    number = coerce_int(value) if not isinstance(value, datetime) else None
    return number in (FILETIME_NEVER_LOW, FILETIME_NEVER_HIGH)


def filetime_to_datetime(value: WireValue) -> datetime | None:
    """Convert a FILETIME wire value to an aware UTC datetime.

    Returns ``None`` for absent values, the "never" sentinels, negatives, and values
    outside :class:`datetime`'s representable range. A ``datetime`` passes through
    (made UTC-aware) since ldap3 pre-formats some attributes.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    number = coerce_int(value)
    if number is None or number <= 0 or number == FILETIME_NEVER_HIGH:
        return None
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=number / 10)
    except OverflowError:
        return None


def parse_generalized_time(value: WireValue) -> datetime | None:
    """Parse an LDAP GeneralizedTime (``YYYYMMDDHHMMSS[.f]Z``) into an aware datetime.

    A ``datetime`` passes through (made UTC-aware); unparseable values yield ``None``.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = coerce_str(value)
    if text is None:
        return None
    text = text.strip().rstrip("Z")
    text = text.split(".")[0].split(",")[0]
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def uac_flag(value: WireValue, flag: int) -> bool | None:
    """Read one flag bit from a ``userAccountControl``-style value (tri-state)."""
    number = coerce_int(value)
    if number is None:
        return None
    return bool(number & flag)


def sid_to_string(value: WireValue) -> str | None:
    """Render an ``objectSid`` wire value as its ``S-1-...`` string form.

    Accepts the binary SID layout (revision, sub-authority count, 48-bit big-endian
    identifier authority, little-endian 32-bit sub-authorities) or an already-formatted
    string. Malformed values yield ``None``.
    """
    if isinstance(value, str):
        return value if value.startswith("S-") else None
    if not isinstance(value, bytes) or len(value) < _SID_HEADER_LEN:
        return None
    revision = value[0]
    count = value[1]
    if len(value) != _SID_HEADER_LEN + 4 * count:
        return None
    authority = int.from_bytes(value[2:8], "big")
    subauthorities = struct.unpack_from(f"<{count}I", value, 8) if count else ()
    parts = [f"S-{revision}-{authority}", *(str(sub) for sub in subauthorities)]
    return "-".join(parts)


def primary_group_sid(object_sid: WireValue, primary_group_id: WireValue) -> str | None:
    """Derive the primary group's SID from a principal's SID and ``primaryGroupID``.

    The primary group lives in the same domain: its SID is the principal's SID with the
    final sub-authority (the RID) replaced by ``primaryGroupID`` (R7).
    """
    sid = sid_to_string(object_sid)
    rid = coerce_int(primary_group_id)
    if sid is None or rid is None or "-" not in sid:
        return None
    prefix = sid.rsplit("-", 1)[0]
    return f"{prefix}-{rid}"


def group_kind(group_type: WireValue) -> str | None:
    """Render a ``groupType`` value as ``"security-global"``-style kind text."""
    number = coerce_int(group_type)
    if number is None:
        return None
    kind = "security" if number & _GROUP_SECURITY else "distribution"
    scope = next(
        (name for bit, name in _GROUP_SCOPES if number & bit),
        "unknown-scope",
    )
    return f"{kind}-{scope}"
