# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tests for the managed-queue primitive in :mod:`hypershell.core.queue`."""


# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import os
import sys
import time
import queue
import socket
import _thread
import threading
import multiprocessing as mp
from multiprocessing import util
from multiprocessing.connection import Listener, Client, Connection, answer_challenge, deliver_challenge
from multiprocessing.managers import Server, BaseManager, State, AutoProxy, RemoteError, convert_to_error

# External libs
from pytest import fixture, mark, raises, skip

# Internal libs
from hypershell.core import queue as queue_module
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig, make_sentinel, unsupported_internals
from hypershell.core.tls import TLSConfig, ensure_default_materials, clear_process_context


AUTH = 'queue-test-key-0123456789abcdef'
NAMES = ('scheduled', 'completed', 'heartbeat', 'confirmed')
TIMEOUT = 0.2
WAIT = 2.0


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture
def tls_config(tls_materials) -> Iterator[TLSConfig]:
    """A TLS configuration whose cert doubles as the client's CA; the process slot is cleared after."""
    cert, key = tls_materials
    yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    clear_process_context()


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS disabled and once with TLS enabled."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
        clear_process_context()


@fixture(params=['fork', 'spawn'])
def start_method(request) -> Iterator[str]:
    """Force a multiprocessing start method for the test, restoring the previous one after."""
    if request.param not in mp.get_all_start_methods():
        skip(f'start method {request.param!r} not available')
    previous = mp.get_start_method(allow_none=True)
    mp.set_start_method(request.param, force=True)
    yield request.param
    mp.set_start_method(previous, force=True)


def make_config(host: str = '127.0.0.1', port: int = 0,
                tls: Optional[TLSConfig] = None, size: int = 2) -> QueueConfig:
    """Queue configuration for tests (port 0 binds an ephemeral port)."""
    return QueueConfig(host=host, port=port, auth=AUTH, size=size, tls=tls)


def connect(host: str, port: int, tls: Optional[TLSConfig] = None) -> QueueClient:
    """Connect a client to a running server."""
    client = QueueClient(make_config(host, port, tls))
    client.connect()
    return client


