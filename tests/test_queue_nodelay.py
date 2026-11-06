# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test that both ends of every queue connection turn off Nagle's algorithm (``TCP_NODELAY``).

The handshake ends with two writes before a read on the client (and a TLS 1.3 server writes its
session tickets before its challenge), so with Nagle's algorithm on, each new connection waits out
the peer's delayed acknowledgement. The option must not change the wire, and it must not leave a
connection non-blocking under a process default socket timeout.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import os
import socket
import threading
from multiprocessing.connection import Listener, Client, Connection, answer_challenge, deliver_challenge
from multiprocessing.managers import listener_client

# External libs
from pytest import fixture, mark

# Internal libs
from hypershell.core import queue as queue_module
from hypershell.core.queue import (QueueServer, QueueClient, QueueConfig,
                                   TLS_SERIALIZER, PLAIN_SERIALIZER)
from hypershell.core.tls import TLSConfig, ensure_default_materials, install_process_context, clear_process_context


AUTH = 'queue-test-key-0123456789abcdef'
NAMES = ('scheduled', 'completed', 'heartbeat', 'confirmed')
WAIT = 10.0


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS disabled and once with TLS enabled."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
        clear_process_context()


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


def make_config(port: int = 0, tls: Optional[TLSConfig] = None) -> QueueConfig:
    """Queue configuration for tests (port 0 binds an ephemeral port)."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


def no_delay(conn) -> bool:
    """Whether Nagle's algorithm is off (``TCP_NODELAY`` set) on the socket behind `conn`."""
    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0


def roundtrip(tls: Optional[TLSConfig]) -> None:
    """Pass items across every queue between a client and a queue server, both ways."""
    with QueueServer(make_config(tls=tls)) as server:
        with QueueClient(make_config(server.address[1], tls)) as client:
            for name in NAMES:
                queue = getattr(client, name)
                queue.put(b'item', timeout=WAIT)
                assert queue.get(timeout=WAIT) == b'item'
                queue.task_done()


@mark.unit
def test_managers_select_the_nodelay_pairs(tls: Optional[TLSConfig]) -> None:
    """Queue managers use the registered pair for their transport, with or without TLS."""
    expected = TLS_SERIALIZER if tls is not None else PLAIN_SERIALIZER
    assert QueueServer(make_config(tls=tls))._serializer == expected
    assert QueueClient(make_config(tls=tls))._serializer == expected
    assert listener_client[TLS_SERIALIZER] == (queue_module.SecureListener, queue_module.secure_client)
    assert listener_client[PLAIN_SERIALIZER] == (queue_module._PlainListener, queue_module._plain_client)


@mark.unit
def test_roundtrip(tls: Optional[TLSConfig]) -> None:
    """Items cross every queue between a client and a queue server."""
    roundtrip(tls)


@mark.unit
def test_second_server_in_one_process(tls: Optional[TLSConfig]) -> None:
    """A queue server started after another in the same process serves its own queues.

    Starting a server must leave the server class's queue getters in place; replaced by
    proxy-making methods, they reach the next server's process (under fork) in place of its queues.
    """
    getters = {name: QueueServer.__dict__[f'_get_{name}'] for name in NAMES}
    roundtrip(tls)
    assert {name: QueueServer.__dict__[f'_get_{name}'] for name in NAMES} == getters
    roundtrip(tls)


@mark.unit
def test_roundtrip_under_default_timeout(tls: Optional[TLSConfig], default_timeout: float) -> None:
    """A process default socket timeout leaves the queue connections working.

    A new socket object takes the default timeout and, when there is one, makes its descriptor
    non-blocking, while the stock connection reads with a blocking read.
    """
    roundtrip(tls)


@mark.unit
def test_client_connection_disables_nagle(tls: Optional[TLSConfig]) -> None:
    """The client end of a live queue connection sets ``TCP_NODELAY``."""
    with QueueServer(make_config(tls=tls)) as server:
        with QueueClient(make_config(server.address[1], tls)) as client:
            client.scheduled.put(b'x', timeout=WAIT)
            assert client.scheduled.get(timeout=WAIT) == b'x'
            assert no_delay(client.scheduled._tls.connection)


@mark.unit
def test_both_ends_disable_nagle(tls: Optional[TLSConfig]) -> None:
    """The listener and client pair a queue manager uses set ``TCP_NODELAY`` on both ends, before
    the authentication handshake, which then goes through."""
    authkey = AUTH.encode()
    listener_type, client_factory = listener_client[TLS_SERIALIZER if tls is not None else PLAIN_SERIALIZER]
    if tls is not None:
        install_process_context(tls)
    dialed = []
    with listener_type(('127.0.0.1', 0)) as listener:

        def dial() -> None:
            conn = client_factory(listener.address, authkey=authkey)
            dialed.append(conn)
            assert conn.recv() == 'ready'

        thread = threading.Thread(target=dial, daemon=True)
        thread.start()
        with listener.accept() as conn:
            assert no_delay(conn)
            deliver_challenge(conn, authkey)
            answer_challenge(conn, authkey)
            conn.send('ready')
            thread.join(WAIT)
        assert not thread.is_alive()
        conn, = dialed
        with conn:
            assert no_delay(conn)


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
