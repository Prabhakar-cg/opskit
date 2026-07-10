"""Unit tests for the opskit.ad API over the offline mock directory (R8)."""

from __future__ import annotations

import pytest

from opskit.ad import api
from opskit.ad.errors import (
    AmbiguousPrincipal,
    AuthenticationFailed,
    DiscoveryError,
    PrincipalNotFound,
)
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

    def test_ambiguous_principal_lists_candidates(self, ad_client):
        with pytest.raises(AmbiguousPrincipal) as excinfo:
            ad_client.user_status("ambig")
        assert "ou=A" in excinfo.value.message
        assert "ou=B" in excinfo.value.message

    def test_computer_account_is_a_valid_principal(self, ad_client):
        report = ad_client.user_status("wks-042$")
        assert report.dn.startswith("cn=wks-042$")

    def test_session_is_reused(self, ad_config, ad_session_factory):
        calls: list[str] = []

        def counting_factory(config, **kwargs):
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
        def no_dcs(domain, timeout):
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

    def test_import_without_extra_is_safe(self):
        import opskit.ad  # noqa: F401  (already imported; must not require ldap3)

    def test_usage_error_before_any_io(self):
        with pytest.raises(UsageError):
            api.user_status("jdoe")  # no server/domain given