def wait_until(predicate, timeout: float = WAIT) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@mark.unit
class TestPrivateInternals:
    """The CPython internals the in-process serve loop relies on are present and shaped as expected.

    These are private to :mod:`multiprocessing`; a future CPython that changes any of them must fail
    here, loudly, rather than leave the queue server misbehaving silently. The presence and shape
    checks are the ones a queue server runs before it binds; the rest exercise the behaviour live.
    """

    def test_shared_checks_pass(self) -> None:
        """Every check the queue server runs at start passes on this Python."""
        assert unsupported_internals() == []

    def test_server_constructor(self) -> None:
        """``Server(registry, address, authkey, serializer)`` binds at construction."""
        server = Server({}, ('127.0.0.1', 0), b'x' * 16, 'pickle')
        try:
            assert isinstance(server.address, tuple) and server.address[1] > 0
            assert hasattr(server, 'listener')
            assert hasattr(server, 'mutex')
            assert isinstance(server.id_to_obj, dict)
            assert server.registry == {}
            assert server.authkey == b'x' * 16
        finally:
            server.listener.close()

    def test_stock_listener_socket(self) -> None:
        """The stock listener keeps its bound socket at ``_listener._socket``."""
        listener = Listener(('127.0.0.1', 0), family='AF_INET')
        try:
            sock = listener._listener._socket
            assert isinstance(sock, socket.socket)
            assert sock.getsockname() == listener.address
        finally:
            listener.close()

    def test_manager_state(self) -> None:
        """``BaseManager`` keeps the attributes the in-process start sets directly."""
        manager = BaseManager(address=('127.0.0.1', 0), authkey=b'x' * 16)
        for name in ('_state', '_address', '_authkey', '_serializer'):
            assert hasattr(manager, name), name
        assert manager._state.value == State.INITIAL

    def test_proxy_finalizer(self) -> None:
        """A client proxy's ``_close`` is a ``util.Finalize`` whose callback takes the token first.

        The stock callback is ``BaseProxy._decref``; a queue client swaps it for its own only when it is.
        """
        with QueueServer(make_config()) as server:
            client = connect('127.0.0.1', server.address[1])
            for name in NAMES:
                proxy = getattr(client, name)
                assert isinstance(proxy._close, util.Finalize), name
                assert proxy._close._callback is queue_module._decref, name
                assert proxy._close._args[0] is proxy._token, name

    def test_thread_registry(self) -> None:
        """``threading._active`` lists a thread from its start-up code on, not at its first instruction,
        with its native identifier already set, and gives a thread that :mod:`threading` did not start a
        ``_DummyThread``.

        An identifier the OS has reused may still map to a stale entry of an ended thread: the
        ``_DummyThread`` of a thread that :mod:`threading` did not start or, on 3.11 and 3.12, the
        ``_MainThread`` of a thread that first imported :mod:`threading`. A stale entry does not carry
        the calling thread's native identifier.
        """
        def unregistered() -> bool:
            entry = threading._active.get(threading.get_ident())
            return (entry is None or isinstance(entry, threading._DummyThread)
                    or entry.native_id != threading.get_native_id())

        def registered() -> bool:
            entry = threading._active.get(threading.get_ident())
            return entry is thread and entry.native_id == threading.get_native_id()

        seen = []

        class Probe(threading.Thread):
            def _bootstrap(self) -> None:
                seen.append(unregistered())
                super()._bootstrap()

        thread = Probe(target=lambda: seen.append(registered()))
        thread.start()
        thread.join()
        assert seen == [True, True] and threading._active.get(thread.ident) is not thread

        done = threading.Event()

        def foreign() -> None:
            seen.append(unregistered())
            seen.append(isinstance(threading.current_thread(), threading._DummyThread))
            done.set()

        _thread.start_new_thread(foreign, ())
        assert done.wait(5) and seen[2:] == [True, True]

    def test_finalize(self) -> None:
        """``util.Finalize`` accepts ``args`` and ``exitpriority``, and runs once."""
        calls = []

        class Owner:
            pass

        owner = Owner()
        finalizer = util.Finalize(owner, calls.append, args=(1,), exitpriority=0)
        finalizer()
        finalizer()
        assert calls == [1]

    def test_serve_client_takes_no_mutex(self) -> None:
        """``Server.serve_client`` runs a method without the server mutex, so a long call blocks no other client."""
        assert 'mutex' not in Server.serve_client.__code__.co_names

    def test_create_passes_args_and_refusal_is_a_traceback(self) -> None:
        """A registry callable gets the client's positional arguments, and an exception it raises reaches
        the client as ``RemoteError`` while the server keeps serving."""
        seen = []

        def provide(*args):
            seen.append(args)
            if not args:
                raise RuntimeError('refused here')
            return queue.Queue()

        manager = queue_module.SecureManager(address=('127.0.0.1', 0), authkey=AUTH.encode())
        manager._serve_in_process({'_get_thing': (provide, queue_module.QUEUE_EXPOSED, None, AutoProxy)})
        try:
            class Caller(BaseManager):
                pass

            Caller.register('_get_thing')
            caller = Caller(address=('127.0.0.1', manager.address[1]), authkey=AUTH.encode())
            caller.connect()
            with raises(RemoteError, match='refused here'):
                caller._get_thing()
            caller._get_thing(7).put(1)
            assert seen == [(), (7, )]
        finally:
            manager.shutdown()

    def test_convert_to_error(self) -> None:
        """``convert_to_error`` turns a ``#TRACEBACK`` reply into ``RemoteError``."""
        assert isinstance(convert_to_error('#TRACEBACK', 'text'), RemoteError)

    def test_proxy_connection_per_thread(self) -> None:
        """``BaseProxy._callmethod`` keeps one connection per thread, cached at ``_tls.connection``."""
        with QueueServer(make_config()) as server:
            client = connect('127.0.0.1', server.address[1])
            client.completed.put(1)
            first = client.completed._tls.connection
            client.completed.put(2)
            assert client.completed._tls.connection is first
            other = []
            thread = threading.Thread(target=lambda: (client.completed.qsize(),
                                                      other.append(client.completed._tls.connection)))
            thread.start()
            thread.join(timeout=WAIT)
            assert other and other[0] is not first


