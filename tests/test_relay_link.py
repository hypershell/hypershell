# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The upstream link: call deadlines, error classes, reconnection with backoff, and its log cadence.

The link is driven with a fake client factory, an injected clock and random source, and a sleep that
records each backoff wait and advances the clock instead of waiting. Calls run on real worker threads
(the deadline is real time, kept short here). A restart of the upstream server is driven for real: a
link on a queue server that is shut down and started again on the same port.

So is a TLS mismatch at the first connection, as the relay connects: a link with TLS on against a
cleartext queue server, and one with TLS off against an `hs server` process with TLS on, each fail
at once with an error that names the relay's setting. Over a path that holds each chunk over a second,
a link with TLS on still fails at once against a cleartext server (its handshake fails on the server's
first bytes), and still connects to a server with TLS on. A link that has connected once keeps trying
again after a mismatch, and one that cannot reach its server at first keeps trying until it can.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, List, Optional

# Standard libs
import ssl
import time
import socket
import logging
import secrets
import threading
from queue import Full as QueueFull
from multiprocessing import AuthenticationError
from multiprocessing.managers import RemoteError, State as ManagerState

# External libs
import pytest
from pytest import mark

# Internal libs
from hypershell.core import reconnect
from hypershell.core.queue import QueueVersionError, QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSError, TLSConfig, ensure_default_materials, build_client_context
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.topology import handback_text
from hypershell.core.reconnect import (UpstreamLink, FixedLink, LinkDown, LinkState, as_link, backoff_delay,
                                       is_fatal, is_transport, REPORT_INTERVAL)
from hypershell.data.model import serialize_tasks
from hypershell.relay import upstream as relay_upstream
from hypershell.relay.upstream import request_text
from tests.conftest import free_port
from tests.test_dispatch_workload import Group, group, wait_listening  # noqa: fixture


DEADLINE = 10.0  # Seconds a test waits on the link's own threads.


class FakeClient:
    """A queue client stand-in: `connect` succeeds or raises the next scripted error."""

    def __init__(self, error: Optional[BaseException] = None) -> None:
        self.error = error
        self.connected = False

        class _State:
            value = ManagerState.INITIAL
        self._state = _State()

    def connect(self) -> None:
        if self.error is not None:
            raise self.error
        self.connected = True
        self._state.value = ManagerState.STARTED


class Script:
    """A client factory whose connect attempts fail with the scripted errors, then succeed."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.clients: List[FakeClient] = []

    def __call__(self) -> FakeClient:
        client = FakeClient(self.errors.pop(0) if self.errors else None)
        self.clients.append(client)
        return client


class Clock:
    """A monotonic clock moved by hand, and a sleep that records its waits and moves the clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.waits: List[float] = []
        self.halted = False

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        return self.halted


def link_with(factory: Callable[[], Any], clock: Optional[Clock] = None, fraction: float = 0.5,
              **options: Any) -> UpstreamLink:
    clock = clock or Clock()
    return UpstreamLink(factory, name='upstream server test:1', clock=clock, rng=lambda: fraction,
                        sleep=clock.sleep, **options)


