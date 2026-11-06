# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's upstream machines re-enter on timeout and halt on halt, on signal and on shutdown.

The puller, the returner and the heartbeat are stepped one action at a time against a fake upstream,
and run in their threads to show a stop ends them within a deadline. A halted puller stops at its
gate: a request it has already sent is inserted and confirmed first. The relay watches its parts:
a site or upstream thread that fails, or ends out of turn, is raised so the relay takes its failure
path. The relay process itself is run
against a queue server in this process standing in for the upstream server, with a launcher that
starts no site client: it registers its zone identity there, pulls nothing while it has no site
clients, and drains on SIGUSR1; against a server without the work-request protocol it exits with the
client's one version error; and it refuses a site of no clients.
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
import threading
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
from hypershell.core.thread import Thread
from hypershell.relay import store, RelayThread, RelayPartEnded
from hypershell.relay.store import ensure_tables
from hypershell.relay.upstream import (UpstreamPuller, UpstreamPullerThread, PullerState, UpstreamHeartbeat,
                                       UpstreamHeartbeatThread, HeartbeatState, UpstreamReturner,
                                       UpstreamReturnerThread, ReturnerState, upstream_identity)
from hypershell.core.reconnect import UpstreamLink
from hypershell.relay.upstream import LINK_STAGES
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
def test_puller_halt_ends_at_the_gate(relay_db: Engine, no_signal) -> None:
    """A halted puller ends at its gate, or before sending a request; one already sent is seen through."""
    connect_clients(relay_db, 1)
    for state in (PullerState.GATE, PullerState.REQUEST):
        fake = FakeDispatcher(reply(dispatched('echo a')))
        machine = puller(relay_db, fake)
        machine.state = state
        machine.halt()
        machine.state = machine.next()
        assert machine.state is PullerState.FINAL
        assert machine.next() is PullerState.HALT
        assert fake.requests == []

    confirmed = Queue()
    machine = puller(relay_db, FakeDispatcher(reply(dispatched('echo b'))), confirmed=confirmed)
    assert machine.gate() is PullerState.REQUEST
    machine.state = machine.request()
    assert machine.state is PullerState.INSERT
    machine.halt()
    for expected in (PullerState.CONFIRM, PullerState.GATE, PullerState.FINAL, PullerState.HALT):
        machine.state = machine.next()
        assert machine.state is expected
    assert confirmed.qsize() == 1


class HeldRequest(FakeDispatcher):
    """Holds each request until released, as an upstream server holds one until it has tasks to cut."""

    def __init__(self, *replies: dict) -> None:
        super().__init__(*replies)
        self.entered = threading.Event()
        self.release = threading.Event()

    def request(self, text_: str) -> str:
        self.entered.set()
        assert self.release.wait(DEADLINE)
        return super().request(text_)


@mark.unit
def test_puller_halted_mid_request_inserts_and_confirms_the_reply(relay_db: Engine, no_signal) -> None:
    """A puller told to stop while its request is outstanding inserts and confirms the reply before it ends."""
    connect_clients(relay_db, 3)
    records = dispatched('echo a', 'echo b', 'echo c')
    fake, confirmed = HeldRequest(reply(records)), Queue()
    thread = UpstreamPullerThread(queue=upstream(fake, confirmed), engine=relay_db, identity=upstream_identity(None),
                                  bundlesize=1000, local_bundlesize=1, poll=POLL)
    thread.start()
    assert fake.entered.wait(DEADLINE)
    thread.stop()  # The halt lands while the request is outstanding ...
    fake.release.set()  # ... and only then does the reply arrive.
    thread.join(timeout=DEADLINE)
    assert not thread.is_alive()

    assert store.count_backlog(relay_db) == 3 and store.count_held(relay_db) == 3
    info = confirmed.get_nowait()
    assert sorted(json.loads(info)['task_ids']) == sorted(record['id'] for record in records)
    assert confirmed.empty() and len(fake.requests) == 1


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


# --- Through the upstream link -----------------------------------------------------------------