@mark.unit
def test_start_creates_no_child(start_method: str) -> None:
    """Starting the queue server leaves the child-process count unchanged."""
    before = len(mp.active_children())
    with QueueServer(make_config()):
        after = len(mp.active_children())
    assert after == before


@mark.unit
def test_two_servers_in_one_process(start_method: str) -> None:
    """Two queue servers alive in one process each serve their own queues."""
    with QueueServer(make_config()) as first, QueueServer(make_config()) as second:
        a = connect('127.0.0.1', first.address[1])
        b = connect('127.0.0.1', second.address[1])
        first.scheduled.put(b'first', timeout=WAIT)
        second.scheduled.put(b'second', timeout=WAIT)
        assert a.scheduled.get(timeout=WAIT) == b'first'
        assert b.scheduled.get(timeout=WAIT) == b'second'


@fixture
def default_timeout() -> Iterator[float]:
    """A process default socket timeout, as an embedding application may set; the previous one is
    restored after."""
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30.0)
    try:
        yield 30.0
    finally:
        socket.setdefaulttimeout(previous)


def roundtrip(tls: Optional[TLSConfig]) -> None:
    """Pass items across every queue in both directions between a server thread and a client."""
    with QueueServer(make_config(tls=tls)) as server:
        client = connect('127.0.0.1', server.address[1], tls)
        for name in NAMES:
            local, remote = getattr(server, name), getattr(client, name)
            local.put(b'down', timeout=WAIT)
            assert remote.get(timeout=WAIT) == b'down'
            remote.task_done()
            remote.put(b'up', timeout=WAIT)
            assert local.get(timeout=WAIT) == b'up'
            local.task_done()


@mark.unit
def test_roundtrip(tls: Optional[TLSConfig]) -> None:
    """Items cross every queue in both directions between a server thread and a remote client."""
    roundtrip(tls)


@mark.unit
def test_roundtrip_under_default_timeout(tls: Optional[TLSConfig], default_timeout: float) -> None:
    """A process default socket timeout leaves the queue connections working, on both ends.

    A new socket object takes the default timeout and, when there is one, makes its descriptor
    non-blocking, while the stock connection reads with a blocking read.
    """
    roundtrip(tls)


def no_delay(conn) -> bool:
    """Whether Nagle's algorithm is off (``TCP_NODELAY`` set) on the socket behind `conn`."""
    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0


@mark.unit
def test_connections_disable_nagle(tls: Optional[TLSConfig]) -> None:
    """Both ends of a live queue connection set ``TCP_NODELAY``.

    The handshake ends with two writes before a read on the client (and a TLS 1.3 server writes its
    session tickets before its challenge), so with Nagle's algorithm on, each new connection waits
    out the peer's delayed acknowledgement.
    """
    with QueueServer(make_config(tls=tls)) as server:
        client = connect('127.0.0.1', server.address[1], tls)
        client.scheduled.put(b'x', timeout=WAIT)
        assert server.scheduled.get(timeout=WAIT) == b'x'
        client_conn = client.scheduled._tls.connection
        assert no_delay(client_conn)
        in_process = server.shutdown._args[0]
        # Only the proxies' shared connection stays open once the handshake connections close.
        assert wait_until(lambda: len(in_process._live) == 1), len(in_process._live)
        with in_process._lock:
            server_conn, = in_process._live
            assert no_delay(server_conn)


def tcp_pair() -> tuple[Connection, Connection]:
    """The accepted and the dialed end of a stock blocking TCP connection on the loopback."""
    with Listener(('127.0.0.1', 0)) as listener:
        dialed = Client(listener.address)
        return listener.accept(), dialed


@mark.unit
@mark.parametrize('blocking', [True, False], ids=['blocking', 'non-blocking'])
def test_no_delay_connection_keeps_blocking_mode(default_timeout: float, blocking: bool) -> None:
    """Setting ``TCP_NODELAY`` through a duplicate descriptor leaves the blocking mode as it was.

    The mode belongs to the open file description that the duplicate shares with the connection,
    and the socket object built on the duplicate takes the process default timeout.
    """
    accepted, dialed = tcp_pair()
    with accepted, dialed:
        os.set_blocking(dialed.fileno(), blocking)
        queue_module._no_delay_connection(dialed)
        assert os.get_blocking(dialed.fileno()) is blocking
        assert no_delay(dialed)


