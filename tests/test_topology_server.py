# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A server autoscaling with relay sites: it records advertisements, plans, and publishes a topology.

The heart monitor records each relay identity's advertised zone, bounds and launched count in a site
registry, and drops the record when the identity is evicted or finishes. The topology publisher plans
from the zones' loads on a throwaway database and publishes a new version only when its entries
change. Relays fetch their own entries from the dispatcher over a real queue connection; a server
that publishes nothing answers with a null version, and an older server has no such method at all.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict

# Standard libs
import os
import json
import time
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, MonkeyPatch
from sqlalchemy.engine import Engine
from cmdkit.app import exit_status

# Internal libs
import hypershell.server
from tests.test_relay_roster import queues, deliver, EVICT  # noqa: fixture
from tests.test_zone_model import task_db, add_tasks  # noqa: fixture
from hypershell.core import queue as queue_module
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.dispatch import Dispatcher
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.topology import Bounds, Entry, Topology, SiteRegistry, request_text, read_topology
from hypershell.core.uuid import uuid
from hypershell.cluster.topology import (TopologyPublisher, TopologyPublisherState, TopologyPublisherThread,
                                         AutoScalingServer, Distribution)
from hypershell.data.model import Zone, zone_id
from hypershell.server import HeartMonitor


AUTH = 'topology-server-key-0123456789'
BOUNDS = Bounds(min=0, max=3, init=0)


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch: MonkeyPatch) -> None:
    """Shorten the server's queue-operation timeout so an empty queue returns quickly."""
    monkeypatch.setattr(hypershell.server, 'DEFAULT_REMOTE_TIMEOUT', 0.05)


def site_beat(identity: str, site: str = 'A', zone: str = 'cpu', bounds: Bounds = BOUNDS, clients: int = 0,
              state: ClientState = ClientState.RUNNING, time: datetime = None, host: str = 'relay-host') -> Heartbeat:
    """A relay identity's beat with its advertisement."""
    return Heartbeat.new(uuid=identity, host=host, state=state, site=site, zone=zone, bounds=bounds,
                         clients=clients, time=time)


def monitor(queues: SimpleNamespace, sites: SiteRegistry = None) -> HeartMonitor:
    """An upstream heart monitor on the stand-in queues, recording into `sites`."""
    return HeartMonitor(queue=queues, evict_after=EVICT, sites=sites)


# --- The heart monitor records advertisements --------------------------------------------------------

@mark.integration
def test_advertised_beats_populate_the_registry(queues, task_db) -> None:
    """Each advertised beat overwrites its identity's record in place; a new identity registers newer."""
    registry = SiteRegistry()
    machine = monitor(queues, registry)
    first, second = str(uuid()), str(uuid())
    deliver(machine, site_beat(first, clients=0))
    deliver(machine, site_beat(first, clients=2, bounds=Bounds(min=0, max=1, init=0)))
    deliver(machine, site_beat(second, site='B', zone='gpu'))
    records = {record.identity: record for record in registry.snapshot()}
    assert set(records) == {first, second}
    assert records[first].clients == 2 and records[first].bounds.max == 1
    assert records[first].registered < records[second].registered
    assert (records[second].site, records[second].zone, records[second].rank) == ('B', 'gpu', 1)


@mark.integration
def test_eviction_and_finish_drop_the_record(queues, task_db) -> None:
    """An identity evicted for missed beats, or finishing, leaves the registry; others stay."""
    registry = SiteRegistry()
    machine = monitor(queues, registry)
    stale, done, live = str(uuid()), str(uuid()), str(uuid())
    deliver(machine, site_beat(stale, time=datetime.now().astimezone() - timedelta(seconds=3 * EVICT)))
    deliver(machine, site_beat(done))
    deliver(machine, site_beat(live))
    deliver(machine, site_beat(done, state=ClientState.FINISHED))
    assert done not in registry
    machine.last_check = datetime.now().astimezone()
    machine.check_clients()
    assert [record.identity for record in registry.snapshot()] == [live]


@mark.integration
def test_old_relays_and_ordinary_clients_are_ignored(queues, task_db) -> None:
    """A relay beat without an advertisement and an ordinary client's beat record nothing."""
    registry = SiteRegistry()
    machine = monitor(queues, registry)
    old, plain = str(uuid()), str(uuid())
    deliver(machine, Heartbeat.new(uuid=old, host='old-relay', site='A', roster=[]))
    deliver(machine, Heartbeat.new(uuid=plain, host='plain-host'))
    assert len(registry) == 0
    assert set(machine.beats) == {old, plain}


