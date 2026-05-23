# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""SSL/TLS context construction and self-signed certificate management for HyperShell queues."""


# Type annotations
from __future__ import annotations
from typing import List, Mapping, Optional, Tuple, Final

# Standard libs
import os
import ssl
import socket
import ipaddress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

# External libs
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Internal libs
from hypershell.core.logging import Logger

# Public interface
__all__ = [
    'TLSConfig', 'TLSError',
    'PARAM_AUTO', 'PARAM_NONE',
    'DEFAULT_MIN_VERSION', 'DEFAULT_CERT_VALIDITY_DAYS', 'DEFAULT_CERT_DIRNAME',
    'build_server_context', 'build_client_context',
    'install_process_context', 'clear_process_context',
    'get_server_context', 'get_client_context', 'get_tls_config',
    'verify_peer_fingerprint',
    'ensure_default_materials', 'resolve_auto_paths',
    'from_namespace', 'default_directory',
    'fingerprint_of_pem', 'fingerprint_of_der',
    'format_fingerprint', 'parse_fingerprint',
]

# Initialize logger
log = Logger.with_name(__name__)


# Sentinel values for TOML configuration (TOML cannot represent None).
PARAM_AUTO: Final[str] = '<auto>'
PARAM_NONE: Final[str] = '<none>'


DEFAULT_MIN_VERSION: Final[str] = 'TLSv1.2'
DEFAULT_CERT_VALIDITY_DAYS: Final[int] = 3650  # ten years
DEFAULT_CERT_DIRNAME: Final[str] = 'tls'
DEFAULT_CERT_FILENAME: Final[str] = 'server.crt'
DEFAULT_KEY_FILENAME: Final[str] = 'server.key'


_MIN_VERSION_MAPPING: Final[dict] = {
    'TLSv1.2': ssl.TLSVersion.TLSv1_2,
    'TLSv1.3': ssl.TLSVersion.TLSv1_3,
}


class TLSError(Exception):
    """Generic TLS configuration or runtime error."""


@dataclass
class TLSConfig:
    """
    Picklable description of a TLS configuration.

    Carries every value needed to rebuild a :class:`ssl.SSLContext` inside a freshly spawned
    server subprocess. Paths must be absolute. Use :meth:`is_active` to detect a disabled config.
    """

    enabled: bool = False
    cert: Optional[str] = None         # Path to PEM-encoded server certificate
    key: Optional[str] = None          # Path to PEM-encoded private key
    cafile: Optional[str] = None       # Path to PEM CA bundle for client-side verification
    fingerprint: Optional[str] = None  # Pinned peer cert fingerprint, e.g. 'SHA256:AB:CD:...'
    insecure: bool = False             # Disable verification entirely (logs a warning)
    min_version: str = DEFAULT_MIN_VERSION
    ciphers: Optional[str] = None
    servername: Optional[str] = None   # Override SNI / hostname check on client side
    sans: List[str] = field(default_factory=list)  # SANs to embed on auto-generated certs

    def is_active(self: TLSConfig) -> bool:
        """True when this config should actually wire TLS into the queue transport."""
        return bool(self.enabled)


def fingerprint_of_der(der: bytes) -> str:
    """Return ``SHA256:AB:CD:...`` colon-formatted fingerprint of DER-encoded certificate."""
    digest = sha256(der).hexdigest().upper()
    return 'SHA256:' + ':'.join(digest[i:i + 2] for i in range(0, len(digest), 2))


def fingerprint_of_pem(pem: bytes) -> str:
    """Return ``SHA256:AB:CD:...`` colon-formatted fingerprint of PEM-encoded certificate."""
    cert = x509.load_pem_x509_certificate(pem)
    return fingerprint_of_der(cert.public_bytes(serialization.Encoding.DER))


def format_fingerprint(raw: bytes) -> str:
    """Format a 32-byte SHA-256 digest as ``SHA256:AB:CD:...``."""
    if len(raw) != 32:
        raise TLSError(f'SHA-256 fingerprint must be 32 bytes, got {len(raw)}')
    hex_str = raw.hex().upper()
    return 'SHA256:' + ':'.join(hex_str[i:i + 2] for i in range(0, len(hex_str), 2))


