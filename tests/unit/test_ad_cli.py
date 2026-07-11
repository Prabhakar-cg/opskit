"""Tests for the ad CLI: envelopes, exit codes, batch contract, credentials, redaction."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.ad.errors import DependencyMissing
from opskit.cli import app

runner = CliRunner()

AD_BASE = "dc=corp,dc=example,dc=com"
AD_BIND_DN = f"cn=ops,cn=Users,{AD_BASE}"
AD_PASSWORD = "S3cret-Passw0rd!"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "OPSKIT_AD_SERVER",
        "OPSKIT_AD_DOMAIN",
        "OPSKIT_AD_USER",
        "OPSKIT_AD_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cli_directory(monkeypatch, ad_session_factory):
    """Route the CLI's connect path to the mock directory, with env credentials."""
    monkeypatch.setattr("opskit.ad.directory.connect_session", ad_session_factory)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    monkeypatch.setenv("OPSKIT_AD_USER", AD_BIND_DN)
    monkeypatch.setenv("OPSKIT_AD_PASSWORD", AD_PASSWORD)


def invoke(args, **kwargs):
    """Invoke `opskit ad ...` and run the redaction scan on everything captured."""
    result = runner.invoke(app, ["ad", *args, "--base-dn", AD_BASE], **kwargs)
    assert AD_PASSWORD not in result.output  # SC-006: the secret never surfaces
    return result


# --- ad user ------------------------------------------------------------------


