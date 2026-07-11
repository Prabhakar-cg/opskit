"""Shared test fixtures."""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import socket

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from opskit.ad.directory import DirectorySession, _bind
from opskit.ad.models import DirectoryConfig, Stage
from opskit.dns.models import DnsRecord, RecordType
from opskit.net.errors import BindPermissionDenied, PortInUse
from opskit.net.listener import Listener


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


@pytest.fixture
def make_cert():
    """Return a factory building a self-signed x509.Certificate for unit tests.

    Generated at runtime (nothing committed); dns_names/ip_sans control the SANs,
    days shifts the validity window (negative -> already expired).
    """

    def _make(
        common_name="unit.test",
        *,
        dns_names=("unit.test",),
        ip_sans=(),
        days=365,
        not_before_days=-1,
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now + datetime.timedelta(days=not_before_days))
            .not_valid_after(now + datetime.timedelta(days=days))
        )
        sans = [x509.DNSName(dns) for dns in dns_names] + [
            x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_sans
        ]
        if sans:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(sans), critical=False
            )
        return builder.sign(key, hashes.SHA256())

    return _make


@pytest.fixture
def entered_listener():
    """Context-manager factory: an already-bound net Listener on a fresh port.

    CI's Windows runners reserve large ephemeral port ranges (WinNAT excluded
    port ranges); a wildcard bind on a port inside one fails with WinError
    10013 even unprivileged, surfacing as BindPermissionDenied. Retrying on a
    fresh port keeps listener tests deterministic instead of failing on the
    runner's port lottery. Read the bound port off ``listener.session.port``.
    """

    def _fresh_port() -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
        probe.close()
        return port

    @contextlib.contextmanager
    def _entered(**kwargs):
        last: Exception | None = None
        for _ in range(5):
            listener = Listener(_fresh_port(), **kwargs)
            try:
                listener.__enter__()
            except (PortInUse, BindPermissionDenied) as exc:
                last = exc
                continue
            try:
                yield listener
            finally:
                listener.__exit__(None, None, None)
            return
        pytest.skip(f"no bindable listener port on this runner: {last}")

    return _entered


# --- ad fixtures (research R8): ldap3 offline mock directory, no network -----------

AD_BASE = "dc=corp,dc=example,dc=com"
AD_BIND_DN = f"cn=ops,cn=Users,{AD_BASE}"
# The suite-wide redaction scan (SC-006) greps captured output for this secret.
AD_TEST_PASSWORD = "S3cret-Passw0rd!"

_FILETIME_EPOCH = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
FILETIME_NEVER = "9223372036854775807"


