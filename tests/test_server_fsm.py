# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Tests for the server state machines that move data through the managed queues.

Unit tests drive each machine's actions one step at a time against plain threading queues.
Integration tests run each machine on its own thread against a started queue server, with a
remote client on the other end of the queue.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator
from pathlib import Path

# Standard libs
import time
from queue import Queue
from types import SimpleNamespace

# External libs
from pytest import fixture, mark
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Internal libs
import hypershell.server
import hypershell.core.signal
from hypershell.core.signal import SIGUSR1
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig, make_sentinel
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, serialize_tasks, deserialize_tasks
from hypershell.client import ClientInfo
from hypershell.server import (Scheduler, SchedulerState, SchedulerThread,
                               Confirm, ConfirmState, ConfirmThread,
                               Receiver, ReceiverState, ReceiverThread,
                               HeartMonitor, HeartbeatState, HeartMonitorThread)


AUTH = 'server-fsm-test-key-0123456789'
TIMEOUT = 0.05
WAIT = 2.0


@fixture(autouse=True)
def short_timeout(monkeypatch) -> None:
    """Shorten the server's queue-operation timeout so re-entry is observed quickly."""
    monkeypatch.setattr(hypershell.server, 'DEFAULT_REMOTE_TIMEOUT', TIMEOUT)


@fixture
def queues() -> SimpleNamespace:
    """Stand-ins for the four managed queues, with the bounded pair holding a single item."""
    return SimpleNamespace(scheduled=Queue(1), completed=Queue(1), heartbeat=Queue(), confirmed=Queue())


