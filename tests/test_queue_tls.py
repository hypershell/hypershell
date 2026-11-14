# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test the TLS transport layer in :mod:`hypershell.core.queue` and the
helpers in :mod:`hypershell.core.tls`."""


# Type annotations
from __future__ import annotations
from typing import Optional, Tuple

# Standard libs
import io
import os
import sys
import subprocess
import ssl
import socket
import struct
import threading
import multiprocessing as mp
import tempfile
import time
import traceback
from pathlib import Path

# External libs
from pytest import fixture, mark, raises

# Internal libs
from hypershell.core import tls as tls_module
from hypershell.core.queue import (SecureConnection, SecureListener, secure_client,
                                   SecureManager, TLS_SERIALIZER)
from hypershell.core.tls import (TLSConfig, TLSError,
                                 build_server_context, build_client_context,
                                 ensure_default_materials, from_namespace, default_directory,
                                 fingerprint_of_pem, fingerprint_of_der,
                                 format_fingerprint, parse_fingerprint,
                                 install_process_context, clear_process_context,
                                 verify_peer_fingerprint,
                                 _select_subject_cn, _default_hostnames,
                                 _X509_CN_MAX_LEN, _FALLBACK_CN)
import hypershell.core.queue
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig, QueueServerError
from hypershell.core.tls import handshake_seconds, DEFAULT_HANDSHAKE, HANDSHAKE_KEY
from hypershell.core.types import TIME_FORMS
from hypershell.core.config import config as hypershell_config


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> Tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture
def install_tls(tls_materials):
    """Install a TLSConfig (cert+key+cafile mirroring cert) for the duration of a test."""
    cert, key = tls_materials
    cfg = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    install_process_context(cfg)
    yield cfg
    clear_process_context()


@mark.unit
class TestFingerprintHelpers:
    """Unit tests for fingerprint parsing, formatting, and DER/PEM derivation."""

    def test_format_roundtrip(self) -> None:
        """``parse_fingerprint`` is the inverse of ``format_fingerprint``."""
        raw = b'\x01' * 32
        formatted = format_fingerprint(raw)
        assert formatted.startswith('SHA256:')
        assert parse_fingerprint(formatted) == raw

    def test_parse_accepts_colon_format(self) -> None:
        """Common ``SHA256:AB:CD:...`` form should be accepted."""
        digest = ':'.join(['AB'] * 32)
        assert parse_fingerprint(f'SHA256:{digest}') == b'\xab' * 32

    def test_parse_accepts_bare_hex(self) -> None:
        """Bare hex without colons or prefix should also be accepted."""
        assert parse_fingerprint('ab' * 32) == b'\xab' * 32

    def test_parse_rejects_short(self) -> None:
        """Anything that doesn't decode to 32 bytes is rejected."""
        with raises(TLSError):
            parse_fingerprint('ab' * 16)

    def test_parse_rejects_garbage(self) -> None:
        """Non-hex input is rejected with TLSError, not ValueError."""
        with raises(TLSError):
            parse_fingerprint('not-a-fingerprint')

    def test_fingerprint_of_pem_matches_der(self, tls_materials) -> None:
        """Fingerprint computed from PEM should match the one computed from DER."""
        cert_path, _ = tls_materials
        with open(cert_path, 'rb') as stream:
            pem = stream.read()
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
        assert fingerprint_of_pem(pem) == fingerprint_of_der(der)


@mark.unit
class TestEnsureDefaultMaterials:
    """Tests for self-signed certificate generation and idempotency."""

    def test_generates_when_missing(self, tmp_path: Path) -> None:
        """First call creates ``server.crt`` and ``server.key`` with the right modes."""
        cert, key = ensure_default_materials(str(tmp_path), ['localhost', '127.0.0.1'])
        assert os.path.exists(cert)
        assert os.path.exists(key)
        assert (os.stat(key).st_mode & 0o777) == 0o600
        assert (os.stat(cert).st_mode & 0o777) == 0o644

    def test_idempotent(self, tmp_path: Path) -> None:
        """Subsequent calls reuse the existing materials byte-for-byte."""
        cert1, key1 = ensure_default_materials(str(tmp_path), ['localhost'])
        with open(cert1, 'rb') as stream:
            first_pem = stream.read()
        cert2, key2 = ensure_default_materials(str(tmp_path), ['localhost'])
        assert cert1 == cert2 and key1 == key2
        with open(cert2, 'rb') as stream:
            assert stream.read() == first_pem

    def test_long_fqdn_does_not_break_cn(self, tmp_path: Path) -> None:
        """Regression: a 72-char IPv6 PTR FQDN (as returned by ``socket.getfqdn()`` on
        some macOS hosts) must not crash cert generation. The shorter follow-up hostname
        becomes the CN; the long name still lands in the SubjectAlternativeName extension.
        """
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        long_fqdn = '1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.ip6.arpa'
        assert len(long_fqdn) == 72  # would have triggered the original ValueError
        cert_path, _ = ensure_default_materials(str(tmp_path),
                                                [long_fqdn, 'localhost', '127.0.0.1'])
        with open(cert_path, 'rb') as stream:
            cert = x509.load_pem_x509_certificate(stream.read())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert len(cn) <= _X509_CN_MAX_LEN
        assert cn == 'localhost'
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert long_fqdn in san.get_values_for_type(x509.DNSName)