@mark.unit
def test_plain_listener_under_default_timeout(default_timeout: float) -> None:
    """The cleartext listener hands over a blocking connection, so a stock client's handshake and
    first exchange go through, as with a manager's server (no `authkey` on the listener)."""
    authkey = AUTH.encode()
    replies = []
    with queue_module._PlainListener(('127.0.0.1', 0)) as listener:

        def dial() -> None:
            with Client(listener.address, authkey=authkey) as conn:
                replies.append(conn.recv())

        thread = threading.Thread(target=dial, daemon=True)
        thread.start()
        with listener.accept() as conn:
            assert os.get_blocking(conn.fileno())
            deliver_challenge(conn, authkey)
            answer_challenge(conn, authkey)
            conn.send('ready')
        thread.join(WAIT)
    assert replies == ['ready']


@mark.unit
def test_plain_client_under_default_timeout(default_timeout: float) -> None:
    """The cleartext client dials a blocking connection, so its handshake and first exchange with a
    stock listener (no `authkey`, as a manager's server) go through."""
    authkey = AUTH.encode()
    with Listener(('127.0.0.1', 0)) as listener:

        def serve() -> None:
            with listener.accept() as conn:
                deliver_challenge(conn, authkey)
                answer_challenge(conn, authkey)
                conn.send(conn.recv())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        with queue_module._plain_client(listener.address, authkey=authkey) as conn:
            assert os.get_blocking(conn.fileno())
            conn.send('ping')
            assert conn.recv() == 'ping'
        thread.join(WAIT)


@mark.unit
def test_sentinel_reaches_remote_consumer(tls: Optional[TLSConfig]) -> None:
    """The shutdown sentinel arrives intact at a remote consumer."""
    with QueueServer(make_config(tls=tls)) as server:
        client = connect('127.0.0.1', server.address[1], tls)
        server.scheduled.put(make_sentinel(), timeout=WAIT)
        assert client.scheduled.get(timeout=WAIT) == make_sentinel()


@mark.unit
def test_shutdown_returns_and_keeps_stdio(tls: Optional[TLSConfig]) -> None:
    """Shutdown returns to the caller and leaves ``sys.stdout``/``sys.stderr`` alone."""
    stdout, stderr = sys.stdout, sys.stderr
    server = QueueServer(make_config(tls=tls))
    server.start()
    connect('127.0.0.1', server.address[1], tls).heartbeat.put(b'beat', timeout=WAIT)
    server.shutdown()
    assert sys.stdout is stdout
    assert sys.stderr is stderr


@mark.unit
def test_shutdown_stops_accepting(tls: Optional[TLSConfig]) -> None:
    """After shutdown, a new connection to the old address is refused."""
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
    with raises(ConnectionRefusedError):
        socket.create_connection(('127.0.0.1', port), timeout=WAIT).close()


@mark.unit
def test_shutdown_releases_port(tls: Optional[TLSConfig]) -> None:
    """A new server binds the same port at once after shutdown."""
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
        connect('127.0.0.1', port, tls).confirmed.put(b'ok', timeout=WAIT)
    with QueueServer(make_config(port=port, tls=tls)) as again:
        assert again.address[1] == port


@mark.unit
@mark.parametrize('host', ['127.0.0.1', 'localhost'])
def test_same_thread_client_after_restart(host: str) -> None:
    """A same-thread client of a second server on the same port works after the first shuts down."""
    with QueueServer(make_config(host=host)) as first:
        port = first.address[1]
        client = connect(host, port)
        first.scheduled.put(b'a', timeout=WAIT)
        assert client.scheduled.get(timeout=WAIT) == b'a'
    with QueueServer(make_config(host=host, port=port)) as second:
        client = connect(host, port)
        second.scheduled.put(b'b', timeout=WAIT)
        assert client.scheduled.get(timeout=WAIT) == b'b'


