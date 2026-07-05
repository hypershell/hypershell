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
