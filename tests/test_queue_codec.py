# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The queue's RPC envelope on the wire: a closed vocabulary of plain values, never unpickled.

Every message between a queue server and its clients is decoded into a small set of plain values
(None, bool, int, float, str, bytes, tuple, list, str-keyed dict, the manager's token, and three
exceptions), and anything else is refused. These tests drive real servers and clients, with TLS and
without, while every way of unpickling in this process is watched: a full session must unpickle
nothing; a frame outside the vocabulary closes that connection only, with one WARNING on a server
and a version error on a client; and a peer that speaks the pickle framing of an earlier release
(its frames begin with byte 0x80) is refused on both sides, by name, without anything of its being
decoded.

The frames sent to the server here hold only plain, benign content: each is refused for its shape.
Tests that need the older-release behaviour have `old_peer` in their names.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Iterator, List, Optional, Tuple

# Standard libs
import os
import sys
import json
import time
import queue
import pickle
import select
import signal
import socket
import logging
import threading
import subprocess
from multiprocessing import reduction
from multiprocessing.connection import Listener, Client, _ConnectionBase, answer_challenge, deliver_challenge
from multiprocessing.managers import BaseManager, Server, RemoteError, AutoProxy, Token, listener_client

# External libs
from pytest import fixture, mark, raises, MonkeyPatch

# Internal libs
from hypershell.core import queue as queue_module
from hypershell.core.queue import (QueueServer, QueueClient, QueueConfig, QueueVersionError, SecureManager,
                                   SecureListener, SecureConnection, secure_client, make_sentinel,
                                   QUEUE_EXPOSED, DISPATCH_EXPOSED, PROTOCOL, TLS_SERIALIZER, PLAIN_SERIALIZER)
from hypershell.core.codec import encode, decode
from hypershell.core.reconnect import UpstreamLink, LinkState
from hypershell.core.topology import request_text, handback_text
from hypershell.core.tls import TLSConfig, ensure_default_materials, clear_process_context, install_process_context


AUTH = 'queue-codec-test-key-0123456789ab'
NAMES = ('scheduled', 'completed', 'heartbeat', 'confirmed')
TIMEOUT = 0.2  # Seconds a get or put waits before raising queue.Empty or queue.Full
WAIT = 5.0  # Seconds a test waits on the server or a peer
LOOPBACK = '127.0.0.1'

# The text every refusal of an earlier release's framing names
OLDER_FRAMING = 'before 2.9.0a10'


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> Tuple[str, str]:
    """A self-signed certificate and key for the loopback, made once for this module."""
    return ensure_default_materials(str(tmp_path_factory.mktemp('tls')), hostnames=['localhost', LOOPBACK])


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS off and once with TLS on; the process-wide TLS context is cleared after."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
        clear_process_context()


@fixture
def unpickled(monkeypatch: MonkeyPatch) -> List[str]:
    """The name of the thread of every unpickling in this process while the test runs.

    `pickle.loads`, `pickle.Unpickler` and the `ForkingPickler.loads` that a stock connection's `recv`
    calls are each wrapped to record the calling thread, then do what they did. A queue server's
    threads are named ``hypershell-queue-...``; a test's own client runs on the main thread.
    """
    seen: List[str] = []
    real_loads = pickle.loads

    def loads(*args, **kwargs):
        seen.append(threading.current_thread().name)
        return real_loads(*args, **kwargs)

    class Unpickler(pickle.Unpickler):
        def load(self):
            seen.append(threading.current_thread().name)
            return super().load()

    monkeypatch.setattr(pickle, 'loads', loads)
    monkeypatch.setattr(pickle, 'Unpickler', Unpickler)
    monkeypatch.setattr(reduction.ForkingPickler, 'loads', staticmethod(loads))
    return seen


def on_server(unpickled: List[str]) -> List[str]:
    """The unpickling done by a queue server's own threads."""
    return [name for name in unpickled if name.startswith('hypershell-queue')]


@fixture
def records() -> Iterator[List[logging.LogRecord]]:
    """Every record the queue module logs, at any level."""
    captured: List[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(queue_module.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


def refusals(records: List[logging.LogRecord], peer: Optional[Tuple[str, int]] = None) -> List[str]:
    """The WARNING messages that refuse a queue connection (from `peer`, when given)."""
    start = 'Refused a queue connection from ' + (f'{peer[0]}:{peer[1]}' if peer else '')
    return [r.getMessage() for r in records if r.levelno == logging.WARNING and r.getMessage().startswith(start)]


def make_config(port: int = 0, tls: Optional[TLSConfig] = None, size: int = 2) -> QueueConfig:
    """Queue configuration for tests on the loopback (port 0 binds an ephemeral port)."""
    return QueueConfig(host=LOOPBACK, port=port, auth=AUTH, size=size, tls=tls)


def connect(port: int, tls: Optional[TLSConfig] = None) -> QueueClient:
    """A client connected to the server at `port`."""
    client = QueueClient(make_config(port, tls))
    client.connect()
    return client


def wait_for(predicate: Callable[[], object], timeout: float = WAIT) -> object:
    """Poll `predicate` until it is true or `timeout` expires; return its last value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    return predicate()


def raw_connection(port: int, tls: Optional[TLSConfig]):
    """An authenticated connection to the queue server at `port`, as a queue client's own connections are made."""
    address = (LOOPBACK, port)
    if tls is None:
        return queue_module._plain_client(address, authkey=AUTH.encode())
    install_process_context(tls)
    return secure_client(address, authkey=AUTH.encode())


def local_address(conn) -> Tuple[str, int]:
    """The address the peer sees `conn` connect from."""
    with socket.fromfd(conn.fileno(), socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.getsockname()


def ask(dispatcher) -> dict:
    """One default-zone work request through `dispatcher`, decoded."""
    return json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': 'codec', 'host': 'h',
                                                     'zone': None, 'size': 5, 'wait': 0})))