@mark.unit
class TestSelectSubjectCN:
    """Unit tests for the ``_select_subject_cn`` helper."""

    def test_returns_first_fitting_hostname(self) -> None:
        """The first hostname within the 64-byte limit wins."""
        assert _select_subject_cn(['host.example.com', 'localhost']) == 'host.example.com'

    def test_skips_long_first_hostname(self) -> None:
        """A too-long first entry is skipped in favor of the next fitting candidate."""
        long_name = 'x' * (_X509_CN_MAX_LEN + 1)
        assert _select_subject_cn([long_name, 'short']) == 'short'

    def test_skips_empty_entries(self) -> None:
        """Empty/None entries are ignored when picking a CN candidate."""
        assert _select_subject_cn(['', None, 'kept']) == 'kept'  # type: ignore[list-item]

    def test_truncates_when_all_too_long(self) -> None:
        """When every non-empty candidate is too long, the first one is truncated."""
        a, b = 'a' * 80, 'b' * 100
        cn = _select_subject_cn([a, b])
        assert len(cn) == _X509_CN_MAX_LEN
        assert cn == a[:_X509_CN_MAX_LEN]

    def test_fallback_when_no_hostnames(self) -> None:
        """With nothing usable to pick, fall back to the documented placeholder."""
        assert _select_subject_cn([]) == _FALLBACK_CN
        assert _select_subject_cn(['', '']) == _FALLBACK_CN

    def test_accepts_exact_limit(self) -> None:
        """A hostname of exactly 64 chars is accepted without truncation."""
        name = 'h' * _X509_CN_MAX_LEN
        assert _select_subject_cn([name]) == name


@mark.unit
class TestDefaultHostnames:
    """Unit tests for the ``_default_hostnames`` helper."""

    def test_falls_back_to_gethostname_on_ip6_arpa(self, monkeypatch) -> None:
        """When ``getfqdn`` returns an IPv6 reverse-DNS PTR, prefer ``gethostname``."""
        ptr = '1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.ip6.arpa'
        monkeypatch.setattr(tls_module.socket, 'getfqdn', lambda *a, **kw: ptr)
        monkeypatch.setattr(tls_module.socket, 'gethostname',
                            lambda *a, **kw: 'host.local')
        names = _default_hostnames()
        assert ptr not in names
        assert names[0] == 'host.local'
        assert 'host' in names  # short hostname derived from the friendly name

    def test_falls_back_on_in_addr_arpa(self, monkeypatch) -> None:
        """IPv4 PTR records (``...in-addr.arpa``) also trigger the fallback."""
        monkeypatch.setattr(tls_module.socket, 'getfqdn',
                            lambda *a, **kw: '1.0.0.127.in-addr.arpa')
        monkeypatch.setattr(tls_module.socket, 'gethostname', lambda *a, **kw: 'box')
        names = _default_hostnames()
        assert '1.0.0.127.in-addr.arpa' not in names
        assert names[0] == 'box'

    def test_keeps_real_fqdn(self, monkeypatch) -> None:
        """A genuine forward FQDN is preserved at the head of the list."""
        monkeypatch.setattr(tls_module.socket, 'getfqdn',
                            lambda *a, **kw: 'node01.cluster.example.org')
        monkeypatch.setattr(tls_module.socket, 'gethostname', lambda *a, **kw: 'node01')
        names = _default_hostnames()
        assert names[0] == 'node01.cluster.example.org'
        assert 'node01' in names
        assert 'localhost' in names

    def test_loopback_addresses_always_present(self, monkeypatch) -> None:
        """The loopback fallbacks should be present regardless of hostname lookups."""
        monkeypatch.setattr(tls_module.socket, 'getfqdn', lambda *a, **kw: 'host')
        monkeypatch.setattr(tls_module.socket, 'gethostname', lambda *a, **kw: 'host')
        names = _default_hostnames()
        assert 'localhost' in names and '127.0.0.1' in names and '::1' in names


@mark.unit
class TestContextBuilders:
    """Unit tests for build_server_context / build_client_context."""

    def test_server_context_requires_cert_and_key(self) -> None:
        """Server context cannot be built without cert+key."""
        with raises(TLSError):
            build_server_context(TLSConfig(enabled=True))

    def test_server_context_min_version(self, tls_materials) -> None:
        """The configured min_version is applied."""
        cert, key = tls_materials
        ctx = build_server_context(TLSConfig(enabled=True, cert=cert, key=key,
                                             min_version='TLSv1.3'))
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3

    def test_unknown_min_version_rejected(self, tls_materials) -> None:
        """Bad min_version strings raise TLSError, not KeyError."""
        cert, key = tls_materials
        with raises(TLSError):
            build_server_context(TLSConfig(enabled=True, cert=cert, key=key,
                                           min_version='TLSv0.5'))

    def test_client_context_fingerprint_disables_ca(self) -> None:
        """Fingerprint pinning disables CA-based verification."""
        ctx = build_client_context(TLSConfig(enabled=True, fingerprint='SHA256:' + ':'.join(['00'] * 32)))
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    def test_client_context_insecure_disables_verification(self) -> None:
        """Insecure mode disables all verification."""
        ctx = build_client_context(TLSConfig(enabled=True, insecure=True))
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    def test_client_context_cafile_requires_verification(self, tls_materials) -> None:
        """Setting a cafile turns CERT_REQUIRED back on."""
        cert, _ = tls_materials
        ctx = build_client_context(TLSConfig(enabled=True, cafile=cert))
        assert ctx.verify_mode == ssl.CERT_REQUIRED


