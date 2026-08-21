"""Unit tests for the ad data model: config validation, identifiers, filter escaping."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opskit.ad.errors import CleartextRefused
from opskit.ad.models import (
    DirectoryConfig,
    GroupMemberEntry,
    GroupMembersReport,
    IdentifierKind,
    classify_identifier,
    escape_filter_value,
    parse_server,
)
from opskit.core.errors import UsageError


class TestGroupMembersModel:
    """008-ad-enhancements US2: to_dict() shapes for the new member-direction models."""

    def test_group_member_entry_to_dict(self):
        entry = GroupMemberEntry(
            name="J Doe",
            dn="cn=J Doe,ou=Staff,dc=corp,dc=example,dc=com",
            object_type="user",
            via="nested",
            path=("VPN Users",),
        )
        assert entry.to_dict() == {
            "name": "J Doe",
            "dn": "cn=J Doe,ou=Staff,dc=corp,dc=example,dc=com",
            "object_type": "user",
            "via": "nested",
            "path": ["VPN Users"],
        }

    def test_group_members_report_to_dict(self):
        entry = GroupMemberEntry(
            name="VPN Users",
            dn="cn=VPN Users,ou=Groups,dc=corp,dc=example,dc=com",
            object_type="group",
            via="direct",
        )
        report = GroupMembersReport(
            group="Remote Access",
            dn="cn=Remote Access,ou=Groups,dc=corp,dc=example,dc=com",
            effective=True,
            members=(entry,),
        )
        payload = report.to_dict()
        assert payload["group"] == "Remote Access"
        assert payload["effective"] is True
        assert payload["members"] == [entry.to_dict()]


class TestDirectoryConfig:
    def test_requires_server_or_domain(self):
        with pytest.raises(UsageError, match="no directory given"):
            DirectoryConfig()

    def test_unknown_security_mode(self):
        with pytest.raises(UsageError, match="unknown security mode"):
            DirectoryConfig(server="dc01", security="tls-ish")

    def test_timeout_must_be_positive(self):
        with pytest.raises(UsageError, match="timeout"):
            DirectoryConfig(server="dc01", timeout=0)

    def test_password_over_plaintext_needs_explicit_opt_in(self):
        with pytest.raises(CleartextRefused):
            DirectoryConfig(
                server="dc01", security="plaintext", bind_user="u", password="pw"
            )
        config = DirectoryConfig(
            server="dc01",
            security="plaintext",
            bind_user="u",
            password="pw",
            allow_cleartext=True,
        )
        assert config.encrypted is False

    def test_password_never_in_repr(self):
        config = DirectoryConfig(server="dc01", bind_user="u", password="TopSecret!")
        assert "TopSecret!" not in repr(config)
        assert "TopSecret!" not in str(config)

    def test_default_ports_by_mode(self):
        assert DirectoryConfig(server="dc01").effective_port == 636
        assert DirectoryConfig(server="dc01", security="starttls").effective_port == 389
        assert (
            DirectoryConfig(server="dc01", security="plaintext").effective_port == 389
        )
        assert DirectoryConfig(server="dc01", port=3269).effective_port == 3269


class TestClassifyIdentifier:
    def test_dn(self):
        kind, value = classify_identifier("CN=J Doe,DC=corp,DC=example,DC=com")
        assert kind is IdentifierKind.DN
        assert value.startswith("CN=J Doe")

    def test_upn(self):
        assert classify_identifier("jdoe@corp.example.com") == (
            IdentifierKind.UPN,
            "jdoe@corp.example.com",
        )

    def test_sam_and_netbios_prefix(self):
        assert classify_identifier("jdoe") == (IdentifierKind.SAM, "jdoe")
        assert classify_identifier("CORP\\jdoe") == (IdentifierKind.SAM, "jdoe")

    def test_computer_account(self):
        assert classify_identifier("wks-042$") == (IdentifierKind.SAM, "wks-042$")

    def test_empty_is_usage_error(self):
        with pytest.raises(UsageError):
            classify_identifier("   ")
        with pytest.raises(UsageError):
            classify_identifier("CORP\\")


class TestEscapeFilterValue:
    def test_specials_escaped(self):
        assert escape_filter_value("a*b") == "a\\2ab"
        assert escape_filter_value("(cn=x)") == "\\28cn=x\\29"
        assert escape_filter_value("back\\slash") == "back\\5cslash"
        assert escape_filter_value("nul\0byte") == "nul\\00byte"

    @given(st.text(max_size=50))
    def test_no_structural_characters_survive(self, value: str):
        escaped = escape_filter_value(value)
        for char in "*()\0":
            assert char not in escaped

    @given(st.text(alphabet=st.characters(blacklist_characters="\\*()\0"), max_size=50))
    def test_plain_text_unchanged(self, value: str):
        assert escape_filter_value(value) == value


class TestParseServer:
    def test_host_only(self):
        assert parse_server("dc01.corp.example.com") == (
            "dc01.corp.example.com",
            None,
        )

    def test_host_port(self):
        assert parse_server("dc01:3269") == ("dc01", 3269)

    def test_ipv6_bracket(self):
        assert parse_server("[2001:db8::7]:636") == ("2001:db8::7", 636)

    def test_empty_is_usage_error(self):
        with pytest.raises(UsageError):
            parse_server("  ")
        with pytest.raises(UsageError):
            parse_server("dc01:")

    def test_ldap_scheme_is_stripped(self):
        assert parse_server("ldap://dc01.corp.example.com") == (
            "dc01.corp.example.com",
            None,
        )

    def test_ldaps_scheme_with_port_is_stripped(self):
        assert parse_server("ldaps://dc01:636") == ("dc01", 636)

    def test_scheme_is_case_insensitive(self):
        assert parse_server("LDAPS://dc01:636") == ("dc01", 636)

    def test_scheme_with_ipv6_bracket(self):
        assert parse_server("ldaps://[2001:db8::7]:636") == ("2001:db8::7", 636)

    def test_scheme_with_trailing_slash_is_allowed(self):
        assert parse_server("ldap://dc01:389/") == ("dc01", 389)

    def test_scheme_with_path_is_rejected(self):
        with pytest.raises(UsageError, match="unexpected path"):
            parse_server("ldap://dc01/dc=example,dc=com")

    def test_scheme_with_empty_host_is_usage_error(self):
        with pytest.raises(UsageError):
            parse_server("ldap://")