def wait_for(predicate: Callable[[], bool], timeout: float = DEADLINE) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@mark.unit
def test_backoff_is_full_jitter_doubling_to_the_cap() -> None:
    """Each wait is the random fraction of min(cap, base · 2^attempt)."""
    assert [backoff_delay(n, 1.0) for n in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert [backoff_delay(n, 0.5, base=2, cap=10) for n in range(4)] == [1, 2, 4, 5]
    assert backoff_delay(1000, 1.0) == 60
    assert backoff_delay(3, 0.0) == 0


@mark.unit
@mark.parametrize('error', [QueueVersionError('protocol'), AuthenticationError('digest'), TLSError('fingerprint'),
                            ssl.SSLCertVerificationError('verify')])
def test_fatal_errors(error: BaseException) -> None:
    """Errors a new connection cannot cure."""
    assert is_fatal(error) and not is_transport(error)


@mark.unit
@mark.parametrize('error', [EOFError(), BrokenPipeError(), ConnectionResetError(), ConnectionRefusedError(),
                            TimeoutError(), OSError('got end of file during message'), ssl.SSLError('bad record')])
def test_transport_errors(error: BaseException) -> None:
    """Errors of the connection, which a new one may cure."""
    assert is_transport(error) and not is_fatal(error)


@mark.unit
@mark.parametrize('error', [QueueFull(), KeyError('x'), ValueError('x')])
def test_other_errors_are_neither(error: BaseException) -> None:
    """Errors the caller handles (a full queue, say)."""
    assert not is_transport(error) and not is_fatal(error)


@mark.unit
def test_first_connection_then_calls_run_on_a_worker_per_role() -> None:
    """The first connection is made at once; calls of each role run on that role's own thread."""
    script, clock = Script(), Clock()
    link = link_with(script, clock)
    link.start()
    try:
        assert link.wait_up(DEADLINE)
        assert clock.waits == []  # No backoff before the first attempt.
        assert link.epoch == 1 and link.outage == 0
        names = {role: link.call(role, lambda client: threading.current_thread().name) for role in ('a', 'b')}
        assert names == {'a': 'hypershell-link-a', 'b': 'hypershell-link-b'}
        assert link.call('a', lambda client: client) is script.clients[0]
    finally:
        link.stop(DEADLINE)
    with pytest.raises(LinkDown):
        link.call('a', lambda client: None)


@mark.unit
def test_unreachable_at_start_backs_off_and_reports(caplog) -> None:
    """Refused attempts wait out the backoff; one WARNING, then INFO at attempts 2, 4, 8, ..."""
    errors = [ConnectionRefusedError('refused')] * 9
    clock = Clock()
    link = link_with(Script(*errors), clock, fraction=1.0, backoff_cap=8)
    with caplog.at_level(logging.DEBUG, logger='hypershell.core.reconnect'):
        link.start()
        try:
            assert link.wait_up(DEADLINE)
        finally:
            link.stop(DEADLINE)
    assert clock.waits == [1, 2, 4, 8, 8, 8, 8, 8, 8]
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1 and warnings[0].startswith('Cannot reach upstream server test:1')
    assert [m.split('(')[1].split()[0] for m in infos if m.startswith('Still')] == ['2', '4', '8']
    assert infos[-1] == 'Connected to upstream server test:1'


@mark.unit
def test_transport_failure_takes_the_link_down_and_it_reconnects(caplog) -> None:
    """A transport error is `LinkDown`; the link reconnects with a new client, epoch and worker thread."""
    script, clock = Script(), Clock()
    link = link_with(script, clock)
    link.start()
    try:
        assert link.wait_up(DEADLINE)
        with caplog.at_level(logging.DEBUG, logger='hypershell.core.reconnect'):
            def broken(client):
                clock.now += 42
                raise EOFError('connection closed')
            with pytest.raises(LinkDown):
                link.call('r', broken)
            assert wait_for(lambda: link.epoch == 2 and link.is_up())
        assert link.state is LinkState.UP and len(script.clients) == 2
        assert script.clients[0]._state.value == ManagerState.SHUTDOWN  # Released without dialing out.
        assert link.call('r', lambda client: client) is script.clients[1]
        assert link.outage == pytest.approx(42 + clock.waits[0])
        messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert messages[0].startswith('Lost connection to upstream server test:1 (EOFError: connection closed)')
        assert messages[-1].startswith('Reconnected to upstream server test:1 after')
    finally:
        link.stop(DEADLINE)


@mark.unit
def test_overrun_retires_the_stuck_worker_and_takes_the_link_down() -> None:
    """A call past its deadline is `LinkDown`; the stuck worker is abandoned and a new one serves the next epoch."""
    script = Script()
    link = link_with(script, deadline=0.2)
    link.start()
    stuck = threading.Event()
    try:
        assert link.wait_up(DEADLINE)
        stuck_thread = {}

        def hang(client):
            stuck_thread['thread'] = threading.current_thread()
            stuck.wait(DEADLINE)

        start = time.monotonic()
        with pytest.raises(LinkDown, match='took longer than 0.2 seconds'):
            link.call('puller', hang)
        assert time.monotonic() - start < 2
        assert wait_for(lambda: link.epoch == 2 and link.is_up())
        fresh = link.call('puller', lambda client: threading.current_thread())
        assert fresh is not stuck_thread['thread'] and stuck_thread['thread'].is_alive()
        stuck.set()
        assert wait_for(lambda: not stuck_thread['thread'].is_alive())  # Retired: ends once its call returns.
    finally:
        stuck.set()
        link.stop(DEADLINE)


@mark.unit
def test_other_errors_leave_the_link_up() -> None:
    """A full queue is the caller's to handle; the link stays on the same connection."""
    link = link_with(Script())
    link.start()
    try:
        assert link.wait_up(DEADLINE)

        def full(client):
            raise QueueFull()
        with pytest.raises(QueueFull):
            link.call('r', full)
        assert link.is_up() and link.epoch == 1
    finally:
        link.stop(DEADLINE)


@mark.unit
@mark.parametrize('error', [QueueVersionError('protocol'), AuthenticationError('digest'), TLSError('mismatch')])
def test_fatal_error_fails_the_link_for_every_caller(error: BaseException) -> None:
    """A fatal error on a call is raised as it is, now and to every later caller and waiter."""
    link = link_with(Script())
    link.start()
    try:
        assert link.wait_up(DEADLINE)

        def refused(client):
            raise error
        with pytest.raises(type(error)):
            link.call('r', refused)
        assert link.state is LinkState.FAILED
        with pytest.raises(type(error)):
            link.call('other', lambda client: None)
        with pytest.raises(type(error)):
            link.wait_up(0.1)
    finally:
        link.stop(DEADLINE)


@mark.unit
def test_fatal_error_on_connect_ends_reconnection(caplog) -> None:
    """A version error on connecting fails the link at once: no backoff, the error raised to waiters."""
    clock = Clock()
    link = link_with(Script(ConnectionRefusedError('refused'), QueueVersionError('old server')), clock)
    with caplog.at_level(logging.DEBUG, logger='hypershell.core.reconnect'):
        link.start()
        with pytest.raises(QueueVersionError, match='old server'):
            end = time.monotonic() + DEADLINE
            while time.monotonic() < end:
                link.wait_up(0.1)
    link.stop(DEADLINE)
    assert len(clock.waits) == 1 and not link.thread.is_alive()
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)  # The program reports it, once.


