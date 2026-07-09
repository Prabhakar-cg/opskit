"""Real-endpoint smoke tests for net — opt-in only, never gate CI (@network)."""

from __future__ import annotations

import pytest

from opskit.net import check, probe
from opskit.net.errors import ConnectTimeout, UdpInconclusive
from opskit.net.models import Protocol, Verdict

pytestmark = pytest.mark.network


def test_check_public_https_endpoint_open():
    result = check("example.com:443", timeout=10.0)
    assert result.verdict is Verdict.OPEN
    assert result.time_ms > 0


def test_check_test_net_address_times_out_filtered():
    # 192.0.2.0/24 (TEST-NET-1) is a documentation range: never answers.
    with pytest.raises(ConnectTimeout):
        check("192.0.2.1:443", timeout=2.0, retries=0)


def test_udp_probe_of_blackhole_is_inconclusive():
    with pytest.raises(UdpInconclusive) as excinfo:
        check("192.0.2.1:123", protocol=Protocol.UDP, timeout=2.0, retries=0)
    assert "open or filtered" in excinfo.value.message


def test_probe_public_endpoint_statistics():
    result = probe("example.com:443", count=3, interval=0.2, timeout=10.0)
    assert result.completed == 3
    assert result.successes >= 1
    assert result.min_ms is not None
