"""Unit tests for AD attribute semantics (FILETIME, UAC bits, SIDs) — pure functions."""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from opskit.ad import attributes as adattr

_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
# Stay well inside datetime's year-9999 ceiling for the property tests.
_MAX_SAFE_FILETIME = 2_500_000_000 * 10_000_000


class TestCoercions:
    def test_int_passthrough_and_strings(self):
        assert adattr.coerce_int(512) == 512
        assert adattr.coerce_int("512") == 512
        assert adattr.coerce_int(b"512") == 512
        assert adattr.coerce_int("not-a-number") is None
        assert adattr.coerce_int(None) is None
        assert adattr.coerce_int(True) is None  # bool is not a numeric attribute

    def test_str_coercion(self):
        assert adattr.coerce_str("x") == "x"
        assert adattr.coerce_str(b"x") == "x"
        assert adattr.coerce_str(None) is None
        assert adattr.coerce_str(5) == "5"


class TestFiletime:
    def test_sentinels_mean_never(self):
        assert adattr.is_never_filetime(0)
        assert adattr.is_never_filetime("0")
        assert adattr.is_never_filetime(adattr.FILETIME_NEVER_HIGH)
        assert adattr.is_never_filetime(str(adattr.FILETIME_NEVER_HIGH))
        assert not adattr.is_never_filetime(133600000000000000)
        assert adattr.filetime_to_datetime(0) is None
        assert adattr.filetime_to_datetime(adattr.FILETIME_NEVER_HIGH) is None

    def test_known_value(self):
        one_day = 24 * 3600 * 10_000_000
        assert adattr.filetime_to_datetime(one_day) == _EPOCH + timedelta(days=1)

    def test_datetime_passes_through_and_gains_utc(self):
        aware = datetime(2026, 7, 1, tzinfo=timezone.utc)
        naive = datetime(2026, 7, 1)
        assert adattr.filetime_to_datetime(aware) == aware
        assert adattr.filetime_to_datetime(naive) == aware

    def test_negative_and_garbage_are_none(self):
        assert adattr.filetime_to_datetime(-5) is None
        assert adattr.filetime_to_datetime("garbage") is None
        assert adattr.filetime_to_datetime(None) is None

    def test_overflow_is_none(self):
        assert adattr.filetime_to_datetime(adattr.FILETIME_NEVER_HIGH - 1) is None

    @given(st.integers(min_value=1, max_value=_MAX_SAFE_FILETIME))
    def test_conversion_is_monotonic_and_utc(self, ticks: int):
        converted = adattr.filetime_to_datetime(ticks)
        assert converted is not None
        assert converted.tzinfo is not None
        later = adattr.filetime_to_datetime(ticks + 10_000_000)
        assert later is not None
        assert later > converted

    @given(st.integers(min_value=1, max_value=_MAX_SAFE_FILETIME))
    def test_string_and_int_forms_agree(self, ticks: int):
        assert adattr.filetime_to_datetime(ticks) == adattr.filetime_to_datetime(
            str(ticks)
        )


class TestGeneralizedTime:
    def test_parses_z_form(self):
        parsed = adattr.parse_generalized_time("20230115083000.0Z")
        assert parsed == datetime(2023, 1, 15, 8, 30, tzinfo=timezone.utc)

    def test_datetime_passthrough_and_garbage(self):
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert adattr.parse_generalized_time(aware) == aware
        assert adattr.parse_generalized_time("nonsense") is None
        assert adattr.parse_generalized_time(None) is None


class TestUacFlags:
    def test_disabled_bit(self):
        assert adattr.uac_flag(514, adattr.UF_ACCOUNTDISABLE) is True
        assert adattr.uac_flag("512", adattr.UF_ACCOUNTDISABLE) is False
        assert adattr.uac_flag(None, adattr.UF_ACCOUNTDISABLE) is None

    def test_computed_bits(self):
        assert adattr.uac_flag(0x10, adattr.UF_LOCKOUT) is True
        assert adattr.uac_flag(0x800000, adattr.UF_PASSWORD_EXPIRED) is True
        assert adattr.uac_flag(0, adattr.UF_LOCKOUT) is False


class TestSids:
    def test_binary_sid_renders(self):
        # S-1-5-21-1-2-3-1104 in binary layout.
        raw = bytes([1, 5]) + (5).to_bytes(6, "big")
        raw += struct.pack("<5I", 21, 1, 2, 3, 1104)
        assert adattr.sid_to_string(raw) == "S-1-5-21-1-2-3-1104"

    def test_string_sid_passthrough(self):
        assert adattr.sid_to_string("S-1-5-21-1-2-3-513") == "S-1-5-21-1-2-3-513"
        assert adattr.sid_to_string("not-a-sid") is None

    def test_malformed_binary_is_none(self):
        assert adattr.sid_to_string(b"\x01") is None
        assert adattr.sid_to_string(None) is None
        # count byte says 5 sub-authorities but only 1 present
        bad = bytes([1, 5]) + (5).to_bytes(6, "big") + struct.pack("<I", 21)
        assert adattr.sid_to_string(bad) is None

    def test_primary_group_sid_replaces_rid(self):
        sid = "S-1-5-21-1111-2222-3333-1104"
        assert adattr.primary_group_sid(sid, "513") == "S-1-5-21-1111-2222-3333-513"
        assert adattr.primary_group_sid(None, "513") is None
        assert adattr.primary_group_sid(sid, None) is None


class TestGroupKind:
    def test_security_global(self):
        assert adattr.group_kind("-2147483646") == "security-global"

    def test_distribution_universal(self):
        assert adattr.group_kind(8) == "distribution-universal"

    def test_unknown(self):
        assert adattr.group_kind(None) is None
