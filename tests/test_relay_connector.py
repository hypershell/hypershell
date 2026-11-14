# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's upstream pull: identity, the backlog gate, the durable insert and the confirmation.

The puller is driven one action at a time against a fake upstream (a scripted dispatcher and plain
queues) and a throwaway relay database, with no threads and no sleeps. One test serves the relay's
request from a real queue server's dispatcher, to show the request is an ordinary client's. A relay
serving several zones is also driven against a real dispatcher that is finishing, with and without an
upstream database, to show a zone with no site client takes what is still staged for it, and hands it
back once the topology gives it no client at the relay before one starts.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional

# Standard libs
import json
import logging
import sqlite3
from uuid import UUID, uuid5
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.core.logging import INSTANCE, HOSTNAME
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig, QueueVersionError, PROTOCOL
from hypershell.client import ClientInfo
from hypershell.data.model import Entity, Task, Client, zone_id, DEFAULT_ZONE_ID
from hypershell.relay import store
from hypershell.relay.store import ensure_tables
from hypershell.core.dispatch import Dispatcher
from hypershell.relay.upstream import (UpstreamPuller, PullerState, RelayInsertError, upstream_identity,
                                       served_zones, INSERT_ATTEMPTS, ZONE_REQUEST_WAIT, DEFAULT_REQUEST_WAIT)
from hypershell.relay.site import TopologyView
from hypershell.core.topology import Bounds, Entry, TopologyReply
from hypershell.cluster.demand import AutoScalerPolicy, ScalingKnobs, ZoneLoad, ZoneRef
from hypershell.cluster.topology import SiteView, SiteZone, parse_distribution, plan_sites


# The upstream server's identity and an offset unlike the relay's, so a stamp's text is not the relay's.
UPSTREAM_SERVER = (str(uuid()), 'upstream-host')
UPSTREAM_TZ = timezone(timedelta(hours=5, minutes=30))

POLL = 0.01  # Seconds the puller waits at its gate in these tests.