@mark.unit
class TestFromNamespace:
    """Tests for the config-namespace adapter."""

    def test_returns_none_when_disabled(self) -> None:
        """Missing or disabled namespace returns None (TLS off)."""
        assert from_namespace(None) is None
        assert from_namespace({}) is None
        assert from_namespace({'enabled': False}) is None

    def test_resolves_auto_paths(self, tmp_path: Path) -> None:
        """``<auto>`` sentinels are resolved against the given directory."""
        ns = {
            'enabled': True,
            'cert': '<auto>',
            'key': '<auto>',
            'cafile': '<auto>',
            'fingerprint': '<none>',
            'insecure': False,
            'min_version': 'TLSv1.2',
            'ciphers': '<none>',
            'servername': '<none>',
        }
        cfg = from_namespace(ns, directory=str(tmp_path))
        assert cfg is not None
        assert cfg.cert == cfg.cafile  # cafile mirrors cert when auto
        assert os.path.exists(cfg.cert)
        assert os.path.exists(cfg.key)
        assert cfg.fingerprint is None
        assert cfg.ciphers is None
        assert cfg.servername is None

    def test_default_directory_under_lib(self) -> None:
        """The default directory is the site lib dir + ``tls``."""
        path = default_directory()
        assert path.endswith(os.sep + 'tls')


class _FakeSSLSocket:
    """
    Stand-in for :class:`ssl.SSLSocket` that records sends and serves prepared receive data.

    :class:`SecureConnection` requires its underlying socket to expose ``fileno``, ``sendall``,
    ``recv``, ``pending``, and ``close``. The fileno is a real file descriptor opened against
    ``/dev/null`` so that :class:`multiprocessing.connection._ConnectionBase` accepts it.
    """

    def __init__(self: _FakeSSLSocket, recv_data: bytes = b'') -> None:
        """Open a real fd so _ConnectionBase is happy; pre-populate recv buffer if provided."""
        self._fd = os.open(os.devnull, os.O_RDWR)
        self._sent: bytearray = bytearray()
        self._recv_buf: bytearray = bytearray(recv_data)
        self.closed: bool = False

    def fileno(self: _FakeSSLSocket) -> int:
        return self._fd

    def sendall(self: _FakeSSLSocket, data) -> None:
        if self.closed:
            raise OSError('closed')
        self._sent.extend(bytes(data))

    def recv(self: _FakeSSLSocket, n: int) -> bytes:
        if self.closed:
            return b''
        chunk = bytes(self._recv_buf[:n])
        del self._recv_buf[:n]
        return chunk

    def pending(self: _FakeSSLSocket) -> int:
        return len(self._recv_buf)

    def close(self: _FakeSSLSocket) -> None:
        if not self.closed:
            self.closed = True
            try:
                os.close(self._fd)
            except OSError:
                pass

    def all_sent(self: _FakeSSLSocket) -> bytes:
        return bytes(self._sent)


@mark.unit
class TestSecureConnectionFraming:
    """Unit-level tests that pin the wire framing without needing a real TLS pair."""

    def test_small_payload(self) -> None:
        """Payloads up to 16384 bytes are concatenated with their 4-byte header."""
        fake = _FakeSSLSocket()
        conn = SecureConnection(fake)
        conn.send_bytes(b'hello')
        sent = fake.all_sent()
        assert sent == struct.pack('!i', 5) + b'hello'
        conn.close()

    def test_medium_payload_uses_separate_send(self) -> None:
        """Payloads > 16384 bytes emit the header and body in two writes (no concat)."""
        fake = _FakeSSLSocket()
        conn = SecureConnection(fake)
        payload = b'x' * 20_000
        conn.send_bytes(payload)
        assert fake.all_sent() == struct.pack('!i', len(payload)) + payload
        conn.close()

    def test_recv_small(self) -> None:
        """A header + body pair is decoded correctly."""
        body = b'roundtrip'
        wire = struct.pack('!i', len(body)) + body
        fake = _FakeSSLSocket(recv_data=wire)
        conn = SecureConnection(fake)
        assert conn.recv_bytes() == body
        conn.close()

    def test_recv_uses_extended_header(self) -> None:
        """When the 4-byte length is -1, the next 8 bytes hold the real length."""
        body = b'a' * 50
        wire = struct.pack('!i', -1) + struct.pack('!Q', len(body)) + body
        fake = _FakeSSLSocket(recv_data=wire)
        conn = SecureConnection(fake)
        assert conn.recv_bytes() == body
        conn.close()

    def test_send_extended_header_marker(self) -> None:
        """``_send_bytes`` emits the -1 sentinel + 8-byte length for messages > 2 GiB.

        We monkey-patch ``len(buf)`` by passing a fake buffer object whose ``__len__``
        reports a value > 0x7fffffff while ``bytes(buf)`` only emits a tiny payload, so the
        test exercises the branch without allocating multiple gigabytes of memory.
        """

        class _HugeBytes:
            """Reports a huge length but holds a tiny payload."""
            def __init__(self, payload: bytes, fake_len: int) -> None:
                self._payload = payload
                self._fake_len = fake_len
            def __len__(self) -> int:
                return self._fake_len
            def __bytes__(self) -> bytes:
                return self._payload

        fake = _FakeSSLSocket()
        conn = SecureConnection(fake)
        # Real payload is tiny so we do not allocate gigabytes; the framing branch is selected
        # by the lie that __len__ returns 0x80000000.
        huge = _HugeBytes(b'x' * 4, 0x80000000)
        conn._send_bytes(huge)
        sent = fake.all_sent()
        # The wire should start with the -1 sentinel followed by the 8-byte true length.
        assert sent.startswith(struct.pack('!i', -1) + struct.pack('!Q', 0x80000000))
        conn.close()

    def test_recv_bytes_respects_maxsize(self) -> None:
        """``_recv_bytes`` returns None when the announced size exceeds maxsize."""
        body = b'x' * 100
        wire = struct.pack('!i', len(body)) + body
        fake = _FakeSSLSocket(recv_data=wire)
        conn = SecureConnection(fake)
        assert conn._recv_bytes(maxsize=50) is None
        conn.close()

    def test_recv_eof_raises(self) -> None:
        """An empty recv on the first byte raises :class:`EOFError`."""
        fake = _FakeSSLSocket(recv_data=b'')
        conn = SecureConnection(fake)
        with raises(EOFError):
            conn.recv_bytes()
        conn.close()

    def test_poll_uses_ssl_pending(self) -> None:
        """``poll`` returns True without touching select() when SSL has buffered bytes."""
        fake = _FakeSSLSocket(recv_data=b'ready')
        conn = SecureConnection(fake)
        assert conn.poll(timeout=0) is True
        conn.close()


