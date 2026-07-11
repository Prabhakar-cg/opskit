"""End-to-end ad flows over the ldap3 offline mock directory (research R8).

The unit layer pins each function; this layer walks the quickstart scenarios through
the real CLI stack (typer -> api -> adapter -> MOCK_SYNC) against the full fixture
topology: every status permutation, nesting with a cycle and a primary group, and the
beyond-paging-limit group.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opskit.cli import app

runner = CliRunner()

AD_BASE = "dc=corp,dc=example,dc=com"
AD_BIND_DN = f"cn=ops,cn=Users,{AD_BASE}"
AD_PASSWORD = "S3cret-Passw0rd!"


@pytest.fixture(autouse=True)
def _wire_mock(monkeypatch, ad_session_factory):
    monkeypatch.setattr("opskit.ad.directory.connect_session", ad_session_factory)
    monkeypatch.setenv("OPSKIT_AD_SERVER", "fake-dc.corp.example.com")
    monkeypatch.setenv("OPSKIT_AD_USER", AD_BIND_DN)
    monkeypatch.setenv("OPSKIT_AD_PASSWORD", AD_PASSWORD)


def invoke(args, **kwargs):
    result = runner.invoke(app, ["ad", *args, "--base-dn", AD_BASE], **kwargs)
    assert AD_PASSWORD not in result.output  # SC-006 redaction scan
    return result


STATUS_MATRIX = [
    ("jdoe", 0, []),
    ("ddisabled", 0, ["disabled"]),
    ("dlocked", 0, ["locked_out"]),
    ("dstale", 0, ["locked_out"]),
    ("dexpiredpw", 0, ["password_expired"]),
    ("dneverpw", 0, []),
    ("dmustchange", 0, ["must_change_password"]),
    ("dacctexpired", 0, ["account_expired"]),
    ("ddouble", 0, ["disabled", "locked_out"]),
    ("ddegraded", 0, []),
]


@pytest.mark.parametrize(("principal", "exit_code", "blockers"), STATUS_MATRIX)
def test_status_permutations_via_cli(principal, exit_code, blockers):
    result = invoke(["user", principal, "--json"])
    assert result.exit_code == exit_code
    payload = json.loads(result.output)
    assert payload["result"]["blockers"] == blockers


def test_degraded_account_reports_unavailable_facts():
    result = invoke(["user", "ddegraded", "--json"])
    payload = json.loads(result.output)
    assert "enabled" in payload["result"]["facts_unavailable"]
    assert payload["result"]["enabled"] is None


def test_quickstart_membership_flow():
    """quickstart §3: direct + primary, nesting with paths, cycle-safe, verdicts."""
    direct = json.loads(invoke(["groups", "jdoe", "--json"]).output)
    names = {group["name"]: group["via"] for group in direct["result"]["groups"]}
    assert names == {
        "VPN Users": "direct",
        "Staff All": "direct",
        "Domain Users": "primary",
    }

    effective = json.loads(invoke(["groups", "jdoe", "--effective", "--json"]).output)
    by_name = {group["name"]: group for group in effective["result"]["groups"]}
    assert by_name["Remote Access"]["path"] == ["VPN Users"]
    assert by_name["Cycle B"]["path"] == ["Staff All", "Cycle A"]
    dns = [group["dn"] for group in effective["result"]["groups"]]
    assert len(dns) == len(set(dns))  # each group exactly once despite the cycle

    assert invoke(["member", "jdoe", "VPN Users"]).exit_code == 0
    assert invoke(["member", "jdoe", "Remote Access"]).exit_code == 0
    assert invoke(["member", "jdoe", "Domain Users"]).exit_code == 0
    assert invoke(["member", "jdoe", "Big Team"]).exit_code == 17


def test_big_group_membership_is_complete():
    payload = json.loads(
        invoke(["show", "Big Team", "--type", "group", "--json"]).output
    )
    assert len(payload["result"]["type_facts"]["members"]) == 1500


def test_quickstart_bulk_show_flow():
    """quickstart §5: mixed users/groups/computer in one run, one envelope each."""
    result = invoke(["show", "jdoe", "VPN Users", "wks-042$", "--jsonl"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    kinds = {line["query"]["name"]: line["result"]["object_type"] for line in lines}
    assert kinds == {"jdoe": "user", "VPN Users": "group", "wks-042$": "computer"}
    user_line = next(line for line in lines if line["query"]["name"] == "jdoe")
    assert user_line["result"]["type_facts"]["mail"] == "jdoe@corp.example.com"


def test_quickstart_batch_partial_flow():
    """quickstart §5: mixed batch -> every envelope present, exit 7."""
    result = invoke(
        ["user", "-i", "-", "--jsonl"], input="jdoe\nasmith\nno-such-user\n"
    )
    assert result.exit_code == 7
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert len(lines) == 3
    failures = [line for line in lines if line["result"] is None]
    assert len(failures) == 2  # asmith and no-such-user don't exist in the fixture
    assert all(line["error"]["code"] == "principal_not_found" for line in failures)


def test_check_stages_via_cli():
    result = invoke(["check", "fake-dc.corp.example.com", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [stage["name"] for stage in payload["result"]["stages"]] == [
        "reached",
        "secured",
        "authenticated",
    ]
    assert all(stage["ok"] for stage in payload["result"]["stages"])