@fixture
def relay_db(tmp_path: Path) -> Iterator[Engine]:
    """A fresh relay database: the task tables and the relay-only tables."""
    engine = create_engine(f'sqlite:///{tmp_path / "relay.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    ensure_tables(engine)
    try:
        yield engine
    finally:
        engine.dispose()


class FakeDispatcher:
    """Answers each request with the next scripted reply, recording the requests."""

    def __init__(self, *replies: dict) -> None:
        self.replies = list(replies)
        self.requests: List[dict] = []

    def request(self, text_: str) -> str:
        assert isinstance(text_, str)
        self.requests.append(json.loads(text_))
        reply = self.replies.pop(0) if self.replies else {'protocol': PROTOCOL, 'tasks': [], 'shutdown': False}
        return json.dumps(reply)


def upstream(dispatcher: FakeDispatcher, confirmed: Optional[Any] = None) -> SimpleNamespace:
    """A fake upstream connection."""
    return SimpleNamespace(dispatcher=dispatcher, confirmed=confirmed or Queue(), heartbeat=Queue())


def puller(engine: Engine, dispatcher: FakeDispatcher, zone: Optional[str] = None, bundlesize: int = 1000,
           local_bundlesize: int = 1, confirmed: Optional[Any] = None) -> UpstreamPuller:
    """A puller on a fake upstream and the relay database."""
    return UpstreamPuller(queue=upstream(dispatcher, confirmed), engine=engine, identity=upstream_identity(zone),
                          zone=zone, bundlesize=bundlesize, local_bundlesize=local_bundlesize, poll=POLL)


def dispatched(*args: str, zone: Optional[str] = None, stamp: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Task records as an upstream server sends them: stamped by that server, decoded from JSON text."""
    records = []
    for arg in args:
        task = Task.new(args=arg, tag={'site': 'a'}, group=3, zone=zone_id(zone))
        task.server_id, task.server_host = UPSTREAM_SERVER
        task.schedule_time = stamp or datetime.now(UPSTREAM_TZ)
        records.append(json.loads(json.dumps(task.to_json())))
    return records


def reply(records: List[Dict[str, Any]], shutdown: bool = False) -> dict:
    """A work-request reply carrying `records`."""
    return {'protocol': PROTOCOL, 'tasks': records, 'shutdown': shutdown}


def connect_clients(engine: Engine, count: int, server_id: str = INSTANCE) -> None:
    """Register `count` connected site clients with the relay-local server."""
    with engine.begin() as conn:
        for _ in range(count):
            conn.execute(Client.__table__.insert().values(
                id=uuid(), host='site-node', server_id=server_id, server_host=HOSTNAME,
                connected_at=datetime.now().astimezone(), disconnected_at=None, evicted=False))


def add_backlog(engine: Engine, count: int) -> List[str]:
    """Put `count` incomplete tasks in the relay database."""
    records = dispatched(*[f'echo backlog-{n}' for n in range(count)])
    store.insert_received(engine, records, identity=upstream_identity(None))
    return [record['id'] for record in records]


def rows(engine: Engine, sql: str) -> List[tuple]:
    """Rows read on a new connection to the relay file, so only committed data is seen."""
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute(sql).fetchall()


# --- Identity ---------------------------------------------------------------------------------


@mark.unit
def test_identity_is_one_per_zone_from_the_relay_instance() -> None:
    """The upstream identity is derived from the relay instance and the zone name."""
    assert upstream_identity('gpu') == str(uuid5(UUID(INSTANCE), 'gpu'))
    assert upstream_identity('gpu') == upstream_identity('gpu')
    assert upstream_identity('gpu') != upstream_identity('cpu')
    assert upstream_identity(None) == upstream_identity('default') == str(uuid5(UUID(INSTANCE), 'default'))
    assert upstream_identity('gpu', instance=str(uuid())) != upstream_identity('gpu')


# --- The gate ----------------------------------------------------------------------------------


@mark.unit
def test_no_request_without_site_clients(relay_db: Engine) -> None:
    """With no connected site client the bound is zero, so no request is sent."""
    fake = FakeDispatcher()
    machine = puller(relay_db, fake)
    assert machine.gate() is PullerState.GATE
    assert fake.requests == []


@mark.unit
def test_no_request_above_the_low_water_mark_then_the_whole_gap(relay_db: Engine) -> None:
    """The bound is connected clients times the local bundle size, twice over; above half of it nothing is
    requested, and at or below half the whole gap is asked for at once (one large request, not a trickle)."""
    connect_clients(relay_db, 2)
    connect_clients(relay_db, 3, server_id=str(uuid()))  # Another relay-local server's clients do not count.
    ids = add_backlog(relay_db, 5)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake, local_bundlesize=2)
    assert machine.bound() == 8
    assert machine.gate() is PullerState.GATE  # Backlog 5, above the low-water mark 4.
    with relay_db.begin() as conn:
        conn.execute(Task.__table__.update().where(Task.__table__.c.id == ids[0])
                     .values(completion_time=datetime.now().astimezone(), exit_status=0))
    assert machine.gate() is PullerState.REQUEST  # Backlog 4, at the low-water mark.
    assert fake.requests == []
    assert machine.request() is PullerState.GATE
    request, = fake.requests
    assert request['size'] == 4  # The gap to the bound, not the one task that just finished.


@mark.unit
def test_request_never_exceeds_the_upstream_bundle_size(relay_db: Engine) -> None:
    """A gap larger than the advertised upstream bundle size is asked for one bundle at a time."""
    connect_clients(relay_db, 10)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake, local_bundlesize=8, bundlesize=50)
    assert machine.gate() is PullerState.REQUEST
    machine.request()
    assert [request['size'] for request in fake.requests] == [50]


@mark.unit
def test_disconnected_clients_do_not_count(relay_db: Engine) -> None:
    """A site client that disconnected is not part of the bound."""
    connect_clients(relay_db, 1)
    with relay_db.begin() as conn:
        conn.execute(Client.__table__.update().values(disconnected_at=datetime.now().astimezone()))
    fake = FakeDispatcher()
    assert puller(relay_db, fake).gate() is PullerState.GATE
    assert fake.requests == []


@mark.unit
def test_request_size_is_capped_by_the_upstream_bundle_size(relay_db: Engine) -> None:
    """Below a large bound the request asks for at most the upstream bundle size."""
    connect_clients(relay_db, 50)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake, local_bundlesize=10, bundlesize=7)
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.GATE
    assert fake.requests[0]['size'] == 7


@mark.unit
def test_request_is_an_ordinary_work_request(relay_db: Engine) -> None:
    """The request names the protocol, the zone identity, this host, the zone and the size."""
    connect_clients(relay_db, 3)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake, zone='gpu', local_bundlesize=2)
    assert machine.gate() is PullerState.REQUEST
    machine.request()
    request, = fake.requests
    assert request['protocol'] == PROTOCOL
    assert request['client'] == upstream_identity('gpu') != INSTANCE
    assert request['host'] == HOSTNAME
    assert request['zone'] == 'gpu'
    assert request['size'] == 12  # Three clients, bundles of two, twice over.
    assert 0 < request['wait'] <= 2


@mark.unit
def test_default_zone_request_names_no_zone(relay_db: Engine) -> None:
    """A relay serving the default zone asks as a client with no zone does."""
    connect_clients(relay_db, 1)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake)
    machine.gate()
    machine.request()
    assert fake.requests[0]['zone'] is None
    assert fake.requests[0]['client'] == upstream_identity(None)


# --- Insert, then confirm ----------------------------------------------------------------------


class CheckedQueue(Queue):
    """A confirmation queue that records, at each put, what a new connection to the relay file reads."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.seen: List[tuple] = []

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None) -> None:
        ids = ClientInfo.unpack_or_none(item).task_ids
        marks = ', '.join(f"'{id}'" for id in ids)
        self.seen.append((rows(self.engine, f'SELECT count(*) FROM task WHERE id IN ({marks})')[0][0],
                          rows(self.engine, f'SELECT count(*) FROM relay_task WHERE id IN ({marks})')[0][0]))
        super().put(item, block, timeout)


@mark.unit
def test_confirm_follows_the_insert_commit(relay_db: Engine) -> None:
    """Received tasks are committed to the relay database before they are confirmed, under the zone identity."""
    connect_clients(relay_db, 3)
    records = dispatched('echo a', 'echo b', 'echo c', zone='gpu')
    confirmed = CheckedQueue(relay_db)
    machine = puller(relay_db, FakeDispatcher(reply(records)), zone='gpu', confirmed=confirmed)
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.INSERT
    assert confirmed.empty()
    assert machine.insert() is PullerState.CONFIRM
    assert confirmed.empty()
    assert machine.confirm() is PullerState.GATE
    assert confirmed.seen == [(3, 3)]
    info = ClientInfo.unpack_or_none(confirmed.get_nowait())
    assert info.client_id == upstream_identity('gpu')
    assert info.client_host == HOSTNAME
    assert info.task_ids == [record['id'] for record in records]


@mark.unit
def test_insert_keeps_the_task_and_the_upstream_stamp_verbatim(relay_db: Engine) -> None:
    """The local row is the upstream task, unscheduled; the upstream stamp is kept as the text it came as."""
    record, = dispatched('echo kept', zone='gpu')
    store.insert_received(relay_db, [record], identity=upstream_identity('gpu'))

    (args, group, zone, tag, attempt, submit_id, fingerprint, schedule_time, server_id, server_host,
     client_id, client_host, submit_time) = rows(relay_db, (
        'SELECT args, "group", zone, tag, attempt, submit_id, fingerprint, schedule_time, server_id, '
        f"server_host, client_id, client_host, submit_time FROM task WHERE id = '{record['id']}'"))[0]
    assert (args, group, zone, json.loads(tag), attempt) == ('echo kept', 3, zone_id('gpu'), {'site': 'a'}, 1)
    assert (submit_id, fingerprint) == (record['submit_id'], record['fingerprint'])
    assert (schedule_time, server_id, server_host, client_id, client_host) == (None, None, None, None, None)
    local = datetime.fromisoformat(record['submit_time']).astimezone().replace(tzinfo=None)
    assert datetime.fromisoformat(submit_time) == local

    stamp, server, host, identity = rows(relay_db, 'SELECT schedule_time, server_id, server_host, identity '
                                                   f"FROM relay_task WHERE id = '{record['id']}'")[0]
    assert stamp == record['schedule_time']
    assert stamp.endswith('+05:30')
    assert (server, host) == UPSTREAM_SERVER
    assert identity == upstream_identity('gpu')


@mark.unit
def test_inserted_task_is_scheduled_by_the_local_server(relay_db: Engine) -> None:
    """An inserted row reads as new to the relay-local scheduler, in its zone."""
    from hypershell.data.core import Session, engine as primary
    record, = dispatched('echo local', zone='gpu')
    store.insert_received(relay_db, [record], identity=upstream_identity('gpu'))
    Session.remove()
    Session.configure(bind=relay_db)
    try:
        assert [task.id for task in Task.select_new(limit=10, zone=zone_id('gpu'))] == [record['id']]
        assert Task.select_new(limit=10, zone=DEFAULT_ZONE_ID) == []
    finally:
        Session.remove()
        Session.configure(bind=primary)


@mark.unit
def test_redispatched_id_upserts_and_refreshes_its_stamp(relay_db: Engine) -> None:
    """A task sent again (reverted and re-dispatched upstream) replaces its row and its upstream stamp."""
    first, = dispatched('echo again', stamp=datetime(2026, 11, 5, 10, 0, tzinfo=UPSTREAM_TZ))
    store.insert_received(relay_db, [first], identity=upstream_identity(None))
    with relay_db.begin() as conn:  # The relay-local server ran it meanwhile.
        conn.execute(Task.__table__.update().where(Task.__table__.c.id == first['id']).values(
            schedule_time=datetime.now().astimezone(), server_id=INSTANCE, server_host=HOSTNAME,
            client_id=str(uuid()), client_host='site-node', start_time=datetime.now().astimezone()))

    second = dict(first, schedule_time=datetime(2026, 11, 5, 11, 0, tzinfo=UPSTREAM_TZ).isoformat(sep=' '),
                  server_id=str(uuid()), server_host='upstream-restarted')
    store.insert_received(relay_db, [second], identity=upstream_identity(None))

    assert rows(relay_db, 'SELECT schedule_time, server_id, client_id, start_time FROM task') == [
        (None, None, None, None)]
    assert rows(relay_db, 'SELECT id, schedule_time, server_id, server_host FROM relay_task') == [
        (first['id'], second['schedule_time'], second['server_id'], 'upstream-restarted')]


@mark.unit
def test_failed_insert_is_never_confirmed(relay_db: Engine) -> None:
    """An insert that fails leaves nothing behind and is never confirmed; repeated failure ends the puller."""
    with relay_db.begin() as conn:
        conn.execute(text('DROP TABLE relay_task'))  # The second statement of the insert fails.
    connect_clients(relay_db, 2)
    records = dispatched('echo a', 'echo b')
    confirmed = Queue()
    machine = puller(relay_db, FakeDispatcher(reply(records)), confirmed=confirmed)
    assert machine.gate() is PullerState.REQUEST
    assert machine.request() is PullerState.INSERT
    for _ in range(INSERT_ATTEMPTS - 1):
        assert machine.insert() is PullerState.INSERT
    with pytest.raises(RelayInsertError):
        machine.insert()
    assert confirmed.empty()
    assert rows(relay_db, 'SELECT count(*) FROM task') == [(0, )]


@mark.unit
def test_insert_recovers_after_a_transient_failure(relay_db: Engine, monkeypatch) -> None:
    """A failed insert is tried again, and confirmed only once it commits."""
    from sqlalchemy.exc import OperationalError
    connect_clients(relay_db, 1)
    records = dispatched('echo a')
    calls = []
    real = store.insert_received

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError('INSERT', {}, Exception('database is locked'))
        return real(*args, **kwargs)

    monkeypatch.setattr(store, 'insert_received', flaky)
    confirmed = Queue()
    machine = puller(relay_db, FakeDispatcher(reply(records)), confirmed=confirmed)
    machine.gate()
    assert machine.request() is PullerState.INSERT
    assert machine.insert() is PullerState.INSERT
    assert confirmed.empty()
    assert machine.insert() is PullerState.CONFIRM
    assert machine.confirm() is PullerState.GATE
    assert ClientInfo.unpack_or_none(confirmed.get_nowait()).task_ids == [records[0]['id']]


# --- Version errors ----------------------------------------------------------------------------


@mark.unit
def test_error_reply_raises_the_client_version_error(relay_db: Engine) -> None:
    """An error reply raises the version error a client gives, with the server's message."""
    connect_clients(relay_db, 1)
    fake = FakeDispatcher({'protocol': 2, 'error': 'protocol', 'server_version': '9.9.9',
                           'message': 'work-request protocol 1 is not supported (this server speaks protocol 2)'})
    machine = puller(relay_db, fake)
    machine.gate()
    with pytest.raises(QueueVersionError) as error:
        machine.request()
    assert 'Server refused the work request' in str(error.value)
    assert 'protocol 2' in str(error.value) and f'protocol {PROTOCOL}' in str(error.value)


# --- A real dispatcher ---------------------------------------------------------------------------


@mark.integration
def test_real_dispatcher_serves_the_relay_as_a_client(relay_db: Engine) -> None:
    """A queue server's dispatcher serves the relay's request and holds its demand under the zone identity."""
    config = QueueConfig(host='127.0.0.1', port=0, auth='relay-connector-key-0123456789', size=2)
    records = dispatched('echo a', 'echo b', 'echo c', zone='gpu')
    connect_clients(relay_db, 2)
    with QueueServer(config) as server:
        server.dispatcher.stage(zone_id('gpu'), [(record['id'], json.dumps(record)) for record in records])
        client = QueueClient(QueueConfig(host='127.0.0.1', port=server.address[1], auth=config.auth, size=2))
        client.connect()
        machine = UpstreamPuller(queue=client, engine=relay_db, identity=upstream_identity('gpu'), zone='gpu',
                                 bundlesize=1000, local_bundlesize=1, poll=POLL)
        assert machine.gate() is PullerState.REQUEST
        assert machine.request() is PullerState.INSERT
        assert server.dispatcher.demand == {upstream_identity('gpu'): (zone_id('gpu'), 4)}  # Two clients, twice.
        assert machine.insert() is PullerState.CONFIRM
        assert machine.confirm() is PullerState.GATE
        info = ClientInfo.unpack_or_none(server.confirmed.get(timeout=5))
        assert info.client_id == upstream_identity('gpu')
        assert info.task_ids == [record['id'] for record in records]
        assert machine.gate() is PullerState.GATE  # Three held: above the low-water mark (two).
    stamps = dict(rows(relay_db, 'SELECT id, schedule_time FROM relay_task'))
    assert stamps == {record['id']: record['schedule_time'] for record in records}



# --- Several zones -----------------------------------------------------------------------------


ZONES = ('cpu', 'gpu')


def zoned_puller(engine: Engine, remote: Any, clients: Dict[Optional[str], int], zones=ZONES,
                 confirmed: Optional[Any] = None) -> UpstreamPuller:
    """A puller for every zone in `zones` on `remote`; `clients` counts the site clients serving each zone."""
    queue = remote if confirmed is None else SimpleNamespace(dispatcher=remote.dispatcher, confirmed=confirmed)
    return UpstreamPuller(queue=queue, engine=engine, identity=upstream_identity(zones[0]), zone=zones[0],
                          bundlesize=1000, local_bundlesize=1, poll=POLL, zones=served_zones(zones),
                          connected=lambda zone: clients.get(zone, 0))


def pull_once(machine: UpstreamPuller) -> PullerState:
    """Run the puller from its gate through one request, and its insert and confirmation if any came back."""
    state = machine.gate()
    if state is not PullerState.REQUEST:
        return state
    state = machine.request()
    if state is PullerState.INSERT:
        assert machine.insert() is PullerState.CONFIRM
        state = machine.confirm()
    return state


@mark.unit
def test_each_zone_requests_independently(relay_db: Engine) -> None:
    """Each zone asks under its own identity, bounded by its own fleet; upstream holds one demand per identity.

    The upstream side is a real dispatcher, as the upstream server runs it, untouched: it holds the
    relay's demand for each zone as it would hold two clients', and cuts each zone's tasks for that
    zone's identity only. Each confirmation names the identity that asked.
    """
    dispatcher = Dispatcher(bundlesize=1, queuesize=1)
    for zone, count in (('cpu', 3), ('gpu', 5)):
        records = dispatched(*[f'echo {zone}-{n}' for n in range(count)], zone=zone)
        dispatcher.stage(zone_id(zone), [(record['id'], json.dumps(record)) for record in records])
    confirmed = Queue()
    machine = zoned_puller(relay_db, SimpleNamespace(dispatcher=dispatcher), {'cpu': 1, 'gpu': 2},
                           confirmed=confirmed)
    assert machine.wait_request == ZONE_REQUEST_WAIT < DEFAULT_REQUEST_WAIT
    assert pull_once(machine) is PullerState.GATE
    assert pull_once(machine) is PullerState.GATE
    cpu, gpu = upstream_identity('cpu'), upstream_identity('gpu')
    assert dispatcher.demand == {cpu: (zone_id('cpu'), 2), gpu: (zone_id('gpu'), 4)}  # Clients x local bundle x 2.

    infos = [ClientInfo.unpack_or_none(confirmed.get_nowait()) for _ in range(2)]
    assert [info.client_id for info in infos] == [cpu, gpu]
    held = dict(rows(relay_db, 'SELECT id, identity FROM relay_task'))
    zones = dict(rows(relay_db, 'SELECT id, zone FROM task'))
    for info, zone in zip(infos, ZONES):
        assert len(info.task_ids) == {'cpu': 2, 'gpu': 4}[zone]
        assert all(held[id] == info.client_id and zones[id] == zone_id(zone) for id in info.task_ids)
    assert dispatcher.staged(zone_id('cpu')) == 1 and dispatcher.staged(zone_id('gpu')) == 1


@mark.unit
def test_a_full_zone_is_passed_over_for_the_next(relay_db: Engine) -> None:
    """A zone whose own backlog is above its low-water mark is not asked; the zone with room is."""
    records = dispatched('echo held-1', 'echo held-2', zone='cpu')
    store.insert_received(relay_db, records, identity=upstream_identity('cpu'))
    fake = FakeDispatcher()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 1})
    for _ in range(3):
        assert machine.gate() is PullerState.REQUEST and machine.request() is PullerState.GATE
    assert [(request['client'], request['zone']) for request in fake.requests] == [(upstream_identity('gpu'), 'gpu')] * 3
    assert fake.requests[0]['size'] == 2 and fake.requests[0]['wait'] == ZONE_REQUEST_WAIT