def parse_fingerprint(value: str) -> bytes:
    """
    Parse fingerprint string in any common form: ``SHA256:AB:CD:...``, ``AB:CD:...``, or bare hex.
    Returns the raw 32-byte digest. Case-insensitive.
    """
    value = value.strip()
    if ':' in value:
        head, _, rest = value.partition(':')
        if head.lower() == 'sha256':
            value = rest
        value = value.replace(':', '')
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise TLSError(f'Could not parse fingerprint {value!r}: {error}')
    if len(raw) != 32:
        raise TLSError(f'Expected 32-byte SHA-256 fingerprint, got {len(raw)} bytes')
    return raw


def _apply_protocol_floor(ctx: ssl.SSLContext, min_version: str) -> None:
    """Set the minimum TLS version on `ctx` from a human-readable name."""
    try:
        ctx.minimum_version = _MIN_VERSION_MAPPING[min_version]
    except KeyError:
        raise TLSError(f'Unsupported min_version {min_version!r}; expected one of '
                       f'{sorted(_MIN_VERSION_MAPPING)}')


def build_server_context(cfg: TLSConfig) -> ssl.SSLContext:
    """Construct the server-side :class:`ssl.SSLContext` from `cfg`."""
    if not cfg.cert or not cfg.key:
        raise TLSError('TLSConfig.cert and TLSConfig.key are required for the server side')
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cfg.cert, keyfile=cfg.key)
    _apply_protocol_floor(ctx, cfg.min_version)
    if cfg.ciphers:
        ctx.set_ciphers(cfg.ciphers)
    # Servers do not verify clients by default; mTLS is out of scope for the initial release.
    ctx.verify_mode = ssl.CERT_NONE
    ctx.check_hostname = False
    return ctx


