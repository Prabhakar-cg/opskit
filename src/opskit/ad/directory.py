# pyright: basic
# ^ ldap3 ships no py.typed/stubs; this adapter is the single quarantine module for it
#   (research R9). Its public surface exposes only opskit types; pyright runs the rest
#   of the package in strict mode and mypy scopes an override to ldap3.* only.
"""The single ldap3 adapter: staged connect, bind, paged search, error normalization.

This is the **only** module that imports ldap3 — and only lazily, so the base install
works without the ``opskit[ad]`` extra (a missing dependency surfaces as
:class:`~opskit.ad.errors.DependencyMissing` with an install hint). Every ldap3 or
socket exception is normalized into the shared typed hierarchy before it leaves this
module (Art. VI): connection failures reuse :mod:`opskit.net.errors`, TLS failures
reuse :mod:`opskit.tls.errors`, directory outcomes raise :mod:`opskit.ad.errors`.
Only bind and search operations are ever issued (Art. X: strictly read-only).
"""

from __future__ import annotations

import contextlib
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any

from opskit.ad.errors import (
    AdError,
    AuthenticationFailed,
    DependencyMissing,
    PermissionDenied,
    decode_bind_data,
)
from opskit.ad.models import DirectoryConfig, ServerInfo, Stage
from opskit.core.errors import OpskitError
from opskit.net.errors import ConnectRefused, ConnectTimeout, ResolutionError
from opskit.tls.errors import CertificateInvalid, HandshakeError

_PAGED_SIZE = 500

_RESULT_SUCCESS = 0
_RESULT_NO_SUCH_OBJECT = 32
_RESULT_INVALID_CREDENTIALS = 49
_RESULT_INSUFFICIENT_ACCESS = 50

_ROOT_DSE_ATTRIBUTES = (
    "defaultNamingContext",
    "dnsHostName",
    "supportedExtension",
    "vendorName",
)
_STARTTLS_OID = "1.3.6.1.4.1.1466.20037"


def _ldap3() -> tuple[Any, Any]:
    """Import ldap3 lazily, translating absence into the typed install hint."""
    try:
        import ldap3
        from ldap3.core import exceptions as ldap3_exceptions
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise DependencyMissing(
            "the Active Directory category needs the optional ldap3 dependency",
            hint='install it with: pip install "opskit[ad]"',
        ) from exc
    return ldap3, ldap3_exceptions


@dataclass(frozen=True)
class DirectoryEntry:
    """One directory object: DN plus a case-insensitive attribute map."""

    dn: str
    attributes: dict[str, Any]  # keys lower-cased at construction

    def values(self, name: str) -> list[Any]:
        """Return an attribute's values as a list (empty when absent)."""
        value = self.attributes.get(name.lower())
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def first(self, name: str) -> Any:
        """Return an attribute's first value, or ``None`` when absent/empty."""
        values = self.values(name)
        return values[0] if values else None


def _entry_from_response(item: dict[str, Any]) -> DirectoryEntry:
    """Build a :class:`DirectoryEntry` from one raw ldap3 search-response item."""
    raw_attributes = item.get("attributes") or {}
    attributes = {str(key).lower(): raw_attributes[key] for key in raw_attributes}
    return DirectoryEntry(dn=str(item.get("dn", "")), attributes=attributes)


def _walk_causes(exc: BaseException) -> list[BaseException]:
    """Flatten an exception with its cause/context/args chain for classification."""
    seen: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.append(current)
        for nested in (current.__cause__, current.__context__):
            if nested is not None:
                stack.append(nested)
        for arg in getattr(current, "args", ()):  # ldap3 nests per-candidate errors
            if isinstance(arg, BaseException):
                stack.append(arg)
            elif isinstance(arg, (list, tuple)):
                stack.extend(a for a in arg if isinstance(a, BaseException))
    return seen


