# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tests for the managed-queue primitive in :mod:`hypershell.core.queue`."""


# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import sys
import time
import queue
import socket
import threading
import multiprocessing as mp
from multiprocessing import util
from multiprocessing.connection import Listener
from multiprocessing.managers import Server, BaseManager, State

# External libs
from pytest import fixture, mark, raises, skip

# Internal libs
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


@mark.unit
def test_roundtrip(tls: Optional[TLSConfig]) -> None:
    """Items cross every queue in both directions between a server thread and a remote client."""
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
    baseline = threading.active_count()
    with QueueServer(make_config(tls=tls)) as server:
        clients = [connect('127.0.0.1', server.address[1], tls) for _ in range(5)]
        for client in clients:
            client.heartbeat.put(b'beat', timeout=WAIT)
        for _ in clients:
            server.heartbeat.get(timeout=WAIT)
    assert wait_until(lambda: threading.active_count() <= baseline), \
        [t.name for t in threading.enumerate()]


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