# --- A full session -------------------------------------------------------------------------------

PAYLOADS: Tuple[bytes, ...] = (bytes(range(256)), b'', make_sentinel(), b'{"id": "t1", "args": "echo \\u2603"}')
"""Payloads that must arrive byte for byte: every byte value, nothing, the shutdown sentinel, a task."""

VALUES: Tuple[object, ...] = (None, True, False, 0, -1, 2 ** 70, 1.5, 'text é ☃ \U0001f600', (1, 'a'),
                              [1, [2, (3,)]], {'key': b'value', 'nested': {'k': [None]}}, ('#RETURN', 'not a reply'))
"""Plain values a queue carries, each of which must arrive equal and of the same type."""


def same(a: object, b: object) -> bool:
    """Whether `a` and `b` are equal and of the same type all the way down."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def run_session(server: QueueServer, client: QueueClient, port: int, tls: Optional[TLSConfig]) -> None:
    """Every message the manager protocol exchanges with a queue server, in one session."""
    in_process = server.shutdown._args[0]
    client.connect()  # dummy, then each queue's create (the first with the protocol) and incref
    for payload in PAYLOADS:
        client.scheduled.put(payload, timeout=WAIT)
        got = server.scheduled.get(timeout=WAIT)
        server.scheduled.task_done()
        assert type(got) is bytes and got == payload
        server.completed.put(payload, timeout=WAIT)
        got = client.completed.get(timeout=WAIT)
        client.completed.task_done()
        assert type(got) is bytes and got == payload
    for value in VALUES:
        client.confirmed.put(value, timeout=WAIT)
        got = server.confirmed.get(timeout=WAIT)
        assert same(got, value), (got, value)
        server.heartbeat.put(value, timeout=WAIT)
        got = client.heartbeat.get(timeout=WAIT)
        assert same(got, value), (got, value)

    # Every exposed queue method, with a timed get and put raising at the caller as they do locally
    remote = client.scheduled
    assert remote.empty() is True and remote.full() is False and remote.qsize() == 0
    with raises(queue.Empty):
        remote.get(timeout=TIMEOUT)
    with raises(queue.Empty):
        remote.get_nowait()
    remote.put(b'1', timeout=TIMEOUT)
    remote.put_nowait(b'2')
    assert remote.full() is True and remote.qsize() == 2
    with raises(queue.Full):
        remote.put(b'3', timeout=TIMEOUT)
    with raises(queue.Full):
        remote.put_nowait(b'3')
    assert remote.get(timeout=WAIT) == b'1' and remote.get_nowait() == b'2'
    remote.task_done()
    remote.task_done()
    remote.join()
    with raises(RemoteError, match='ValueError'):
        remote.task_done()  # Any other error a method raises reaches the caller as RemoteError

    # Every exposed dispatcher method
    assert type(client.dispatcher)._exposed_ == DISPATCH_EXPOSED
    assert ask(client.dispatcher)['tasks'] == []
    assert isinstance(client.dispatcher.takes_submissions(), bool)
    assert isinstance(client.dispatcher.topology(request_text('codec-site', ['codec'])), str)
    assert isinstance(client.dispatcher.handback(handback_text('codec', [])), str)

    # incref and decref: a second proxy of a queue, released on the server side (handed off to the
    # decref sender) and on the client's own thread (the stock decref)
    extra = client._get_completed()
    ident = extra._id
    assert in_process.id_to_refcount[ident] == 2
    handed = queue_module._ServerThread(target=extra._close, name='hypershell-queue-codec-test')
    handed.start()
    handed.join(WAIT)
    assert wait_for(lambda: in_process.id_to_refcount[ident] == 1)
    other = client._get_heartbeat()
    assert in_process.id_to_refcount[other._id] == 2
    finalizer = threading.Thread(target=other._close)
    finalizer.start()
    finalizer.join(WAIT)
    assert wait_for(lambda: in_process.id_to_refcount[other._id] == 1)

    # The manager's own requests on a connection of their own
    counter = QueueClient(make_config(port, tls))
    assert counter._number_of_objects() == 5
    assert isinstance(counter._debug_info(), str)


@mark.integration
def test_session_unpickles_nothing(tls: Optional[TLSConfig], unpickled: List[str],
                                   records: List[logging.LogRecord]) -> None:
    """A whole session, every message of it on both ends, decodes without unpickling anything.

    Payloads arrive byte for byte, plain values arrive equal and of the same type, a timed get and
    put raise `queue.Empty` and `queue.Full` at the caller, and no connection is refused.
    """
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
        client = QueueClient(make_config(port, tls))
        try:
            run_session(server, client, port, tls)
        finally:
            assert not unpickled, f'unpickled peer bytes on {sorted(set(unpickled))}'
    assert refusals(records) == []


# --- Frames outside the vocabulary ----------------------------------------------------------------

def nested(depth: int) -> bytes:
    """A request whose arguments nest arrays to `depth` containers, counting the request itself."""
    return b'[null, "dummy", ' + b'[' * (depth - 1) + b'1' + b']' * (depth - 1) + b', {"d": {}}]'


HOSTILE = {
    'unknown-tag': b'[null, "dummy", [{"z": 1}], {"d": {}}]',
    'two-key-tag': b'[null, "dummy", [{"l": [], "d": {}}], {"d": {}}]',
    'non-str-key': b'[null, "dummy", [], {"d": [[1, "one"]]}]',
    'bytes-above-ff': b'[null, "dummy", [{"b": "\\u0100"}], {"d": {}}]',
    'depth-9': nested(9),
    '65-elements': b'[null, "dummy", [' + b', '.join([b'0'] * 65) + b'], {"d": {}}]',
    'nan': b'[null, "dummy", [NaN], {"d": {}}]',
    'bad-utf8': b'[null, "dummy", ["\xff"], {"d": {}}]',
    'bad-json': b'[null, "dummy", ',
    'trailing-data': b'[null, "dummy", [], {"d": {}}] []',
    'empty': b'',
}
"""Frames a queue end refuses, each for its shape alone; none holds anything but plain values."""


@mark.integration
@mark.parametrize('stage', ['first', 'serving'])
@mark.parametrize('kind', list(HOSTILE))
def test_server_refuses_frame_outside_vocabulary(kind: str, stage: str, tls: Optional[TLSConfig],
                                                 unpickled: List[str], records: List[logging.LogRecord]) -> None:
    """A frame outside the vocabulary closes its connection, answered with nothing, with one WARNING naming
    the peer, as a connection's first message or once it is serving; another client is served throughout."""
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
        bystander = connect(port, tls)
        bystander.completed.put(b'before', timeout=WAIT)
        conn = raw_connection(port, tls)
        try:
            peer = local_address(conn)
            if stage == 'serving':
                # The server names its handler thread after the one given here
                conn.send((None, 'accept_connection', ('hypershell-queue-codec-test',), {}))
                assert conn.recv() == ('#RETURN', None)
            conn.send_bytes(HOSTILE[kind])
            assert conn.poll(WAIT), 'the server neither answered nor closed the connection'
            with raises(EOFError):
                conn.recv_bytes()
        finally:
            conn.close()
        refused = wait_for(lambda: refusals(records, peer))
        time.sleep(0.2)
        assert refused and len(refusals(records, peer)) == 1, refusals(records)
        bystander.completed.put(b'after', timeout=WAIT)
        assert server.completed.get(timeout=WAIT) == b'before' and server.completed.get(timeout=WAIT) == b'after'
    assert not on_server(unpickled)