class FakeUpstream:
    """A fake upstream reached through a real link: every connection shares its queues; `down` refuses one."""

    def __init__(self, dispatcher: Optional[FakeDispatcher] = None, confirmed: Optional[Any] = None) -> None:
        self.down = False
        self.queues = upstream(dispatcher or FakeDispatcher(), confirmed)
        self.queues.completed = Queue()

    def client(self) -> SimpleNamespace:
        outer = self

        class Client(SimpleNamespace):
            def connect(self) -> None:
                if outer.down:
                    raise ConnectionRefusedError('upstream down')

        return Client(**vars(self.queues))


@fixture
def link_to() -> Iterator[Any]:
    """Make started links to fake upstreams (fast backoff); stop them all afterwards."""
    links = []

    def make(fake: FakeUpstream, **options: Any) -> UpstreamLink:
        link = UpstreamLink(fake.client, stages=LINK_STAGES, backoff_base=0.01, backoff_cap=0.02, **options)
        links.append(link)
        link.start()
        assert link.wait_up(DEADLINE)
        return link
    try:
        yield make
    finally:
        for link in links:
            link.stop(DEADLINE)


def take_down(fake: FakeUpstream, link: UpstreamLink) -> None:
    """Take the link down and keep it down until `fake.down` is cleared."""
    fake.down = True
    link.mark_down(link.epoch, 'test outage')
    assert not link.is_up()