def build_client_context(cfg: TLSConfig) -> ssl.SSLContext:
    """Construct the client-side :class:`ssl.SSLContext` from `cfg`."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _apply_protocol_floor(ctx, cfg.min_version)
    if cfg.ciphers:
        ctx.set_ciphers(cfg.ciphers)
    if cfg.insecure:
        log.warning('TLS verification disabled (insecure mode) - transport is encrypted '
                    'but the peer identity is not authenticated')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif cfg.fingerprint:
        # Fingerprint pinning happens after the handshake. We must still let the handshake
        # complete, so we disable hostname/CA verification at the SSLContext level and let
        # verify_peer_fingerprint() reject mismatched peers.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif cfg.cafile:
        ctx.load_verify_locations(cafile=cfg.cafile)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = bool(cfg.servername)
    else:
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
    return ctx


def verify_peer_fingerprint(ssl_sock: ssl.SSLSocket, cfg: TLSConfig) -> None:
    """
    Manually verify the peer certificate fingerprint against the pin in `cfg`.

    Called immediately after a successful TLS handshake on the client side. Raises
    :class:`TLSError` when the pin is set but does not match the peer.
    """
    if not cfg.fingerprint:
        return
    der = ssl_sock.getpeercert(binary_form=True)
    if not der:
        raise TLSError('No peer certificate available for fingerprint verification')
    expected = parse_fingerprint(cfg.fingerprint)
    actual = sha256(der).digest()
    if expected != actual:
        raise TLSError(f'Peer certificate fingerprint mismatch '
                       f'(expected {format_fingerprint(expected)}, '
                       f'got {format_fingerprint(actual)})')


# Process-local state populated by install_process_context().
# The server subprocess and each client process call install_process_context() once.
_PROCESS_CONFIG: Optional[TLSConfig] = None
_PROCESS_SERVER_CONTEXT: Optional[ssl.SSLContext] = None
_PROCESS_CLIENT_CONTEXT: Optional[ssl.SSLContext] = None


def install_process_context(cfg: Optional[TLSConfig]) -> None:
    """
    Stash `cfg` for use by :func:`get_server_context` / :func:`get_client_context`.

    Called once per process: by the server subprocess as part of its initializer and by each
    client process before connecting. Passing ``None`` (or a config with ``enabled=False``)
    leaves TLS disabled in this process.
    """
    global _PROCESS_CONFIG, _PROCESS_SERVER_CONTEXT, _PROCESS_CLIENT_CONTEXT
    _PROCESS_SERVER_CONTEXT = None
    _PROCESS_CLIENT_CONTEXT = None
    _PROCESS_CONFIG = cfg if (cfg is not None and cfg.is_active()) else None


def clear_process_context() -> None:
    """Drop any installed TLS configuration in this process. Intended for tests."""
    install_process_context(None)


def get_tls_config() -> Optional[TLSConfig]:
    """Return the currently installed :class:`TLSConfig` for this process, if any."""
    return _PROCESS_CONFIG


def get_server_context() -> ssl.SSLContext:
    """Return (or lazily build) the server-side context for this process."""
    global _PROCESS_SERVER_CONTEXT
    if _PROCESS_CONFIG is None:
        raise TLSError('No TLS configuration installed in this process')
    if _PROCESS_SERVER_CONTEXT is None:
        _PROCESS_SERVER_CONTEXT = build_server_context(_PROCESS_CONFIG)
    return _PROCESS_SERVER_CONTEXT


def get_client_context() -> ssl.SSLContext:
    """Return (or lazily build) the client-side context for this process."""
    global _PROCESS_CLIENT_CONTEXT
    if _PROCESS_CONFIG is None:
        raise TLSError('No TLS configuration installed in this process')
    if _PROCESS_CLIENT_CONTEXT is None:
        _PROCESS_CLIENT_CONTEXT = build_client_context(_PROCESS_CONFIG)
    return _PROCESS_CLIENT_CONTEXT


def _local_san_entries(hostnames: List[str]) -> List[x509.GeneralName]:
    """Construct x509 SubjectAlternativeName entries appropriate for a local self-signed cert."""
    entries: List[x509.GeneralName] = []
    seen: set = set()
    for name in hostnames:
        if not name:
            continue
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            if name not in seen:
                entries.append(x509.DNSName(name))
                seen.add(name)
        else:
            if name not in seen:
                entries.append(x509.IPAddress(ip))
                seen.add(name)
    return entries


def _default_hostnames() -> List[str]:
    """Best-effort list of local hostnames and IPs to embed in an auto-generated cert."""
    fqdn = socket.getfqdn()
    short = fqdn.split('.', 1)[0] if fqdn else ''
    return [name for name in (fqdn, short, 'localhost', '127.0.0.1', '::1') if name]


def _generate_self_signed(hostnames: List[str], validity_days: int) -> Tuple[bytes, bytes]:
    """
    Generate a self-signed RSA-3072 keypair + certificate for the given hostnames.

    Returns ``(cert_pem, key_pem)``. The certificate is signed with SHA-256.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    subject_cn = hostnames[0] if hostnames else 'hypershell'
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'HyperShell'),
        x509.NameAttribute(NameOID.COMMON_NAME, subject_cn),
    ])
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    san_entries = _local_san_entries(hostnames)
    if san_entries:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    cert = builder.sign(private_key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return cert_pem, key_pem


def _write_private_file(path: str, data: bytes) -> None:
    """Write `data` to `path` with mode 0600 (owner read/write only)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    # os.open() may not enforce the mode if the file pre-existed; chmod to be sure.
    os.chmod(path, 0o600)


def _write_public_file(path: str, data: bytes) -> None:
    """Write `data` to `path` with mode 0644."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(path, 0o644)


def ensure_default_materials(directory: str,
                             hostnames: Optional[List[str]] = None,
                             validity_days: int = DEFAULT_CERT_VALIDITY_DAYS,
                             ) -> Tuple[str, str]:
    """
    Make sure a self-signed cert+key pair exists in `directory`; generate them if missing.

    Returns ``(cert_path, key_path)``. Idempotent: if both files already exist they are left
    untouched and the fingerprint is logged at TRACE level. When materials are generated for
    the first time the fingerprint is logged at INFO so the operator can pin it on clients.
    """
    os.makedirs(directory, exist_ok=True)
    cert_path = os.path.join(directory, DEFAULT_CERT_FILENAME)
    key_path = os.path.join(directory, DEFAULT_KEY_FILENAME)
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, 'rb') as stream:
            existing_pem = stream.read()
        log.trace(f'Using existing TLS materials ({fingerprint_of_pem(existing_pem)})')
        return cert_path, key_path
    cert_pem, key_pem = _generate_self_signed(hostnames or _default_hostnames(), validity_days)
    _write_private_file(key_path, key_pem)
    _write_public_file(cert_path, cert_pem)
    log.info(f'Generated self-signed TLS certificate ({cert_path})')
    log.info(f'Certificate fingerprint: {fingerprint_of_pem(cert_pem)}')
    return cert_path, key_path


