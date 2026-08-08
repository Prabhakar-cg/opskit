"""Unit tests for the opskit.ad API over the offline mock directory (R8)."""

from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest

from opskit.ad import api
from opskit.ad.directory import DirectorySession
from opskit.ad.errors import (
    AmbiguousPrincipal,
    AuthenticationFailed,
    DiscoveryError,
    PrincipalIsGroup,
    PrincipalNotFound,
)
from opskit.ad.models import DirectoryConfig
from opskit.core.errors import UsageError

AD_BASE = "dc=corp,dc=example,dc=com"


class TestUserStatus:
    def test_healthy_account_has_no_blockers(self, ad_client):
        report = ad_client.user_status("jdoe")
        assert report.blockers == ()
        assert report.enabled is True
        assert report.locked is False
        assert report.password_expired is False
        assert report.password_expires_at is not None
        assert report.password_last_set is not None
        assert report.account_never_expires is True
        assert report.account_expired is False
        assert report.sam_account_name == "jdoe"
        assert report.dn.startswith("cn=J Doe")
        assert report.facts_unavailable == ()

    def test_upn_and_dn_forms_resolve(self, ad_client):
        by_upn = ad_client.user_status("jdoe@corp.example.com")
        by_dn = ad_client.user_status(f"cn=J Doe,ou=Staff,{AD_BASE}")
        assert by_upn.dn == by_dn.dn

    def test_disabled(self, ad_client):
        report = ad_client.user_status("ddisabled")
        assert report.enabled is False
        assert report.blockers == ("disabled",)

    def test_locked_with_time(self, ad_client):
        report = ad_client.user_status("dlocked")
        assert report.locked is True
        assert report.lockout_time is not None
        assert report.lockout_stale_possible is False
        assert report.blockers == ("locked_out",)

    def test_stale_lockout_flagged_when_computed_missing(self, ad_client):
        report = ad_client.user_status("dstale")
        assert report.locked is True
        assert report.lockout_stale_possible is True

    def test_password_expired(self, ad_client):
        report = ad_client.user_status("dexpiredpw")
        assert report.password_expired is True
        assert "password_expired" in report.blockers

    def test_password_never_expires(self, ad_client):
        report = ad_client.user_status("dneverpw")
        assert report.password_never_expires is True
        assert report.password_expires_at is None
        assert report.blockers == ()

    def test_must_change_password(self, ad_client):
        report = ad_client.user_status("dmustchange")
        assert report.must_change_password is True
        assert "must_change_password" in report.blockers
        assert report.password_last_set is None

    def test_account_expired(self, ad_client):
        report = ad_client.user_status("dacctexpired")
        assert report.account_expired is True
        assert report.account_expires_at is not None
        assert report.blockers == ("account_expired",)

    def test_simultaneous_blockers_all_reported(self, ad_client):
        report = ad_client.user_status("ddouble")
        assert set(report.blockers) >= {"disabled", "locked_out"}

    def test_non_ad_degradation(self, ad_client):
        report = ad_client.user_status("ddegraded")
        assert report.enabled is None
        assert report.locked is None
        assert "enabled" in report.facts_unavailable
        assert "locked" in report.facts_unavailable

    def test_unknown_principal(self, ad_client):
        with pytest.raises(PrincipalNotFound) as excinfo:
            ad_client.user_status("no-such-user")
        assert excinfo.value.hint is not None

    def test_group_identifier_redirects_instead_of_not_found(self, ad_client):
        """008-ad-enhancements US1: a group name gets a redirect, not bare not-found."""
        with pytest.raises(PrincipalIsGroup) as excinfo:
            ad_client.user_status("VPN Users")
        assert "VPN Users" in excinfo.value.message
        assert excinfo.value.hint is not None
        assert "ad members" in excinfo.value.hint
        assert "ad member" in excinfo.value.hint

    def test_truly_unknown_identifier_stays_generic_not_found(self, ad_client):
        """Regression: FR-002 — no group match either -> unchanged PrincipalNotFound."""
        with pytest.raises(PrincipalNotFound) as excinfo:
            ad_client.user_status("not-a-user-or-a-group")
        assert not isinstance(excinfo.value, PrincipalIsGroup)

    def test_group_dn_redirects_instead_of_not_found(self, ad_client):
        """A DN identifier is exact, but the redirect must still apply (code review)."""
        with pytest.raises(PrincipalIsGroup) as excinfo:
            ad_client.user_status(f"cn=VPN Users,ou=Groups,{AD_BASE}")
        assert "VPN Users" in excinfo.value.message

    def test_ambiguous_principal_lists_candidates(self, ad_client):
        with pytest.raises(AmbiguousPrincipal) as excinfo:
            ad_client.user_status("ambig")
        assert "ou=A" in excinfo.value.message
        assert "ou=B" in excinfo.value.message

    def test_computer_account_is_a_valid_principal(self, ad_client):
        report = ad_client.user_status("wks-042$")
        assert report.dn.startswith("cn=wks-042$")

    def test_mail_only_identifier_resolves(self, ad_client):
        """008-ad-enhancements US3 (FR-008): mail differs from userPrincipalName."""
        report = ad_client.user_status("jane.doe@example.com")
        assert report.sam_account_name == "dmailonly"

    def test_upn_and_mail_ambiguity_across_two_accounts(self, ad_client):
        """FR-009: one account's UPN equals another's mail -> still ambiguous."""
        with pytest.raises(AmbiguousPrincipal) as excinfo:
            ad_client.user_status("dupnshared@corp.example.com")
        assert "dupnshared" in excinfo.value.message
        assert "dmailshared" in excinfo.value.message

    def test_matching_both_attributes_on_one_entry_is_not_ambiguous(self, ad_client):
        """FR-010: jdoe's mail and userPrincipalName are already identical in the fixture."""
        report = ad_client.user_status("jdoe@corp.example.com")
        assert report.sam_account_name == "jdoe"

    def test_session_is_reused(self, ad_config, ad_session_factory):
        calls: list[str] = []

        def counting_factory(
            config: DirectoryConfig, **kwargs: Any
        ) -> DirectorySession:
            calls.append(kwargs["host"])
            return ad_session_factory(config, **kwargs)

        with api.AdClient(ad_config, session_factory=counting_factory) as client:
            client.user_status("jdoe")
            client.user_status("ddisabled")
            client.membership("jdoe")
        assert len(calls) == 1


