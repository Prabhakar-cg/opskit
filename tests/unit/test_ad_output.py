"""Rendering tests: tables, verdict wording, and markup escaping of directory strings."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from rich.console import Console

from opskit.ad.models import (
    AccountStatusReport,
    ConnectivityReport,
    MembershipEntry,
    MembershipReport,
    MembershipVerdict,
    ObjectSummary,
    ServerInfo,
    Stage,
)
from opskit.ad.output import (
    render_check,
    render_member_verdict,
    render_membership,
    render_object,
    render_status,
)

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, no_color=True, width=200), buffer


def _status(**overrides) -> AccountStatusReport:
    values: dict = {
        "principal": "jdoe",
        "dn": "CN=J Doe,OU=Staff,DC=corp,DC=example,DC=com",
        "sam_account_name": "jdoe",
        "user_principal_name": "jdoe@corp.example.com",
        "enabled": True,
        "locked": False,
        "lockout_time": None,
        "lockout_stale_possible": False,
        "password_expired": False,
        "password_expires_at": _NOW,
        "password_never_expires": False,
        "must_change_password": False,
        "password_last_set": _NOW,
        "account_expires_at": None,
        "account_never_expires": True,
        "account_expired": False,
        "blockers": (),
        "facts_unavailable": (),
    }
    values.update(overrides)
    return AccountStatusReport(**values)


class TestRenderStatus:
    def test_clean_account(self):
        console, buffer = _console()
        render_status(_status(), console=console)
        text = buffer.getvalue()
        assert "no sign-in blockers" in text
        assert "account expires" in text
        assert "never" in text

    def test_all_blockers_listed(self):
        console, buffer = _console()
        render_status(
            _status(
                enabled=False,
                locked=True,
                blockers=("disabled", "locked_out"),
                lockout_time=_NOW,
            ),
            console=console,
        )
        text = buffer.getvalue()
        assert "2 blocker(s)" in text
        assert "disabled" in text
        assert "locked out" in text

    def test_stale_lockout_wording(self):
        console, buffer = _console()
        render_status(
            _status(locked=True, lockout_stale_possible=True, blockers=("locked_out",)),
            console=console,
        )
        assert "may have lapsed" in buffer.getvalue()

    def test_unavailable_facts_footer(self):
        console, buffer = _console()
        render_status(
            _status(enabled=None, facts_unavailable=("enabled",)), console=console
        )
        text = buffer.getvalue()
        assert "not available from this server" in text

    def test_markup_injection_escaped(self):
        console, buffer = _console()
        render_status(
            _status(sam_account_name="[bold]evil[/bold]"),
            console=console,
        )
        assert "[bold]evil[/bold]" in buffer.getvalue()  # literal, not styled


class TestRenderMembership:
    def test_effective_table_with_paths(self):
        console, buffer = _console()
        report = MembershipReport(
            principal="jdoe",
            dn="CN=J Doe,DC=x",
            effective=True,
            groups=(
                MembershipEntry("VPN Users", "CN=VPN Users,DC=x", "direct"),
                MembershipEntry("Domain Users", "CN=Domain Users,DC=x", "primary"),
                MembershipEntry(
                    "Remote Access",
                    "CN=Remote Access,DC=x",
                    "nested",
                    ("VPN Users",),
                ),
            ),
        )
        render_membership(report, console=console)
        text = buffer.getvalue()
        assert "effective group membership" in text
        assert "Remote Access" in text
        assert "VPN Users" in text
        assert "primary" in text

    def test_empty_membership(self):
        console, buffer = _console()
        render_membership(
            MembershipReport(principal="x", dn="CN=x", effective=False, groups=()),
            console=console,
        )
        assert "no group memberships" in buffer.getvalue()

    def test_group_name_injection_escaped(self):
        console, buffer = _console()
        report = MembershipReport(
            principal="jdoe",
            dn="CN=J Doe,DC=x",
            effective=False,
            groups=(
                MembershipEntry("[red]Fake[/red]", "CN=[red]Fake[/red],DC=x", "direct"),
            ),
        )
        render_membership(report, console=console)
        assert "[red]Fake[/red]" in buffer.getvalue()


class TestRenderVerdict:
    def test_member_with_chain(self):
        console, buffer = _console()
        verdict = MembershipVerdict(
            principal="jdoe",
            principal_dn="CN=J Doe,DC=x",
            group="Remote Access",
            group_dn="CN=Remote Access,DC=x",
            member=True,
            via="nested",
            path=("VPN Users",),
        )
        render_member_verdict(verdict, console=console)
        text = buffer.getvalue()
        assert "member" in text
        assert "VPN Users > Remote Access" in text

    def test_not_a_member(self):
        console, buffer = _console()
        verdict = MembershipVerdict(
            principal="jdoe",
            principal_dn="CN=J Doe,DC=x",
            group="Big Team",
            group_dn="CN=Big Team,DC=x",
            member=False,
        )
        render_member_verdict(verdict, console=console)
        assert "not a member" in buffer.getvalue()


class TestRenderCheck:
    def _report(self, **overrides) -> ConnectivityReport:
        values: dict = {
            "server_used": "dc01.corp.example.com",
            "port": 636,
            "security": "ldaps",
            "encrypted": True,
            "discovered": False,
            "candidates_tried": ("dc01.corp.example.com",),
            "stages": (
                Stage("reached", True, 3.2),
                Stage("secured", True, 11.8),
                Stage("authenticated", True, 4.4),
            ),
            "bind_user": "ops@corp.example.com",
            "server_info": ServerInfo(
                default_naming_context="DC=corp,DC=example,DC=com",
                dns_host_name="dc01.corp.example.com",
                supports_starttls=True,
                vendor=None,
            ),
        }
        values.update(overrides)
        return ConnectivityReport(**values)

    def test_staged_table(self):
        console, buffer = _console()
        render_check(self._report(), console=console)
        text = buffer.getvalue()
        assert "reached" in text
        assert "secured" in text
        assert "authenticated" in text
        assert "naming context" in text

    def test_plaintext_warning(self):
        console, buffer = _console()
        render_check(
            self._report(security="plaintext", encrypted=False), console=console
        )
        assert "NOT encrypted" in buffer.getvalue()

    def test_discovery_candidates_listed(self):
        console, buffer = _console()
        render_check(
            self._report(
                discovered=True,
                candidates_tried=("dead-dc.corp.example.com", "dc01.corp.example.com"),
            ),
            console=console,
        )
        assert "candidates tried" in buffer.getvalue()

    def test_anonymous_bind_shown(self):
        console, buffer = _console()
        render_check(self._report(bind_user=None), console=console)
        assert "anonymous" in buffer.getvalue()


class TestRenderObject:
    def test_group_members_table(self):
        console, buffer = _console()
        summary = ObjectSummary(
            name="VPN Users",
            dn="CN=VPN Users,DC=x",
            object_type="group",
            identifiers={
                "sam_account_name": "VPN Users",
                "user_principal_name": None,
                "sid": "S-1-5-21-1-2-3-1201",
            },
            created=_NOW,
            changed=_NOW,
            description="Remote access",
            type_facts={
                "group_kind": "security-global",
                "members": [{"name": "J Doe", "dn": "CN=J Doe,DC=x"}],
            },
        )
        render_object(summary, console=console)
        text = buffer.getvalue()
        assert "direct members (1)" in text
        assert "J Doe" in text
        assert "security-global" in text

    def test_user_facts_and_injection(self):
        console, buffer = _console()
        summary = ObjectSummary(
            name="[blink]x[/blink]",
            dn="CN=[blink]x[/blink],DC=x",
            object_type="user",
            identifiers={
                "sam_account_name": "x",
                "user_principal_name": "x@y",
                "sid": None,
            },
            created=None,
            changed=None,
            description=None,
            type_facts={
                "mail": "x@y",
                "display_name": None,
                "title": None,
                "department": None,
            },
        )
        render_object(summary, console=console)
        text = buffer.getvalue()
        assert "[blink]x[/blink]" in text
        assert "x@y" in text