def resolve_auto_paths(cfg: TLSConfig, directory: str) -> TLSConfig:
    """
    Resolve ``<auto>``/``<none>`` sentinels in `cfg` to concrete file paths under `directory`.

    Generates self-signed materials with :func:`ensure_default_materials` when ``cert``/``key``
    are ``<auto>`` and not already present. Returns a new :class:`TLSConfig` with sentinels
    replaced; sentinel ``<none>`` becomes ``None``. The original `cfg` is not mutated.
    """
    cert = cfg.cert
    key = cfg.key
    if cert == PARAM_AUTO or key == PARAM_AUTO:
        materials = ensure_default_materials(directory, cfg.sans or None)
        cert = materials[0] if cert == PARAM_AUTO else cert
        key = materials[1] if key == PARAM_AUTO else key
    cafile = cfg.cafile
    if cafile == PARAM_AUTO:
        cafile = cert  # Mirror the server cert; useful for single-host out-of-the-box setup.
    return replace(
        cfg,
        cert=None if cert in (PARAM_NONE, None) else cert,
        key=None if key in (PARAM_NONE, None) else key,
        cafile=None if cafile in (PARAM_NONE, None) else cafile,
        fingerprint=None if cfg.fingerprint in (PARAM_NONE, None) else cfg.fingerprint,
        ciphers=None if cfg.ciphers in (PARAM_NONE, None) else cfg.ciphers,
        servername=None if cfg.servername in (PARAM_NONE, None) else cfg.servername,
    )


def default_directory() -> str:
    """Default base directory for auto-generated TLS materials (``<site>/tls``)."""
    from hypershell.core.platform import default_path  # local import: core.platform is a leaf
    return os.path.join(default_path.lib, DEFAULT_CERT_DIRNAME)


def from_namespace(ns: Optional[Mapping] = None,
                   directory: Optional[str] = None) -> Optional[TLSConfig]:
    """
    Build a :class:`TLSConfig` from a hypershell config namespace (e.g. ``config.server.tls``).

    Returns ``None`` when `ns` is missing or its ``enabled`` flag is false, so callers can
    treat the result as an opt-in toggle. ``<auto>`` sentinels for ``cert``/``key``/``cafile``
    are resolved against `directory`, which defaults to :func:`default_directory`.
    """
    if ns is None:
        return None
    if not bool(ns.get('enabled', False)):
        return None
    cfg = TLSConfig(
        enabled=True,
        cert=ns.get('cert'),
        key=ns.get('key'),
        cafile=ns.get('cafile'),
        fingerprint=ns.get('fingerprint'),
        insecure=bool(ns.get('insecure', False)),
        min_version=ns.get('min_version') or DEFAULT_MIN_VERSION,
        ciphers=ns.get('ciphers'),
        servername=ns.get('servername'),
    )
    return resolve_auto_paths(cfg, directory if directory is not None else default_directory())
