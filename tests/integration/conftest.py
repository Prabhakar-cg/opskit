"""Fixtures for deterministic TLS integration tests (research R6).

Certificates are generated **at runtime** (nothing committed — Art. III) with a small CA
hierarchy: root -> intermediate -> leaf variants (valid / expired / not-yet-valid /
wrong-name / no-SAN / self-signed / short-lived). Loopback stdlib-ssl servers present them
so every failure class is reproducible without external network access.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import socket
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

_ONE_DAY = datetime.timedelta(days=1)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _build_cert(
    *,
    subject: x509.Name,
    issuer: x509.Name,
    public_key: ec.EllipticCurvePublicKey,
    signing_key: ec.EllipticCurvePrivateKey,
    not_before: datetime.datetime,
    not_after: datetime.datetime,
    is_ca: bool,
    sans: list[x509.GeneralName] | None,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(sans), critical=False
        )
    return builder.sign(signing_key, hashes.SHA256())


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


_LOCAL_SANS: list[x509.GeneralName] = [
    x509.DNSName("localhost"),
    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    x509.IPAddress(ipaddress.ip_address("::1")),
]


@dataclass(frozen=True)
class CertFixture:
    """Paths for one served identity: key + full chain PEM, plus the trust root."""

    key_file: Path
    chain_file: Path  # leaf (+ intermediate unless omitted)
    root_file: Path  # what a client should trust (--ca-file)


@dataclass(frozen=True)
class CertFactory:
    """All generated identities, ready to serve from loopback TLS servers."""

    valid: CertFixture
    expired: CertFixture
    not_yet_valid: CertFixture
    wrong_name: CertFixture
    no_san: CertFixture
    self_signed: CertFixture
    short_lived: CertFixture  # expires in 5 days -> inside default warn window
    missing_intermediate: CertFixture  # chain file omits the intermediate
    other_root_file: Path  # a root that did NOT issue anything above


@pytest.fixture(scope="session")
def cert_factory(tmp_path_factory: pytest.TempPathFactory) -> CertFactory:
    """Generate the CA hierarchy and leaf variants once per session."""
    outdir = tmp_path_factory.mktemp("tls-certs")
    now = datetime.datetime.now(datetime.timezone.utc)

    root_key = _new_key()
    root = _build_cert(
        subject=_name("opskit test root"),
        issuer=_name("opskit test root"),
        public_key=root_key.public_key(),
        signing_key=root_key,
        not_before=now - _ONE_DAY,
        not_after=now + 3650 * _ONE_DAY,
        is_ca=True,
        sans=None,
    )
    intermediate_key = _new_key()
    intermediate = _build_cert(
        subject=_name("opskit test intermediate"),
        issuer=root.subject,
        public_key=intermediate_key.public_key(),
        signing_key=root_key,
        not_before=now - _ONE_DAY,
        not_after=now + 1825 * _ONE_DAY,
        is_ca=True,
        sans=None,
    )
    root_file = outdir / "root.pem"
    root_file.write_bytes(_pem(root))
    other_root_key = _new_key()
    other_root = _build_cert(
        subject=_name("opskit unrelated root"),
        issuer=_name("opskit unrelated root"),
        public_key=other_root_key.public_key(),
        signing_key=other_root_key,
        not_before=now - _ONE_DAY,
        not_after=now + 3650 * _ONE_DAY,
        is_ca=True,
        sans=None,
    )
    other_root_file = outdir / "other-root.pem"
    other_root_file.write_bytes(_pem(other_root))

    def leaf_fixture(
        tag: str,
        *,
        not_before: datetime.datetime,
        not_after: datetime.datetime,
        sans: list[x509.GeneralName] | None,
        self_signed: bool = False,
        include_intermediate: bool = True,
    ) -> CertFixture:
        key = _new_key()
        if self_signed:
            cert = _build_cert(
                subject=_name(f"opskit {tag} leaf"),
                issuer=_name(f"opskit {tag} leaf"),
                public_key=key.public_key(),
                signing_key=key,
                not_before=not_before,
                not_after=not_after,
                is_ca=False,
                sans=sans,
            )
            chain = _pem(cert)
        else:
            cert = _build_cert(
                subject=_name(f"opskit {tag} leaf"),
                issuer=intermediate.subject,
                public_key=key.public_key(),
                signing_key=intermediate_key,
                not_before=not_before,
                not_after=not_after,
                is_ca=False,
                sans=sans,
            )
            chain = _pem(cert) + (_pem(intermediate) if include_intermediate else b"")
        key_file = outdir / f"{tag}.key"
        key_file.write_bytes(_key_pem(key))
        chain_file = outdir / f"{tag}.pem"
        chain_file.write_bytes(chain)
        return CertFixture(
            key_file=key_file, chain_file=chain_file, root_file=root_file
        )

    return CertFactory(
        valid=leaf_fixture(
            "valid",
            not_before=now - _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=_LOCAL_SANS,
        ),
        expired=leaf_fixture(
            "expired",
            not_before=now - 30 * _ONE_DAY,
            not_after=now - _ONE_DAY,
            sans=_LOCAL_SANS,
        ),
        not_yet_valid=leaf_fixture(
            "notyet",
            not_before=now + 30 * _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=_LOCAL_SANS,
        ),
        wrong_name=leaf_fixture(
            "wrongname",
            not_before=now - _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=[x509.DNSName("otherhost.test"), x509.DNSName("*.wild.test")],
        ),
        no_san=leaf_fixture(
            "nosan",
            not_before=now - _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=None,
        ),
        self_signed=leaf_fixture(
            "selfsigned",
            not_before=now - _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=_LOCAL_SANS,
            self_signed=True,
        ),
        short_lived=leaf_fixture(
            "shortlived",
            not_before=now - _ONE_DAY,
            not_after=now + 5 * _ONE_DAY,
            sans=_LOCAL_SANS,
        ),
        missing_intermediate=leaf_fixture(
            "nointermediate",
            not_before=now - _ONE_DAY,
            not_after=now + 365 * _ONE_DAY,
            sans=_LOCAL_SANS,
            include_intermediate=False,
        ),
        other_root_file=other_root_file,
    )


class LoopbackTlsServer:
    """A threaded stdlib-ssl server presenting a given identity on 127.0.0.1."""

    def __init__(self, fixture: CertFixture, *, host: str = "127.0.0.1") -> None:
        """Bind, wrap with the fixture's chain/key, and start accepting."""
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(
            certfile=str(fixture.chain_file), keyfile=str(fixture.key_file)
        )
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self._server = socket.socket(family, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, 0))
        self._server.listen(8)
        self.host = host
        self.port = int(self._server.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(3)
        try:
            with (
                self._context.wrap_socket(client, server_side=True) as tls,
                contextlib.suppress(OSError, ValueError),
            ):
                tls.recv(1)  # wait for client close; no data expected
        except (ssl.SSLError, OSError):
            pass  # client-side validation failures close abruptly; fine
        finally:
            with contextlib.suppress(OSError):
                client.close()

    def close(self) -> None:
        """Stop accepting and release the port."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def tls_server(cert_factory: CertFactory):
    """Factory fixture: start a loopback TLS server for a named cert variant."""
    servers: list[LoopbackTlsServer] = []

    def _start(variant: str, *, host: str = "127.0.0.1") -> LoopbackTlsServer:
        fixture: CertFixture = getattr(cert_factory, variant)
        server = LoopbackTlsServer(fixture, host=host)
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.close()


@pytest.fixture
def plain_tcp_server():
    """A loopback listener that talks plaintext immediately (non-TLS service)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    stop = threading.Event()

    def _serve() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with contextlib.suppress(OSError):
                client.sendall(b"220 plaintext service ready\r\n")
                client.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    yield ("127.0.0.1", int(server.getsockname()[1]))
    stop.set()
    with contextlib.suppress(OSError):
        server.close()
    thread.join(timeout=2)


@pytest.fixture
def closed_port() -> int:
    """A loopback port that is guaranteed closed (bound then released)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


# --- net fixtures (research R6): real loopback sockets, no external network ---


class TcpAcceptListener:
    """A threaded loopback TCP server that accepts and immediately closes."""

    def __init__(self) -> None:
        """Bind an ephemeral loopback port and start the accept loop."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(16)
        self.port = int(self._server.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with contextlib.suppress(OSError):
                conn.close()  # accept-then-immediately-close: still "open"

    def close(self) -> None:
        """Stop accepting and release the port."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def tcp_listener():
    """A running loopback TCP accept-listener (its port is open)."""
    server = TcpAcceptListener()
    yield server
    server.close()


class UdpEchoServer:
    """A threaded loopback UDP server echoing every datagram back to its sender."""

    def __init__(self) -> None:
        """Bind an ephemeral loopback port and start the echo loop."""
        self._server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._server.bind(("127.0.0.1", 0))
        self.port = int(self._server.getsockname()[1])
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, peer = self._server.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            with contextlib.suppress(OSError):
                self._server.sendto(data or b"pong", peer)

    def close(self) -> None:
        """Stop echoing and release the port."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def udp_echo():
    """A running loopback UDP echo server (its port replies -> "open")."""
    server = UdpEchoServer()
    yield server
    server.close()


@pytest.fixture
def udp_closed_port() -> int:
    """A loopback UDP port that is guaranteed closed (bound then released)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


@pytest.fixture
def free_port() -> int:
    """An ephemeral loopback port that is free to bind (allocated then released)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port