# --- Requests outside the manager's public surface ------------------------------------------------

NON_PUBLIC = {
    'server-method': (None, 'serve_client', (), {}),
    'stop': (None, 'stop', (), {}),
    'unknown-name': (None, 'not a request', (), {}),
    'name-not-text': (None, 1, (), {}),
    'list-shaped': [None, 'dummy', (), {}],
}
"""First requests a queue server refuses: each names no public request of the manager, or is not shaped as one."""


@mark.integration
@mark.parametrize('case', list(NON_PUBLIC))
def test_server_refuses_non_public_requests(case: str, tls: Optional[TLSConfig], unpickled: List[str],
                                            records: List[logging.LogRecord]) -> None:
    """A connection's first request must name one of the manager's public requests: any other is refused as
    outside the vocabulary, closing that connection unanswered with one WARNING naming the peer; another
    client is served throughout, and a later connection is served as before."""
    with QueueServer(make_config(tls=tls, size=4)) as server:
        port = server.address[1]
        bystander = connect(port, tls)
        bystander.completed.put(b'before', timeout=WAIT)
        conn = raw_connection(port, tls)
        try:
            peer = local_address(conn)
            conn.send(NON_PUBLIC[case])
            assert conn.poll(WAIT), 'the server neither answered nor closed the connection'
            with raises(EOFError):
                conn.recv_bytes()
        finally:
            conn.close()
        refused = wait_for(lambda: refusals(records, peer))
        time.sleep(0.2)
        assert refused and len(refusals(records, peer)) == 1, refusals(records)
        assert 'public' in refused[0]
        bystander.completed.put(b'after', timeout=WAIT)
        connect(port, tls).completed.put(b'later', timeout=WAIT)
        assert [server.completed.get(timeout=WAIT) for _ in range(3)] == [b'before', b'after', b'later']
    assert not on_server(unpickled)


@mark.unit
def test_manager_server_refuses_non_public_requests() -> None:
    """The server a queue manager runs in a process of its own refuses a first request outside the manager's
    public requests too, closing the connection unanswered."""
    server = SecureManager._Server({}, (LOOPBACK, 0), AUTH.encode(), PLAIN_SERIALIZER)
    try:
        handler = threading.Thread(target=lambda: server.handle_request(server.listener.accept()), daemon=True)
        handler.start()
        with queue_module._plain_client(server.address, authkey=AUTH.encode()) as conn:
            conn.send(NON_PUBLIC['server-method'])
            assert conn.poll(WAIT), 'the server neither answered nor closed the connection'
            with raises(EOFError):
                conn.recv_bytes()
        handler.join(WAIT)
        assert not handler.is_alive()
    finally:
        server.listener.close()


@mark.unit
def test_get_server_builds_the_manager_server(tls: Optional[TLSConfig]) -> None:
    """The server a queue manager builds for its caller to run is the queue manager's own server, listening
    with the manager's own transport at the manager's address with the manager's key."""
    manager = SecureManager(address=(LOOPBACK, 0), authkey=AUTH.encode(), tls=tls)
    server = manager.get_server()
    try:
        assert isinstance(server, queue_module._ManagerServer)
        assert type(server.listener) is listener_client[manager._serializer][0]
        assert server.address[0] == LOOPBACK and server.address[1] > 0
        assert server.registry is manager._registry and server.authkey == AUTH.encode()
    finally:
        server.listener.close()