@mark.unit
def test_evict_hint_warns_once_per_outage(caplog) -> None:
    """Past the eviction hint the link warns once that held results will likely be refused."""
    clock = Clock()
    link = link_with(Script(), clock, fraction=1.0, backoff_cap=200, evict_hint=600)
    link.start()
    try:
        assert link.wait_up(DEADLINE)
        script_errors = [ConnectionRefusedError('refused')] * 12
        link.factory = Script(*script_errors)
        with caplog.at_level(logging.DEBUG, logger='hypershell.core.reconnect'):
            link.mark_down(link.epoch, 'test')
            assert wait_for(lambda: link.is_up())
        hints = [r.message for r in caplog.records if 'likely evicted' in r.message]
        assert len(hints) == 1 and 'past 600' in hints[0]
        assert link.outage > 600
    finally:
        link.stop(DEADLINE)


@mark.unit
def test_outage_reports_at_least_every_report_interval(caplog) -> None:
    """Between powers of two the outage is still reported at least every five minutes."""
    clock = Clock()
    link = link_with(Script(*[ConnectionRefusedError('refused')] * 20), clock, fraction=1.0, backoff_cap=100)
    with caplog.at_level(logging.INFO, logger='hypershell.core.reconnect'):
        link.start()
        try:
            assert link.wait_up(DEADLINE)
        finally:
            link.stop(DEADLINE)
    reports = [r.message for r in caplog.records if r.message.startswith('Still cannot reach')]
    attempts = [int(m.split('(')[1].split()[0]) for m in reports]
    assert attempts[:4] == [2, 4, 8, 11] and 16 in attempts  # 11: past REPORT_INTERVAL since the report at 8.
    assert REPORT_INTERVAL == 300