def classify_connect_error(  # noqa: PLR0911 - one return per documented outcome class
    exc: BaseException, *, host: str, port: int
) -> OpskitError:
    """Normalize a connect/TLS-stage exception into the shared typed hierarchy.

    Inspects the exception chain first (exact stdlib types), then falls back to
    message markers (ldap3 frequently stringifies the original error). A raw
    ldap3/socket/ssl exception never escapes this module (Art. VI).
    """
    target = f"{host}:{port}"
    for cause in _walk_causes(exc):
        if isinstance(cause, ssl.SSLCertVerificationError):
            return CertificateInvalid(
                f"certificate verification failed for {target}: {cause.verify_message}"
                if getattr(cause, "verify_message", None)
                else f"certificate verification failed for {target}",
                hint=(
                    f"inspect it with: opskit tls check {target}; "
                    "for a private CA pass --ca-file"
                ),
            )
        if isinstance(cause, ssl.SSLError):
            return HandshakeError(
                f"TLS handshake failed with {target}",
                hint="the server may not offer TLS on this port — try --starttls "
                "on 389, or check the port",
            )
        if isinstance(cause, ConnectionRefusedError):
            return ConnectRefused(
                f"connection refused by {target}",
                hint="nothing is listening there — check the port for the security "
                "mode (LDAPS 636, StartTLS/plaintext 389)",
            )
        if isinstance(cause, (socket.timeout, TimeoutError)):
            return ConnectTimeout(
                f"no response from {target} before the timeout",
                hint=f"host down or port filtered — try: opskit net check {target}",
            )
        if isinstance(cause, socket.gaierror):
            return ResolutionError(
                f"cannot resolve server name: {host}",
                hint=f"check the name — try: opskit dns lookup {host}",
            )
    text = str(exc).lower()
    if "certificate" in text:
        return CertificateInvalid(
            f"certificate verification failed for {target}",
            hint=f"inspect it with: opskit tls check {target}; "
            "for a private CA pass --ca-file",
        )
    if any(marker in text for marker in ("ssl", "tls", "handshake", "wrap socket")):
        return HandshakeError(
            f"TLS handshake failed with {target}",
            hint="the server may not offer TLS on this port — try --starttls "
            "on 389, or check the port",
        )
    if "refused" in text or "unreachable" in text:
        return ConnectRefused(
            f"connection refused by {target}",
            hint="nothing is listening there — check the port for the security "
            "mode (LDAPS 636, StartTLS/plaintext 389)",
        )
    if "timed out" in text or "timeout" in text:
        return ConnectTimeout(
            f"no response from {target} before the timeout",
            hint=f"host down or port filtered — try: opskit net check {target}",
        )
    if "invalid server address" in text:
        return ResolutionError(
            f"cannot resolve server name: {host}",
            hint=f"check the name — try: opskit dns lookup {host}",
        )
    return AdError(f"cannot connect to {target}: {exc}")