@mark.unit
def test_threads_drain_after_shutdown(tls: Optional[TLSConfig]) -> None:
    """Accept and handler threads are gone shortly after shutdown."""

    def count() -> int:
        # The decref sender, which the first server of a process starts, lives as long as the process
        return sum(thread.name != 'hypershell-queue-decref' for thread in threading.enumerate())

    baseline = count()
    with QueueServer(make_config(tls=tls)) as server:
        clients = [connect('127.0.0.1', server.address[1], tls) for _ in range(5)]
        for client in clients:
            client.heartbeat.put(b'beat', timeout=WAIT)
        for _ in clients:
            server.heartbeat.get(timeout=WAIT)
    assert wait_until(lambda: count() <= baseline), [t.name for t in threading.enumerate()]


QUEUE_METHODS = {'empty', 'full', 'get', 'get_nowait', 'join', 'put', 'put_nowait', 'qsize', 'task_done'}


@mark.unit
def test_queue_types_and_bounds() -> None:
    """The managed queues are threading queues; two are bounded by the configured size."""
    with QueueServer(make_config(size=7)) as server:
        for name in NAMES:
            assert type(getattr(server, name)) is queue.Queue, name
        assert server.scheduled.maxsize == 7
        assert server.completed.maxsize == 7
        assert server.heartbeat.maxsize == 0
        assert server.confirmed.maxsize == 0


@mark.unit
def test_exposed_methods() -> None:
    """Remote clients see the same nine queue methods on every Python version, and no ``shutdown``."""
    with QueueServer(make_config()) as server:
        client = connect('127.0.0.1', server.address[1])
        for name in NAMES:
            exposed = set(getattr(client, name)._exposed_)
            assert exposed == QUEUE_METHODS, name
            assert 'shutdown' not in exposed


@mark.unit
def test_remote_get_empty_raises(tls: Optional[TLSConfig]) -> None:
    """A remote ``get`` on an empty queue raises ``queue.Empty`` within its timeout."""
    with QueueServer(make_config(tls=tls)) as server:
        client = connect('127.0.0.1', server.address[1], tls)
        for name in NAMES:
            start = time.monotonic()
            with raises(queue.Empty):
                getattr(client, name).get(timeout=TIMEOUT)
            assert time.monotonic() - start < TIMEOUT + 1.0


@mark.unit
def test_remote_put_full_raises(tls: Optional[TLSConfig]) -> None:
    """A remote ``put`` on a full bounded queue raises ``queue.Full`` within its timeout."""
    with QueueServer(make_config(tls=tls, size=2)) as server:
        client = connect('127.0.0.1', server.address[1], tls)
        for name in ('scheduled', 'completed'):
            remote = getattr(client, name)
            remote.put(b'1', timeout=TIMEOUT)
            remote.put(b'2', timeout=TIMEOUT)
            start = time.monotonic()
            with raises(queue.Full):
                remote.put(b'3', timeout=TIMEOUT)
            assert time.monotonic() - start < TIMEOUT + 1.0


@mark.unit
def test_join_after_task_done() -> None:
    """``join`` returns, locally and remotely, once every item has been marked done."""
    with QueueServer(make_config()) as server:
        client = connect('127.0.0.1', server.address[1])

        server.scheduled.put(b'a', timeout=WAIT)
        server.scheduled.put(b'b', timeout=WAIT)
        local = threading.Thread(target=server.scheduled.join, daemon=True)
        local.start()
        for _ in range(2):
            client.scheduled.get(timeout=WAIT)
            assert local.is_alive()
            client.scheduled.task_done()
        local.join(WAIT)
        assert not local.is_alive()

        client.completed.put(b'c', timeout=WAIT)

        def remote_join() -> None:
            connect('127.0.0.1', server.address[1]).completed.join()

        remote = threading.Thread(target=remote_join, daemon=True)
        remote.start()
        assert server.completed.get(timeout=WAIT) == b'c'
        time.sleep(TIMEOUT)
        assert remote.is_alive()
        server.completed.task_done()
        remote.join(WAIT)
        assert not remote.is_alive()
