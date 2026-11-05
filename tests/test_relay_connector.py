# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's upstream pull: identity, the backlog gate, the durable insert and the confirmation.

The puller is driven one action at a time against a fake upstream (a scripted dispatcher and plain
queues) and a throwaway relay database, with no threads and no sleeps. One test serves the relay's
request from a real queue server's dispatcher, to show the request is an ordinary client's.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional

# Standard libs
import json
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
from hypershell.relay.upstream import (UpstreamPuller, PullerState, RelayInsertError, upstream_identity,
                                       INSERT_ATTEMPTS)


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
def test_no_request_at_or_above_the_bound_one_below_it(relay_db: Engine) -> None:
    """At or above connected clients times the local bundle size nothing is requested; below it, the gap is."""
    connect_clients(relay_db, 2)
    connect_clients(relay_db, 3, server_id=str(uuid()))  # Another relay-local server's clients do not count.
    ids = add_backlog(relay_db, 4)
    fake = FakeDispatcher()
    machine = puller(relay_db, fake, local_bundlesize=2)
    assert machine.gate() is PullerState.GATE  # Backlog 4, bound 4.
    with relay_db.begin() as conn:
        conn.execute(Task.__table__.update().where(Task.__table__.c.id == ids[0])
                     .values(completion_time=datetime.now().astimezone(), exit_status=0))
    assert machine.gate() is PullerState.REQUEST  # Backlog 3, bound 4.
    assert fake.requests == []
    assert machine.request() is PullerState.GATE
    request, = fake.requests
    assert request['size'] == 1


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
    assert request['size'] == 6
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
        assert server.dispatcher.demand == {upstream_identity('gpu'): (zone_id('gpu'), 2)}
        assert machine.insert() is PullerState.CONFIRM
        assert machine.confirm() is PullerState.GATE
        info = ClientInfo.unpack_or_none(server.confirmed.get(timeout=5))
        assert info.client_id == upstream_identity('gpu')
        assert info.task_ids == [record['id'] for record in records[:2]]
        assert machine.gate() is PullerState.GATE  # Two received, two clients: the bound is met.
    stamps = dict(rows(relay_db, 'SELECT id, schedule_time FROM relay_task'))
    assert stamps == {record['id']: record['schedule_time'] for record in records[:2]}