class TestMembership:
    def test_direct_includes_primary_group(self, ad_client):
        report = ad_client.membership("jdoe")
        assert report.effective is False
        names = {(entry.name, entry.via) for entry in report.groups}
        assert ("VPN Users", "direct") in names
        assert ("Staff All", "direct") in names
        assert ("Domain Users", "primary") in names

    def test_effective_resolves_nesting_with_paths(self, ad_client):
        report = ad_client.membership("jdoe", effective=True)
        by_name = {entry.name: entry for entry in report.groups}
        remote = by_name["Remote Access"]
        assert remote.via == "nested"
        assert remote.path == ("VPN Users",)
        cycle_b = by_name["Cycle B"]
        assert cycle_b.via == "nested"
        assert cycle_b.path == ("Staff All", "Cycle A")

    def test_cycle_terminates_and_reports_each_group_once(self, ad_client):
        report = ad_client.membership("jdoe", effective=True)
        dns = [entry.dn.lower() for entry in report.groups]
        assert len(dns) == len(set(dns))
        names = {entry.name for entry in report.groups}
        assert {"Cycle A", "Cycle B"} <= names

    def test_empty_membership_is_success(self, ad_client):
        report = ad_client.membership("ddisabled")
        assert report.groups == ()

    def test_group_identifier_redirects_instead_of_not_found(self, ad_client):
        """008-ad-enhancements US1: same redirect applies to ad groups' lookup."""
        with pytest.raises(PrincipalIsGroup) as excinfo:
            ad_client.membership("VPN Users")
        assert "VPN Users" in excinfo.value.message


class TestIsMember:
    def test_direct_member(self, ad_client):
        verdict = ad_client.is_member("jdoe", "VPN Users")
        assert verdict.member is True
        assert verdict.via == "direct"
        assert verdict.path == ()

    def test_nested_member_has_chain(self, ad_client):
        verdict = ad_client.is_member("jdoe", "Remote Access")
        assert verdict.member is True
        assert verdict.via == "nested"
        assert verdict.path == ("VPN Users",)

    def test_primary_member(self, ad_client):
        verdict = ad_client.is_member("jdoe", "Domain Users")
        assert verdict.member is True
        assert verdict.via == "primary"

    def test_not_a_member(self, ad_client):
        verdict = ad_client.is_member("jdoe", "Big Team")
        assert verdict.member is False
        assert verdict.via is None

    def test_unknown_group(self, ad_client):
        with pytest.raises(PrincipalNotFound, match="group"):
            ad_client.is_member("jdoe", "No Such Group")

    def test_group_as_principal_redirects_instead_of_not_found(self, ad_client):
        """008-ad-enhancements US1: the principal argument gets the redirect too."""
        with pytest.raises(PrincipalIsGroup) as excinfo:
            ad_client.is_member("VPN Users", "VPN Users")
        assert "VPN Users" in excinfo.value.message


