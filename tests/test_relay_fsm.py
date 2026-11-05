# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's upstream machines re-enter on timeout and halt on halt, on signal and on shutdown.

The puller, the returner and the heartbeat are stepped one action at a time against a fake upstream,
and run in their threads to show a stop ends them within a deadline. The relay process itself is run
against a queue server in this process standing in for the upstream server, with no site clients
launched: it registers its zone identity there, pulls nothing while it has no site clients, and
drains on SIGUSR1; against a server without the work-request protocol it exits with the client's one
version error.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Standard libs
import os
import re
import sys
import json
import time
import signal
import socket
import subprocess
from pathlib import Path
from queue import Queue, Full as QueueFull
from types import SimpleNamespace
from datetime import datetime

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from cmdkit.app import exit_status

# Internal libs
import hypershell.core.signal
import hypershell.relay.upstream as upstream_module
from hypershell.core.signal import SIGUSR1, SIGUSR2
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.queue import QueueServer, QueueConfig, PROTOCOL
from hypershell.core.uuid import uuid
from hypershell.data.model import Entity, Client, Task
from hypershell.relay import store
from hypershell.relay.store import ensure_tables
from hypershell.relay.upstream import (UpstreamPuller, UpstreamPullerThread, PullerState, UpstreamHeartbeat,
                                       UpstreamHeartbeatThread, HeartbeatState, UpstreamReturner,
                                       UpstreamReturnerThread, ReturnerState, upstream_identity)
from tests.test_relay_connector import FakeDispatcher, upstream, connect_clients, dispatched, reply, POLL
from tests.test_dispatch_version import stock_server, AUTH as OLD_SERVER_AUTH


DEADLINE = 30.0  # Seconds for a machine or a relay to come up, or to end once stopped.
AUTH = 'relay-fsm-upstream-key-0123456789'
READY = 'Relay-local server ready'