@mark.unit
def test_a_zone_without_site_clients_is_never_asked(relay_db: Engine) -> None:
    """A zone none of whose site clients is connected has no room: only the other zone asks."""
    fake = FakeDispatcher()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 2})
    for _ in range(3):
        assert machine.gate() is PullerState.REQUEST and machine.request() is PullerState.GATE
    assert {request['zone'] for request in fake.requests} == {'cpu'}
    assert zoned_puller(relay_db, upstream(FakeDispatcher()), {}).gate() is PullerState.GATE


@mark.unit
def test_zones_take_turns(relay_db: Engine) -> None:
    """With room in every zone, the zones are asked in turn."""
    fake = FakeDispatcher()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 1, None: 1}, zones=('cpu', 'gpu', None))
    for _ in range(6):
        assert machine.gate() is PullerState.REQUEST and machine.request() is PullerState.GATE
    assert [request['zone'] for request in fake.requests] == ['cpu', 'gpu', None] * 2


@mark.unit
def test_pulling_ends_once_every_zone_is_shut_down(relay_db: Engine) -> None:
    """A shutdown reply ends requests for that zone only; pulling ends once every zone has had one."""
    fake = FakeDispatcher(reply([], shutdown=True), reply([]), reply([], shutdown=True))
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 1})
    assert pull_once(machine) is PullerState.GATE   # cpu: shut down.
    assert pull_once(machine) is PullerState.GATE   # gpu: nothing yet.
    assert pull_once(machine) is PullerState.FINAL  # gpu: shut down; cpu is not asked again.
    assert [request['zone'] for request in fake.requests] == ['cpu', 'gpu', 'gpu']