class FailsOnce(Queue):
    """A queue whose first put fails on the transport, as a lost connection does."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def put(self, item, block=True, timeout=None):
        if self.failures:
            self.failures -= 1
            raise EOFError('connection closed')
        super().put(item, block, timeout)


@mark.unit
def test_puller_waits_for_the_link_and_its_stages_before_requesting(relay_db: Engine, no_signal, link_to) -> None:
    """A new connection is used for requests only after a heartbeat and the outbox drain have passed on it."""
    connect_clients(relay_db, 1)
    fake = FakeUpstream(FakeDispatcher(reply([])))
    link = link_to(fake)
    machine = UpstreamPuller(queue=link, engine=relay_db, identity=upstream_identity(None), poll=POLL)
    assert machine.gate() is PullerState.WAIT_LINK
    link.passed('beat')
    assert machine.wait_link() is PullerState.WAIT_LINK  # Within its bounded wait: the drain is still to come.
    link.passed('drain')
    assert machine.wait_link() is PullerState.GATE
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.GATE
    assert len(fake.queues.dispatcher.requests) == 1


@mark.unit
def test_puller_request_lost_with_the_link_waits_for_it(relay_db: Engine, no_signal, link_to) -> None:
    """A request the link loses sends the puller to wait for the link, not to fail."""

    class Breaks(FakeDispatcher):
        def request(self, text_: str) -> str:
            raise ConnectionResetError('reset')

    connect_clients(relay_db, 1)
    fake = FakeUpstream(Breaks())
    link = link_to(fake)
    for stage in ('beat', 'drain'):
        link.passed(stage)
    machine = UpstreamPuller(queue=link, engine=relay_db, identity=upstream_identity(None), poll=POLL)
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.WAIT_LINK
    assert not machine.unconfirmed


@mark.unit
def test_puller_sends_a_lost_confirmation_again_after_a_short_outage(relay_db: Engine, no_signal, link_to) -> None:
    """A confirmation lost with the link is sent again once the link is back and has carried a heartbeat."""
    connect_clients(relay_db, 1)
    confirmed = FailsOnce()
    fake = FakeUpstream(FakeDispatcher(reply(dispatched('echo a'))), confirmed)
    link = link_to(fake, evict_hint=600)
    for stage in ('beat', 'drain'):
        link.passed(stage)
    machine = UpstreamPuller(queue=link, engine=relay_db, identity=upstream_identity(None), poll=POLL)
    machine.gate()
    machine.request()
    assert machine.insert() is PullerState.CONFIRM
    assert machine.confirm() is PullerState.WAIT_LINK and machine.unconfirmed
    assert until(lambda: link.is_up() and link.epoch == 2)
    assert machine.wait_link() is PullerState.WAIT_LINK  # No heartbeat on the new connection yet.
    link.passed('beat')
    assert machine.wait_link() is PullerState.CONFIRM
    assert machine.confirm() is PullerState.GATE
    info = json.loads(confirmed.get_nowait())
    assert info['client_id'] == upstream_identity(None) and len(info['task_ids']) == 1


@mark.unit
def test_puller_drops_a_lost_confirmation_after_an_outage_past_the_evict_hint(relay_db: Engine, no_signal,
                                                                              link_to, caplog) -> None:
    """After an outage longer than the eviction hint, a lost confirmation is not sent: the tasks run on here."""
    connect_clients(relay_db, 1)
    confirmed = FailsOnce()
    fake = FakeUpstream(FakeDispatcher(reply(dispatched('echo a'))), confirmed)
    link = link_to(fake, evict_hint=0)
    for stage in ('beat', 'drain'):
        link.passed(stage)
    machine = UpstreamPuller(queue=link, engine=relay_db, identity=upstream_identity(None), poll=POLL)
    machine.gate()
    machine.request()
    machine.insert()
    assert machine.confirm() is PullerState.WAIT_LINK
    assert until(lambda: link.is_up() and link.epoch == 2)
    link.passed('beat')
    assert machine.wait_link() is PullerState.GATE
    assert confirmed.empty() and not machine.unconfirmed
    assert store.count_backlog(relay_db) == 1  # Still held, to run here.
    assert 'Not confirming 1 tasks upstream after an outage' in caplog.text


@mark.unit
def test_puller_stopping_while_the_link_is_down_ends(relay_db: Engine, no_signal, link_to) -> None:
    """A puller told to stop while it waits for the link ends rather than waiting on."""
    fake = FakeUpstream()
    link = link_to(fake)
    take_down(fake, link)
    machine = UpstreamPuller(queue=link, engine=relay_db, identity=upstream_identity(None), poll=POLL)
    assert machine.gate() is PullerState.WAIT_LINK
    machine.halt()
    assert machine.wait_link() is PullerState.FINAL


@mark.unit
def test_returner_holds_results_while_down_and_sends_them_first(relay_db: Engine, link_to) -> None:
    """While the link is down completed rows still move to the outbox; once it is back they go at once."""
    fake = FakeUpstream()
    link = link_to(fake)
    take_down(fake, link)
    task_id = complete_one(relay_db)
    machine = UpstreamReturner(queue=link, engine=relay_db, bundlesize=100, bundlewait=3600, poll=POLL,
                               retention=300)
    assert machine.collect() is ReturnerState.WAIT_LINK
    assert store.count_backlog(relay_db) == 0  # Moved to the outbox meanwhile.
    assert machine.wait_link() is ReturnerState.COLLECT
    assert machine.collect() is ReturnerState.WAIT_LINK

    fake.down = False
    assert until(lambda: link.is_up())
    assert machine.collect() is ReturnerState.WAIT_LINK  # Results wait for the heartbeat on the new link.
    link.passed('beat')
    assert machine.collect() is ReturnerState.SEND  # At once, though the bundle wait is an hour.
    assert machine.send() is ReturnerState.MARK
    assert not link.is_passed('drain')
    assert machine.mark() is ReturnerState.COLLECT
    assert link.is_passed('drain')
    assert [task['id'] for task in json.loads(fake.queues.completed.get_nowait())] == [task_id]


@mark.unit
def test_returner_send_lost_with_the_link_stays_pending(relay_db: Engine, link_to) -> None:
    """A bundle the link loses is not marked; it is sent again once the link is back."""
    fake = FakeUpstream()
    fake.queues.completed = FailsOnce()
    link = link_to(fake)
    link.passed('beat')
    task_id = complete_one(relay_db)
    machine = UpstreamReturner(queue=link, engine=relay_db, bundlesize=100, bundlewait=0, poll=POLL, retention=300)
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.WAIT_LINK
    assert store.count_held(relay_db) == 1
    assert until(lambda: link.is_up() and link.epoch == 2)
    link.passed('beat')
    assert machine.wait_link() is ReturnerState.COLLECT
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.MARK and machine.mark() is ReturnerState.COLLECT
    assert [task['id'] for task in json.loads(fake.queues.completed.get_nowait())] == [task_id]
    assert store.count_held(relay_db) == 0


@mark.unit
def test_heartbeat_waits_for_the_link_then_beats_and_passes_its_stage(link_to) -> None:
    """A beat lost with the link waits for it; the first beat on the new connection passes the beat stage."""
    fake = FakeUpstream()
    link = link_to(fake)
    machine = UpstreamHeartbeat(queue=link, identity=upstream_identity(None), heartrate=60)
    assert machine.submit() is HeartbeatState.WAIT and link.is_passed('beat')
    take_down(fake, link)
    assert machine.submit() is HeartbeatState.WAIT_LINK
    assert machine.wait_link() is HeartbeatState.WAIT_LINK
    fake.down = False
    assert until(lambda: link.is_up())
    assert not link.is_passed('beat')
    assert machine.wait_link() is HeartbeatState.SUBMIT
    assert machine.submit() is HeartbeatState.WAIT and link.is_passed('beat')
    assert fake.queues.heartbeat.qsize() == 2


# --- The relay's watch on its parts ---------------------------------------------------------------


class Part(Thread):
    """A stand-in for one of the relay's threads: it runs until ended, cleanly or with a failure."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.ending = threading.Event()
        self.failure: Optional[Exception] = None

    def run_with_exceptions(self) -> None:
        self.ending.wait(DEADLINE)
        if self.failure is not None:
            raise self.failure

    def end(self, failure: Optional[Exception] = None) -> None:
        """End the thread (with `failure`, if given) and wait for it, without raising."""
        self.failure = failure
        self.ending.set()
        threading.Thread.join(self, timeout=DEADLINE)
        assert not self.is_alive()