@mark.integration
class TestSecureListenerSecureClient:
    """End-to-end test of the listener/client pair over real loopback TLS."""

    def test_roundtrip(self, install_tls) -> None:
        """Listener and client complete a TLS handshake and exchange a pickle object."""
        listener = SecureListener(address=('127.0.0.1', 0), backlog=1)
        try:
            accepted: list = []
            error_box: list = []

            def _accept():
                try:
                    accepted.append(listener.accept())
                except Exception:
                    error_box.append(traceback.format_exc())

            t = threading.Thread(target=_accept, daemon=True)
            t.start()
            client = secure_client(listener.address)
            t.join(timeout=10)
            assert not error_box, error_box
            assert accepted, 'listener.accept() never returned'
            server_conn = accepted[0]
            try:
                payload = {'task_id': 'abc', 'cmd': 'echo hi', 'large': 'x' * 50_000}
                client.send(payload)
                assert server_conn.recv() == payload
                server_conn.send(b'pong')
                assert client.recv() == b'pong'
            finally:
                server_conn.close()
                client.close()
        finally:
            listener.close()

    def test_fingerprint_mismatch_is_caught(self, tls_materials) -> None:
        """A pinned fingerprint that doesn't match the server's cert rejects the connection."""
        cert, key = tls_materials
        # Build a TLSConfig that pins a fake fingerprint (all-zeros).
        bad_pin = format_fingerprint(b'\x00' * 32)
        cfg = TLSConfig(enabled=True, cert=cert, key=key, fingerprint=bad_pin)
        install_process_context(cfg)
        try:
            listener = SecureListener(address=('127.0.0.1', 0), backlog=1)
            try:
                # Server accept must run in another thread to not deadlock the test.
                accepted_box: list = []
                accept_error_box: list = []

                def _accept():
                    try:
                        accepted_box.append(listener.accept())
                    except Exception as e:
                        accept_error_box.append(e)

                t = threading.Thread(target=_accept, daemon=True)
                t.start()
                with raises(TLSError):
                    secure_client(listener.address)
                t.join(timeout=5)
            finally:
                listener.close()
        finally:
            clear_process_context()


def _manager_subprocess_target(address_pipe, tls_cfg: TLSConfig, authkey: bytes) -> None:
    """Run a :class:`SecureManager` server inside a subprocess for the integration test below."""
    try:
        install_process_context(tls_cfg)
        manager = SecureManager(address=('127.0.0.1', 0), authkey=authkey, tls=tls_cfg)
        manager.register('get_value', callable=_manager_value)
        manager.start()
        address_pipe.send(manager.address)
        # Wait for the parent to signal shutdown by closing the pipe.
        try:
            address_pipe.recv()
        except EOFError:
            pass
        manager.shutdown()
    except Exception:
        try:
            address_pipe.send(('ERROR', traceback.format_exc()))
        except Exception:
            pass


def _manager_value() -> dict:
    """Module-level callable so :mod:`pickle` can ship it across the spawn boundary."""
    return {'status': 'ok', 'value': 42}


