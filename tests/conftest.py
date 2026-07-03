"""Shared test fixtures."""

from __future__ import annotations

import pytest

from opskit.dns.models import DnsRecord, RecordType


class MockResolver:
    """A resolver stub returning preset records per type, or raising a preset error."""

    def __init__(self, records=None, error=None):
        self._records: dict[RecordType, list[DnsRecord]] = records or {}
        self._error = error

    def query(self, name, rtype, *, server, transport, timeout, retries, port):
        if self._error is not None:
            raise self._error
        return tuple(self._records.get(rtype, ()))


@pytest.fixture
def make_resolver():
    """Return a factory building a MockResolver from records/error."""

    def _make(records=None, error=None):
        return MockResolver(records=records, error=error)

    return _make