class DirectorySession:
    """A bound, reusable directory connection exposing typed read-only operations.

    Produced by :func:`connect_session` (or built directly around a prepared ldap3
    connection in tests). Never opens more than the one connection it wraps.
    """

    def __init__(
        self,
        conn: Any,
        *,
        host: str,
        port: int,
        config: DirectoryConfig,
        stages: tuple[Stage, ...] = (),
    ) -> None:
        """Wrap a bound ldap3 connection with its origin metadata."""
        self._conn = conn
        self.host = host
        self.port = port
        self.config = config
        self.stages = stages
        self._root_dse: DirectoryEntry | None = None
        self._root_dse_read = False

    def close(self) -> None:
        """Unbind and close the underlying connection (idempotent)."""
        with contextlib.suppress(Exception):  # best-effort cleanup on teardown
            self._conn.unbind()

    # -- rootDSE ---------------------------------------------------------------

    def _read_root_dse(self) -> DirectoryEntry | None:
        """Read the rootDSE once (best effort; some servers restrict it)."""
        if self._root_dse_read:
            return self._root_dse
        self._root_dse_read = True
        try:
            entries = self.search(
                base="",
                ldap_filter="(objectClass=*)",
                scope="base",
                attributes=list(_ROOT_DSE_ATTRIBUTES),
            )
        except OpskitError:
            entries = []
        self._root_dse = entries[0] if entries else None
        return self._root_dse

    def server_info(self) -> ServerInfo:
        """Return basic server identity from the rootDSE (fields None when unknown)."""
        root = self._read_root_dse()
        if root is None:
            return ServerInfo()
        extensions = [str(value) for value in root.values("supportedExtension")]
        naming = root.first("defaultNamingContext")
        dns_host = root.first("dnsHostName")
        vendor = root.first("vendorName")
        return ServerInfo(
            default_naming_context=str(naming) if naming is not None else None,
            dns_host_name=str(dns_host) if dns_host is not None else None,
            supports_starttls=(_STARTTLS_OID in extensions) if extensions else None,
            vendor=str(vendor) if vendor is not None else None,
        )

    def default_base(self) -> str:
        """Return the search base: the config override or the server's default.

        Raises:
            AdError: When neither a ``base_dn`` nor a readable
                ``defaultNamingContext`` is available.
        """
        if self.config.base_dn:
            return self.config.base_dn
        root = self._read_root_dse()
        naming = root.first("defaultNamingContext") if root is not None else None
        if naming:
            return str(naming)
        raise AdError(
            "could not determine the search base from the server",
            hint="pass --base-dn (e.g. DC=corp,DC=example,DC=com)",
        )

    # -- search ----------------------------------------------------------------

    def search(
        self,
        *,
        base: str,
        ldap_filter: str,
        attributes: list[str],
        scope: str = "subtree",
    ) -> list[DirectoryEntry]:
        """Run a read-only search, following paging cookies until complete (FR-012).

        Args:
            base: Search base DN ('' for the rootDSE).
            ldap_filter: An already-escaped LDAP filter.
            attributes: Attributes to request.
            scope: ``"subtree"`` or ``"base"``.

        Raises:
            PermissionDenied: When the bound account may not read there.
            AdError: For any other directory-reported failure.
        """
        ldap3, ldap3_exceptions = _ldap3()
        search_scope = ldap3.BASE if scope == "base" else ldap3.SUBTREE
        entries: list[DirectoryEntry] = []
        cookie: bytes | None = None
        while True:
            try:
                self._conn.search(
                    search_base=base,
                    search_filter=ldap_filter,
                    search_scope=search_scope,
                    attributes=attributes,
                    paged_size=_PAGED_SIZE,
                    paged_cookie=cookie,
                )
            except ldap3_exceptions.LDAPException as exc:
                raise classify_connect_error(
                    exc, host=self.host, port=self.port
                ) from exc
            result: dict[str, Any] = dict(self._conn.result or {})
            code = int(result.get("result", _RESULT_SUCCESS))
            if code == _RESULT_INSUFFICIENT_ACCESS:
                raise PermissionDenied(
                    "the bound account is not authorized to read this",
                    hint="try credentials with directory read rights",
                )
            if code not in (_RESULT_SUCCESS, _RESULT_NO_SUCH_OBJECT):
                description = str(result.get("description", "")) or "unknown error"
                raise AdError(f"directory search failed: {description}")
            for item in self._conn.response or []:
                if item.get("type") == "searchResEntry":
                    entries.append(_entry_from_response(item))
            controls = result.get("controls") or {}
            paged = controls.get("1.2.840.113556.1.4.319") or {}
            cookie = (paged.get("value") or {}).get("cookie") or None
            if not cookie:
                return entries

    def read_entry(self, dn: str, *, attributes: list[str]) -> DirectoryEntry | None:
        """Read one object by DN (base-scope); ``None`` when it does not exist."""
        entries = self.search(
            base=dn,
            ldap_filter="(objectClass=*)",
            scope="base",
            attributes=attributes,
        )
        return entries[0] if entries else None


def _build_tls(config: DirectoryConfig, ldap3: Any) -> Any:
    """Build the ldap3 Tls settings: verify against the platform store or --ca-file."""
    ca_file = str(config.ca_file) if config.ca_file is not None else None
    return ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_file)


