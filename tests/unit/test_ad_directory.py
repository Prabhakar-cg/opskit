"""Unit tests for the ldap3 adapter: error normalization, bind mapping, search paging."""

from __future__ import annotations

import socket
import ssl
from typing import ClassVar

import pytest

from opskit.ad import directory
from opskit.ad.errors import (
    AdError,
    AuthenticationFailed,
    PermissionDenied,
)
from opskit.ad.models import DirectoryConfig
from opskit.core.errors import OpskitError
from opskit.net.errors import ConnectRefused, ConnectTimeout, ResolutionError
from opskit.tls.errors import CertificateInvalid, HandshakeError

AD_BASE = "dc=corp,dc=example,dc=com"
AD_BIND_DN = f"cn=ops,cn=Users,{AD_BASE}"


class TestClassifyConnectError:
    """Every raw exception family normalizes into the shared hierarchy (Art. VI)."""

    def _classify(self, exc: BaseException) -> OpskitError:
        return directory.classify_connect_error(exc, host="dc01", port=636)

    def test_connection_refused_instance(self):
        error = self._classify(ConnectionRefusedError(111, "refused"))
        assert isinstance(error, ConnectRefused)
        assert "dc01:636" in error.message

    def test_timeout_instance(self):
        assert isinstance(self._classify(socket.timeout("timed out")), ConnectTimeout)
        assert isinstance(self._classify(TimeoutError()), ConnectTimeout)

    def test_gaierror_is_resolution_failure(self):
        error = self._classify(socket.gaierror(-2, "Name or service not known"))
        assert isinstance(error, ResolutionError)
        assert "dc01" in error.message

    def test_ssl_cert_verification(self):
        error = self._classify(
            ssl.SSLCertVerificationError("certificate verify failed")
        )
        assert isinstance(error, CertificateInvalid)
        assert error.hint is not None
        assert "opskit tls check" in error.hint

    def test_ssl_generic_is_handshake(self):
        assert isinstance(self._classify(ssl.SSLError("bad handshake")), HandshakeError)

    def test_nested_cause_is_found(self):
        outer = RuntimeError("wrapped by ldap3")
        outer.__cause__ = ConnectionRefusedError(111, "refused")
        assert isinstance(self._classify(outer), ConnectRefused)

    def test_exception_nested_in_args(self):
        # ldap3 packs per-candidate errors into exception args.
        outer = RuntimeError([ConnectionRefusedError(111, "no"), ValueError("x")])
        assert isinstance(self._classify(outer), ConnectRefused)

    def test_text_markers(self):
        assert isinstance(
            self._classify(RuntimeError("connection refused by peer")), ConnectRefused
        )
        assert isinstance(
            self._classify(RuntimeError("operation timed out")), ConnectTimeout
        )
        assert isinstance(
            self._classify(RuntimeError("certificate verify failed: self signed")),
            CertificateInvalid,
        )
        assert isinstance(
            self._classify(RuntimeError("error wrapping socket for tls")),
            HandshakeError,
        )
        assert isinstance(
            self._classify(RuntimeError("invalid server address dc01")),
            ResolutionError,
        )

    def test_unknown_becomes_ad_error(self):
        error = self._classify(RuntimeError("something odd happened"))
        assert isinstance(error, AdError)
        assert not isinstance(error, (ConnectRefused, ConnectTimeout))


class _FakeConn:
    """Minimal connection double for bind-mapping tests."""

    def __init__(self, bind_ok: bool, result: dict):
        self._bind_ok = bind_ok
        self.result = result

    def bind(self) -> bool:
        return self._bind_ok