class ShuttingDown(FakeDispatcher):
    """An upstream server that is finishing: every request is answered with a shutdown."""

    def request(self, text_: str) -> str:
        self.requests.append(json.loads(text_))
        return json.dumps(reply([], shutdown=True))


def drive(machine: UpstreamPuller, steps: int = 20) -> PullerState:
    """Run the puller from its gate, one action at a time, until it ends or `steps` actions have run."""
    state = PullerState.GATE
    for _ in range(steps):
        if state is PullerState.FINAL:
            break
        state = machine.actions[state]()
    return state


def complete_all(engine: Engine) -> None:
    """Mark every task in the relay database complete, as the site does once it has run them."""
    with engine.begin() as conn:
        now = datetime.now().astimezone().isoformat(sep=' ')
        conn.execute(text('UPDATE task SET completion_time = :now'), {'now': now})


@mark.unit
@mark.parametrize('zones', [('cpu', 'gpu'), ('gpu', 'cpu')])
def test_a_zone_without_site_clients_asks_once_and_ends_on_its_own_shutdown(relay_db: Engine, zones) -> None:
    """A zone with no site client never asks for work, so once another zone has heard the shutdown it asks once
    for itself and ends on its own reply.

    The upstream server finishes for every zone at once, but serves each zone what is still staged for
    it first. A zone with nothing staged, no site client and nothing held is told at once and ends;
    pulling ends, so the relay sends its final heartbeats and a non-forever upstream server exits.
    """
    fake = ShuttingDown()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 0}, zones=zones)
    assert drive(machine) is PullerState.FINAL
    assert [request['zone'] for request in fake.requests] == ['cpu', 'gpu']
    assert machine.finished == {upstream_identity('cpu'), upstream_identity('gpu')}


