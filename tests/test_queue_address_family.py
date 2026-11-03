# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test that queue clients reach the server on the address family it listens on.

The server listens on IPv4 only. A client that follows the resolver to ``::1`` can land
on an unrelated process holding an IPv6-only listener on the same port number. These
tests build that condition on purpose: a test-owned ``::1`` listener that accepts and
resets every connection, plus a resolver that offers ``::1`` first for ``localhost``.
Each trap test first proves the trap is live (an unrestricted dial lands on it) so that
a pass is never vacuous. Hosts without IPv6 loopback skip the trap tests.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Final, Iterator, List, Optional, Tuple

# Standard libs
import os
import ssl
import sys
import time
import errno
import signal
import socket
import struct
import sqlite3
import threading
import traceback
import subprocess
from contextlib import closing, contextmanager
from multiprocessing.connection import Client, Listener, answer_challenge, deliver_challenge
from pathlib import Path

# External libs
from pytest import fixture, mark, raises, skip
from cmdkit.app import exit_status

# Internal libs
from tests import conftest, create_taskfile_echo
from hypershell.core import queue as queue_module
from hypershell.core.queue import SecureListener, secure_client
from hypershell.core.tls import (TLSConfig, ensure_default_materials,
                                 install_process_context, clear_process_context)


AUTHKEY: Final[bytes] = b'address-family-test-key'
TIMEOUT: Final[float] = 10.0
CLUSTER_TIMEOUT: Final[float] = 180.0


# Source of a resolver wrapper that offers ``('::1', port)`` first for ``localhost`` when the
# caller accepts IPv6 (family 0/AF_UNSPEC/AF_INET6), injecting it if the real resolver lacks
# it. IPv4-only lookups pass through untouched. Executed in process for transport tests and
# written out as ``sitecustomize.py`` for the command-line drive.
RESOLVER_SOURCE: Final[str] = '''\
import socket as _socket

_real_getaddrinfo = _socket.getaddrinfo


def ipv6_first_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Return the real results for `host`, with ``::1`` first for ``localhost``."""
    results = _real_getaddrinfo(host, port, family, type, proto, flags)
    if host != 'localhost' or family not in (0, _socket.AF_UNSPEC, _socket.AF_INET6):
        return results
    try:
        number = int(port)
    except (TypeError, ValueError):
        return results
    first = (_socket.AF_INET6, type or _socket.SOCK_STREAM, proto or _socket.IPPROTO_TCP,
             '', ('::1', number, 0, 0))
    rest = [entry for entry in results
            if not (entry[0] == _socket.AF_INET6 and entry[4][0] == '::1')]
    return [first, *rest]
'''

SITECUSTOMIZE_SOURCE: Final[str] = (RESOLVER_SOURCE +
                                    '\n\n_socket.getaddrinfo = ipv6_first_getaddrinfo\n')


def build_resolver() -> Callable:
    """Compile :data:`RESOLVER_SOURCE` around the current ``socket.getaddrinfo``."""
    namespace: Dict[str, object] = {}
    exec(compile(RESOLVER_SOURCE, 'ipv6_first_resolver', 'exec'), namespace)
    return namespace['ipv6_first_getaddrinfo']  # type: ignore[return-value]


def wait_for(predicate: Callable[[], bool], timeout: float = TIMEOUT) -> bool:
    """Poll `predicate` until it holds or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class IPv6Resetter:
    """An IPv6-only listener on ``::1`` that accepts and resets every connection.

    Stands in for an unrelated local process holding the server's port number on IPv6.
    Counts the connections it receives so a test can prove nothing reached it.
    """

    def __init__(self: IPv6Resetter, sock: socket.socket) -> None:
        self._socket = sock
        self._count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def port(self: IPv6Resetter) -> int:
        return self._socket.getsockname()[1]

    @property
    def count(self: IPv6Resetter) -> int:
        with self._lock:
            return self._count

    def _serve(self: IPv6Resetter) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            with self._lock:
                self._count += 1

    def close(self: IPv6Resetter) -> None:
        self._stop.set()
        self._thread.join(timeout=TIMEOUT)
        self._socket.close()


def bind_ipv6_loopback(port: int) -> socket.socket:
    """Bind an IPv6-only listening socket on ``('::1', port)``; raises OSError on failure."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind(('::1', port))
        sock.listen(16)
        sock.settimeout(0.1)
    except OSError:
        sock.close()
        raise
    return sock