def to_filetime(dt: datetime.datetime) -> str:
    """Render a datetime as a FILETIME wire string (100 ns ticks since 1601)."""
    return str(int((dt - _FILETIME_EPOCH).total_seconds() * 10_000_000))


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def default_ad_entries() -> dict[str, dict]:
    """The fixture directory: status permutations, nested/cyclic groups, computer."""
    now = _now()
    recent = to_filetime(now - datetime.timedelta(days=40))
    lock_stamp = to_filetime(now - datetime.timedelta(hours=2))
    future = to_filetime(now + datetime.timedelta(days=30))
    past = to_filetime(now - datetime.timedelta(days=3))
    sid_prefix = "S-1-5-21-1111111111-2222222222-3333333333"

    def user(  # noqa: PLR0913 - one knob per status permutation axis
        sam: str,
        *,
        uac: int = 512,
        computed: int | None = 0,
        lockout: str | None = "0",
        pwd_last_set: str | None = None,
        expiry: str | None = None,
        account_expires: str | None = FILETIME_NEVER,
        member_of: list[str] | None = None,
        rid: int | None = None,
        extra: dict | None = None,
    ) -> dict:
        attrs: dict = {
            "objectClass": ["top", "person", "user"],
            "cn": sam,
            "sAMAccountName": sam,
            "userPrincipalName": f"{sam}@corp.example.com",
            "userAccountControl": str(uac),
            "whenCreated": "20230115083000.0Z",
            "whenChanged": "20260601090000.0Z",
        }
        if computed is not None:
            attrs["msDS-User-Account-Control-Computed"] = str(computed)
        if lockout is not None:
            attrs["lockoutTime"] = lockout
        attrs["pwdLastSet"] = pwd_last_set if pwd_last_set is not None else recent
        if expiry is not None:
            attrs["msDS-UserPasswordExpiryTimeComputed"] = expiry
        if account_expires is not None:
            attrs["accountExpires"] = account_expires
        if member_of:
            attrs["memberOf"] = member_of
        if rid is not None:
            attrs["objectSid"] = f"{sid_prefix}-{rid}"
            attrs["primaryGroupID"] = "513"
        if extra:
            attrs.update(extra)
        return attrs

    staff = f"ou=Staff,{AD_BASE}"
    groups = f"ou=Groups,{AD_BASE}"
    vpn = f"cn=VPN Users,{groups}"
    staff_all = f"cn=Staff All,{groups}"
    remote = f"cn=Remote Access,{groups}"
    cycle_a = f"cn=Cycle A,{groups}"
    cycle_b = f"cn=Cycle B,{groups}"
    jdoe_dn = f"cn=J Doe,{staff}"

    entries: dict[str, dict] = {
        AD_BIND_DN: {
            "objectClass": ["top", "person", "user"],
            "cn": "ops",
            "sAMAccountName": "ops",
            "userPassword": AD_TEST_PASSWORD,
        },
        jdoe_dn: user(
            "jdoe",
            expiry=future,
            member_of=[vpn, staff_all],
            rid=1104,
            extra={
                "mail": "jdoe@corp.example.com",
                "displayName": "J. Doe",
                "title": "SRE",
                "department": "Platform",
                "description": "Staff engineer",
            },
        ),
        f"cn=ddisabled,{staff}": user("ddisabled", uac=514, expiry=future),
        f"cn=dlocked,{staff}": user(
            "dlocked", computed=0x10, lockout=lock_stamp, expiry=future
        ),
        f"cn=dstale,{staff}": user(
            "dstale", computed=None, lockout=lock_stamp, expiry=future
        ),
        f"cn=dexpiredpw,{staff}": user("dexpiredpw", computed=0x800000, expiry=past),
        f"cn=dneverpw,{staff}": user(
            "dneverpw", uac=512 + 0x10000, expiry=FILETIME_NEVER
        ),
        f"cn=dmustchange,{staff}": user("dmustchange", pwd_last_set="0", expiry=future),
        f"cn=dacctexpired,{staff}": user(
            "dacctexpired", expiry=future, account_expires=past
        ),
        f"cn=ddouble,{staff}": user(
            "ddouble", uac=514, computed=0x10, lockout=lock_stamp, expiry=future
        ),
        f"cn=ddegraded,{staff}": {
            "objectClass": ["top", "person", "user"],
            "cn": "ddegraded",
            "sAMAccountName": "ddegraded",
        },
        f"cn=ambig,ou=A,{AD_BASE}": {
            "objectClass": ["top", "person", "user"],
            "cn": "ambig",
            "sAMAccountName": "ambig",
        },
        f"cn=ambig,ou=B,{AD_BASE}": {
            "objectClass": ["top", "person", "user"],
            "cn": "ambig2",
            "sAMAccountName": "ambig",
        },
        f"cn=wks-042$,ou=Machines,{AD_BASE}": {
            "objectClass": ["top", "person", "user", "computer"],
            "cn": "wks-042$",
            "sAMAccountName": "wks-042$",
            "dNSHostName": "wks-042.corp.example.com",
            "operatingSystem": "Windows 11 Enterprise",
            "operatingSystemVersion": "10.0 (26100)",
            "whenCreated": "20240301120000.0Z",
            "whenChanged": "20260520100000.0Z",
        },
        f"cn=Domain Users,cn=Users,{AD_BASE}": {
            "objectClass": ["top", "group"],
            "cn": "Domain Users",
            "sAMAccountName": "Domain Users",
            "objectSid": f"{sid_prefix}-513",
            "groupType": "-2147483646",
        },
        vpn: {
            "objectClass": ["top", "group"],
            "cn": "VPN Users",
            "sAMAccountName": "VPN Users",
            "groupType": "-2147483646",
            "description": "Remote-access VPN entitlement",
            "member": [jdoe_dn],
            "memberOf": [remote],
            "whenCreated": "20220101000000.0Z",
        },
        staff_all: {
            "objectClass": ["top", "group"],
            "cn": "Staff All",
            "sAMAccountName": "Staff All",
            "groupType": "-2147483646",
            "member": [jdoe_dn],
            "memberOf": [cycle_a],
        },
        remote: {
            "objectClass": ["top", "group"],
            "cn": "Remote Access",
            "sAMAccountName": "Remote Access",
            "groupType": "-2147483646",
            "member": [vpn],
        },
        cycle_a: {
            "objectClass": ["top", "group"],
            "cn": "Cycle A",
            "sAMAccountName": "Cycle A",
            "groupType": "-2147483646",
            "member": [staff_all, cycle_b],
            "memberOf": [cycle_b],
        },
        cycle_b: {
            "objectClass": ["top", "group"],
            "cn": "Cycle B",
            "sAMAccountName": "Cycle B",
            "groupType": "-2147483646",
            "member": [cycle_a],
            "memberOf": [cycle_a],
        },
        f"cn=Big Team,{groups}": {
            "objectClass": ["top", "group"],
            "cn": "Big Team",
            "sAMAccountName": "Big Team",
            "groupType": "-2147483646",
            "member": [f"cn=m{i:04d},ou=Bulk,{AD_BASE}" for i in range(1500)],
        },
    }
    return entries