class TestMembers:
    """008-ad-enhancements US2: opskit.ad.AdClient.members() (the reverse of membership())."""

    def test_default_is_effective_nested(self, ad_client):
        report = ad_client.members("Remote Access")
        assert report.effective is True
        by_name = {entry.name: entry for entry in report.members}
        assert by_name["VPN Users"].via == "direct"
        assert by_name["VPN Users"].object_type == "group"
        assert by_name["J Doe"].via == "nested"
        assert by_name["J Doe"].path == ("VPN Users",)
        assert by_name["J Doe"].object_type == "user"

    def test_direct_only_excludes_nested(self, ad_client):
        report = ad_client.members("Remote Access", effective=False)
        assert report.effective is False
        names = {entry.name for entry in report.members}
        assert names == {"VPN Users"}

    def test_direct_only_skips_classification(self, ad_client):
        """--direct's fast path reports object_type="unknown" (no per-member read)."""
        report = ad_client.members("Remote Access", effective=False)
        entry = next(iter(report.members))
        assert entry.object_type == "unknown"
        assert report.to_dict()["members"][0]["object_type"] == "unknown"

    def test_cycle_terminates_and_reports_each_member_once(self, ad_client):
        report = ad_client.members("Cycle A")
        dns = [entry.dn.lower() for entry in report.members]
        assert len(dns) == len(set(dns))
        by_name = {entry.name: entry for entry in report.members}
        assert by_name["Staff All"].via == "direct"
        assert by_name["Cycle B"].via == "direct"
        assert by_name["J Doe"].via == "nested"
        assert by_name["J Doe"].path == ("Staff All",)
        # Cycle A must not reappear as one of its own (in)direct members.
        assert "Cycle A" not in by_name

    def test_empty_group_is_success(self, ad_client):
        report = ad_client.members("Domain Users")
        assert report.members == ()

    def test_unknown_group(self, ad_client):
        with pytest.raises(PrincipalNotFound, match="group"):
            ad_client.members("No Such Group")

    def test_ambiguous_or_principal_only_identifier(self, ad_client):
        """A group-scoped lookup for a pure user/computer identifier stays not-found."""
        with pytest.raises(PrincipalNotFound):
            ad_client.members("jdoe")

    def test_user_dn_is_not_found_not_silently_empty(self, ad_client):
        """A user's DN passed to a group-scoped lookup must not resolve (code review)."""
        with pytest.raises(PrincipalNotFound):
            ad_client.members(f"cn=J Doe,ou=Staff,{AD_BASE}")


class TestShow:
    def test_user_summary_includes_email(self, ad_client):
        summary = ad_client.show("jdoe")
        assert summary.object_type == "user"
        assert summary.type_facts["mail"] == "jdoe@corp.example.com"
        assert summary.type_facts["title"] == "SRE"
        assert summary.identifiers["sid"] is not None
        assert summary.identifiers["sid"].startswith("S-1-5-21-")
        assert summary.created is not None
        assert summary.description == "Staff engineer"

    def test_group_summary_lists_members_completely(self, ad_client):
        summary = ad_client.show("Big Team", object_type="group")
        assert summary.object_type == "group"
        assert summary.type_facts["group_kind"] == "security-global"
        members = summary.type_facts["members"]
        assert len(members) == 1500
        assert members[0]["name"] == "m0000"

    def test_computer_summary(self, ad_client):
        summary = ad_client.show("wks-042$")
        assert summary.object_type == "computer"
        assert summary.type_facts["dns_host_name"] == "wks-042.corp.example.com"
        assert summary.type_facts["operating_system"] == "Windows 11 Enterprise"

    def test_type_restriction(self, ad_client):
        with pytest.raises(PrincipalNotFound):
            ad_client.show("jdoe", object_type="group")

    def test_unknown_object_type_is_usage_error(self, ad_client):
        with pytest.raises(UsageError, match="unknown object type"):
            ad_client.show("jdoe", object_type="printer")