@mark.unit
def test_a_relay_with_no_site_client_in_any_zone_never_asks(relay_db: Engine) -> None:
    """With no site client in any zone the relay never asks, so it cannot hear a shutdown: it waits for a drain or stop.

    Asking would take tasks the site cannot run (its bound is zero), so it never does; it ends at its
    gate when drained or stopped.
    """
    fake = ShuttingDown()
    single = puller(relay_db, fake)
    assert drive(single) is PullerState.GATE and not single.finished
    several = zoned_puller(relay_db, upstream(fake), {'cpu': 0, 'gpu': 0})
    assert drive(several) is PullerState.GATE and not several.finished
    assert fake.requests == []
    single.halt()
    assert drive(single) is PullerState.FINAL


@mark.unit
def test_a_zone_with_site_clients_still_asks_for_itself_after_a_shutdown(relay_db: Engine) -> None:
    """A shutdown in one zone does not end a zone that has site clients: it asks, and ends on its own reply."""
    fake = ShuttingDown()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 2})
    assert drive(machine) is PullerState.FINAL
    assert [request['zone'] for request in fake.requests] == ['cpu', 'gpu']


@mark.unit
def test_a_zone_without_site_clients_that_holds_tasks_ends_only_once_they_are_done(relay_db: Engine) -> None:
    """A zone with no site client but tasks still held does not end at another zone's shutdown until they are done."""
    store.insert_received(relay_db, dispatched('echo held-1', 'echo held-2', zone='gpu'),
                          identity=upstream_identity('gpu'))
    fake = ShuttingDown()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 0})
    assert drive(machine) is PullerState.GATE
    assert machine.finished == {upstream_identity('cpu')}
    complete_all(relay_db)
    assert drive(machine) is PullerState.FINAL
    assert [request['zone'] for request in fake.requests] == ['cpu', 'gpu']


@mark.unit
def test_a_zone_without_site_clients_is_not_ended_before_any_shutdown(relay_db: Engine) -> None:
    """Until some zone has had a shutdown reply, a zone with no site client is only passed over, never ended."""
    fake = FakeDispatcher()
    machine = zoned_puller(relay_db, upstream(fake), {'cpu': 1, 'gpu': 0})
    assert drive(machine) is PullerState.GATE
    assert machine.finished == set() and {request['zone'] for request in fake.requests} == {'cpu'}