GETSERVER_SERVER = r'''
import sys, json, pickle, threading
from multiprocessing import reduction
from hypershell.core.queue import SecureManager
from hypershell.core.tls import TLSConfig, install_process_context

seen = []
real_loads = pickle.loads

def loads(*args, **kwargs):
    seen.append(threading.current_thread().name)
    return real_loads(*args, **kwargs)

pickle.loads = loads
reduction.ForkingPickler.loads = staticmethod(loads)

auth, cert, key = sys.argv[1:4]
tls = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert) if cert else None
if tls is not None:
    install_process_context(tls)
server = SecureManager(address=('127.0.0.1', 0), authkey=auth.encode(), tls=tls).get_server()
listening = [type(server.listener).__name__, list(server.address)]
threading.Thread(target=server.serve_forever, daemon=True, name='served').start()
print(json.dumps({'port': server.address[1], 'optimize': sys.flags.optimize}), flush=True)
sys.stdin.readline()
print(json.dumps({'listening': listening, 'still_listening': [type(server.listener).__name__,
                  list(server.address)], 'unpickled': seen}), flush=True)
'''
"""A server built by a queue manager's ``get_server`` and run by its caller in a process of its own, under Python's
optimisation flag, by the test below. It prints its port, waits for a line on its input, then prints its listener
before and after, and what it unpickled."""


def unused_port() -> int:
    """A loopback port that nothing listens on as this returns."""
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