def _bind(conn: Any, *, host: str, port: int) -> None:
    """Bind the connection, raising the typed error on rejection."""
    ok = bool(conn.bind())
    if ok:
        return
    result: dict[str, Any] = dict(conn.result or {})
    code = int(result.get("result", -1))
    message = str(result.get("message", ""))
    if code == _RESULT_INVALID_CREDENTIALS:
        reason = decode_bind_data(message)
        raise AuthenticationFailed(
            f"the directory at {host}:{port} rejected the credentials",
            hint=(
                f"server says: {reason}"
                if reason
                else "check the account name (UPN or DOMAIN\\name) and password"
            ),
        )
    description = str(result.get("description", "")) or "unknown error"
    raise AdError(f"bind failed: {description}")


def connect_session(
    config: DirectoryConfig,
    *,
    host: str,
    port: int,
    stages: list[Stage] | None = None,
) -> DirectorySession:
    """Open and bind one connection to ``host:port`` per the config's security mode.

    Appends per-stage timings to ``stages`` when given: the connection open is the
    ``secured`` stage under TLS (``reached`` under plaintext), a StartTLS upgrade adds
    ``secured``, and the bind is ``authenticated``. Encryption is never silently
    skipped: a failed StartTLS upgrade raises instead of continuing in cleartext.

    Raises:
        DependencyMissing: When ldap3 is not installed.
        ConnectRefused / ConnectTimeout / ResolutionError: Reach-stage failures.
        HandshakeError / CertificateInvalid: TLS-stage failures.
        AuthenticationFailed: The directory rejected the credentials.
        AdError: Any other failure.
    """
    ldap3, _ = _ldap3()
    use_ssl = config.security == "ldaps"
    if stages is not None and use_ssl:
        # LDAPS wraps TLS around the very first byte, so the reach stage is proven
        # with a plain connect first (closed immediately; only the staged `ad check`
        # path pays this extra connect).
        from opskit.net import tcp

        sock, connection = tcp.connect(host, port, timeout=config.timeout, retries=0)
        sock.close()
        stages.append(Stage("reached", True, connection.connect_ms))
    tls = _build_tls(config, ldap3) if config.encrypted else None
    server = ldap3.Server(
        host,
        port=port,
        use_ssl=use_ssl,
        tls=tls,
        get_info=ldap3.NONE,
        connect_timeout=config.timeout,
    )
    authentication = ldap3.SIMPLE if config.bind_user else ldap3.ANONYMOUS
    conn = ldap3.Connection(
        server,
        user=config.bind_user,
        password=config.password,
        authentication=authentication,
        auto_range=True,
        # ldap3 packs receive_timeout with struct.pack('ll', ...) — it must be an int.
        receive_timeout=max(1, round(config.timeout)),
        raise_exceptions=False,
    )

    open_stage = "secured" if use_ssl else "reached"
    start = time.perf_counter()
    try:
        conn.open()
    except Exception as exc:
        raise classify_connect_error(exc, host=host, port=port) from exc
    if stages is not None:
        stages.append(Stage(open_stage, True, (time.perf_counter() - start) * 1000.0))

    if config.security == "starttls":
        start = time.perf_counter()
        try:
            upgraded = bool(conn.start_tls())
        except Exception as exc:
            raise classify_connect_error(exc, host=host, port=port) from exc
        if not upgraded:
            raise HandshakeError(
                f"StartTLS upgrade failed with {host}:{port}",
                hint="the server may not support StartTLS — try LDAPS (default, "
                "port 636) or check the server configuration",
            )
        if stages is not None:
            stages.append(
                Stage("secured", True, (time.perf_counter() - start) * 1000.0)
            )

    start = time.perf_counter()
    try:
        _bind(conn, host=host, port=port)
    except OpskitError:
        raise
    except Exception as exc:
        raise classify_connect_error(exc, host=host, port=port) from exc
    if stages is not None:
        stages.append(
            Stage("authenticated", True, (time.perf_counter() - start) * 1000.0)
        )

    return DirectorySession(
        conn,
        host=host,
        port=port,
        config=config,
        stages=tuple(stages or ()),
    )