def finishing_upstream(upstream_db: bool) -> tuple:
    """A real dispatcher that is finishing with three tasks still staged for a zone; that zone, the other, the ids.

    With an upstream database the rows are staged (stamped) for the gpu zone, and the cpu zone has
    none. Without one, a submitted bundle of four default-zone tasks waits in the inbound queue; one
    earlier request takes one of them, which stages the other three; the gpu zone has none.
    """
    if upstream_db:
        dispatcher = Dispatcher(bundlesize=1, queuesize=1)
        records = dispatched(*[f'echo gpu-{n}' for n in range(3)], zone='gpu')
        dispatcher.stage(zone_id('gpu'), [(record['id'], json.dumps(record)) for record in records])
        dispatcher.finish()
        return dispatcher, 'gpu', 'cpu', [record['id'] for record in records]
    dispatcher = Dispatcher(inbound=Queue())
    records = dispatched(*[f'echo default-{n}' for n in range(4)])
    dispatcher.inbound.put(json.dumps(records).encode('utf-8'))
    taken, = json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': 'gone', 'size': 1,
                                                       'wait': 0})))['tasks']
    dispatcher.finish()
    return dispatcher, None, 'gpu', [record['id'] for record in records if record['id'] != taken['id']]


@mark.unit
@mark.parametrize('upstream_db', [True, False], ids=['database', 'no-database'])
def test_a_zone_without_site_clients_takes_what_is_staged_for_it_at_a_shutdown(relay_db: Engine, upstream_db: bool,
                                                                               caplog) -> None:
    """Once another zone has heard the shutdown, a zone with no site client takes one task still staged for it,
    holds it for a site client, then takes the rest once one is connected, and pulling ends only once every task
    has run at the site.

    The upstream server is a real dispatcher that is finishing, with and without an upstream database.
    """
    dispatcher, idle, busy, staged = finishing_upstream(upstream_db)
    clients = {busy: 1, idle: 0}
    machine = zoned_puller(relay_db, upstream(dispatcher), clients, zones=(busy, idle))
    with caplog.at_level(logging.INFO, logger='hypershell.relay.upstream'):
        assert drive(machine) is PullerState.GATE
    assert machine.finished == {upstream_identity(busy)}
    assert store.count_backlog(relay_db, zone=zone_id(idle)) == 1
    assert dispatcher.staged(zone_id(idle)) == 2
    held, = [record.getMessage() for record in caplog.records if 'no site client' in record.getMessage()]
    assert f'zone {idle or "default"}' in held and 'holding 1 task' in held

    clients[idle] = 1
    complete_all(relay_db)
    assert drive(machine) is PullerState.GATE
    assert dispatcher.staged(zone_id(idle)) == 0
    assert machine.finished == {upstream_identity(busy)}

    complete_all(relay_db)
    assert drive(machine) is PullerState.FINAL
    assert machine.finished == {upstream_identity(busy), upstream_identity(idle)}
    assert set(staged) <= {id for id, in rows(relay_db, 'SELECT id FROM relay_task')}
    assert rows(relay_db, 'SELECT COUNT(*) FROM task WHERE completion_time IS NULL') == [(0, )]
    if not upstream_db:
        assert dispatcher.inbound.empty()


@mark.unit
@mark.parametrize('upstream_db', [True, False], ids=['database', 'no-database'])
def test_a_zone_without_site_clients_ends_at_once_with_nothing_staged_for_it(relay_db: Engine,
                                                                             upstream_db: bool) -> None:
    """With nothing staged anywhere, a finishing upstream server answers the idle zone's one request with a
    shutdown, and pulling ends at once with every zone finished."""
    dispatcher = Dispatcher(bundlesize=1, queuesize=1) if upstream_db else Dispatcher(inbound=Queue())
    dispatcher.finish()
    busy, idle = ('cpu', 'gpu') if upstream_db else ('gpu', None)
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1}, zones=(busy, idle))
    assert drive(machine) is PullerState.FINAL
    assert machine.finished == {upstream_identity(busy), upstream_identity(idle)}
    assert dispatcher.demand == {upstream_identity(busy): (zone_id(busy), 2),
                                 upstream_identity(idle): (zone_id(idle), 1)}
    assert rows(relay_db, 'SELECT COUNT(*) FROM relay_task') == [(0, )]


def site_plan(policy: str, distribution: str, b_running: int) -> Dict[str, Dict[str, int]]:
    """The plan of a server autoscaling with relay sites, with one gpu task left: site -> zone name -> target.

    Relay B (first registered) serves gpu and runs `b_running` gpu clients; relay A serves cpu and
    gpu and runs one cpu client. Each zone at each site has min 0, init 0 and max 2. The gpu task's
    pressure is 1 while a gpu client runs, else 2. The server plans with `plan_sites`, as it publishes.
    """
    cpu, gpu = ZoneRef.from_name('cpu'), ZoneRef.from_name('gpu')
    bounds = Bounds(min=0, max=2, init=0)
    sites = [SiteView('B', 0, {gpu.id: SiteZone(gpu, bounds, running=b_running, starting=False)}),
             SiteView('A', 1, {cpu.id: SiteZone(cpu, bounds, running=1, starting=False),
                               gpu.id: SiteZone(gpu, bounds, running=0, starting=False)})]
    pressure = None if policy == 'fixed' else 1.0 if b_running else 2.0
    loads = {gpu.id: ZoneLoad(gpu, remaining=1, retryable=0, pressure=pressure)}
    knobs = ScalingKnobs(AutoScalerPolicy.from_name(policy), 1.0, init_size=0, min_size=0, max_size=2)
    targets = plan_sites(loads, knobs, sites, distribution=parse_distribution(distribution))
    names = {cpu.id: cpu.name, gpu.id: gpu.name}
    return {site: {names[zone]: target for zone, target in zones.items()} for site, zones in targets.items()}