def make_mock_connection(
    entries: dict[str, dict],
    *,
    user: str | None = AD_BIND_DN,
    password: str | None = AD_TEST_PASSWORD,
):
    """Build an unbound ldap3 MOCK_SYNC connection over the fixture entries."""
    import ldap3

    server = ldap3.Server("fake-dc.corp.example.com", get_info=ldap3.NONE)
    conn = ldap3.Connection(
        server,
        user=user,
        password=password,
        client_strategy=ldap3.MOCK_SYNC,
        authentication=ldap3.SIMPLE if user else ldap3.ANONYMOUS,
        auto_range=True,
        raise_exceptions=False,
    )
    for dn, attrs in entries.items():
        conn.strategy.add_entry(dn, attrs)
    return conn


def make_ad_config(**overrides) -> DirectoryConfig:
    """A DirectoryConfig aimed at the mock directory (base_dn preset)."""
    values: dict = {
        "server": "fake-dc.corp.example.com",
        "base_dn": AD_BASE,
        "bind_user": AD_BIND_DN,
        "password": AD_TEST_PASSWORD,
    }
    values.update(overrides)
    return DirectoryConfig(**values)


def make_mock_session_factory(entries: dict[str, dict] | None = None):
    """A directory.connect_session stand-in backed by the mock directory.

    Binds with the config's credentials so wrong-password paths raise the real
    AuthenticationFailed, and records plausible stage timings.
    """
    directory_entries = entries if entries is not None else default_ad_entries()

    def factory(config, *, host, port, stages=None):
        conn = make_mock_connection(
            directory_entries, user=config.bind_user, password=config.password
        )
        if stages is not None:
            stages.append(Stage("reached", True, 1.0))
            if config.security in ("ldaps", "starttls"):
                stages.append(Stage("secured", True, 1.0))
        _bind(conn, host=host, port=port)
        if stages is not None:
            stages.append(Stage("authenticated", True, 1.0))
        return DirectorySession(
            conn, host=host, port=port, config=config, stages=tuple(stages or ())
        )

    return factory


@pytest.fixture
def ad_entries() -> dict[str, dict]:
    """The default mock-directory entries (mutable per test)."""
    return default_ad_entries()


@pytest.fixture
def ad_session_factory(ad_entries):
    """A connect_session stand-in over the default fixture directory."""
    return make_mock_session_factory(ad_entries)


@pytest.fixture
def ad_config() -> DirectoryConfig:
    """A DirectoryConfig aimed at the mock directory."""
    return make_ad_config()


@pytest.fixture
def ad_client(ad_config, ad_session_factory):
    """An AdClient wired to the mock directory (one reusable session)."""
    from opskit.ad.api import AdClient

    client = AdClient(ad_config, session_factory=ad_session_factory)
    yield client
    client.close()


@pytest.fixture
def ad_conn_factory():
    """Factory building a MOCK_SYNC connection over given entries (unbound)."""
    return make_mock_connection


@pytest.fixture
def ad_config_factory():
    """Factory building DirectoryConfigs aimed at the mock directory."""
    return make_ad_config


@pytest.fixture
def ad_filetime():
    """The FILETIME wire-string helper, for building fixture values in tests."""
    return to_filetime