@contextmanager
def ipv6_resetter() -> Iterator[IPv6Resetter]:
    """Hold a fresh port number on ``::1`` with a resetter; skip without IPv6 loopback.

    The port is drawn free on IPv4 loopback, so a real server can bind it there next.
    """
    if not socket.has_ipv6:
        skip('host has no IPv6 support')
    sock = None
    for _ in range(32):
        port = conftest.free_port()
        try:
            sock = bind_ipv6_loopback(port)
            break
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                continue
            skip(f'host has no IPv6 loopback ({error})')
    if sock is None:
        raise RuntimeError('could not find a port number free on both loopbacks')
    resetter = IPv6Resetter(sock)
    try:
        yield resetter
    finally:
        resetter.close()


def assert_trap_armed(resetter: IPv6Resetter) -> int:
    """An unrestricted dial to ``('localhost', port)`` must land on the resetter.

    Proves the forced resolver order took effect on this host. Returns the resetter's
    count afterwards, the baseline every later assertion compares against.
    """
    before = resetter.count
    with socket.create_connection(('localhost', resetter.port), timeout=TIMEOUT):
        pass
    assert wait_for(lambda: resetter.count > before), 'trap not armed: dial missed the ::1 listener'
    return resetter.count


@fixture
def trap(monkeypatch) -> Iterator[IPv6Resetter]:
    """A ``::1`` resetter plus an in-process resolver that offers ``::1`` first, armed."""
    with ipv6_resetter() as resetter:
        monkeypatch.setattr(socket, 'getaddrinfo', build_resolver())
        assert_trap_armed(resetter)
        yield resetter


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> Tuple[str, str]:
    """Self-signed cert and key for ``localhost`` and ``127.0.0.1``."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture
def install_tls(tls_materials) -> Iterator[TLSConfig]:
    """Install a TLS configuration (cert as its own CA) for the duration of a test."""
    cert, key = tls_materials
    cfg = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    install_process_context(cfg)
    yield cfg
    clear_process_context()


class AcceptThread:
    """Accept one connection on `listener` in the background and run `setup` on it."""

    def __init__(self: AcceptThread, listener, address: Tuple[str, int],
                 setup: Optional[Callable] = None) -> None:
        self._listener = listener
        self._address = address
        self._setup = setup
        self.conn = None
        self.error: Optional[str] = None
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self: AcceptThread) -> None:
        try:
            conn = self._listener.accept()
            if self._setup is not None:
                self._setup(conn)
            self.conn = conn
        except Exception:
            self.error = traceback.format_exc()

    def join(self: AcceptThread) -> None:
        self._thread.join(timeout=TIMEOUT)

    def close(self: AcceptThread) -> None:
        """Release a still-blocked accept with a throwaway IPv4 connection, then clean up."""
        if self._thread.is_alive():
            try:
                with socket.create_connection(self._address, timeout=TIMEOUT):
                    pass
            except OSError:
                pass
            self._thread.join(timeout=TIMEOUT)
        if self.conn is not None:
            self.conn.close()


def server_handshake(conn) -> None:
    """Server half of the manager's auth handshake (mirrors the client's order)."""
    deliver_challenge(conn, AUTHKEY)
    answer_challenge(conn, AUTHKEY)


def assert_roundtrip(client, accepter: AcceptThread) -> None:
    """The accepted server side and `client` exchange a payload both ways."""
    accepter.join()
    assert accepter.error is None, accepter.error
    assert accepter.conn is not None, 'accept() never returned'
    payload = {'task_id': 'abc', 'args': 'echo hi'}
    client.send(payload)
    assert accepter.conn.recv() == payload
    accepter.conn.send(b'pong')
    assert client.recv() == b'pong'


@mark.integration
def test_tls_client_skips_ipv6_listener(trap: IPv6Resetter, install_tls) -> None:
    """The TLS client reaches the IPv4 server, not an IPv6-only listener on its port."""
    baseline = trap.count
    address = ('127.0.0.1', trap.port)
    listener = SecureListener(address=address, backlog=1)
    accepter = AcceptThread(listener, address, setup=server_handshake)
    client = None
    try:
        client = secure_client(('localhost', trap.port), authkey=AUTHKEY)
        assert_roundtrip(client, accepter)
        assert trap.count == baseline
    finally:
        if client is not None:
            client.close()
        accepter.close()
        listener.close()


@mark.integration
def test_cleartext_client_skips_ipv6_listener(trap: IPv6Resetter) -> None:
    """The cleartext client reaches the IPv4 server under the same trap (reference)."""
    baseline = trap.count
    address = ('127.0.0.1', trap.port)
    listener = Listener(address=address, backlog=1, authkey=AUTHKEY)
    accepter = AcceptThread(listener, address)
    client = None
    try:
        client = Client(('localhost', trap.port), authkey=AUTHKEY)
        assert_roundtrip(client, accepter)
        assert trap.count == baseline
    finally:
        if client is not None:
            client.close()
        accepter.close()
        listener.close()


def run_bounded(argv: List[str], env: Dict[str, str], timeout: float) -> Tuple[int, str, str]:
    """Run `argv` in its own process group; kill the whole group if it outlives `timeout`."""
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        raise AssertionError(f'{argv} timed out after {timeout}s:\n{stderr.decode()}')
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


@mark.integration
@mark.parametrize('tls_option', [[], ['--no-tls']], ids=['tls', 'no-tls'])
def test_cluster_skips_ipv6_listener(temp_site: Path, tmp_path: Path, tls_option: List[str]) -> None:
    """``hs cluster`` completes every task while an IPv6-only listener holds its port."""
    with ipv6_resetter() as resetter:
        shim = tmp_path / 'shim'
        shim.mkdir()
        (shim / 'sitecustomize.py').write_text(SITECUSTOMIZE_SOURCE, encoding='utf-8')
        env = dict(os.environ)
        env['HYPERSHELL_SERVER_PORT'] = str(resetter.port)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(shim), env.get('PYTHONPATH')]))

        # The resolver shim must be live in a fresh interpreter with this environment.
        before = resetter.count
        rc, stdout, stderr = run_bounded([sys.executable, '-c', (
            'import socket, sitecustomize, sys; '
            'assert sitecustomize.__file__.startswith(sys.argv[1]), sitecustomize.__file__; '
            'socket.create_connection(("localhost", int(sys.argv[2])), timeout=10).close()'
        ), str(shim), str(resetter.port)], env, TIMEOUT)
        assert rc == 0, stderr
        assert wait_for(lambda: resetter.count > before), 'trap not armed in subprocess'
        baseline = resetter.count

        taskfile = create_taskfile_echo(temp_site, count=4)
        rc, stdout, stderr = run_bounded(['hs', 'cluster', str(taskfile), *tls_option],
                                         env, CLUSTER_TIMEOUT)
        assert rc == exit_status.success, f'exit status {rc}:\n{stderr}'
        assert stdout == '\n'.join(map(str, range(4)))
        assert resetter.count == baseline

    database = os.environ['HYPERSHELL_DATABASE_FILE']
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT exit_status, completion_time FROM task').fetchall()
    assert len(rows) == 4
    assert all(status == 0 and completed is not None for status, completed in rows)


@mark.integration
def test_refused_when_nothing_listening(install_tls) -> None:
    """Both transports raise ConnectionRefusedError when no server listens on the port."""
    port = conftest.free_port()
    with raises(ConnectionRefusedError):
        secure_client(('localhost', port), authkey=AUTHKEY)
    with raises(ConnectionRefusedError):
        Client(('localhost', port), authkey=AUTHKEY)


@mark.unit
def test_no_ipv4_address_fails_promptly(install_tls) -> None:
    """Both transports fail to resolve a host with no IPv4 address, at once and alike."""
    port = conftest.free_port()
    for dial in (secure_client, Client):
        start = time.monotonic()
        with raises(socket.gaierror):
            dial(('::1', port), authkey=AUTHKEY)
        assert time.monotonic() - start < 5


@mark.integration
def test_hostname_check_uses_given_name(tmp_path: Path, monkeypatch) -> None:
    """With host-name checking on, the client checks the name it was given.

    The certificate names ``localhost`` only, so a check against the resolved address
    (``127.0.0.1``) would fail the handshake.
    """
    cert, key = ensure_default_materials(str(tmp_path), hostnames=['localhost'])
    install_process_context(TLSConfig(enabled=True, cert=cert, key=key, cafile=cert))
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=cert)
        context.check_hostname = True
        monkeypatch.setattr(queue_module, 'get_client_context', lambda: context)
        port = conftest.free_port()
        address = ('127.0.0.1', port)
        listener = SecureListener(address=address, backlog=1)
        accepter = AcceptThread(listener, address, setup=server_handshake)
        client = None
        try:
            client = secure_client(('localhost', port), authkey=AUTHKEY)
            assert client._ssl_sock.server_hostname == 'localhost'
            assert_roundtrip(client, accepter)
        finally:
            if client is not None:
                client.close()
            accepter.close()
            listener.close()
    finally:
        clear_process_context()


@mark.unit
def test_free_port_skips_port_held_on_ipv6_loopback(monkeypatch) -> None:
    """free_port() draws again when its first draw is already bound on ``::1``."""
    if not socket.has_ipv6:
        skip('host has no IPv6 support')
    try:
        holder = bind_ipv6_loopback(0)
    except OSError as error:
        skip(f'host has no IPv6 loopback ({error})')
    with holder:
        held = holder.getsockname()[1]
        draws = iter([held])
        real_draw = conftest.draw_port
        monkeypatch.setattr(conftest, 'draw_port', lambda: next(draws, None) or real_draw())
        port = conftest.free_port()
        assert port != held
    with bind_ipv6_loopback(port):
        pass