@mark.unit
def test_stages_are_passed_per_connection() -> None:
    """A stage passed on one connection is not passed on the next; unknown stages never hold a caller."""
    link = link_with(Script(), stages=('beat', 'drain'))
    link.start()
    try:
        assert link.wait_up(DEADLINE)
        assert link.ready(()) and link.ready(('other', )) and not link.ready(('beat', ))
        link.passed('beat')
        assert link.ready(('beat', )) and not link.ready(('beat', 'drain'))
        link.passed('drain', epoch=link.epoch + 5)  # A stale epoch passes nothing.
        assert not link.ready(('drain', ))
        link.passed('drain')
        assert link.ready(('beat', 'drain'))
        link.mark_down(link.epoch, 'test')
        assert not link.ready(())
        assert wait_for(lambda: link.ready(()))
        assert not link.ready(('beat', ))
    finally:
        link.stop(DEADLINE)


@mark.unit
def test_wait_ready_is_bounded_and_wakes_on_halt() -> None:
    """A waiter for a link that never comes up returns false within its timeout."""
    link = UpstreamLink(Script(*[ConnectionRefusedError('refused')] * 100_000), backoff_base=0.01, backoff_cap=0.05)
    link.start()
    start = time.monotonic()
    assert link.wait_up(0.3) is False
    assert time.monotonic() - start < 2
    link.stop(DEADLINE)
    assert not link.thread.is_alive()


@mark.unit
def test_connect_attempt_past_the_deadline_is_abandoned() -> None:
    """A dial that hangs counts as a failed attempt after the deadline; its client is released when it lands."""
    release = threading.Event()
    made: List[FakeClient] = []

    class Hangs(FakeClient):
        def connect(self) -> None:
            release.wait(DEADLINE)
            super().connect()

    def factory():
        client = Hangs() if not made else FakeClient()
        made.append(client)
        return client

    link = link_with(factory, deadline=0.2)
    link.start()
    try:
        assert link.wait_up(DEADLINE)
        assert link.client is made[1] and link.attempts == 0
        release.set()
        assert wait_for(lambda: made[0]._state.value == ManagerState.SHUTDOWN)
    finally:
        release.set()
        link.stop(DEADLINE)


@mark.unit
def test_fixed_link_calls_in_place() -> None:
    """A connected client used as a link: always up, calls run in the caller's thread."""
    client = object()
    link = as_link(client)
    assert isinstance(link, FixedLink) and as_link(link) is link
    assert link.call('r', lambda c: (c, threading.current_thread())) == (client, threading.current_thread())
    assert link.ready(('beat', )) and link.wait_up() and link.outage == 0


# --- A restarted upstream server -----------------------------------------------------------------

AUTH = 'relay-link-restart-key-0123456789'
IDENTITY = 'relay-1'

STALE_TRACEBACK = ('Traceback (most recent call last):\n'
                   '  File "/usr/lib/python3.14/multiprocessing/managers.py", line 211, in serve_client\n'
                   '    obj, exposed, gettypeid = id_to_obj[ident]\n'
                   '                              ~~~~~~~~~^^^^^^^\n'
                   "KeyError: '10a2cf820'\n")

ROLE_CALLS = {
    'puller': lambda client: client.dispatcher.request(request_text(IDENTITY, None, 1, 0)),
    'heartbeat': lambda client: client.heartbeat.put(Heartbeat.new(uuid=IDENTITY, host='relay-host').pack(),
                                                     timeout=2),
    'returner': lambda client: client.completed.put(serialize_tasks([]), timeout=2),
    'handback': lambda client: client.dispatcher.handback(handback_text(IDENTITY, [])),
}


def loopback(port: int = 0) -> QueueConfig:
    """Loopback queue configuration without TLS."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2)


@mark.unit
def test_stale_proxy_is_a_transport_error() -> None:
    """A server's answer to a proxy id it does not hold is a lost link; other remote errors are not."""
    stale = RemoteError(STALE_TRACEBACK)
    assert reconnect.is_stale_proxy(stale) and is_transport(stale) and not is_fatal(stale)
    assert reconnect.describe(stale) == 'upstream server restarted (unknown proxy id 10a2cf820)'
    exposed = RemoteError('Traceback (most recent call last):\n'
                          '  File "/usr/lib/python3.14/multiprocessing/managers.py", line 215, in serve_client\n'
                          "AttributeError: method 'shutdown' of <class 'queue.Queue'> object "
                          "is not in exposed=('put',)\n")
    for error in (exposed, KeyError('a1b2c3'), RemoteError('Unserializable message: <object>'), RemoteError()):
        assert not reconnect.is_stale_proxy(error) and not is_transport(error)