class TestBindMapping:
    def test_invalid_credentials_maps_to_auth_failed(self):
        conn = _FakeConn(False, {"result": 49, "message": "80090308: data 52e, v4563"})
        with pytest.raises(AuthenticationFailed) as excinfo:
            directory._bind(conn, host="dc01", port=636)
        assert excinfo.value.hint is not None
        assert "bad password" in excinfo.value.hint

    def test_locked_out_sub_code_decoded(self):
        conn = _FakeConn(False, {"result": 49, "message": "80090308: data 775, v4563"})
        with pytest.raises(AuthenticationFailed) as excinfo:
            directory._bind(conn, host="dc01", port=636)
        assert "locked out" in str(excinfo.value.hint)

    def test_no_sub_code_gets_generic_hint(self):
        conn = _FakeConn(False, {"result": 49, "message": ""})
        with pytest.raises(AuthenticationFailed) as excinfo:
            directory._bind(conn, host="dc01", port=636)
        assert "account name" in str(excinfo.value.hint)

    def test_other_failure_is_ad_error(self):
        conn = _FakeConn(False, {"result": 53, "description": "unwillingToPerform"})
        with pytest.raises(AdError, match="unwillingToPerform"):
            directory._bind(conn, host="dc01", port=636)

    def test_success_returns(self):
        directory._bind(_FakeConn(True, {}), host="dc01", port=636)


class TestDependencyMissing:
    def test_import_failure_becomes_typed_hint(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "ldap3" or name.startswith("ldap3."):
                raise ImportError("No module named 'ldap3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        from opskit.ad.errors import DependencyMissing

        with pytest.raises(DependencyMissing) as excinfo:
            directory._ldap3()
        assert 'pip install "opskit[ad]"' in str(excinfo.value.hint)


@pytest.fixture
def session(
    ad_conn_factory, ad_entries, ad_config_factory
) -> directory.DirectorySession:
    conn = ad_conn_factory(ad_entries)
    assert conn.bind()
    return directory.DirectorySession(
        conn, host="fake-dc", port=636, config=ad_config_factory()
    )


class TestSearch:
    def test_search_returns_typed_entries(self, session):
        entries = session.search(
            base=AD_BASE,
            ldap_filter="(sAMAccountName=jdoe)",
            attributes=["sAMAccountName", "userAccountControl"],
        )
        assert len(entries) == 1
        entry = entries[0]
        assert entry.dn.startswith("cn=J Doe")
        assert entry.first("samaccountname") == "jdoe"  # case-insensitive access
        assert entry.first("sAMAccountName") == "jdoe"
        assert entry.first("missing") is None
        assert entry.values("missing") == []

    def test_nonexistent_base_returns_empty(self, session):
        assert session.read_entry(f"cn=ghost,{AD_BASE}", attributes=["cn"]) is None

    def test_permission_denied_mapping(self, session):

        class _DenyConn:
            result: ClassVar[dict] = {
                "result": 50,
                "description": "insufficientAccessRights",
            }
            response: ClassVar[list] = []

            def search(self, **kwargs):
                return False

        session._conn = _DenyConn()
        with pytest.raises(PermissionDenied):
            session.search(base=AD_BASE, ldap_filter="(cn=x)", attributes=["cn"])

    def test_other_result_code_is_ad_error(self, session):

        class _BadConn:
            result: ClassVar[dict] = {"result": 1, "description": "operationsError"}
            response: ClassVar[list] = []

            def search(self, **kwargs):
                return False

        session._conn = _BadConn()
        with pytest.raises(AdError, match="operationsError"):
            session.search(base=AD_BASE, ldap_filter="(cn=x)", attributes=["cn"])

    def test_default_base_prefers_config_override(self, session):
        assert session.default_base() == AD_BASE

    def test_default_base_without_override_or_rootdse(
        self, ad_conn_factory, ad_entries
    ):
        config = DirectoryConfig(server="fake-dc", bind_user=None, password=None)
        conn = ad_conn_factory(ad_entries, user=None, password=None)
        assert conn.bind()
        session = directory.DirectorySession(
            conn, host="fake-dc", port=636, config=config
        )
        with pytest.raises(AdError, match="search base"):
            session.default_base()

    def test_server_info_degrades_to_none_fields(self, session):
        info = session.server_info()
        assert info.default_naming_context is None
        assert info.vendor is None

    def test_close_is_idempotent(self, session):
        session.close()
        session.close()