SITE_ORDER = ['scheduler', 'heartmonitor', 'receiver', 'confirm', 'server']
"""Threads of the relay-local server in the order a clean stop ends them (the server thread last)."""


@fixture
def watched() -> Iterator[Tuple[RelayThread, Dict[str, Part]]]:
    """A relay (not started) whose site and upstream parts are stand-in threads, all running."""
    parts = {name: Part(f'part-{name}') for name in [*SITE_ORDER, 'autoscaler', 'puller', 'returner', 'heartbeat']}
    for part in parts.values():
        part.start()
    server = parts['server']
    for name in SITE_ORDER[:-1]:
        setattr(server, name, parts[name])
    relay = object.__new__(RelayThread)  # Only the watch is exercised; nothing is built or started.
    relay.server = server
    relay.cluster = SimpleNamespace(autoscaler=parts['autoscaler'], join=lambda timeout=None: time.sleep(0.01),
                                    is_alive=lambda: True)
    relay.puller, relay.returner, relay.heartbeat = parts['puller'], parts['returner'], parts['heartbeat']
    try:
        yield relay, parts
    finally:
        for part in parts.values():
            part.end()


@mark.unit
def test_site_check_passes_a_running_site_and_a_clean_stop(watched) -> None:
    """Nothing is raised while every part runs, nor as a clean stop ends the site's threads in turn."""
    relay, parts = watched
    relay.check_site()
    parts['autoscaler'].end()
    for name in SITE_ORDER:
        parts[name].end()
        relay.check_site(stopping=True)


@mark.unit
@mark.parametrize('ended, running', [('heartmonitor', 'scheduler'), ('receiver', 'heartmonitor'),
                                     ('confirm', 'receiver'), ('server', 'confirm')])