def test_user_json_envelope_shape(cli_directory):
    result = invoke(["user", "jdoe", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "ad.user"
    assert payload["query"]["principal"] == "jdoe"
    assert payload["query"]["bind_user"] == AD_BIND_DN
    assert payload["query"]["security"] == "ldaps"
    assert "password" not in payload["query"]  # the secret has no envelope path
    assert payload["result"]["enabled"] is True
    assert payload["result"]["blockers"] == []
    assert payload["error"] is None


def test_user_human_table(cli_directory):
    result = invoke(["user", "jdoe", "--no-color"])
    assert result.exit_code == 0
    assert "no sign-in blockers" in result.output
    assert "enabled" in result.output
    assert "password last set" in result.output


def test_user_blockers_rendered(cli_directory):
    result = invoke(["user", "ddouble", "--no-color"])
    assert result.exit_code == 0  # the query succeeded; the verdict is the answer
    assert "sign-in blocked" in result.output
    assert "disabled" in result.output
    assert "locked" in result.output


def test_user_unknown_exits_not_found(cli_directory):
    result = invoke(["user", "no-such-user"])
    assert result.exit_code == 16
    assert "no user or computer account found" in result.output


def test_user_unknown_json_envelope_keeps_failure(cli_directory):
    result = invoke(["user", "no-such-user", "--json"])
    assert result.exit_code == 16
    payload = json.loads(result.output)
    assert payload["result"] is None
    assert payload["error"]["code"] == "principal_not_found"
    assert payload["error"]["hint"]


def test_user_ambiguous_is_usage_class(cli_directory):
    result = invoke(["user", "ambig"])
    assert result.exit_code == 2
    assert "more than one object" in result.output


def test_user_batch_mixed_partial(cli_directory):
    result = invoke(["user", "jdoe", "ddisabled", "no-such-user", "--jsonl"])
    assert result.exit_code == 7  # mixed outcomes -> PARTIAL (Art. IX)
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert len(lines) == 3
    assert lines[0]["result"]["blockers"] == []
    assert lines[1]["result"]["blockers"] == ["disabled"]
    assert lines[2]["result"] is None
    assert lines[2]["error"]["code"] == "principal_not_found"


def test_user_batch_uniform_failure_class(cli_directory):
    result = invoke(["user", "ghost1", "ghost2", "--jsonl"])
    assert result.exit_code == 16


def test_user_batch_fifty_over_one_session(monkeypatch, ad_session_factory):
    """SC-004: 50 principals, all reported, one bind."""
    calls: list[str] = []

    def counting(config, **kwargs):
        calls.append(kwargs["host"])
        return ad_session_factory(config, **kwargs)

    monkeypatch.setattr("opskit.ad.directory.connect_session", counting)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    monkeypatch.setenv("OPSKIT_AD_USER", AD_BIND_DN)
    monkeypatch.setenv("OPSKIT_AD_PASSWORD", AD_PASSWORD)
    names = ["jdoe" if i % 2 == 0 else "no-such-user" for i in range(50)]
    result = invoke(["user", *names, "--jsonl"])
    assert result.exit_code == 7
    assert len(result.output.strip().splitlines()) == 50
    assert len(calls) == 1


def test_user_stdin_batch_with_comments(cli_directory):
    result = invoke(
        ["user", "-i", "-", "--jsonl"],
        input="jdoe\n# a comment\n\nddisabled\n",
    )
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert [line["query"]["principal"] for line in lines] == ["jdoe", "ddisabled"]


def test_user_connect_failure_reported_for_every_principal(monkeypatch):
    def refused(config, **kwargs):
        from opskit.net.errors import ConnectRefused

        raise ConnectRefused("connection refused by fake-dc:636")

    monkeypatch.setattr("opskit.ad.directory.connect_session", refused)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    result = invoke(["user", "jdoe", "asmith", "--jsonl"])
    assert result.exit_code == 8
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert len(lines) == 2
    assert all(line["error"]["code"] == "connect_refused" for line in lines)


def test_user_no_principals_is_usage_error(cli_directory):
    result = invoke(["user"])
    assert result.exit_code == 2


def test_user_watch_flags_changes(cli_directory, monkeypatch):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("opskit.core.cliutils.time.sleep", interrupt)
    result = invoke(["user", "jdoe", "--watch", "1s", "--no-color"])
    assert result.exit_code == 0
    assert "initial" in result.output


# --- credentials --------------------------------------------------------------


def test_password_env_is_used(cli_directory):
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 0


def test_password_missing_on_piped_stdin_is_usage_error(
    monkeypatch, ad_session_factory
):
    monkeypatch.setattr("opskit.ad.directory.connect_session", ad_session_factory)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    monkeypatch.setenv("OPSKIT_AD_USER", AD_BIND_DN)
    # No OPSKIT_AD_PASSWORD; CliRunner stdin is not a TTY.
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 2
    assert "OPSKIT_AD_PASSWORD" in result.output


def test_password_prompted_on_tty(monkeypatch, ad_session_factory):
    monkeypatch.setattr("opskit.ad.directory.connect_session", ad_session_factory)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    monkeypatch.setenv("OPSKIT_AD_USER", AD_BIND_DN)

    prompts: list[str] = []

    def fake_prompt(text, hide_input=False):
        prompts.append(text)
        assert hide_input is True
        return AD_PASSWORD

    monkeypatch.setattr("opskit.ad.cli._stdin_is_tty", lambda: True)
    monkeypatch.setattr("opskit.ad.cli.typer.prompt", fake_prompt)
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 0
    assert prompts and AD_BIND_DN in prompts[0]


def test_anonymous_when_no_bind_user(cli_directory, monkeypatch):
    monkeypatch.delenv("OPSKIT_AD_USER")
    monkeypatch.delenv("OPSKIT_AD_PASSWORD")
    result = invoke(["user", "jdoe", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["query"]["bind_user"] is None


def test_wrong_password_exits_auth_failed(cli_directory, monkeypatch):
    monkeypatch.setenv("OPSKIT_AD_PASSWORD", "wrong-password")
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 14
    assert "rejected the credentials" in result.output


def test_missing_extra_hint(monkeypatch):
    def missing(config, **kwargs):
        raise DependencyMissing(
            "the Active Directory category needs the optional ldap3 dependency",
            hint='install it with: pip install "opskit[ad]"',
        )

    monkeypatch.setattr("opskit.ad.directory.connect_session", missing)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 2
    assert "opskit[ad]" in result.output


# --- connection modes -----------------------------------------------------------


def test_starttls_and_plaintext_conflict(cli_directory):
    result = invoke(["user", "jdoe", "--starttls", "--plaintext"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_plaintext_marked_unencrypted(cli_directory):
    result = invoke(["user", "jdoe", "--plaintext", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["query"]["security"] == "plaintext"
    assert payload["query"]["encrypted"] is False


def test_no_server_or_domain_is_usage_error():
    result = invoke(["user", "jdoe"])
    assert result.exit_code == 2
    assert "no directory given" in result.output


# --- ad groups / ad member -------------------------------------------------------


def test_groups_json(cli_directory):
    result = invoke(["groups", "jdoe", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "ad.groups"
    names = {group["name"] for group in payload["result"]["groups"]}
    assert {"VPN Users", "Staff All", "Domain Users"} <= names


def test_groups_effective_human(cli_directory):
    result = invoke(["groups", "jdoe", "--effective", "--no-color"])
    assert result.exit_code == 0
    assert "Remote Access" in result.output
    assert "nested" in result.output
    assert "primary" in result.output


def test_member_yes_exit_zero(cli_directory):
    result = invoke(["member", "jdoe", "VPN Users", "--no-color"])
    assert result.exit_code == 0
    assert "member" in result.output


def test_member_no_exit_seventeen(cli_directory):
    result = invoke(["member", "jdoe", "Big Team", "--no-color"])
    assert result.exit_code == 17
    assert "not a member" in result.output


def test_member_json_carries_verdict(cli_directory):
    result = invoke(["member", "jdoe", "Remote Access", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["result"]["member"] is True
    assert payload["result"]["via"] == "nested"
    assert payload["result"]["path"] == ["VPN Users"]


def test_member_unknown_group_is_not_found(cli_directory):
    result = invoke(["member", "jdoe", "No Such Group"])
    assert result.exit_code == 16


# --- ad show ---------------------------------------------------------------------


def test_show_user_includes_email(cli_directory):
    result = invoke(["show", "jdoe", "--no-color"])
    assert result.exit_code == 0
    assert "jdoe@corp.example.com" in result.output
    assert "SRE" in result.output


def test_show_group_lists_members(cli_directory):
    result = invoke(["show", "VPN Users", "--type", "group", "--no-color"])
    assert result.exit_code == 0
    assert "direct members" in result.output
    assert "J Doe" in result.output


def test_show_batch_mixed_types_jsonl(cli_directory):
    result = invoke(["show", "jdoe", "VPN Users", "wks-042$", "--jsonl"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert [line["result"]["object_type"] for line in lines] == [
        "user",
        "group",
        "computer",
    ]
    assert all(line["command"] == "ad.show" for line in lines)


def test_show_batch_failure_included(cli_directory):
    result = invoke(["show", "jdoe", "no-such-thing", "--jsonl"])
    assert result.exit_code == 7
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert lines[1]["result"] is None
    assert lines[1]["error"]["code"] == "principal_not_found"


def test_show_stdin_batch(cli_directory):
    result = invoke(["show", "-i", "-", "--jsonl"], input="jdoe\nVPN Users\n")
    assert result.exit_code == 0
    assert len(result.output.strip().splitlines()) == 2


def test_show_unknown_type_is_usage_error(cli_directory):
    result = invoke(["show", "jdoe", "--type", "printer"])
    assert result.exit_code == 2


# --- ad check --------------------------------------------------------------------


def test_check_json_stages(cli_directory):
    result = invoke(["check", "fake-dc.corp.example.com", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "ad.check"
    names = [stage["name"] for stage in payload["result"]["stages"]]
    assert names == ["reached", "secured", "authenticated"]
    assert payload["result"]["encrypted"] is True


def test_check_human(cli_directory):
    result = invoke(["check", "fake-dc.corp.example.com", "--no-color"])
    assert result.exit_code == 0
    assert "reached" in result.output
    assert "authenticated" in result.output
    assert "bind account" in result.output


def test_check_wrong_password_exit_fourteen(cli_directory, monkeypatch):
    monkeypatch.setenv("OPSKIT_AD_PASSWORD", "wrong-password")
    result = invoke(["check", "fake-dc.corp.example.com"])
    assert result.exit_code == 14


def test_check_conflicting_servers(cli_directory):
    result = invoke(["check", "dc-a", "--server", "dc-b"])
    assert result.exit_code == 2
    assert "conflicting servers" in result.output


def test_check_requires_server_or_domain():
    result = invoke(["check"])
    assert result.exit_code == 2


def test_check_positional_wins_over_domain_env(cli_directory, monkeypatch):
    monkeypatch.setenv("OPSKIT_AD_DOMAIN", "corp.example.com")
    result = invoke(["check", "fake-dc.corp.example.com", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["result"]["discovered"] is False