@mark.integration
@mark.parametrize('role', list(ROLE_CALLS))
def test_first_call_after_an_upstream_restart_is_a_lost_link(role: str, caplog) -> None:
    """A role's first call through proxies the server before a restart minted counts as a lost link.

    The link connects and mints the dispatcher through another role; the server is shut down and a new
    one is started on its port. The role's first call raises `LinkDown` naming the restart, the link
    reconnects, and the same call then succeeds on the new connection.
    """
    old = QueueServer(loopback())
    old.start()
    port = old.address[1]
    link = UpstreamLink(lambda: QueueClient(loopback(port)), name=f'127.0.0.1:{port}',
                        deadline=5, backoff_base=0.01, backoff_cap=0.02)
    new = None
    try:
        link.start()
        assert link.wait_up(DEADLINE)
        link.call('warm-up', lambda client: client.dispatcher)
        epoch = link.epoch
        old.shutdown()  # Kept referenced, so the new server cannot mint an id the old one did.
        new = QueueServer(loopback(port))
        new.start()
        with caplog.at_level(logging.WARNING, logger='hypershell.core.reconnect'):
            with pytest.raises(LinkDown, match='upstream server restarted') as raised:
                link.call(role, ROLE_CALLS[role])
        lost, = [record.getMessage() for record in caplog.records if 'Lost connection' in record.getMessage()]
        assert lost.startswith(f'Lost connection to 127.0.0.1:{port} (upstream server restarted (unknown proxy id ')
        assert 'Traceback' not in lost and 'Traceback' not in str(raised.value)
        assert link.wait_up(DEADLINE)
        link.call(role, ROLE_CALLS[role])
        assert link.epoch == epoch + 1
    finally:
        link.stop(DEADLINE)
        if new is not None:
            new.shutdown()


# --- A TLS mismatch at the first connection --------------------------------------------------------

@pytest.fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple:
    """A self-signed certificate and key for loopback, made once for this module."""
    return ensure_default_materials(str(tmp_path_factory.mktemp('tls')), hostnames=['localhost', '127.0.0.1'])


def server_tls(materials: tuple) -> TLSConfig:
    """A queue server's TLS configuration from `materials`."""
    cert, key = materials
    return TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)


def client_tls(materials: tuple) -> TLSConfig:
    """A queue client's TLS configuration, trusting the certificate of `materials`."""
    cert, _ = materials
    return TLSConfig(enabled=True, cafile=cert)


def relay_link(config: QueueConfig) -> UpstreamLink:
    """A link to `config`'s server that connects first as the relay does."""
    return UpstreamLink(lambda: QueueClient(config), name=f'{config.host}:{config.port}',
                        deadline=5, backoff_base=0.01, backoff_cap=0.02,
                        first_connect=relay_upstream.first_connect(config))


@mark.integration
def test_tls_link_against_a_cleartext_upstream_fails_at_once(tls_materials) -> None:
    """A link with TLS on against a server with TLS off fails at its first connection, naming its setting."""
    server = QueueServer(loopback())
    server.start()
    config = QueueConfig(host='127.0.0.1', port=server.address[1], auth=AUTH, size=2, tls=client_tls(tls_materials))
    link = None
    try:
        start = time.monotonic()
        link = relay_link(config)
        link.start()
        with pytest.raises(TLSError) as raised:
            link.wait_up(DEADLINE)
        assert time.monotonic() - start < 1
        assert link.state is LinkState.FAILED
        message = str(raised.value)
        assert 'does not use TLS' in message and '[relay.upstream.tls] enabled' in message
        assert 'must agree on TLS' in message
    finally:
        if link is not None:
            link.stop(DEADLINE)
        server.shutdown()