def test_site_check_raises_a_thread_that_ended_out_of_turn(watched, ended: str, running: str) -> None:
    """A site thread that ends while the one before it still runs has died, stopping or not."""
    relay, parts = watched
    for name in SITE_ORDER[:SITE_ORDER.index(running)]:
        parts[name].end()
    parts[ended].end()
    for stopping in (False, True):
        with pytest.raises(RelayPartEnded, match=f'part-{ended} ended while part-{running} still runs'):
            relay.check_site(stopping=stopping)


@mark.unit
@mark.parametrize('name', [*SITE_ORDER, 'autoscaler'])
def test_site_check_raises_the_failure_that_ended_a_thread(watched, name: str) -> None:
    """A site thread that ended with a failure raises it, whatever else still runs."""
    relay, parts = watched
    parts[name].end(OSError(f'{name} failed'))
    with pytest.raises(OSError, match=f'{name} failed'):
        relay.check_site()


@mark.unit
def test_site_check_needs_the_autoscaler_until_the_site_stops(watched) -> None:
    """The autoscaler ending is a death while the site runs, and expected once it is told to stop."""
    relay, parts = watched
    parts['autoscaler'].end()
    with pytest.raises(RelayPartEnded, match='part-autoscaler ended'):
        relay.check_site()
    relay.check_site(stopping=True)


@mark.unit
@mark.parametrize('name', ['receiver', 'returner', 'heartbeat'])
def test_relay_wait_raises_a_part_that_died(watched, name: str) -> None:
    """While the relay waits on its site, a site or upstream thread that ends without a failure is raised."""
    relay, parts = watched
    parts[name].end()
    with pytest.raises(RelayPartEnded, match=f'part-{name} ended'):
        relay.wait_site()


@mark.unit
def test_relay_stop_raises_a_site_thread_that_died_meanwhile(watched) -> None:
    """While the site stops, a receiver that ends before the heart monitor has is raised, not waited on."""
    relay, parts = watched
    looks = iter(range(100))  # A site that never stops by itself, for a bounded number of looks.
    relay.cluster.is_alive = lambda: next(looks, None) is not None
    parts['heartmonitor'].machine = SimpleNamespace(startup_phase=False)
    parts['receiver'].end()
    with pytest.raises(RelayPartEnded, match='part-receiver ended while part-heartmonitor still runs'):
        relay.stop_site()


# --- The relay process -------------------------------------------------------------------------


def loopback_port() -> int:
    """A loopback port nothing listens on, for the relay-local server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start_relay(tmp_path: Path, port: int, auth: str = AUTH, *args: str) -> Tuple[subprocess.Popen, Path]:
    """Start `hs relay` against an upstream on loopback `port`, in its own process group.

    Its one site client is launched through `true`, which exits at once: the site never has a client.
    """
    site = tmp_path / 'site'
    site.mkdir(exist_ok=True)
    log = tmp_path / 'relay.log'
    env = {**os.environ, 'HYPERSHELL_SITE': str(site), 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
           'HYPERSHELL_CONFIG_FILE': '', 'HYPERSHELL_RELAY_UPSTREAM_AUTH': auth}
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    with open(log, mode='w') as stream:
        proc = subprocess.Popen(['hs', 'relay', '--bind', '127.0.0.1', '--port', str(loopback_port()),
                                 '-H', '127.0.0.1', '-p', str(port), '--no-tls', '-N', '1', '--launcher', 'true',
                                 *args],
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


@mark.integration
@mark.parametrize('size', ['0', '-1'])
def test_relay_refuses_a_site_of_no_clients(tmp_path: Path, size: str) -> None:
    """A relay with no site client would never ask for work; a size below 1 is refused at start."""
    proc, log = start_relay(tmp_path, closed_port(), AUTH, '-N', size)
    try:
        assert proc.wait(DEADLINE) == exit_status.bad_argument, log.read_text()
    finally:
        finish(proc)
    assert f'Site size must be at least 1 (given {size})' in log.read_text()
    assert READY not in log.read_text()


def closed_port() -> int:
    """A loopback port nothing listens on."""
    return loopback_port()