@fixture
def served() -> Iterator[tuple[QueueServer, QueueClient]]:
    """A started queue server on an ephemeral loopback port and a connected remote client."""
    with QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=AUTH, size=1)) as server:
        client = QueueClient(QueueConfig(host='127.0.0.1', port=server.address[1], auth=AUTH, size=1))
        client.connect()
        yield server, client


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file, then restore it."""
    engine = create_engine(f'sqlite:///{tmp_path / "tasks.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


def bundle(*args: str) -> bytes:
    """A packed bundle of new tasks."""
    return serialize_tasks([Task.new(args=arg) for arg in args])


def assert_halts(thread, timeout: float = WAIT) -> None:
    """The thread finishes within `timeout` (and raised nothing)."""
    thread.join(timeout=timeout)
    assert not thread.is_alive()


@mark.unit
class TestReceiver:
    """The receiver drains finished bundles from ``completed``."""

    def test_reenters_on_timeout(self, queues) -> None:
        receiver = Receiver(queue=queues, in_memory=True)
        start = time.monotonic()
        assert receiver.unload_bundle() is ReceiverState.UNLOAD
        assert time.monotonic() - start < TIMEOUT + 1.0

    def test_moves_bundle(self, queues) -> None:
        receiver = Receiver(queue=queues, in_memory=True)
        queues.completed.put(bundle('echo a', 'echo b'))
        assert receiver.unload_bundle() is ReceiverState.UNPACK
        assert receiver.unpack_bundle() is ReceiverState.UPDATE
        assert [task.args for task in receiver.tasks] == ['echo a', 'echo b']
        assert receiver.update_tasks() is ReceiverState.UNLOAD
        assert queues.completed.empty()

    def test_halts_on_sentinel(self, queues) -> None:
        receiver = Receiver(queue=queues, in_memory=True)
        queues.completed.put(make_sentinel())
        assert receiver.unload_bundle() is ReceiverState.UNPACK
        assert receiver.unpack_bundle() is ReceiverState.FINAL

    def test_thread_halts_on_sentinel(self, queues) -> None:
        thread = ReceiverThread(queue=queues, in_memory=True)
        thread.start()
        time.sleep(3 * TIMEOUT)
        assert thread.is_alive()
        queues.completed.put(make_sentinel())
        assert_halts(thread)


@mark.unit
class TestConfirm:
    """The confirm machine drains bundle acknowledgements from ``confirmed``."""

    def test_reenters_on_timeout(self, queues) -> None:
        confirm = Confirm(queue=queues, in_memory=True)
        start = time.monotonic()
        assert confirm.unload_info() is ConfirmState.UNLOAD
        assert time.monotonic() - start < TIMEOUT + 1.0

    def test_moves_info(self, queues) -> None:
        confirm = Confirm(queue=queues, in_memory=True)
        queues.confirmed.put(ClientInfo(client_id='c1', client_host='h1', task_ids=['t1', 't2']).pack())
        assert confirm.unload_info() is ConfirmState.UNPACK
        assert confirm.unpack_info() is ConfirmState.UPDATE
        assert confirm.client_info.task_ids == ['t1', 't2']
        assert confirm.update_info() is ConfirmState.UNLOAD

    def test_halts_on_sentinel(self, queues) -> None:
        confirm = Confirm(queue=queues, in_memory=True)
        queues.confirmed.put(make_sentinel())
        assert confirm.unload_info() is ConfirmState.UNPACK
        assert confirm.unpack_info() is ConfirmState.FINAL

    def test_thread_halts_on_sentinel(self, queues) -> None:
        thread = ConfirmThread(queue=queues, in_memory=True)
        thread.start()
        time.sleep(3 * TIMEOUT)
        assert thread.is_alive()
        queues.confirmed.put(make_sentinel())
        assert_halts(thread)


@mark.unit
class TestHeartMonitor:
    """The heart monitor drains ``heartbeat`` and posts disconnect sentinels on ``scheduled``."""

    def test_reenters_on_timeout(self, queues) -> None:
        monitor = HeartMonitor(queue=queues, evict_after=10, in_memory=True)
        start = time.monotonic()
        assert monitor.get_next() is HeartbeatState.SWITCH
        assert time.monotonic() - start < TIMEOUT + 1.0
        assert monitor.switch_mode() is HeartbeatState.NEXT

    def test_registers_beat(self, queues) -> None:
        monitor = HeartMonitor(queue=queues, evict_after=10, in_memory=True)
        queues.heartbeat.put(Heartbeat.new(uuid='c1', host='h1').pack())
        assert monitor.get_next() is HeartbeatState.UPDATE
        assert monitor.update_client() is HeartbeatState.SWITCH
        assert list(monitor.beats) == ['c1']

    def test_signals_clients(self, queues) -> None:
        monitor = HeartMonitor(queue=queues, evict_after=10, in_memory=True)
        queues.heartbeat.put(Heartbeat.new(uuid='c1', host='h1').pack())
        monitor.get_next()
        monitor.update_client()
        monitor.should_signal = True
        assert monitor.switch_mode() is HeartbeatState.SIGNAL
        assert monitor.signal_clients() is HeartbeatState.SWITCH
        assert queues.scheduled.get_nowait() == make_sentinel()
        assert queues.scheduled.empty()

    def test_halts_on_sentinel(self, queues) -> None:
        monitor = HeartMonitor(queue=queues, evict_after=10, in_memory=True)
        queues.heartbeat.put(make_sentinel())
        assert monitor.get_next() is HeartbeatState.FINAL

    def test_thread_halts_on_sentinel(self, queues) -> None:
        thread = HeartMonitorThread(queue=queues, evict_after=10, in_memory=True)
        thread.start()
        queues.heartbeat.put(Heartbeat.new(uuid='c1', host='h1').pack())
        time.sleep(3 * TIMEOUT)
        assert thread.is_alive()
        assert list(thread.machine.beats) == ['c1']
        queues.heartbeat.put(make_sentinel())
        assert_halts(thread)


@mark.unit
class TestScheduler:
    """The scheduler posts bundles on ``scheduled``."""

    def test_posts_bundle(self, queues) -> None:
        scheduler = Scheduler(queue=queues)
        scheduler.tasks = [Task.new(args='echo a')]
        assert scheduler.pack_bundle() is SchedulerState.POST
        assert scheduler.post_bundle() is SchedulerState.LOAD
        assert [task.args for task in deserialize_tasks(queues.scheduled.get_nowait())] == ['echo a']

    def test_reenters_on_timeout(self, queues) -> None:
        scheduler = Scheduler(queue=queues)
        queues.scheduled.put(b'occupied')
        scheduler.tasks = [Task.new(args='echo a')]
        scheduler.pack_bundle()
        start = time.monotonic()
        assert scheduler.post_bundle() is SchedulerState.POST
        assert time.monotonic() - start < TIMEOUT + 1.0
        assert queues.scheduled.get_nowait() == b'occupied'
        assert scheduler.post_bundle() is SchedulerState.LOAD
        assert [task.args for task in deserialize_tasks(queues.scheduled.get_nowait())] == ['echo a']

    def test_halts_on_signal(self, queues, monkeypatch) -> None:
        scheduler = Scheduler(queue=queues)
        monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', SIGUSR1)
        assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.integration
class TestThroughQueueServer:
    """Each machine moves data to or from a remote client through a started queue server."""

    def test_receiver(self, served) -> None:
        server, client = served
        thread = ReceiverThread(queue=server, in_memory=True)
        thread.start()
        client.completed.put(bundle('echo a'), timeout=WAIT)
        assert wait_for(lambda: [task.args for task in thread.machine.tasks] == ['echo a'])
        client.completed.put(make_sentinel(), timeout=WAIT)
        assert_halts(thread)

    def test_confirm(self, served) -> None:
        server, client = served
        thread = ConfirmThread(queue=server, in_memory=True)
        thread.start()
        client.confirmed.put(ClientInfo(client_id='c1', client_host='h1', task_ids=['t1']).pack(), timeout=WAIT)
        assert wait_for(lambda: thread.machine.client_info is not None
                        and thread.machine.client_info.task_ids == ['t1'])
        client.confirmed.put(make_sentinel(), timeout=WAIT)
        assert_halts(thread)

    def test_heartmonitor(self, served) -> None:
        server, client = served
        thread = HeartMonitorThread(queue=server, evict_after=10, in_memory=True)
        thread.start()
        client.heartbeat.put(Heartbeat.new(uuid='c1', host='h1').pack(), timeout=WAIT)
        assert wait_for(lambda: list(thread.machine.beats) == ['c1'])
        thread.signal_clients()
        assert client.scheduled.get(timeout=WAIT) == make_sentinel()
        client.heartbeat.put(Heartbeat.new(uuid='c1', host='h1', state=ClientState.FINISHED).pack(), timeout=WAIT)
        assert wait_for(lambda: not thread.machine.beats)
        client.heartbeat.put(make_sentinel(), timeout=WAIT)
        assert_halts(thread)

    def test_scheduler(self, served, task_db, monkeypatch) -> None:
        server, client = served
        Task.add_all([Task.new(args=f'echo {i}') for i in range(3)])
        thread = SchedulerThread(queue=server, bundlesize=1, forever_mode=True, poll=1)
        thread.start()
        received = [deserialize_tasks(client.scheduled.get(timeout=WAIT))[0].args for _ in range(3)]
        assert sorted(received) == ['echo 0', 'echo 1', 'echo 2']
        monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', SIGUSR1)
        assert_halts(thread, timeout=2 * WAIT)


def wait_for(predicate, timeout: float = WAIT) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