@mark.unit
@mark.parametrize('policy', ['fixed', 'dynamic'])
@mark.parametrize('distribution', ['spread', 'fill'])
@mark.parametrize('b_running', [1, 0], ids=['b-running', 'b-exited'])
def test_a_zone_the_topology_gives_no_client_here_ends_at_a_shutdown_and_leaves_its_tasks_upstream(
        relay_db: Engine, policy: str, distribution: str, b_running: int, caplog) -> None:
    """Once another zone has heard the shutdown, a zone with no site client that the topology gives no client at
    this relay does not ask for itself: it ends with the others, and what is staged for it stays upstream.

    Relay A serves cpu and gpu, relay B gpu; the plan puts the gpu zone's one client at B, so A's gpu
    target is zero. A's fleet launches nothing at that target, so a task A took for gpu would never
    run, and A would never end its gpu requests. The rows stay staged for B's gpu client.
    """
    plan = site_plan(policy, distribution, b_running)
    assert plan == {'A': {'cpu': 1, 'gpu': 0}, 'B': {'gpu': 1}}
    reply = TopologyReply('upstream:1', {name: Entry(name, target, 0) for name, target in plan['A'].items()})
    dispatcher, idle, busy, staged = finishing_upstream(True)
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(reply)
    with caplog.at_level(logging.INFO, logger='hypershell.relay.upstream'):
        assert drive(machine) is PullerState.FINAL
    assert machine.finished == {upstream_identity(busy), upstream_identity(idle)}
    assert upstream_identity(idle) not in dispatcher.demand
    assert dispatcher.staged(zone_id(idle)) == len(staged)
    assert rows(relay_db, 'SELECT COUNT(*) FROM relay_task') == [(0, )]
    ended, = [record.getMessage() for record in caplog.records if 'none planned here' in record.getMessage()]
    assert f'zone {idle}' in ended


@mark.unit
@mark.parametrize('reply', [TopologyReply('upstream:1', {'cpu': Entry('cpu', 1, 1), 'gpu': Entry('gpu', 1, 0)}),
                            None], ids=['gpu-target-1', 'no-topology'])
def test_a_zone_its_fleet_would_launch_for_takes_what_is_staged_for_it_at_a_shutdown(
        relay_db: Engine, reply: Optional[TopologyReply]) -> None:
    """A zone with no site client still asks once at a shutdown when its fleet would launch a client for it: the
    topology gives it a target above zero here, or none is published (the fleet then keeps at least one)."""
    dispatcher, idle, busy, staged = finishing_upstream(True)
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(reply)
    assert drive(machine) is PullerState.GATE
    assert machine.finished == {upstream_identity(busy)}
    assert store.count_backlog(relay_db, zone=zone_id(idle)) == 1
    assert dispatcher.staged(zone_id(idle)) == len(staged) - 1


def planned(idle: Optional[str], busy: str, target: int) -> TopologyReply:
    """A topology giving the busy zone one client at this relay, and the idle zone `target`."""
    name = ZoneRef.from_name(idle).name
    return TopologyReply('upstream:1', {busy: Entry(busy, 1, 1), name: Entry(name, target, 0)})


def held_by_zone(engine: Engine, idle: Optional[str]) -> int:
    """Tasks of the relay database's `idle` zone not yet complete."""
    return store.count_backlog(engine, zone=zone_id(idle))


@mark.unit
def test_a_zone_that_took_a_task_at_a_shutdown_hands_it_back_once_no_client_is_planned_here(
        relay_db: Engine, caplog) -> None:
    """A zone with no site client takes one task at a shutdown while the topology gives it a client here; a newer
    topology then gives it none before one starts. The zone hands the task back upstream, under its identity and
    with the stamp it was sent under, and ends: pulling ends, and the relay holds nothing for it.

    Before, the zone held the task with no client to run it and never asked whether one was still coming, so
    pulling never ended (and a non-forever upstream server waited for the relay).
    """
    dispatcher, idle, busy, staged = finishing_upstream(True)
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(planned(idle, busy, 1))
    assert drive(machine) is PullerState.GATE
    assert held_by_zone(relay_db, idle) == 1
    (task_id, stamp), = rows(relay_db, 'SELECT id, schedule_time FROM relay_task WHERE id IN (SELECT id FROM task '
                                       f"WHERE zone = '{zone_id(idle)}')")
    machine.view.set(planned(idle, busy, 0))
    with caplog.at_level(logging.INFO, logger='hypershell.relay.upstream'):
        assert drive(machine) is PullerState.FINAL
    assert machine.finished == {upstream_identity(busy), upstream_identity(idle)}
    assert dispatcher.take_handbacks() == [(upstream_identity(idle), [(task_id, stamp)])]
    assert held_by_zone(relay_db, idle) == 0 and store.count_held(relay_db) == 0
    assert rows(relay_db, 'SELECT COUNT(*) FROM relay_task') == [(0, )]
    assert dispatcher.staged(zone_id(idle)) == len(staged) - 1
    handed, = [record.getMessage() for record in caplog.records if record.getMessage().startswith('Handed back')]
    assert handed == (f'Handed back 1 tasks for zone {idle}, which has no site client and none planned here '
                      f'({upstream_identity(idle)})')