class SlowPath:
    """A loopback proxy to `port` that holds each chunk `delay` seconds before passing it on, both ways."""

    def __init__(self, port: int, delay: float) -> None:
        self.target, self.delay = port, delay
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.sockets: List[socket.socket] = []
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self) -> None:
        while True:
            try:
                near, _ = self.listener.accept()
                far = socket.create_connection(('127.0.0.1', self.target))
            except OSError:
                return
            self.sockets.extend((near, far))
            for source, sink in ((near, far), (far, near)):
                threading.Thread(target=self.forward, args=(source, sink), daemon=True).start()

    def forward(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                time.sleep(self.delay)
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for end in (source, sink):
                try:
                    end.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self.listener.close()
        for end in self.sockets:
            end.close()


SLOW = 1.1  # Seconds a slow path holds each chunk: longer than any fixed wait for a first message would be.


@mark.integration
def test_tls_link_against_a_cleartext_upstream_on_a_slow_path_fails_at_once(tls_materials) -> None:
    """A link with TLS on reaching a server with TLS off over a path that holds each chunk 1.1 s fails at its
    first connection, as on a fast path: the handshake itself fails on the server's first bytes, which are not
    a TLS record, however late they arrive."""
    server = QueueServer(loopback())
    server.start()
    path = SlowPath(server.address[1], SLOW)
    config = QueueConfig(host='127.0.0.1', port=path.port, auth=AUTH, size=2, tls=client_tls(tls_materials))
    link = UpstreamLink(lambda: QueueClient(config), name=f'127.0.0.1:{path.port}', deadline=DEADLINE,
                        backoff_base=0.01, backoff_cap=0.02, first_connect=relay_upstream.first_connect(config))
    try:
        start = time.monotonic()
        link.start()
        with pytest.raises(TLSError) as raised:
            link.wait_up(DEADLINE)
        assert time.monotonic() - start < SLOW + 2
        assert link.state is LinkState.FAILED and link.attempts == 0
        message = str(raised.value)
        assert 'does not use TLS' in message and 'must agree on TLS' in message
    finally:
        link.stop(DEADLINE)
        path.close()
        server.shutdown()


class Handshake(FakeClient):
    """A queue client stand-in whose `connect` is the TLS handshake alone, with the queue client's context.

    A queue client's whole connection takes many round trips (each proxy it makes is one more), which a
    path holding each chunk over a second makes minutes long; the handshake is the part that tells TLS.
    """

    def __init__(self, config: QueueConfig) -> None:
        super().__init__()
        self.config = config

    def connect(self) -> None:
        context = build_client_context(self.config.tls)
        with socket.create_connection((self.config.host, self.config.port)) as raw:
            with context.wrap_socket(raw, server_hostname=self.config.host):
                super().connect()


@mark.integration
def test_tls_link_against_a_tls_upstream_on_a_slow_path_connects(tls_materials) -> None:
    """A link with TLS on reaching a server with TLS on over the same slow path completes its handshake, seconds
    long, and connects: it is never taken for a server without TLS."""
    server = QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=AUTH, size=2, tls=server_tls(tls_materials)))
    server.start()
    path = SlowPath(server.address[1], SLOW)
    config = QueueConfig(host='127.0.0.1', port=path.port, auth=AUTH, size=2, tls=client_tls(tls_materials))
    clients: List[Handshake] = []
    link = UpstreamLink(lambda: clients.append(Handshake(config)) or clients[-1], name=f'127.0.0.1:{path.port}',
                        deadline=DEADLINE, backoff_base=0.01, backoff_cap=0.02,
                        first_connect=relay_upstream.first_connect(config))
    try:
        start = time.monotonic()
        link.start()
        assert link.wait_up(DEADLINE)
        assert time.monotonic() - start > SLOW
        assert link.state is LinkState.UP and link.failure is None and link.attempts == 0
        assert len(clients) == 1 and clients[0].connected
    finally:
        link.stop(DEADLINE)
        path.close()
        server.shutdown()


@mark.unit
@mark.parametrize('error', [ssl.SSLCertVerificationError(1, 'certificate verify failed'),
                            ssl.SSLEOFError(8, 'EOF occurred in violation of protocol'),
                            ssl.SSLZeroReturnError(6, 'TLS/SSL connection has been closed'),
                            ssl.SSLError(1, 'tlsv1 alert protocol version'),
                            ConnectionRefusedError(61, 'Connection refused')],
                  ids=['certificate', 'eof', 'zero-return', 'alert', 'refused'])
