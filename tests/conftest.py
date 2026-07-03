"""Shared test fixtures."""

from __future__ import annotations

import pytest

from opskit.dns.models import DnsRecord, RecordType


class MockResolver:
    """A resolver stub: preset records per type, a global error, or per-type errors."""

    def __init__(self, records=None, error=None, errors=None):
        self._records: dict[RecordType, list[DnsRecord]] = records or {}
        self._error = error
        self._errors = errors or {}

    def query(self, name, rtype, *, server, transport, timeout, retries, port):
        if self._error is not None:
            raise self._error
        if rtype in self._errors:
            raise self._errors[rtype]
        return tuple(self._records.get(rtype, ()))


@pytest.fixture
def make_resolver():
    """Return a factory building a MockResolver from records/error/errors."""

    def _make(records=None, error=None, errors=None):
        return MockResolver(records=records, error=error, errors=errors)

    return _make
