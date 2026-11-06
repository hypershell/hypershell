# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The upstream link: call deadlines, error classes, reconnection with backoff, and its log cadence.

The link is driven with a fake client factory, an injected clock and random source, and a sleep that
records each backoff wait and advances the clock instead of waiting. Calls run on real worker threads
(the deadline is real time, kept short here).
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, List, Optional

# Standard libs
import ssl
import time
import logging
import threading
from queue import Full as QueueFull
from multiprocessing import AuthenticationError
from multiprocessing.managers import State as ManagerState

# External libs
import pytest
from pytest import mark

# Internal libs
from hypershell.core.queue import QueueVersionError
from hypershell.core.tls import TLSError
from hypershell.core.reconnect import (UpstreamLink, FixedLink, LinkDown, LinkState, as_link, backoff_delay,
                                       is_fatal, is_transport, REPORT_INTERVAL)


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