@mark.integration
def test_secure_manager_subprocess_roundtrip(tls_materials) -> None:
    """A SecureManager in a subprocess accepts a TLS-authenticated proxy RPC end-to-end."""
    cert, key = tls_materials
    cfg = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    authkey = b'integration-test-authkey'

    parent_pipe, child_pipe = mp.Pipe(duplex=True)
    ctx = mp.get_context('spawn')
    proc = ctx.Process(target=_manager_subprocess_target,
                       args=(child_pipe, cfg, authkey))
    proc.start()

    try:
        announcement = parent_pipe.recv()
        assert not (isinstance(announcement, tuple) and announcement and announcement[0] == 'ERROR'), \
            f'server failed to start: {announcement}'
        address = announcement

        install_process_context(cfg)
        try:
            client_mgr = SecureManager(address=address, authkey=authkey, tls=cfg)
            client_mgr.register('get_value')
            client_mgr.connect()
            proxy = client_mgr.get_value()
            assert proxy._getvalue() == {'status': 'ok', 'value': 42}
        finally:
            clear_process_context()
    finally:
        # Signal shutdown by closing our side of the pipe, then reap the subprocess.
        try:
            parent_pipe.send('shutdown')
        except Exception:
            pass
        parent_pipe.close()
        child_pipe.close()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)


# ---------------------------------------------------------------------------------------------------
# Handshake deadline: a TLS queue server keeps serving others while a connection is slow or silent in
# its TLS handshake, and closes one that has not completed it within the deadline.
# ---------------------------------------------------------------------------------------------------

AUTH = 'queue-test-key-0123456789abcdef'
HANDLER = 'hypershell-queue-handler'


@fixture
def server_tls(tls_materials):
    """A server TLSConfig; the process-wide client context that connecting clients install is cleared after."""
    cert, key = tls_materials
    yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    clear_process_context()


def queue_config(tls: Optional[TLSConfig], port: int = 0) -> QueueConfig:
    """Queue configuration on loopback (port 0 binds an ephemeral port)."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


def with_handshake(cfg: TLSConfig, seconds) -> TLSConfig:
    """`cfg` with its handshake deadline set by assignment."""
    cfg.handshake = seconds
    return cfg


def closed_after(sock: socket.socket, opened: float, limit: float) -> Optional[float]:
    """Seconds from `opened` until the server closed `sock` (anything it sends is read and dropped), or
    None if it is still open `limit` seconds after `opened`."""
    end = opened + limit
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return None
        sock.settimeout(left)
        try:
            data = sock.recv(4096)
        except TimeoutError:
            return None
        except OSError:  # Reset: closed with our bytes unread
            return time.monotonic() - opened
        if not data:
            return time.monotonic() - opened


def client_hello() -> bytes:
    """The first flight of a real TLS client: a ClientHello, built in memory."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ctx.wrap_bio(incoming, outgoing)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


class Trickle(threading.Thread):
    """Send `data` on `sock` one byte every `interval` seconds until stopped or the socket fails."""

    def __init__(self: Trickle, sock: socket.socket, data: bytes, interval: float = 0.25) -> None:
        super().__init__(daemon=True)
        self.sock, self.data, self.interval = sock, data, interval
        self.halt = threading.Event()
        self.start()

    def run(self: Trickle) -> None:
        for n in range(len(self.data)):
            if self.halt.wait(self.interval):
                return
            try:
                self.sock.send(self.data[n:n + 1])
            except OSError:
                return

    def stop(self: Trickle) -> None:
        self.halt.set()
        self.join(5)


def handlers() -> int:
    """Live handler threads of in-process queue servers."""
    return sum(1 for thread in threading.enumerate() if thread.name == HANDLER)


def descriptors() -> int:
    """Open descriptors of this process."""
    return len(os.listdir('/dev/fd'))


def settles(predicate, limit: float = 1.0) -> bool:
    """Whether `predicate` holds within `limit` seconds."""
    end = time.monotonic() + limit
    while not predicate():
        if time.monotonic() > end:
            return False
        time.sleep(0.02)
    return True


def first_call(port: int, tls: TLSConfig, result: dict) -> None:
    """Connect a client and make its first queue call, recording the seconds taken or the error."""
    start = time.monotonic()
    try:
        client = QueueClient(queue_config(tls, port))
        client.connect()
        client.completed.qsize()
        result['seconds'] = time.monotonic() - start
    except Exception as error:
        result['error'] = repr(error)


@mark.integration
def test_client_served_while_connections_stall_in_handshake(server_tls) -> None:
    """With 8 connections open that never send a byte, another client connects and makes its first
    queue call within 5 s."""
    with_handshake(server_tls, 300)
    stalled, caller = [], None
    with QueueServer(queue_config(server_tls)) as server:
        port = server.address[1]
        try:
            for _ in range(8):
                stalled.append(socket.create_connection(('127.0.0.1', port), timeout=5))
            time.sleep(0.25)
            result = {}
            caller = threading.Thread(target=first_call, args=(port, server_tls, result), daemon=True)
            caller.start()
            caller.join(5)
            assert 'error' not in result, result
            assert result.get('seconds') is not None and result['seconds'] <= 5, \
                f'no connect and first call within 5 s with 8 connections stalled ({result})'
        finally:
            for sock in stalled:
                sock.close()
            if caller is not None:
                caller.join(10)