class WithoutHandback:
    """An upstream dispatcher of an earlier build: it serves requests and has no hand-back call."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self.request = dispatcher.request


@mark.unit
@mark.parametrize('server', ['no-call', 'no-database'])
def test_a_zone_that_cannot_hand_back_what_it_took_leaves_it_to_eviction_and_ends(relay_db: Engine, server: str,
                                                                                 caplog) -> None:
    """An upstream server without the hand-back call, or one without a database (which refuses it), cannot take
    back what the zone took: the zone says so once and ends, and pulling ends. The task leaves the relay-local
    server, but its upstream stamp stays, so the relay still counts it held and leaves it to the server's
    eviction of the relay."""
    dispatcher, idle, busy, _ = finishing_upstream(server == 'no-call')
    remote = upstream(WithoutHandback(dispatcher) if server == 'no-call' else dispatcher)
    machine = zoned_puller(relay_db, remote, {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(planned(idle, busy, 1))
    assert drive(machine) is PullerState.GATE
    assert held_by_zone(relay_db, idle) == 1
    machine.view.set(planned(idle, busy, 0))
    with caplog.at_level(logging.INFO, logger='hypershell.relay.upstream'):
        assert drive(machine) is PullerState.FINAL
    assert machine.finished == {upstream_identity(busy), upstream_identity(idle)}
    assert held_by_zone(relay_db, idle) == 0 and store.count_held(relay_db) == 1
    assert dispatcher.take_handbacks() == []
    warned = [record.getMessage() for record in caplog.records
              if record.levelno >= logging.WARNING and record.name == 'hypershell.relay.upstream']
    reason = 'the upstream server takes none' if server == 'no-call' else 'Hand-back refused (server has no database)'
    assert warned == [f'Could not hand back 1 tasks for zone {idle or "default"}, which has no site client and none '
                      f"planned here ({reason}) - they are left to the upstream server's eviction of this relay"]


@mark.unit
@mark.parametrize('local', ['staged', 'received'])
def test_a_zone_hands_back_only_what_no_site_client_has_received(relay_db: Engine, local: str) -> None:
    """The relay-local server has scheduled the task the zone took. Still in its staging pool, no site client
    has it: it is taken out of the pool and handed back. Cut for a site client (which then left), it is never
    handed back: the zone holds it until it completes, then ends with nothing handed back."""
    dispatcher, idle, busy, _ = finishing_upstream(True)
    site = Dispatcher()
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(planned(idle, busy, 1))
    machine.unstage = lambda zone, ids: site.withdraw(zone_id(zone), ids)
    assert drive(machine) is PullerState.GATE
    (task_id, stamp), = rows(relay_db, f"SELECT id, schedule_time FROM relay_task WHERE id IN "
                                       f"(SELECT id FROM task WHERE zone = '{zone_id(idle)}')")
    with relay_db.begin() as conn:
        conn.execute(text('UPDATE task SET schedule_time = :now WHERE id = :id'),
                     {'now': datetime.now().astimezone().isoformat(sep=' '), 'id': task_id})
    site.stage(zone_id(idle), [(task_id, json.dumps({'id': task_id}))])
    if local == 'received':
        taken = json.loads(site.request(json.dumps({'protocol': PROTOCOL, 'client': 'site-client', 'zone': idle,
                                                    'size': 1, 'wait': 0})))['tasks']
        assert [task['id'] for task in taken] == [task_id]
    machine.view.set(planned(idle, busy, 0))
    if local == 'staged':
        assert drive(machine) is PullerState.FINAL
        assert dispatcher.take_handbacks() == [(upstream_identity(idle), [(task_id, stamp)])]
        assert site.staged(zone_id(idle)) == 0 and held_by_zone(relay_db, idle) == 0
        return
    assert drive(machine) is PullerState.GATE
    assert held_by_zone(relay_db, idle) == 1 and dispatcher.take_handbacks() == []
    complete_all(relay_db)
    assert drive(machine) is PullerState.FINAL
    assert dispatcher.take_handbacks() == []


@mark.unit
def test_a_zone_that_took_a_task_keeps_it_while_a_client_is_still_planned_here(relay_db: Engine) -> None:
    """While the topology still gives the zone a client here, what it took waits for that client: nothing is
    handed back, and pulling goes on."""
    dispatcher, idle, busy, _ = finishing_upstream(True)
    machine = zoned_puller(relay_db, upstream(dispatcher), {busy: 1, idle: 0}, zones=(busy, idle))
    machine.view = TopologyView(planned(idle, busy, 1))
    assert drive(machine) is PullerState.GATE
    assert drive(machine) is PullerState.GATE
    assert held_by_zone(relay_db, idle) == 1 and dispatcher.take_handbacks() == []


@mark.unit
def test_default_zone_backlog_is_its_own_in_a_relay_serving_several_zones(relay_db: Engine) -> None:
    """Default-zone tasks are stored with no zone; a relay serving it beside another zone counts them, and only them."""
    add_backlog(relay_db, 2)
    store.insert_received(relay_db, dispatched('echo gpu-1', zone='gpu'), identity=upstream_identity('gpu'))
    assert rows(relay_db, 'SELECT COUNT(*) FROM task WHERE zone IS NULL') == [(2, )]
    assert store.count_backlog(relay_db, zone=DEFAULT_ZONE_ID) == 2
    assert store.count_backlog(relay_db, zone=zone_id('gpu')) == 1
    assert store.count_backlog(relay_db) == 3
    fake = FakeDispatcher()
    machine = zoned_puller(relay_db, upstream(fake), {None: 1, 'gpu': 1}, zones=(None, 'gpu'))
    assert machine.gate() is PullerState.REQUEST  # The default zone is above its low-water mark (2 of 2).
    assert machine.zone == 'gpu' and machine.size == 1


@mark.unit
def test_several_zones_need_their_own_client_counts(relay_db: Engine) -> None:
    """A puller for several zones cannot fall back on the relay's total client count."""
    with pytest.raises(ValueError):
        UpstreamPuller(queue=upstream(FakeDispatcher()), engine=relay_db, identity=upstream_identity('cpu'),
                       zones=served_zones(ZONES))