@mark.integration
def test_get_server_refuses_non_public_requests_under_optimisation(tls: Optional[TLSConfig],
                                                                   tls_materials: Tuple[str, str], tmp_path) -> None:
    """A server built by a queue manager's ``get_server`` and run under Python's optimisation flag refuses every
    first request outside the manager's public requests, closing the connection unanswered; its listener and
    address are unchanged, so no later connection is decoded any other way, and it keeps serving, having
    unpickled nothing."""
    cert, key = tls_materials if tls is not None else ('', '')
    env = {**os.environ, 'HYPERSHELL_SITE': str(tmp_path)}
    other, other_key = unused_port(), b'another-key-0123456789abcdef'
    requests = {**NON_PUBLIC, 'listen-elsewhere': (None, '__init__', ((LOOPBACK, other), other_key, 'pickle'), {})}
    proc = subprocess.Popen([sys.executable, '-O', '-c', GETSERVER_SERVER, AUTH, cert, key], env=env, cwd=tmp_path,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        ready = read_line(proc)
        assert ready['optimize'] >= 1
        port = ready['port']
        for case, request in requests.items():
            conn = raw_connection(port, tls)
            try:
                conn.send(request)
                assert conn.poll(WAIT), f'{case}: the server neither answered nor closed the connection'
                with raises(EOFError):
                    conn.recv_bytes()
            finally:
                conn.close()
        conn = raw_connection(port, tls)
        try:
            conn.send((None, 'dummy', (), {}))
            assert conn.recv() == ('#RETURN', None)
        finally:
            conn.close()
        with raises(ConnectionRefusedError):
            Client((LOOPBACK, other), authkey=other_key).close()
        proc.stdin.write('\n')
        proc.stdin.flush()
        report = read_line(proc)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(WAIT)
    assert report['still_listening'] == report['listening']
    assert report['unpickled'] == []


OPTIMISED_SERVER = r'''
import sys, json, queue, pickle, logging, threading
from multiprocessing import reduction
from hypershell.core.queue import QueueServer, QueueConfig
from hypershell.core.tls import TLSConfig

seen, warnings = [], []
real_loads = pickle.loads

def loads(*args, **kwargs):
    seen.append(threading.current_thread().name)
    return real_loads(*args, **kwargs)

pickle.loads = loads
reduction.ForkingPickler.loads = staticmethod(loads)

class Collect(logging.Handler):
    def emit(self, record):
        warnings.append(record.getMessage())

logger = logging.getLogger('hypershell.core.queue')
logger.addHandler(Collect(level=logging.WARNING))
logger.setLevel(logging.WARNING)

auth, cert, key = sys.argv[1:4]
tls = TLSConfig(enabled=True, cert=cert, key=key, cafile=cert) if cert else None
with QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=auth, size=4, tls=tls)) as server:
    in_process = server.shutdown._args[0]
    listening = [type(in_process.listener).__name__, list(in_process.address)]
    print(json.dumps({'port': server.address[1], 'optimize': sys.flags.optimize}), flush=True)
    sys.stdin.readline()
    received = []
    while True:
        try:
            received.append(server.completed.get(timeout=1).decode())
        except queue.Empty:
            break
    print(json.dumps({'listening': listening, 'still_listening': [type(in_process.listener).__name__,
                      list(in_process.address)], 'halted': in_process._halted, 'received': received,
                      'warnings': warnings, 'unpickled': [n for n in seen if n.startswith('hypershell-queue')]}),
          flush=True)
'''
"""A queue server in a process of its own, run under Python's optimisation flag by the test below. It prints its
port, waits for a line on its input, then prints what it received, logged and unpickled."""


def read_line(proc: subprocess.Popen, timeout: float = 30) -> dict:
    """The next line `proc` prints, as JSON, waiting at most `timeout` seconds for it."""
    assert select.select([proc.stdout], [], [], timeout)[0], 'the server process printed nothing'
    line = proc.stdout.readline()
    assert line, f'the server process ended: {proc.stderr.read()[-2000:]}'
    return json.loads(line)


@mark.integration
def test_server_refuses_non_public_requests_under_optimisation(tls: Optional[TLSConfig], tls_materials: Tuple[str, str],
                                                               tmp_path) -> None:
    """A queue server run under Python's optimisation flag refuses every first request outside the manager's
    public requests, each with one WARNING; its listener and address are unchanged, so every later connection
    is decoded as before, and it keeps serving its clients, having unpickled nothing."""
    cert, key = tls_materials if tls is not None else ('', '')
    env = {**os.environ, 'HYPERSHELL_SITE': str(tmp_path)}
    proc = subprocess.Popen([sys.executable, '-O', '-c', OPTIMISED_SERVER, AUTH, cert, key], env=env, cwd=tmp_path,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        ready = read_line(proc)
        assert ready['optimize'] >= 1
        port = ready['port']
        bystander = connect(port, tls)
        bystander.completed.put(b'before', timeout=WAIT)
        for case, request in NON_PUBLIC.items():
            conn = raw_connection(port, tls)
            try:
                conn.send(request)
                assert conn.poll(WAIT), f'{case}: the server neither answered nor closed the connection'
                with raises(EOFError):
                    conn.recv_bytes()
            finally:
                conn.close()
        connect(port, tls).completed.put(b'later', timeout=WAIT)
        bystander.completed.put(b'after', timeout=WAIT)
        proc.stdin.write('\n')
        proc.stdin.flush()
        report = read_line(proc)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(WAIT)
    assert report['still_listening'] == report['listening'] and not report['halted']
    assert report['received'] == ['before', 'later', 'after']
    refused = [line for line in report['warnings'] if line.startswith('Refused a queue connection from')]
    assert len(refused) == len(NON_PUBLIC) and all('public' in line for line in refused), report['warnings']
    assert report['unpickled'] == []


class StandIn:
    """A stand-in queue server that answers the first request of each connection with one fixed frame.

    It runs the manager's challenge with the test's key, reads the request without decoding it, sends
    `reply`, and holds the connection until the peer closes it. It counts the connections it accepts.
    """

    def __init__(self: StandIn, reply: bytes, tls: Optional[TLSConfig]) -> None:
        self.reply = reply
        if tls is None:
            self.listener = Listener((LOOPBACK, 0), family='AF_INET')
        else:
            self.listener = SecureListener((LOOPBACK, 0), backlog=16)
            self.listener.serve_with(tls)
        self.port = self.listener.address[1]
        self.accepted = 0
        self.requests: List[bytes] = []
        self.halted = threading.Event()
        self.thread = threading.Thread(target=self.serve, name='codec-stand-in', daemon=True)

    def serve(self: StandIn) -> None:
        sock = queue_module._listening_socket(self.listener)
        while not self.halted.is_set():
            if not select.select([sock], [], [], 0.1)[0]:
                continue
            try:
                conn = self.listener.accept()
            except OSError:
                continue
            self.accepted += 1
            threading.Thread(target=self.answer, args=(conn,), daemon=True).start()

    def answer(self: StandIn, conn) -> None:
        with conn:
            try:
                deliver_challenge(conn, AUTH.encode())
                answer_challenge(conn, AUTH.encode())
                self.requests.append(conn.recv_bytes())
                conn.send_bytes(self.reply)
                while conn.poll(WAIT):
                    conn.recv_bytes()
            except (OSError, EOFError):
                pass

    def __enter__(self: StandIn) -> StandIn:
        self.thread.start()
        return self

    def __exit__(self: StandIn, *exc) -> None:
        self.halted.set()
        self.thread.join(WAIT)
        self.listener.close()


OUT_OF_VOCABULARY: bytes = b'["#RETURN", {"z": 1}]'
"""A reply in the codec's framing, refused for its unknown tag."""


@mark.integration
def test_client_refuses_reply_outside_vocabulary(tls: Optional[TLSConfig], unpickled: List[str]) -> None:
    """A client whose server answers outside the vocabulary fails at once with a version error naming the
    server, having unpickled nothing."""
    with StandIn(OUT_OF_VOCABULARY, tls) as stand_in:
        client = QueueClient(make_config(stand_in.port, tls))
        start = time.monotonic()
        with raises(Exception) as caught:
            client.connect()
        elapsed = time.monotonic() - start
    assert not unpickled, f'unpickled peer bytes on {sorted(set(unpickled))}'
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert f'{LOOPBACK}:{stand_in.port}' in str(caught.value)
    assert elapsed < WAIT and stand_in.accepted == 1


def link_to(port: int, tls: Optional[TLSConfig]) -> UpstreamLink:
    """An upstream link to the server at `port`, quick to redial if it were to."""
    config = make_config(port, tls)
    return UpstreamLink(lambda: QueueClient(config), name=f'{LOOPBACK}:{port}',
                        deadline=WAIT, backoff_base=0.01, backoff_cap=0.02)


@mark.integration
def test_link_fails_at_once_on_reply_outside_vocabulary(tls: Optional[TLSConfig], unpickled: List[str]) -> None:
    """An upstream link whose server answers outside the vocabulary fails for good at its first connection,
    with a version error, and never dials again."""
    with StandIn(OUT_OF_VOCABULARY, tls) as stand_in:
        link = link_to(stand_in.port, tls)
        try:
            start = time.monotonic()
            link.start()
            with raises(Exception) as caught:
                link.wait_up(WAIT)
            elapsed = time.monotonic() - start
            time.sleep(0.5)
            state, accepted = link.state, stand_in.accepted
        finally:
            link.stop(WAIT)
    assert not unpickled, f'unpickled peer bytes on {sorted(set(unpickled))}'
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert state is LinkState.FAILED and accepted == 1 and elapsed < WAIT


ERROR_WITHOUT_TEXT: bytes = b'["#ERROR", {"e": ["RemoteError", []]}]'
"""An error reply in the codec's framing whose remote error carries no text, which the vocabulary refuses."""


@mark.integration
def test_client_refuses_remote_error_without_text(tls: Optional[TLSConfig]) -> None:
    """A client whose server answers its first request with a remote error that carries no text fails at once
    with a version error naming the server."""
    with StandIn(ERROR_WITHOUT_TEXT, tls) as stand_in:
        client = QueueClient(make_config(stand_in.port, tls))
        with raises(Exception) as caught:
            client.connect()
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert f'{LOOPBACK}:{stand_in.port}' in str(caught.value) and stand_in.accepted == 1


@mark.integration
def test_link_fails_cleanly_on_remote_error_without_text(tls: Optional[TLSConfig]) -> None:
    """An upstream link whose server answers with a remote error that carries no text fails for good with a
    version error, its thread ending on its own, and never dials again."""
    with StandIn(ERROR_WITHOUT_TEXT, tls) as stand_in:
        link = link_to(stand_in.port, tls)
        try:
            link.start()
            with raises(Exception) as caught:
                link.wait_up(WAIT)
            link.thread.join(WAIT)
            ended = not link.thread.is_alive()
            time.sleep(0.5)
            state, accepted = link.state, stand_in.accepted
        finally:
            link.stop(WAIT)
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert ended and state is LinkState.FAILED and accepted == 1


@mark.integration
def test_client_command_exits_with_version_error_on_remote_error_without_text(tmp_path) -> None:
    """`hs client` whose server answers with a remote error that carries no text exits with status 5 and one
    critical line naming the version error, with no traceback."""
    with StandIn(ERROR_WITHOUT_TEXT, None) as stand_in:
        env = {**os.environ, 'HYPERSHELL_SITE': str(tmp_path), 'HYPERSHELL_LOGGING_LEVEL': 'INFO',
               'HYPERSHELL_SERVER_AUTH': AUTH}
        proc = subprocess.Popen(['hs', 'client', '-H', LOOPBACK, '-p', str(stand_in.port), '--no-tls'], env=env,
                                cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            _, stderr = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(WAIT)
    critical = [line for line in stderr.splitlines() if line.startswith('CRITICAL')]
    assert proc.returncode == 5, stderr[-2000:]
    assert len(critical) == 1 and 'QueueVersionError' in critical[0], stderr[-2000:]
    assert 'Traceback' not in stderr


# --- A client's proxies ---------------------------------------------------------------------------

class CodecStandIn(StandIn):
    """A stand-in queue server that speaks the codec: it runs the manager's challenge with the test's key,
    then answers each request it decodes with ``respond(request)``, encoded. It records every request."""

    def __init__(self: CodecStandIn, respond: Callable[[tuple], tuple], tls: Optional[TLSConfig]) -> None:
        super().__init__(b'', tls)
        self.respond = respond

    def answer(self: CodecStandIn, conn) -> None:
        with conn:
            try:
                deliver_challenge(conn, AUTH.encode())
                answer_challenge(conn, AUTH.encode())
                while conn.poll(WAIT):
                    request = decode(conn.recv_bytes())
                    self.requests.append(request)
                    conn.send_bytes(encode(self.respond(request)))
            except (OSError, EOFError):
                pass


def created(request: tuple) -> Optional[str]:
    """The type id a `create` request asks for, else None."""
    _, name, args, _ = request
    return args[0] if name == 'create' else None


def serving(names: Callable[[str], object]) -> Callable[[tuple], tuple]:
    """Replies of a queue server that offers ``names(typeid)`` as the methods of each object it creates, and
    answers every other request with nothing."""
    def respond(request: tuple) -> tuple:
        typeid = created(request)
        return ('#RETURN', (f'{typeid}-id', names(typeid))) if typeid else ('#RETURN', None)
    return respond


def own_names(typeid: str) -> Tuple[str, ...]:
    """The methods this version offers for `typeid`."""
    return DISPATCH_EXPOSED if typeid == '_get_dispatch' else QUEUE_EXPOSED


NOT_AN_IDENTIFIER = 'not a method'
"""A method name that no queue offers and that is not an identifier."""

UNEXPECTED_NAMES = {
    'not-an-identifier': ('_get_scheduled', QUEUE_EXPOSED + (NOT_AN_IDENTIFIER, )),
    'unknown-method': ('_get_scheduled', QUEUE_EXPOSED + ('shutdown', )),
    'not-a-list': ('_get_completed', 'get'),
    'dispatcher': ('_get_dispatch', DISPATCH_EXPOSED + (NOT_AN_IDENTIFIER, )),
}
"""A `create` reply's method names that a client must refuse, by case: the type id and the names offered."""


@mark.integration
@mark.parametrize('case', list(UNEXPECTED_NAMES))
def test_client_refuses_unexpected_method_names(case: str, tls: Optional[TLSConfig]) -> None:
    """A client builds its proxies from its own method lists: a server that offers a method name outside
    them is refused at once with a version error naming the server, and the client builds no proxy for it."""
    typeid, offered = UNEXPECTED_NAMES[case]
    respond = serving(lambda name: offered if name == typeid else own_names(name))
    with CodecStandIn(respond, tls) as stand_in:
        client = QueueClient(make_config(stand_in.port, tls))
        start = time.monotonic()
        with raises(Exception) as caught:
            client.connect()
            _ = client.dispatcher
        elapsed = time.monotonic() - start
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert f'{LOOPBACK}:{stand_in.port}' in str(caught.value)
    # A proxy asks its server to hold the object as it is built: no such request, no proxy
    assert not [r for r in stand_in.requests if r[1] == 'incref' and r[2] == (f'{typeid}-id', )]
    assert elapsed < WAIT


UNASKED_TOKEN = Token('_get_scheduled', (LOOPBACK, 1), 'unasked')
"""The token of an object the client never asked for."""

UNASKED_PROXY = {
    'tuple': ('#PROXY', (QUEUE_EXPOSED, UNASKED_TOKEN)),
    'list': ['#PROXY', [QUEUE_EXPOSED, UNASKED_TOKEN]],
}
"""A proxy in a reply, by shape: the stock server's tuple, and the same reply as a list."""

UNASKED_STAGES = {
    'connect': lambda ident, name: name == 'dummy',
    'queue': lambda ident, name: ident == '_get_scheduled-id',
    'dispatcher': lambda ident, name: ident == '_get_dispatch-id',
}
"""Which request is answered with the proxy, by stage: the manager's first request, a call on a queue, or
a call on the dispatcher."""


@mark.integration
@mark.parametrize('shape', list(UNASKED_PROXY))
@mark.parametrize('stage', list(UNASKED_STAGES))
def test_client_refuses_proxy_it_did_not_ask_for(stage: str, shape: str, tls: Optional[TLSConfig]) -> None:
    """A client never asks its server for a proxy in a reply: one sent anyway, as a tuple or as a list, to the
    manager's first request, a queue call or a dispatcher call, is refused with a version error naming the
    server, and no proxy is built for it."""
    unasked, answers_with_proxy = UNASKED_PROXY[shape], UNASKED_STAGES[stage]
    offers = serving(own_names)

    def respond(request: tuple) -> object:
        ident, name, _, _ = request
        return unasked if answers_with_proxy(ident, name) else offers(request)

    with CodecStandIn(respond, tls) as stand_in:
        client = QueueClient(make_config(stand_in.port, tls))
        with raises(Exception) as caught:
            client.connect()
            if stage == 'dispatcher':
                client.dispatcher.takes_submissions()
            else:
                client.scheduled.qsize()
    assert isinstance(caught.value, QueueVersionError), repr(caught.value)
    assert f'{LOOPBACK}:{stand_in.port}' in str(caught.value)
    assert not [r for r in stand_in.requests if r[1] == 'incref' and r[2] == ('unasked', )]
    assert not [r for r in stand_in.requests if r[1] == 'decref' and r[2] == ('unasked', )]


# --- Nothing selects the pickle framing -----------------------------------------------------------

def plain_pair() -> Tuple[object, object]:
    """The accepted and the dialled end of a cleartext queue connection on the loopback."""
    with queue_module._PlainListener((LOOPBACK, 0), family='AF_INET') as listener:
        dialled = queue_module._plain_client(listener.address)
        return listener.accept(), dialled


@mark.unit
def test_connections_decode_with_the_codec() -> None:
    """Both connection classes, TLS and cleartext, receive through the codec, never the stock unpickling `recv`."""
    accepted, dialled = plain_pair()
    with accepted, dialled:
        classes = {SecureConnection, type(accepted), type(dialled)}
    for cls in classes:
        assert cls.recv is not _ConnectionBase.recv, cls
        assert cls.send is not _ConnectionBase.send, cls
        names = set(cls.recv.__code__.co_names)
        assert not names & {'loads', '_ForkingPickler', 'Unpickler'}, (cls, names)
        assert not names & {'environ', 'getenv', 'config', '_config'}, (cls, names)


@mark.unit
@mark.parametrize('serializer', ['pickle', 'xmlrpclib'])
def test_secure_manager_refuses_other_serializers(serializer: str) -> None:
    """A manager of the queue speaks only its own two serializers; the stock ones are refused."""
    with raises(ValueError, match=serializer):
        SecureManager(address=(LOOPBACK, 0), authkey=AUTH.encode(), serializer=serializer)
    for own in (TLS_SERIALIZER, PLAIN_SERIALIZER):
        assert SecureManager(address=(LOOPBACK, 0), authkey=AUTH.encode(), serializer=own)._serializer == own


# --- Peers of an earlier release ------------------------------------------------------------------

OLD_TLS_SERIALIZER = 'hypershell-test-pickle-tls'
"""A serializer key for the TLS pair of an earlier release, whose connections pickle."""


class PickleSecureConnection(SecureConnection):
    """A TLS connection that pickles, as the TLS connection of every release before 2.9.0a10 does."""

    send = _ConnectionBase.send
    recv = _ConnectionBase.recv


class PickleSecureListener(SecureListener):
    """A TLS listener whose connections pickle (an earlier release's server)."""

    def accept(self: PickleSecureListener) -> SecureConnection:
        conn = super().accept()
        conn.__class__ = PickleSecureConnection
        return conn


def pickle_secure_client(address, family=None, authkey: Optional[bytes] = None) -> SecureConnection:
    """A TLS client connection that pickles (an earlier release's client)."""
    conn = secure_client(address, family=family, authkey=authkey)
    conn.__class__ = PickleSecureConnection
    return conn


listener_client[OLD_TLS_SERIALIZER] = (PickleSecureListener, pickle_secure_client)


class OldClient(BaseManager):
    """A client of an earlier release: the same queues, over the pickle framing."""


for _name in (*NAMES, 'dispatch'):
    OldClient.register(f'_get_{_name}')


def old_client(port: int, tls: Optional[TLSConfig]) -> OldClient:
    """An unconnected client of an earlier release for the server at `port`."""
    if tls is None:
        return OldClient(address=(LOOPBACK, port), authkey=AUTH.encode(), serializer='pickle')
    install_process_context(tls)
    return OldClient(address=(LOOPBACK, port), authkey=AUTH.encode(), serializer=OLD_TLS_SERIALIZER)


@mark.integration
def test_old_peer_client_is_refused_by_name(tls: Optional[TLSConfig], unpickled: List[str],
                                            records: List[logging.LogRecord]) -> None:
    """A client of an earlier release gets one error naming the version mismatch at its first request, the
    server logs one WARNING naming it, and a current client is served meanwhile."""
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
        current = connect(port, tls)
        old = old_client(port, tls)
        start = time.monotonic()
        with raises(RuntimeError, match=OLDER_FRAMING) as caught:
            old.connect()
        assert time.monotonic() - start < WAIT
        assert type(caught.value) is RuntimeError
        current.completed.put(b'served', timeout=WAIT)
        assert server.completed.get(timeout=WAIT) == b'served'
        assert wait_for(lambda: refusals(records))
        time.sleep(0.2)
        refused = refusals(records)
    assert len(refused) == 1 and OLDER_FRAMING in refused[0], refused
    assert refused[0].startswith(f'Refused a queue connection from {LOOPBACK}:')
    assert not on_server(unpickled)


@mark.integration
def test_old_peer_refused_whatever_the_environment(monkeypatch: MonkeyPatch, unpickled: List[str]) -> None:
    """No environment variable brings back the pickle framing: an earlier release's client is still refused."""
    for section in ('SERVER', 'CLIENT', 'SUBMIT', 'RELAY', 'QUEUE'):
        for name in ('SERIALIZER', 'PICKLE', 'WIRE', 'CODEC', 'FRAMING', 'COMPAT', 'LEGACY'):
            monkeypatch.setenv(f'HYPERSHELL_{section}_{name}', 'pickle')
    with QueueServer(make_config()) as server:
        with raises(RuntimeError, match=OLDER_FRAMING):
            old_client(server.address[1], None).connect()
    assert not on_server(unpickled)


class OldServer:
    """A queue server of an earlier release: the stock manager server over the pickle framing, on threads."""

    def __init__(self: OldServer, tls: Optional[TLSConfig]) -> None:
        self.queues = {name: queue.Queue(maxsize=2) for name in NAMES}
        registry = {f'_get_{name}': (self.provider(q), QUEUE_EXPOSED, None, AutoProxy)
                    for name, q in self.queues.items()}
        if tls is None:
            self.server = Server(registry, (LOOPBACK, 0), AUTH.encode(), 'pickle')
        else:
            self.server = Server(registry, (LOOPBACK, 0), AUTH.encode(), OLD_TLS_SERIALIZER)
            self.server.listener.serve_with(tls)
        self.server.stop_event = threading.Event()
        self.port = self.server.address[1]
        self.accepted = 0
        self.halted = threading.Event()
        self.thread = threading.Thread(target=self.serve, name='codec-old-server', daemon=True)

    @staticmethod
    def provider(obj: object) -> Callable[..., object]:
        return lambda *args: obj

    def serve(self: OldServer) -> None:
        sock = queue_module._listening_socket(self.server.listener)
        while not self.halted.is_set():
            if not select.select([sock], [], [], 0.1)[0]:
                continue
            try:
                conn = self.server.listener.accept()
            except OSError:
                continue
            self.accepted += 1
            threading.Thread(target=self.server.handle_request, args=(conn,), daemon=True).start()

    def __enter__(self: OldServer) -> OldServer:
        self.thread.start()
        return self

    def __exit__(self: OldServer, *exc) -> None:
        self.halted.set()
        self.thread.join(WAIT)
        self.server.listener.close()


@mark.integration
def test_old_peer_server_is_refused_by_name(tls: Optional[TLSConfig], unpickled: List[str]) -> None:
    """A current client meeting a server of an earlier release fails at once with a version error naming the
    older framing, having unpickled nothing."""
    with OldServer(tls) as old:
        client = QueueClient(make_config(old.port, tls))
        start = time.monotonic()
        with raises(QueueVersionError, match=OLDER_FRAMING) as caught:
            client.connect()
        elapsed = time.monotonic() - start
    assert f'{LOOPBACK}:{old.port}' in str(caught.value)
    assert elapsed < WAIT
    assert not [name for name in unpickled if not name.startswith('Thread-')], unpickled


@mark.integration
def test_old_peer_server_fails_link_at_once(tls: Optional[TLSConfig], unpickled: List[str]) -> None:
    """An upstream link to a server of an earlier release fails for good at its first connection, with a
    version error, and never dials again."""
    with OldServer(tls) as old:
        link = link_to(old.port, tls)
        try:
            start = time.monotonic()
            link.start()
            with raises(QueueVersionError, match=OLDER_FRAMING):
                link.wait_up(WAIT)
            elapsed = time.monotonic() - start
            time.sleep(0.5)
            state, accepted = link.state, old.accepted
        finally:
            link.stop(WAIT)
    assert state is LinkState.FAILED and accepted == 1 and elapsed < WAIT
    assert not [name for name in unpickled if not name.startswith('Thread-')], unpickled