@mark.integration
@mark.skipif(not os.path.isdir('/dev/fd'), reason='needs /dev/fd to count descriptors')
@mark.parametrize('peer', ['silent', 'slow'])
def test_handshake_deadline_closes_and_releases(server_tls, peer: str) -> None:
    """With a 2 s deadline, a connection that sends nothing and one that sends a real ClientHello a byte
    every 0.25 s are each closed 2 to 5 s after connecting, and their thread and descriptor are released
    within 1 s of the close. The bytes the slow peer keeps sending do not extend the deadline."""
    with_handshake(server_tls, 2)
    with QueueServer(queue_config(server_tls)) as server:
        port = server.address[1]
        time.sleep(0.25)
        threads, fds = handlers(), descriptors()
        sock = socket.create_connection(('127.0.0.1', port), timeout=5)
        opened = time.monotonic()
        hello = client_hello()
        assert len(hello) > 40  # More than 10 s of bytes at this rate
        trickle = Trickle(sock, hello) if peer == 'slow' else None
        try:
            after = closed_after(sock, opened, 6)
        finally:
            if trickle is not None:
                trickle.stop()
            sock.close()
        assert after is not None and 2 <= after <= 5, f'{peer} connection closed after {after} s (expected 2 to 5 s)'
        assert settles(lambda: handlers() <= threads), f'{handlers()} handler threads, {threads} before'
        assert settles(lambda: descriptors() <= fds), f'{descriptors()} descriptors, {fds} before'


@mark.integration
def test_stop_releases_a_pending_handshake(server_tls) -> None:
    """Stopping the server closes a connection still in its handshake and ends its thread within 1 s."""
    with_handshake(server_tls, 300)
    server = QueueServer(queue_config(server_tls))
    server.start()
    sock = None
    try:
        time.sleep(0.25)
        threads = handlers()
        sock = socket.create_connection(('127.0.0.1', server.address[1]), timeout=5)
        time.sleep(0.5)
        server.shutdown()
        stopped = time.monotonic()
        after = closed_after(sock, stopped, 1)
        assert after is not None, 'connection still open 1 s after the server stopped'
        assert settles(lambda: handlers() <= threads), f'{handlers()} handler threads, {threads} before'
    finally:
        if sock is not None:
            sock.close()
        server.shutdown()


@mark.integration
def test_reserve_refuses_a_pending_connection_at_once(server_tls, monkeypatch) -> None:
    """A connection that would use a reserved descriptor is closed at once, before any handshake."""
    limit = {'value': None}
    monkeypatch.setattr(hypershell.core.queue, '_open_file_limit', lambda: limit['value'])
    with_handshake(server_tls, 300)
    with QueueServer(queue_config(server_tls)) as server:
        limit['value'] = 0
        sock = socket.create_connection(('127.0.0.1', server.address[1]), timeout=5)
        try:
            after = closed_after(sock, time.monotonic(), 2)
        finally:
            sock.close()
        assert after is not None and after <= 1, f'refused connection closed after {after} s (expected at once)'


@mark.integration
def test_plain_server_has_no_handshake_deadline(server_tls) -> None:
    """A server without TLS never applies the deadline: a silent connection is still open after 3 s with
    a 2 s deadline set on a TLS configuration that is not enabled."""
    unused = with_handshake(TLSConfig(enabled=False), 2)
    with QueueServer(queue_config(unused)) as server:
        sock = socket.create_connection(('127.0.0.1', server.address[1]), timeout=5)
        try:
            after = closed_after(sock, time.monotonic(), 3)
        finally:
            sock.close()
        assert after is None, f'plain server closed a silent connection after {after} s'


@mark.integration
def test_burst_of_clients_with_default_deadline(server_tls) -> None:
    """64 clients connecting at once with the default deadline all connect and complete a queue call."""
    with QueueServer(queue_config(server_tls)) as server:
        port = server.address[1]
        results = [{} for _ in range(64)]
        callers = [threading.Thread(target=first_call, args=(port, server_tls, result), daemon=True)
                   for result in results]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(60)
        errors = [result['error'] for result in results if 'error' in result]
        done = [result for result in results if 'seconds' in result]
        assert not errors, errors[:3]
        assert len(done) == 64, f'{len(done)} of 64 clients completed a call'


# Handshake deadline configuration and logging

@mark.unit
@mark.parametrize('value, seconds', [(30, 30), ('30', 30), ('30s', 30), ('1m', 60), (2.5, 3), ('1:30', 90)])
def test_handshake_seconds_accepts_duration_forms(value, seconds: int) -> None:
    """The deadline takes any duration form, rounded up to whole seconds."""
    assert handshake_seconds(value) == seconds


@mark.unit
def test_handshake_default() -> None:
    """The deadline defaults to 30 seconds, in the configuration and on a TLSConfig."""
    assert DEFAULT_HANDSHAKE == 30
    assert TLSConfig().handshake == 30
    assert hypershell_config.server.tls.handshake == 30
    assert handshake_seconds(hypershell_config.server.tls.handshake) == 30


@mark.unit
@mark.parametrize('value', [0, '0', -5, '-5', 'abc', True, ''])
def test_handshake_seconds_refuses(value) -> None:
    """Zero, a negative or anything not a duration is refused, naming the key and the accepted forms."""
    with raises(TLSError) as exc_info:
        handshake_seconds(value)
    message = str(exc_info.value)
    assert message.startswith(HANDSHAKE_KEY) and TIME_FORMS in message


@mark.unit
def test_from_namespace_keeps_the_configured_value() -> None:
    """A TLSConfig built from a section carries the configured value unparsed, so a client never refuses
    a key only servers read."""
    section = {'enabled': True, 'cert': '<none>', 'key': '<none>', 'cafile': '<none>'}
    assert from_namespace({**section, 'handshake': '1m'}).handshake == '1m'
    assert from_namespace({**section, 'handshake': 'abc'}).handshake == 'abc'
    assert from_namespace(section).handshake == DEFAULT_HANDSHAKE