def test_a_tls_link_takes_only_a_failed_first_record_for_a_cleartext_upstream(error: BaseException,
                                                                              tls_materials) -> None:
    """With TLS on, only the handshake error for a first record that is not TLS (`WRONG_VERSION_NUMBER`) is a
    mismatch; a certificate that does not verify, a closed connection, an alert from a TLS server and a refused
    connection are raised as they are, so the link treats each as before."""
    config = QueueConfig(host='127.0.0.1', port=free_port(), auth=AUTH, size=2, tls=client_tls(tls_materials))
    connect = relay_upstream.first_connect(config)
    if isinstance(error, ssl.SSLError) and type(error) is ssl.SSLError:
        error.reason = 'TLSV1_ALERT_PROTOCOL_VERSION'
    with pytest.raises(type(error)) as raised:
        connect(FakeClient(error), DEADLINE)
    assert raised.value is error
    cleartext = ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number')
    cleartext.reason = 'WRONG_VERSION_NUMBER'
    with pytest.raises(reconnect.TLSMismatchError) as raised:
        connect(FakeClient(cleartext), DEADLINE)
    assert raised.value.__cause__ is cleartext


@mark.integration
def test_cleartext_link_against_a_tls_upstream_fails_at_once(group: Group) -> None:
    """A link with TLS off against an `hs server` with TLS on fails at its first connection, naming its setting."""
    port, key = free_port(), secrets.token_hex(16)
    server = group.start('server', 'server', '--forever', '-H', 'localhost', '-p', str(port),
                         env={'HYPERSHELL_SERVER_AUTH': key})
    wait_listening(server, port, group, 'server')
    config = QueueConfig(host='127.0.0.1', port=port, auth=key, size=2)
    link = None
    try:
        start = time.monotonic()
        link = relay_link(config)
        link.start()
        with pytest.raises(TLSError) as raised:
            link.wait_up(DEADLINE)
        assert time.monotonic() - start < 1
        assert link.state is LinkState.FAILED
        message = str(raised.value)
        assert 'uses TLS' in message and '--no-tls' in message and 'must agree on TLS' in message
    finally:
        if link is not None:
            link.stop(DEADLINE)


@mark.integration
def test_tls_mismatch_after_a_first_connection_is_retried(tls_materials) -> None:
    """Once a link has connected, a server on its port with TLS off is a lost link: it keeps trying, never fails."""
    old = QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=AUTH, size=2, tls=server_tls(tls_materials)))
    old.start()
    port = old.address[1]
    config = QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=client_tls(tls_materials))
    link = relay_link(config)
    new = None
    try:
        link.start()
        assert link.wait_up(DEADLINE)
        link.call('warm-up', lambda client: client.dispatcher)
        old.shutdown()
        new = QueueServer(loopback(port))
        new.start()
        with pytest.raises(LinkDown):
            link.call('puller', ROLE_CALLS['puller'])
        assert wait_for(lambda: link.attempts >= 3)
        assert link.state is LinkState.DOWN and link.failure is None
        assert not link.wait_up(0.2)
    finally:
        link.stop(DEADLINE)
        if new is not None:
            new.shutdown()


@mark.integration
@mark.parametrize('tls', [True, False], ids=['tls', 'cleartext'])
def test_upstream_unreachable_at_first_is_retried_until_it_answers(tls: bool, tls_materials) -> None:
    """A link whose server refuses at first keeps trying, and connects once a server with its TLS setting answers."""
    port = free_port()
    config = QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2,
                         tls=client_tls(tls_materials) if tls else None)
    link = relay_link(config)
    server = None
    try:
        link.start()
        assert wait_for(lambda: link.attempts >= 3)
        assert link.state is LinkState.DOWN and link.failure is None
        server = QueueServer(QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2,
                                         tls=server_tls(tls_materials) if tls else None))
        server.start()
        assert link.wait_up(DEADLINE)
        link.call('puller', ROLE_CALLS['puller'])
    finally:
        link.stop(DEADLINE)
        if server is not None:
            server.shutdown()
