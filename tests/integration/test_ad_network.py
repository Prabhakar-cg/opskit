"""Opt-in real-directory smoke tests (`pytest -m network`) — never gate CI.

Driven by the OPSKIT_AD_* environment: set OPSKIT_AD_SERVER (or OPSKIT_AD_DOMAIN),
OPSKIT_AD_USER, OPSKIT_AD_PASSWORD, and OPSKIT_AD_SMOKE_PRINCIPAL to a known account.
Validates the mock layer's assumptions (constructed attributes, AD bind sub-codes)
against a real domain controller.
"""

from __future__ import annotations

import os

import pytest

from opskit.ad import api
from opskit.ad.models import DirectoryConfig

pytestmark = pytest.mark.network


def _live_config() -> DirectoryConfig:
    server = os.environ.get("OPSKIT_AD_SERVER")
    domain = os.environ.get("OPSKIT_AD_DOMAIN")
    if not server and not domain:
        pytest.skip("set OPSKIT_AD_SERVER or OPSKIT_AD_DOMAIN for live AD smoke tests")
    return DirectoryConfig(
        server=server,
        domain=domain,
        bind_user=os.environ.get("OPSKIT_AD_USER"),
        password=os.environ.get("OPSKIT_AD_PASSWORD"),
        ca_file=None,
        timeout=10.0,
    )


def test_live_check_reaches_and_binds():
    with api.AdClient(_live_config()) as client:
        report = client.check()
    assert report.stages
    assert all(stage.ok for stage in report.stages)
    assert report.server_info.default_naming_context


def test_live_user_status_reads_computed_attributes():
    principal = os.environ.get("OPSKIT_AD_SMOKE_PRINCIPAL")
    if not principal:
        pytest.skip("set OPSKIT_AD_SMOKE_PRINCIPAL to a known account name")
    with api.AdClient(_live_config()) as client:
        report = client.user_status(principal)
    assert report.enabled is not None
    assert report.locked is not None  # constructed attribute readable on real AD


def test_live_membership_includes_primary_group():
    principal = os.environ.get("OPSKIT_AD_SMOKE_PRINCIPAL")
    if not principal:
        pytest.skip("set OPSKIT_AD_SMOKE_PRINCIPAL to a known account name")
    with api.AdClient(_live_config()) as client:
        report = client.membership(principal)
    assert any(entry.via == "primary" for entry in report.groups)