@fixture
def relay_db(tmp_path: Path) -> Iterator[Engine]:
    """A fresh relay database."""
    engine = create_engine(f'sqlite:///{tmp_path / "relay.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    ensure_tables(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@fixture
def no_signal(monkeypatch) -> None:
    """No signal received, whatever an earlier test left."""
    monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', None)


def puller(engine: Engine, dispatcher: FakeDispatcher, confirmed: Optional[Any] = None) -> UpstreamPuller:
    """A default-zone puller on a fake upstream."""
    return UpstreamPuller(queue=upstream(dispatcher, confirmed), engine=engine, identity=upstream_identity(None),
                          bundlesize=1000, local_bundlesize=1, poll=POLL)


def until(predicate, timeout: float = DEADLINE) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- The puller --------------------------------------------------------------------------------


@mark.unit
def test_puller_reenters_on_empty_reply(relay_db: Engine, no_signal) -> None:
    """A reply with no tasks (the request's wait ran out) sends the puller back to its gate."""
    connect_clients(relay_db, 1)
    fake = FakeDispatcher(reply([]))
    machine = puller(relay_db, fake)
    assert machine.start() is PullerState.GATE
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.GATE
    assert machine.gate() is PullerState.REQUEST
    assert len(fake.requests) == 1


@mark.unit
def test_puller_gate_wait_is_finite(relay_db: Engine, no_signal) -> None:
    """A closed gate waits one poll period and re-enters."""
    machine = puller(relay_db, FakeDispatcher())
    start = time.monotonic()
    assert machine.gate() is PullerState.GATE
    assert time.monotonic() - start < 1.0


@mark.unit
def test_puller_finals_on_upstream_shutdown(relay_db: Engine, no_signal) -> None:
    """A shutdown reply ends pulling."""
    connect_clients(relay_db, 1)
    machine = puller(relay_db, FakeDispatcher(reply([], shutdown=True)))
    machine.gate()
    assert machine.request() is PullerState.FINAL
    assert machine.finalize() is PullerState.HALT


@mark.unit
@mark.parametrize('received', [SIGUSR1, SIGUSR2])
def test_puller_finals_on_signal_before_requesting(relay_db: Engine, monkeypatch, received: int) -> None:
    """A drain signal ends pulling at the gate, before another request is sent."""
    connect_clients(relay_db, 1)
    fake = FakeDispatcher()
    monkeypatch.setattr(hypershell.core.signal, 'RECEIVED', received)
    assert puller(relay_db, fake).gate() is PullerState.FINAL
    assert fake.requests == []


@mark.unit
def test_puller_halts_on_halt(relay_db: Engine, no_signal) -> None:
    """A halted puller's next state is HALT, from any state."""
    machine = puller(relay_db, FakeDispatcher())
    for state in (PullerState.GATE, PullerState.REQUEST, PullerState.INSERT, PullerState.CONFIRM):
        machine.state = state
        machine.halt()
        assert machine.next() is PullerState.HALT


@mark.unit
def test_puller_reenters_confirm_on_full_queue(relay_db: Engine, no_signal) -> None:
    """A confirmation that does not fit is put again."""

    class Refuses(Queue):
        refusals = 1

        def put(self, item, block=True, timeout=None):
            if self.refusals:
                self.refusals -= 1
                raise QueueFull()
            super().put(item, block, timeout)

    connect_clients(relay_db, 1)
    confirmed = Refuses()
    machine = puller(relay_db, FakeDispatcher(reply(dispatched('echo a'))), confirmed=confirmed)
    machine.gate()
    machine.request()
    assert machine.insert() is PullerState.CONFIRM
    assert machine.confirm() is PullerState.CONFIRM
    assert machine.confirm() is PullerState.GATE
    assert confirmed.qsize() == 1


@mark.unit
def test_puller_thread_stops_within_deadline(relay_db: Engine, no_signal) -> None:
    """A running puller, waiting at its gate, ends promptly once stopped."""
    thread = UpstreamPullerThread(queue=upstream(FakeDispatcher()), engine=relay_db,
                                  identity=upstream_identity(None), bundlesize=1000, local_bundlesize=1, poll=60)
    thread.start()
    assert until(lambda: thread.machine.state is PullerState.GATE)
    start = time.monotonic()
    thread.stop(wait=True, timeout=DEADLINE)
    assert not thread.is_alive()
    assert time.monotonic() - start < 5


# --- The returner ------------------------------------------------------------------------------


def returner(engine: Engine, completed: Optional[Any] = None, bundlewait: float = 0) -> UpstreamReturner:
    """A returner on a fake upstream."""
    return UpstreamReturner(queue=SimpleNamespace(completed=completed if completed is not None else Queue()),
                            engine=engine, bundlesize=100, bundlewait=bundlewait, poll=POLL, retention=300)


def complete_one(engine: Engine) -> str:
    """Receive one task and complete it at the site; return its id."""
    record, = dispatched('echo returned')
    store.insert_received(engine, [record], identity=upstream_identity(None))
    with engine.begin() as conn:
        conn.execute(Task.__table__.update().values(completion_time=datetime.now().astimezone(), exit_status=0,
                                                    client_id=str(uuid()), client_host='site-node'))
    return record['id']


@mark.unit
def test_returner_reenters_collect_with_nothing_to_return(relay_db: Engine) -> None:
    """With no completed row the returner waits one poll period and collects again."""
    machine = returner(relay_db)
    assert machine.start() is ReturnerState.COLLECT
    start = time.monotonic()
    assert machine.collect() is ReturnerState.COLLECT
    assert time.monotonic() - start < 1.0
    assert machine.queue.completed.empty()


@mark.unit
def test_returner_reenters_send_on_full_queue(relay_db: Engine) -> None:
    """A bundle that does not fit is put again, and only then marked."""

    class Refuses(Queue):
        refusals = 1

        def put(self, item, block=True, timeout=None):
            if self.refusals:
                self.refusals -= 1
                raise QueueFull()
            super().put(item, block, timeout)

    complete_one(relay_db)
    machine = returner(relay_db, completed=Refuses())
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.SEND
    assert machine.send() is ReturnerState.MARK
    assert machine.mark() is ReturnerState.COLLECT
    assert machine.queue.completed.qsize() == 1


@mark.unit
def test_returner_halts_on_halt(relay_db: Engine) -> None:
    """A halted returner's next state is HALT, from any state."""
    machine = returner(relay_db)
    for state in (ReturnerState.COLLECT, ReturnerState.SEND, ReturnerState.MARK):
        machine.state = state
        machine.halt()
        assert machine.next() is ReturnerState.HALT
    assert machine.queue.completed.empty()


@mark.unit
def test_returner_finals_on_flush_once_drained(relay_db: Engine) -> None:
    """Told to flush, the returner sends what it holds at once, whatever the bundle wait, then ends."""
    task_id = complete_one(relay_db)
    machine = returner(relay_db, bundlewait=3600)
    assert machine.collect() is ReturnerState.COLLECT
    machine.flush()
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.MARK
    assert machine.mark() is ReturnerState.COLLECT
    assert machine.collect() is ReturnerState.FINAL
    assert machine.finalize() is ReturnerState.HALT
    assert [task['id'] for task in json.loads(machine.queue.completed.get_nowait())] == [task_id]


@mark.unit
def test_returner_thread_stops_within_deadline(relay_db: Engine) -> None:
    """A running returner, waiting for results, ends promptly once stopped."""
    thread = UpstreamReturnerThread(queue=SimpleNamespace(completed=Queue()), engine=relay_db, bundlesize=100,
                                    bundlewait=600, poll=60, retention=300)
    thread.start()
    assert until(lambda: thread.machine.state is ReturnerState.COLLECT)
    start = time.monotonic()
    thread.stop(wait=True, timeout=DEADLINE)
    assert not thread.is_alive()
    assert time.monotonic() - start < 5


@mark.unit
def test_returner_thread_flushes_and_ends(relay_db: Engine) -> None:
    """A returner told to flush returns what it holds and ends on its own."""
    task_id = complete_one(relay_db)
    queue = Queue()
    thread = UpstreamReturnerThread(queue=SimpleNamespace(completed=queue), engine=relay_db, bundlesize=100,
                                    bundlewait=600, poll=60, retention=300)
    thread.start()
    thread.flush()
    thread.join(timeout=DEADLINE)
    assert not thread.is_alive()
    assert [task['id'] for task in json.loads(queue.get_nowait())] == [task_id]
    assert store.count_held(relay_db) == 0


# --- The heartbeat -----------------------------------------------------------------------------


def heartbeat(queue: Optional[Any] = None, heartrate: int = 10) -> UpstreamHeartbeat:
    """A heartbeat machine for the default-zone identity on a fake upstream."""
    remote = SimpleNamespace(heartbeat=queue if queue is not None else Queue())
    return UpstreamHeartbeat(queue=remote, identity=upstream_identity(None), heartrate=heartrate)


def beats(machine: UpstreamHeartbeat) -> List[Heartbeat]:
    """Every heartbeat sent so far."""
    found = []
    while not machine.queue.heartbeat.empty():
        found.append(Heartbeat.unpack_or_none(machine.queue.heartbeat.get_nowait()))
    return found


@mark.unit
def test_heartbeat_beats_under_the_zone_identity() -> None:
    """Each beat names the zone identity, this host, and the running state."""
    machine = heartbeat()
    assert machine.start() is HeartbeatState.SUBMIT
    assert machine.submit() is HeartbeatState.WAIT
    beat, = beats(machine)
    assert beat.uuid == upstream_identity(None)
    assert beat.state is ClientState.RUNNING


@mark.unit
def test_heartbeat_reenters_wait_until_due(monkeypatch) -> None:
    """Between beats the wait is finite and re-enters; once the heartrate has passed it beats again."""
    monkeypatch.setattr(upstream_module, 'HEARTBEAT_WAIT_STEP', 0.01)
    machine = heartbeat(heartrate=60)
    machine.submit()
    start = time.monotonic()
    assert machine.wait() is HeartbeatState.WAIT
    assert time.monotonic() - start < 1.0
    machine.previous -= 60
    assert machine.wait() is HeartbeatState.SUBMIT


@mark.unit
def test_heartbeat_reenters_submit_on_full_queue() -> None:
    """A beat that does not fit is sent again."""

    class Refuses(Queue):
        refusals = 1

        def put(self, item, block=True, timeout=None):
            if self.refusals:
                self.refusals -= 1
                raise QueueFull()
            super().put(item, block, timeout)

    machine = heartbeat(queue=Refuses())
    assert machine.submit() is HeartbeatState.SUBMIT
    assert machine.submit() is HeartbeatState.WAIT
    assert len(beats(machine)) == 1


@mark.unit
def test_heartbeat_finishes_with_a_final_beat() -> None:
    """Once told to finish, the next beat is FINISHED and the machine ends."""
    machine = heartbeat(heartrate=60)
    machine.submit()
    machine.signal_finished()
    assert machine.wait() is HeartbeatState.SUBMIT
    assert machine.submit() is HeartbeatState.FINAL
    assert machine.finalize() is HeartbeatState.HALT
    assert [beat.state for beat in beats(machine)] == [ClientState.RUNNING, ClientState.FINISHED]


@mark.unit
def test_heartbeat_halts_on_halt() -> None:
    """A halted heartbeat's next state is HALT, with no further beat."""
    machine = heartbeat()
    for state in (HeartbeatState.SUBMIT, HeartbeatState.WAIT):
        machine.state = state
        machine.halt()
        assert machine.next() is HeartbeatState.HALT
    assert beats(machine) == []


@mark.unit
def test_heartbeat_thread_stops_within_deadline() -> None:
    """A heartbeat waiting a long heartrate ends promptly once stopped."""
    thread = UpstreamHeartbeatThread(queue=SimpleNamespace(heartbeat=Queue()), identity=upstream_identity(None),
                                     heartrate=600)
    thread.start()
    assert until(lambda: thread.machine.state is HeartbeatState.WAIT)
    start = time.monotonic()
    thread.stop(wait=True, timeout=DEADLINE)
    assert not thread.is_alive()
    assert time.monotonic() - start < 5


@mark.unit
def test_heartbeat_thread_sends_finished_and_ends() -> None:
    """A heartbeat told to finish sends its FINISHED beat at once and ends."""
    queue = Queue()
    thread = UpstreamHeartbeatThread(queue=SimpleNamespace(heartbeat=queue), identity=upstream_identity(None),
                                     heartrate=600)
    thread.start()
    assert until(lambda: thread.machine.state is HeartbeatState.WAIT)
    thread.signal_finished()
    thread.join(timeout=5)
    assert not thread.is_alive()
    states = [Heartbeat.unpack_or_none(queue.get_nowait()).state for _ in range(queue.qsize())]
    assert states == [ClientState.RUNNING, ClientState.FINISHED]


# --- The relay process -------------------------------------------------------------------------


def loopback_port() -> int:
    """A loopback port nothing listens on, for the relay-local server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start_relay(tmp_path: Path, port: int, auth: str = AUTH) -> Tuple[subprocess.Popen, Path]:
    """Start `hs relay` against an upstream on loopback `port`, in its own process group."""
    site = tmp_path / 'site'
    site.mkdir(exist_ok=True)
    log = tmp_path / 'relay.log'
    env = {**os.environ, 'HYPERSHELL_SITE': str(site), 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
           'HYPERSHELL_CONFIG_FILE': '', 'HYPERSHELL_RELAY_UPSTREAM_AUTH': auth}
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    with open(log, mode='w') as stream:
        proc = subprocess.Popen(['hs', 'relay', '--bind', '127.0.0.1', '--port', str(loopback_port()),
                                 '-H', '127.0.0.1', '-p', str(port), '--no-tls', '-N', '0'],
                                env=env, stdout=subprocess.DEVNULL, stderr=stream, start_new_session=True)
    return proc, log


def finish(proc: subprocess.Popen) -> None:
    """Kill whatever is left of a relay's process group."""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(DEADLINE)


def received_beats(server: QueueServer) -> List[Heartbeat]:
    """Every heartbeat the stand-in upstream has received so far."""
    found = []
    while not server.heartbeat.empty():
        found.append(Heartbeat.unpack_or_none(server.heartbeat.get_nowait()))
    return found


@mark.integration
def test_relay_registers_its_identity_and_pulls_nothing_without_site_clients(tmp_path: Path) -> None:
    """The relay beats upstream under its logged zone identity, sends no request with no site client, and drains."""
    config = QueueConfig(host='127.0.0.1', port=0, auth=AUTH, size=2)
    with QueueServer(config) as server:
        proc, log = start_relay(tmp_path, server.address[1])
        try:
            assert until(lambda: READY in log.read_text() or proc.poll() is not None), log.read_text()
            assert proc.poll() is None, log.read_text()
            match = re.search(r'Upstream identity (\S+) for zone default', log.read_text())
            assert match, log.read_text()
            identity = match.group(1)
            seen: List[Heartbeat] = []
            assert until(lambda: seen.extend(received_beats(server)) or any(b.uuid == identity for b in seen))
            assert {beat.uuid for beat in seen} == {identity}
            assert server.dispatcher.demand == {}
            proc.send_signal(signal.SIGUSR1)
            assert proc.wait(DEADLINE) == exit_status.success, log.read_text()
            seen.extend(received_beats(server))
            assert seen[-1].uuid == identity and seen[-1].state is ClientState.FINISHED
            assert server.dispatcher.demand == {}
        finally:
            finish(proc)


@mark.integration
def test_relay_against_old_server_exits_with_one_version_error(tmp_path: Path) -> None:
    """Against a server without the work-request protocol, the relay exits non-zero with one critical line."""
    manager, _ = stock_server()
    proc = None
    try:
        proc, log = start_relay(tmp_path, manager.address[1], auth=OLD_SERVER_AUTH)
        assert proc.wait(DEADLINE) != exit_status.success
    finally:
        if proc is not None:
            finish(proc)
        manager.shutdown()
    lines = log.read_text().splitlines()
    critical = [line for line in lines if line.startswith('CRITICAL')]
    assert len(critical) == 1, lines
    assert 'QueueVersionError' in critical[0] and 'work-request protocol' in critical[0]
    assert not any(READY in line for line in lines)