@mark.integration
def test_without_a_registry_nothing_is_recorded(queues, task_db) -> None:
    """A heart monitor with no registry (every server not autoscaling with sites) treats beats as today."""
    machine = monitor(queues)
    identity = str(uuid())
    deliver(machine, site_beat(identity))
    deliver(machine, site_beat(identity, state=ClientState.FINISHED))
    assert machine.sites is None and not machine.beats


# --- The dispatcher serves the topology ----------------------------------------------------------------

def loopback(port: int = 0) -> QueueConfig:
    """Loopback queue configuration without TLS."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2)


@mark.integration
def test_dispatcher_serves_the_topology_over_the_wire() -> None:
    """A relay fetches null before anything is published, its own entries after, and zero for a zone the
    topology does not name (an older duplicate); a request it cannot read is refused."""
    with QueueServer(loopback()) as server:
        client = QueueClient(loopback(server.address[1]))
        client.connect()
        ask = request_text('A', ['a-cpu', 'a-gpu'])
        assert read_topology(client.dispatcher.topology(ask)) is None
        server.dispatcher.publish(Topology('srv:1', {'a-cpu': Entry('cpu', 2, 1), 'b-cpu': Entry('cpu', 1, 0)}))
        reply = read_topology(client.dispatcher.topology(ask))
        assert reply.version == 'srv:1'
        assert reply.assignment('cpu') == Entry('cpu', 2, 1)
        assert reply.assignment('gpu') == Entry('gpu', 0, 0)
        refused = client.dispatcher.topology('not json')
        assert json.loads(refused)['error'] == 'request'
        with pytest.raises(ValueError, match='refused'):
            read_topology(refused)
        server.dispatcher.publish(None)
        assert read_topology(client.dispatcher.topology(ask)) is None


@mark.integration
def test_work_requests_are_unchanged_by_a_topology() -> None:
    """A published topology adds nothing to a work request's reply."""
    with QueueServer(loopback()) as server:
        server.dispatcher.publish(Topology('srv:1', {'a-cpu': Entry('cpu', 2, 1)}))
        client = QueueClient(loopback(server.address[1]))
        client.connect()
        reply = json.loads(client.dispatcher.request(json.dumps({'protocol': 1, 'client': 'c', 'host': 'h',
                                                                 'zone': None, 'size': 1, 'wait': 0})))
        assert reply == {'protocol': 1, 'tasks': [], 'shutdown': False}


# --- The publisher ----------------------------------------------------------------------------------------

def publisher(registry: SiteRegistry, dispatcher: Dispatcher, **options: Any) -> TopologyPublisher:
    """A publisher over `registry` onto `dispatcher`, reading the group from the database."""
    return TopologyPublisher(dispatcher, registry, **{'policy': 'fixed', 'period': 3600, **options})


def run_check(machine: TopologyPublisher) -> TopologyPublisherState:
    """One check, followed by the publish it asks for; return the check's state."""
    state = machine.check()
    if state is TopologyPublisherState.PUBLISH:
        assert machine.publish() is TopologyPublisherState.WAIT
    return state


def fetched(dispatcher: Dispatcher, *identities: str) -> Dict[str, Any]:
    """The reply a relay with `identities` reads."""
    return json.loads(dispatcher.topology(request_text('A', list(identities))))