@mark.unit
@mark.parametrize('value', [0, -5, 'abc'])
def test_tls_server_refuses_bad_handshake_before_binding(server_tls, value) -> None:
    """A TLS queue server with a bad deadline does not start and leaves its port free."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server = QueueServer(queue_config(with_handshake(server_tls, value), port))
    with raises(QueueServerError) as exc_info:
        server.start()
    assert HANDSHAKE_KEY in str(exc_info.value) and TIME_FORMS in str(exc_info.value)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))  # Nothing was left listening there


@mark.unit
@mark.parametrize('value', [0, 'abc'])
def test_plain_server_ignores_handshake(value) -> None:
    """A server without TLS never reads the deadline, so any value lets it start."""
    with QueueServer(queue_config(with_handshake(TLSConfig(enabled=False), value))) as server:
        assert server.address[1] > 0


@mark.integration
def test_handshake_environment_variable(tmp_path: Path) -> None:
    """HYPERSHELL_SERVER_TLS_HANDSHAKE reaches the key and a TLSConfig built from its section."""
    code = ('from hypershell.core.config import config\n'
            'from hypershell.core.tls import from_namespace, handshake_seconds\n'
            "section = {**config.server.tls, 'cert': '<none>', 'key': '<none>', 'cafile': '<none>'}\n"
            'value = from_namespace(section).handshake\n'
            'print(repr(config.server.tls.handshake), repr(value), handshake_seconds(value))\n')
    env = {**os.environ, 'HYPERSHELL_SERVER_TLS_HANDSHAKE': '1m', 'HYPERSHELL_SITE': str(tmp_path)}
    out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == ["'1m'", "'1m'", '60']


@mark.unit
@mark.parametrize('app', ['server', 'cluster'])
def test_commands_carry_handshake_onto_tls(app: str, monkeypatch, tmp_path: Path) -> None:
    """hs server and hs cluster put the configured deadline on the TLSConfig their server starts with."""
    from hypershell.server import ServerApp
    from hypershell.cluster import ClusterApp
    monkeypatch.setitem(hypershell_config['server']['tls'], 'handshake', '2m')
    cert, key = ensure_default_materials(str(tmp_path), hostnames=['localhost', '127.0.0.1'])
    command = (ServerApp if app == 'server' else ClusterApp).from_cmdline(
        ['--tls-cert', cert, '--tls-key', key, '--tls-ca', cert])
    command.enable_tls()
    assert command.tls.handshake == '2m'


@mark.unit
@mark.parametrize('app', ['server', 'cluster', 'relay'])
@mark.parametrize('value', [0, -5, 'abc'])
def test_commands_refuse_bad_handshake(app: str, value, monkeypatch) -> None:
    """hs server, hs cluster and hs relay refuse a bad deadline as an argument error naming the key."""
    from cmdkit.cli import ArgumentError
    from hypershell.server import ServerApp
    from hypershell.cluster import ClusterApp
    from hypershell.relay import RelayApp
    monkeypatch.setitem(hypershell_config['server']['tls'], 'handshake', value)
    with raises(ArgumentError) as exc_info:
        if app == 'relay':
            RelayApp.from_cmdline([]).check_args()
        else:
            (ServerApp if app == 'server' else ClusterApp).from_cmdline([]).enable_tls()
    assert str(exc_info.value).startswith(HANDSHAKE_KEY)


@mark.unit
@mark.parametrize('app', ['server', 'cluster'])
def test_commands_without_tls_ignore_handshake(app: str, monkeypatch) -> None:
    """With --no-tls, hs server and hs cluster never read the deadline."""
    from hypershell.server import ServerApp
    from hypershell.cluster import ClusterApp
    monkeypatch.setitem(hypershell_config['server']['tls'], 'handshake', 'abc')
    command = (ServerApp if app == 'server' else ClusterApp).from_cmdline(['--no-tls'])
    command.enable_tls()
    assert command.tls is None


MARKER = 'handshake-peer-marker.example'


@mark.integration
def test_deadline_closes_are_logged_without_peer_data(server_tls, monkeypatch, caplog) -> None:
    """Each close at the deadline is logged at DEBUG with the peer address and the deadline; one WARNING
    names the key for several closes inside the interval; nothing the peers sent is logged."""
    monkeypatch.setattr(hypershell.core.queue, 'REFUSAL_WARNING_INTERVAL', 3600.0)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ctx.wrap_bio(incoming, outgoing, server_hostname=MARKER)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    hello = outgoing.read()
    assert MARKER.encode() in hello
    with_handshake(server_tls, 1)
    socks, trickle = [], None
    with caplog.at_level('DEBUG', logger='hypershell.core.queue'):
        with QueueServer(queue_config(server_tls)) as server:
            port = server.address[1]
            try:
                for _ in range(3):
                    socks.append(socket.create_connection(('127.0.0.1', port), timeout=5))
                opened = time.monotonic()
                socks[1].sendall(hello[:-1])  # All but the last byte: the name is sent, the hello never ends
                trickle = Trickle(socks[2], hello)
                closes = [closed_after(sock, opened, 4) for sock in socks]
            finally:
                if trickle is not None:
                    trickle.stop()
                for sock in socks:
                    sock.close()
            assert all(after is not None for after in closes), closes
            assert settles(lambda: sum('did not complete its TLS handshake' in r.getMessage()
                                       for r in caplog.records) >= 3)
    records = [r for r in caplog.records if r.name == 'hypershell.core.queue']
    debug = [r.getMessage() for r in records
             if r.levelname == 'DEBUG' and 'did not complete its TLS handshake within 1 seconds' in r.getMessage()]
    warning = [r.getMessage() for r in records if r.levelname == 'WARNING']
    assert len(debug) == 3, debug
    assert all('127.0.0.1:' in line for line in debug), debug
    assert len(warning) == 1 and HANDSHAKE_KEY in warning[0] and '1 seconds' in warning[0], warning
    text = ' '.join(r.getMessage() for r in caplog.records)
    for fragment in (MARKER, hello[:8].hex(), repr(hello[:8])[2:-1]):
        assert fragment not in text


@mark.integration
def test_other_handshake_failures_logged_as_before(server_tls, caplog) -> None:
    """A peer that sends something other than TLS and closes is dropped with the usual DEBUG line and
    no deadline warning."""
    with_handshake(server_tls, 300)
    with caplog.at_level('DEBUG', logger='hypershell.core.queue'):
        with QueueServer(queue_config(server_tls)) as server:
            with socket.create_connection(('127.0.0.1', server.address[1]), timeout=5) as sock:
                sock.sendall(b'GET / HTTP/1.0\r\n\r\n')
                closed_after(sock, time.monotonic(), 2)
            assert settles(lambda: any('Queue server dropped a connection' in r.getMessage()
                                       for r in caplog.records), 2)
    records = [r for r in caplog.records if r.name == 'hypershell.core.queue']
    assert any(r.levelname == 'DEBUG' and 'Queue server dropped a connection' in r.getMessage() for r in records)
    assert not [r for r in records if r.levelname == 'WARNING'], [r.getMessage() for r in records]
    assert not any('did not complete its TLS handshake' in r.getMessage() for r in records)


# Accepting goes on when one connection's handler cannot start

@mark.integration
@mark.parametrize('secure', [True, False], ids=['tls', 'plain'])
def test_accepting_goes_on_when_a_handler_does_not_start(server_tls, monkeypatch, caplog, secure: bool) -> None:
    """When the handler thread for one accepted connection fails to start, that connection is closed,
    forgotten and logged with one WARNING naming its peer, and a later client connects and makes its
    first queue call."""
    tls = with_handshake(server_tls, 300) if secure else with_handshake(TLSConfig(enabled=False), 300)
    original = hypershell.core.queue._ServerThread.start
    failing = {'left': 0}

    def start(thread) -> None:
        if failing['left'] > 0 and thread.name == HANDLER:
            failing['left'] -= 1
            raise RuntimeError("can't start new thread")
        original(thread)

    monkeypatch.setattr(hypershell.core.queue._ServerThread, 'start', start)
    caller = None
    with caplog.at_level('DEBUG', logger='hypershell.core.queue'):
        with QueueServer(queue_config(tls)) as server:
            port = server.address[1]
            in_process = server.shutdown._args[0]
            failing['left'] = 1
            sock = socket.create_connection(('127.0.0.1', port), timeout=5)
            try:
                after = closed_after(sock, time.monotonic(), 2)
            finally:
                sock.close()
            assert failing['left'] == 0, 'the handler start was never attempted'
            assert after is not None and after <= 1, f'connection closed after {after} s (expected at once)'
            assert settles(lambda: not in_process._live), f'{len(in_process._live)} live connections left'
            result = {}
            caller = threading.Thread(target=first_call, args=(port, tls, result), daemon=True)
            caller.start()
            caller.join(10)
            assert result.get('seconds') is not None, f'a later client was not served ({result})'
    if caller is not None:
        caller.join(10)
    warnings = [r.getMessage() for r in caplog.records
                if r.name == 'hypershell.core.queue' and r.levelname == 'WARNING']
    assert len(warnings) == 1 and 'handler did not start' in warnings[0] and '127.0.0.1:' in warnings[0], warnings


@mark.unit
def test_handler_start_failures_warn_once_per_interval(monkeypatch, caplog) -> None:
    """Of several connections whose handlers do not start inside the interval, the first is logged as a
    WARNING and the rest at DEBUG; each is closed and forgotten."""
    monkeypatch.setattr(hypershell.core.queue, 'REFUSAL_WARNING_INTERVAL', 3600.0)
    server = hypershell.core.queue._InProcessServer.__new__(hypershell.core.queue._InProcessServer)
    server._lock, server._live, server._unstarted_warned_at = threading.Lock(), set(), None
    pairs = [socket.socketpair() for _ in range(3)]
    try:
        with caplog.at_level('DEBUG', logger='hypershell.core.queue'):
            for n, (conn, _) in enumerate(pairs):
                server._live.add(conn)
                server._unstarted(conn, ('127.0.0.1', 4000 + n), RuntimeError("can't start new thread"))
                assert conn.fileno() == -1 and conn not in server._live
    finally:
        for conn, other in pairs:
            conn.close()
            other.close()
    levels = [r.levelname for r in caplog.records
              if r.name == 'hypershell.core.queue' and 'handler did not start' in r.getMessage()]
    assert levels == ['WARNING', 'DEBUG', 'DEBUG'], levels