class TestCheck:
    def test_staged_report(self, ad_client):
        report = ad_client.check()
        assert report.server_used == "fake-dc.corp.example.com"
        assert report.port == 636
        assert report.encrypted is True
        assert report.discovered is False
        stage_names = [stage.name for stage in report.stages]
        assert stage_names == ["reached", "secured", "authenticated"]
        assert all(stage.ok for stage in report.stages)

    def test_wrong_password_raises_auth_failed(
        self, ad_config_factory, ad_session_factory
    ):
        config = ad_config_factory(password="wrong-password")
        with api.AdClient(config, session_factory=ad_session_factory) as client:
            with pytest.raises(AuthenticationFailed):
                client.check()

    def test_discovery_flow_reports_server_used(
        self, ad_config_factory, ad_session_factory, monkeypatch
    ):
        monkeypatch.setattr(
            "opskit.ad.api.discovery.discover_dcs",
            lambda domain, timeout: ["fake-dc.corp.example.com"],
        )
        config = ad_config_factory(server=None, domain="corp.example.com")
        with api.AdClient(config, session_factory=ad_session_factory) as client:
            report = client.check()
        assert report.discovered is True
        assert report.server_used == "fake-dc.corp.example.com"
        assert report.candidates_tried == ("fake-dc.corp.example.com",)

    def test_discovery_failure_propagates(
        self, ad_config_factory, ad_session_factory, monkeypatch
    ):
        def no_dcs(domain: str, timeout: float) -> list[str]:
            raise DiscoveryError(f"no directory servers found for domain: {domain}")

        monkeypatch.setattr("opskit.ad.api.discovery.discover_dcs", no_dcs)
        config = ad_config_factory(server=None, domain="empty.example.com")
        with api.AdClient(config, session_factory=ad_session_factory) as client:
            with pytest.raises(DiscoveryError):
                client.check()


class TestLibraryContract:
    def test_documented_example_runs(self, ad_config, ad_session_factory, capsys):
        """The contracts/python-api.md usage example, against the mock directory."""
        from opskit import ad

        with ad.AdClient(ad_config, session_factory=ad_session_factory) as client:
            report = client.check()
            status = client.user_status("jdoe")
            groups = client.membership("jdoe", effective=True)
            verdict = client.is_member("jdoe", "VPN Users")
            obj = client.show("VPN Users", object_type="group")
        assert report.server_used
        assert status.blockers == ()
        assert groups.effective is True
        assert verdict.member is True
        assert obj.object_type == "group"
        captured = capsys.readouterr()
        assert captured.out == ""  # the library layer never prints (Art. VII)
        assert captured.err == ""

    def test_convenience_functions(self, ad_config, ad_session_factory):
        from opskit import ad

        status = ad.user_status(
            "jdoe", config=ad_config, session_factory=ad_session_factory
        )
        assert status.enabled is True
        verdict = ad.is_member(
            "jdoe", "VPN Users", config=ad_config, session_factory=ad_session_factory
        )
        assert verdict.member is True
        summary = ad.show("jdoe", config=ad_config, session_factory=ad_session_factory)
        assert summary.type_facts["mail"] == "jdoe@corp.example.com"
        groups = ad.membership(
            "jdoe", config=ad_config, session_factory=ad_session_factory
        )
        assert groups.groups

    def test_api_never_reads_environment(
        self, ad_config_factory, ad_session_factory, monkeypatch
    ):
        monkeypatch.setenv("OPSKIT_AD_PASSWORD", "env-secret")
        monkeypatch.setenv("OPSKIT_AD_SERVER", "env-server.example.com")
        config = ad_config_factory(password="wrong-password")
        with api.AdClient(config, session_factory=ad_session_factory) as client:
            with pytest.raises(AuthenticationFailed):
                client.user_status("jdoe")  # env secret was NOT picked up

    def test_config_repr_and_dicts_never_leak_password(self, ad_config, ad_client):
        assert "S3cret-Passw0rd!" not in repr(ad_config)
        report = ad_client.user_status("jdoe")
        assert "S3cret-Passw0rd!" not in str(report.to_dict())

    def test_import_without_extra_is_safe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh opskit.ad import succeeds without ldap3; the first LDAP
        operation raises DependencyMissing (with the install hint) instead."""
        import opskit

        monkeypatch.setitem(sys.modules, "ldap3", None)  # blocks `import ldap3`
        # The fresh import below rebinds the `ad` attribute on the parent package;
        # register it with monkeypatch so teardown restores the original binding
        # (sys.modules alone is not enough — attribute lookups resolve through it).
        monkeypatch.setattr(opskit, "ad", opskit.ad)
        cached = [
            name
            for name in sys.modules
            if name == "opskit.ad" or name.startswith("opskit.ad.")
        ]
        for name in cached:
            monkeypatch.delitem(sys.modules, name)  # force real re-execution

        ad = importlib.import_module("opskit.ad")  # must not require ldap3

        fresh_errors = importlib.import_module("opskit.ad.errors")
        with pytest.raises(fresh_errors.DependencyMissing) as excinfo:
            ad.check(server="127.0.0.1:636", timeout=1.0)
        assert "opskit[ad]" in str(excinfo.value.hint)

    def test_usage_error_before_any_io(self):
        with pytest.raises(UsageError):
            api.user_status("jdoe")  # no server/domain given