@mark.integration
def test_publisher_plans_from_the_database_and_publishes_once_per_change(task_db: Engine) -> None:
    """With cpu work in group 0 and gpu work gated in group 1, the first check publishes one cpu client at the
    first site and the gpu floor at the gpu site; the same inputs publish no new version, only the count of
    checks under the same one; a changed report publishes a new version."""
    Zone.ensure('cpu')
    Zone.ensure('gpu')
    add_tasks(5, zone_id('cpu'), group=0)
    add_tasks(3, zone_id('gpu'), group=1)
    registry = SiteRegistry()
    registry.record('a-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=0, host='ha')
    registry.record('b-cpu', site='B', zone='cpu', bounds=BOUNDS, clients=0, host='hb')
    registry.record('b-gpu', site='B', zone='gpu', bounds=Bounds(min=1, max=2, init=1), clients=0, host='hb')
    registry.record('b-mem', site='B', zone='highmem', bounds=BOUNDS, clients=0, host='hb')
    dispatcher = Dispatcher()
    machine = publisher(registry, dispatcher)
    assert machine.start() is TopologyPublisherState.CHECK
    assert run_check(machine) is TopologyPublisherState.PUBLISH
    topology = dispatcher.published
    assert topology.version.endswith(':1') and topology.checks == 1
    assert topology.entries == {'a-cpu': Entry('cpu', 1, 0), 'b-cpu': Entry('cpu', 0, 0),
                                'b-gpu': Entry('gpu', 1, 0), 'b-mem': Entry('highmem', 0, 0)}
    assert run_check(machine) is TopologyPublisherState.WAIT
    assert dispatcher.published == Topology(topology.version, topology.entries, checks=2)
    assert fetched(dispatcher, 'a-cpu')['checks'] == 2
    registry.record('a-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=1, host='ha')
    assert run_check(machine) is TopologyPublisherState.PUBLISH
    assert dispatcher.published.version.endswith(':2') and dispatcher.published.checks == 3
    assert dispatcher.published.entries['a-cpu'] == Entry('cpu', 1, 1)
    assert fetched(dispatcher, 'b-gpu', 'b-cpu')['zones'] == {'gpu': {'target': 1, 'seen': 0},
                                                             'cpu': {'target': 0, 'seen': 0}}


@mark.integration
def test_publisher_fills_in_the_listed_order(task_db: Engine) -> None:
    """Under fill with B listed first, B's cpu takes the growth although A registered first."""
    Zone.ensure('cpu')
    add_tasks(5, zone_id('cpu'), group=0)
    registry = SiteRegistry()
    registry.record('a-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=0, host='ha')
    registry.record('b-cpu', site='B', zone='cpu', bounds=BOUNDS, clients=0, host='hb')
    dispatcher = Dispatcher()
    machine = publisher(registry, dispatcher, distribution=Distribution.FILL, order=['B'])
    run_check(machine)
    assert dispatcher.published.entries == {'a-cpu': Entry('cpu', 0, 0), 'b-cpu': Entry('cpu', 1, 0)}


@mark.integration
def test_publisher_warns_once_per_duplicate(task_db: Engine, caplog) -> None:
    """Two live identities advertising one site's zone are named in one warning; the older gets zero, and
    the newer is planned on its own clients (its floor here), not held back by the older's."""
    registry = SiteRegistry()
    registry.record('a1-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=2, host='h1')
    registry.record('a2-cpu', site='A', zone='cpu', bounds=Bounds(min=1, max=1, init=1), clients=0, host='h2')
    dispatcher = Dispatcher()
    machine = publisher(registry, dispatcher)
    with caplog.at_level('WARNING', logger='hypershell.cluster'):
        run_check(machine)
        run_check(machine)
    warnings = [r.getMessage() for r in caplog.records if 'advertised by two relays' in r.getMessage()]
    assert len(warnings) == 1
    assert 'a1-cpu on h1' in warnings[0] and 'a2-cpu on h2' in warnings[0]
    assert dispatcher.published.entries == {'a1-cpu': Entry('cpu', 0, 2), 'a2-cpu': Entry('cpu', 1, 0)}


@mark.integration
def test_publisher_warns_once_per_duplicated_relay(task_db: Engine, caplog) -> None:
    """Two relays advertising one site with an instance key each: one warning names both relays, and
    every identity of the older relay is assigned zero, including a zone the newer relay does not serve."""
    registry = SiteRegistry()
    registry.record('a1-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=2, host='h1', instance='a1-cpu')
    registry.record('a1-mem', site='A', zone='highmem', bounds=BOUNDS, clients=1, host='h1', instance='a1-cpu')
    registry.record('a2-cpu', site='A', zone='cpu', bounds=BOUNDS, clients=0, host='h2', instance='a2-cpu')
    dispatcher = Dispatcher()
    machine = publisher(registry, dispatcher)
    with caplog.at_level('WARNING', logger='hypershell.cluster'):
        run_check(machine)
        run_check(machine)
    warnings = [r.getMessage() for r in caplog.records if 'advertised by two relays' in r.getMessage()]
    assert len(warnings) == 1
    assert 'older: a1-cpu on h1' in warnings[0] and 'newer: a2-cpu on h2' in warnings[0]
    assert 'zone:' not in warnings[0]
    published = dispatcher.published.entries
    assert published['a1-cpu'] == Entry('cpu', 0, 2) and published['a1-mem'] == Entry('highmem', 0, 1)
    assert published['a2-cpu'].zone == 'cpu'


@mark.integration
def test_heart_monitor_records_the_relay_instance(queues, task_db) -> None:
    """A beat's instance key is recorded with its identity; a beat without one records none."""
    registry = SiteRegistry()
    machine = monitor(queues, registry)
    keyed, plain = str(uuid()), str(uuid())
    deliver(machine, Heartbeat.new(uuid=keyed, host='relay-host', site='A', zone='cpu', bounds=BOUNDS,
                                   clients=0, instance='relay-1'))
    deliver(machine, site_beat(plain))
    instances = {record.identity: record.instance for record in registry.snapshot()}
    assert instances == {keyed: 'relay-1', plain: None}


@mark.integration
def test_publisher_with_no_sites_publishes_an_empty_topology(task_db: Engine) -> None:
    """With no live relay the topology is published empty, so every relay that asks reads zero."""
    add_tasks(2, group=0)
    dispatcher = Dispatcher()
    machine = publisher(SiteRegistry(), dispatcher)
    assert run_check(machine) is TopologyPublisherState.PUBLISH
    assert dispatcher.published.entries == {}
    assert fetched(dispatcher, 'a-cpu')['zones'] == {}


@mark.integration
def test_publisher_thread_stops_within_its_wait(task_db: Engine) -> None:
    """The publisher's waits are bounded, so stopping its thread returns promptly."""
    thread = TopologyPublisherThread(Dispatcher(), SiteRegistry(), policy='dynamic', period=3600)
    thread.start()
    time.sleep(0.5)
    start = time.monotonic()
    thread.stop(wait=True, timeout=10)
    assert not thread.is_alive()
    assert time.monotonic() - start < 3


# --- The server --------------------------------------------------------------------------------------------

@mark.unit
def test_autoscaling_server_wires_one_registry() -> None:
    """The server's heart monitor records into the registry the publisher reads, on the server's dispatcher."""
    server = AutoScalingServer(address=('127.0.0.1', 0), auth=AUTH, policy='dynamic', period=5,
                               distribution='fill', sites='B,A')
    assert server.server.sites is server.registry
    assert server.server.heartmonitor.machine.sites is server.registry
    machine = server.publisher.machine
    assert machine.registry is server.registry
    assert machine.dispatcher is server.server.queue.dispatcher
    assert (machine.distribution, machine.order) == (Distribution.FILL, ['B', 'A'])
    with pytest.raises(ValueError, match='site distribution'):
        AutoScalingServer(address=('127.0.0.1', 0), auth=AUTH, distribution='even')


@mark.unit
def test_autoscaling_server_publishes_before_it_listens() -> None:
    """A relay connecting before the first check reads an empty topology, never none, so it launches nothing."""
    server = AutoScalingServer(address=('127.0.0.1', 0), auth=AUTH, policy='dynamic', period=5)
    dispatcher = server.server.queue.dispatcher
    assert dispatcher.published.entries == {} and dispatcher.published.version.endswith(':0')
    reply = read_topology(dispatcher.topology(request_text('A', ['a-cpu'])))
    assert reply.assignment('cpu') == Entry('cpu', 0, 0)


@mark.integration
def test_unknown_distribution_names_its_source(temp_site: Path) -> None:
    """An unknown `[autoscale] distribution` is refused before the server starts, naming where it was set."""
    env = {**os.environ, 'HYPERSHELL_AUTOSCALE_DISTRIBUTION': 'even'}
    proc = subprocess.Popen(['hs', 'server', '--autoscaling', '-k', AUTH, '--no-tls', '-H', '127.0.0.1'],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        _, stderr = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    assert proc.returncode == exit_status.bad_config
    assert 'even' in stderr.decode() and 'HYPERSHELL_AUTOSCALE_DISTRIBUTION' in stderr.decode()


@mark.integration
def test_old_server_has_no_topology_method(monkeypatch: MonkeyPatch) -> None:
    """A server exposing the dispatcher surface of an earlier release hands a relay a proxy with no
    `topology`, which the relay detects locally with no round trip."""
    monkeypatch.setattr(queue_module, 'DISPATCH_EXPOSED', ('request', 'takes_submissions'))
    with QueueServer(loopback()) as server:
        client = QueueClient(loopback(server.address[1]))
        client.connect()
        assert not hasattr(client.dispatcher, 'topology')
        assert isinstance(client.dispatcher.takes_submissions(), bool)
