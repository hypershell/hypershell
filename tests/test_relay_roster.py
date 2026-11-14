# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A relay's site clients appear upstream through its heartbeat, attributed to its site.

The relay's heartbeat carries its site and a snapshot of the site clients connected to its local
server (the roster). The upstream heart monitor is stepped one action at a time on stand-in queues
and a throwaway database: it registers each leaf with the relay's site, disconnects the leaves that
drop out of the roster, and disconnects the whole roster when the relay is evicted or finishes. A
leaf never heartbeats upstream, so the monitor never evicts one on its own, and it never holds the
server's shutdown. A heartbeat from an earlier release, with neither field, is still accepted, and a
database from before these columns upgrades in place.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional

# Standard libs
import logging
import shutil
import sqlite3
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

# Internal libs
import hypershell.server
import hypershell.relay.upstream as upstream_module
from hypershell.core.heartbeat import Heartbeat, ClientState, RosterEntry
from hypershell.core.logging import HOSTNAME, INSTANCE
from hypershell.core.dispatch import Dispatcher
from hypershell.core.types import serialize, deserialize
from hypershell.core.uuid import uuid
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Client, Task, zone_id
from hypershell.data.upgrade import upgrade_schema
from hypershell.relay import store
from hypershell.relay.store import ensure_tables
from hypershell.relay.upstream import (UpstreamHeartbeat, HeartbeatState, upstream_identity, relay_site,
                                       request_text)
from hypershell.server import HeartMonitor, HeartbeatState as MonitorState
from tests import FIXTURE_283


EVICT = 10  # Seconds without a heartbeat before the monitor evicts a client.
SITE = 'site-a'


@fixture(autouse=True)
def short_timeout(monkeypatch) -> None:
    """Shorten the server's queue-operation timeout so an empty queue returns quickly."""
    monkeypatch.setattr(hypershell.server, 'DEFAULT_REMOTE_TIMEOUT', 0.05)


@fixture
def queues() -> SimpleNamespace:
    """Stand-ins for the upstream server's managed queues and dispatcher."""
    return SimpleNamespace(scheduled=Queue(1), completed=Queue(1), heartbeat=Queue(), confirmed=Queue(),
                           dispatcher=Dispatcher())


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file (the upstream database), then restore it."""
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


def monitor(queues: SimpleNamespace, **options: Any) -> HeartMonitor:
    """An upstream heart monitor on the stand-in queues."""
    return HeartMonitor(queue=queues, evict_after=EVICT, **options)


def leaf(host: str = 'site-node', state: ClientState = ClientState.RUNNING) -> RosterEntry:
    """A site client as a relay lists it."""
    return RosterEntry(uuid=uuid(), host=host, state=state)


def relay_beat(relay: str, roster: Optional[List[RosterEntry]] = None, site: Optional[str] = SITE,
               state: ClientState = ClientState.RUNNING, time: Optional[datetime] = None) -> Heartbeat:
    """A heartbeat from a relay identity."""
    return Heartbeat.new(uuid=relay, host='relay-host', state=state, site=site, roster=roster, time=time)


def deliver(machine: HeartMonitor, beat: Heartbeat | bytes) -> None:
    """Put one heartbeat on the queue and step the monitor through it."""
    machine.queue.heartbeat.put(beat if isinstance(beat, bytes) else beat.pack())
    assert machine.get_next() is MonitorState.UPDATE
    assert machine.update_client() is MonitorState.SWITCH


def clients() -> Dict[str, Dict[str, Any]]:
    """Every upstream client row, by id, read fresh."""
    return {client.id: client.to_dict() for client in Client.query(caching=False).all()}


def connected(rows: Dict[str, Dict[str, Any]]) -> set:
    """Ids of the rows still connected."""
    return {id for id, row in rows.items() if row['disconnected_at'] is None}


# --- The heartbeat -----------------------------------------------------------------------------


@mark.unit
def test_beat_without_roster_fields_decodes() -> None:
    """A heartbeat from an earlier release (no site, no roster) decodes; a plain beat packs neither field."""
    now = datetime.now().astimezone()
    old = serialize({'uuid': 'c1', 'host': 'h1', 'time': str(now), 'state': ClientState.RUNNING.value})
    beat = Heartbeat.unpack_or_none(old)
    assert (beat.uuid, beat.host, beat.time, beat.state) == ('c1', 'h1', now, ClientState.RUNNING)
    assert beat.site is None and beat.roster is None
    assert set(deserialize(Heartbeat.new(uuid='c1').pack())) == {'uuid', 'host', 'time', 'state'}


@mark.unit
def test_beat_with_site_and_roster_round_trips() -> None:
    """Site and roster survive packing; a roster entry carries a client's id, host and state, nothing else."""
    roster = [leaf('node-1'), leaf('node-2', ClientState.FINISHED)]
    beat = Heartbeat.unpack_or_none(relay_beat('r1', roster).pack())
    assert beat.site == SITE and beat.roster == roster
    packed = deserialize(relay_beat('r1', roster).pack())
    assert set(packed) == {'uuid', 'host', 'time', 'state', 'site', 'roster'}
    assert all(set(entry) == {'uuid', 'host', 'state'} for entry in packed['roster'])
    empty = Heartbeat.unpack_or_none(relay_beat('r1', []).pack())
    assert empty.roster == [] and empty.site == SITE


# --- The upstream heart monitor ----------------------------------------------------------------


@mark.unit
def test_leaf_registered_with_site(queues, task_db) -> None:
    """A relay's beat registers it (site, relay = itself) and each leaf (site, relay = the relay); leaves never beat."""
    machine = monitor(queues)
    relay, leaves = str(uuid()), [leaf('node-1'), leaf('node-2')]
    deliver(machine, relay_beat(relay, leaves))
    rows = clients()
    assert set(rows) == {relay} | {entry.uuid for entry in leaves}
    assert (rows[relay]['site'], rows[relay]['relay']) == (SITE, relay)
    for entry in leaves:
        row = rows[entry.uuid]
        assert (row['site'], row['relay'], row['host']) == (SITE, relay, entry.host)
        assert (row['server_id'], row['server_host']) == (INSTANCE, HOSTNAME)
        assert row['disconnected_at'] is None and row['evicted'] is False
    assert list(machine.beats) == [relay]


@mark.unit
def test_leaf_disconnect_forwarded(queues, task_db) -> None:
    """A leaf that drops out of the roster, or reports FINISHED, is disconnected (not evicted); a new one is added."""
    machine = monitor(queues)
    relay, (one, two, three) = str(uuid()), [leaf(), leaf(), leaf()]
    deliver(machine, relay_beat(relay, [one, two, three]))
    four = leaf()
    deliver(machine, relay_beat(relay, [one, RosterEntry(three.uuid, three.host, ClientState.FINISHED), four]))
    rows = clients()
    assert connected(rows) == {relay, one.uuid, four.uuid}
    assert rows[two.uuid]['evicted'] is False and rows[three.uuid]['evicted'] is False
    assert rows[four.uuid]['relay'] == relay


@mark.unit
def test_beat_without_roster_keeps_the_roster(queues, task_db) -> None:
    """A beat that carries no roster changes no leaf; an empty roster disconnects them all."""
    machine = monitor(queues)
    relay, leaves = str(uuid()), [leaf(), leaf()]
    deliver(machine, relay_beat(relay, leaves))
    deliver(machine, relay_beat(relay, None))
    assert connected(clients()) == {relay} | {entry.uuid for entry in leaves}
    deliver(machine, relay_beat(relay, []))
    assert connected(clients()) == {relay}


@mark.unit
def test_roster_never_takes_another_relays_leaf(queues, task_db) -> None:
    """A roster entry applies only to a client with no relay or with the sender as its relay.

    A leaf of relay A named in relay B's roster, or in an ordinary client's, keeps relay A's site and
    relay, and is not disconnected when it drops out of that roster. Relay A's own roster still governs it.
    """
    machine = monitor(queues)
    relay_a, relay_b, plain, mine = str(uuid()), str(uuid()), str(uuid()), leaf('node-a')
    deliver(machine, relay_beat(relay_a, [mine]))
    deliver(machine, relay_beat(relay_b, [mine], site='site-b'))
    deliver(machine, Heartbeat.new(uuid=plain, host='plain-host', roster=[mine]))
    assert (clients()[mine.uuid]['site'], clients()[mine.uuid]['relay']) == (SITE, relay_a)
    deliver(machine, relay_beat(relay_b, [], site='site-b'))
    deliver(machine, Heartbeat.new(uuid=plain, host='plain-host', roster=[]))
    assert mine.uuid in connected(clients())
    # Dropped by its own relay, it is disconnected; named again by another, it stays with its own relay.
    deliver(machine, relay_beat(relay_a, []))
    deliver(machine, relay_beat(relay_b, [mine], site='site-b'))
    row = clients()[mine.uuid]
    assert (row['site'], row['relay']) == (SITE, relay_a) and row['disconnected_at'] is not None
    assert mine.uuid not in machine.rosters[relay_b] and mine.uuid not in machine.rosters[plain]


@mark.unit
def test_relay_eviction_disconnects_roster(queues, task_db) -> None:
    """A relay evicted for missed beats takes its whole roster with it; other clients are untouched."""
    machine = monitor(queues)
    relay, other, leaves = str(uuid()), str(uuid()), [leaf(), leaf()]
    deliver(machine, relay_beat(relay, leaves))
    machine.received[relay] -= 3 * EVICT  # Its last beat arrived three eviction periods ago.
    deliver(machine, Heartbeat.new(uuid=other, host='plain-host'))
    machine.last_check = datetime.now().astimezone()
    assert machine.check_clients() is MonitorState.SWITCH
    rows = clients()
    assert connected(rows) == {other}
    assert rows[relay]['evicted'] is True
    assert all(rows[entry.uuid]['evicted'] is False for entry in leaves)
    assert relay not in machine.rosters and list(machine.beats) == [other]


@mark.unit
def test_relay_finished_disconnects_roster(queues, task_db) -> None:
    """A relay's FINISHED beat disconnects it and its roster; with nothing else beating, the monitor may finish."""
    machine = monitor(queues)
    relay, leaves = str(uuid()), [leaf(), leaf()]
    deliver(machine, relay_beat(relay, leaves))
    deliver(machine, relay_beat(relay, None, state=ClientState.FINISHED))
    rows = clients()
    assert connected(rows) == set()
    assert not any(row['evicted'] for row in rows.values())
    machine.scheduler_done = True
    assert machine.switch_mode() is MonitorState.FINAL


@mark.unit
def test_leaf_never_evicted_on_its_own(queues, task_db) -> None:
    """Leaves have no beats of their own: past the eviction period they stay connected while their relay beats."""
    machine = monitor(queues)
    relay, leaves = str(uuid()), [leaf(), leaf()]
    old = datetime.now().astimezone() - timedelta(seconds=3 * EVICT)
    deliver(machine, relay_beat(relay, leaves, time=old))
    deliver(machine, relay_beat(relay, None))  # The relay keeps beating; its leaves never do.
    machine.last_check = datetime.now().astimezone()
    machine.check_clients()
    assert connected(clients()) == {relay} | {entry.uuid for entry in leaves}
    assert list(machine.beats) == [relay]


@mark.unit
def test_relay_with_a_clock_behind_is_not_evicted(queues, task_db) -> None:
    """A relay whose clock is three eviction periods behind the server's is aged from when its beat arrived.

    It is not evicted, its roster stays connected, and the task it holds is not put back.
    """
    machine = monitor(queues)
    relay, leaves = str(uuid()), [leaf(), leaf()]
    task = Task.new(args='echo held')
    task.schedule_time = datetime.now().astimezone()
    task.server_id, task.server_host, task.client_id, task.client_host = INSTANCE, HOSTNAME, relay, 'relay-host'
    Task.add(task)
    behind = datetime.now().astimezone() - timedelta(seconds=3 * EVICT)
    deliver(machine, relay_beat(relay, leaves, time=behind))
    machine.last_check = datetime.now().astimezone()
    assert machine.check_clients() is MonitorState.SWITCH
    rows = clients()
    assert connected(rows) == {relay} | {entry.uuid for entry in leaves}
    assert rows[relay]['evicted'] is False
    assert list(machine.beats) == [relay] and set(machine.rosters[relay]) == {entry.uuid for entry in leaves}
    held = Task.from_id(task.id, caching=False)
    assert str(held.client_id) == relay and held.schedule_time is not None


@mark.unit
def test_count_connected_counts_leaves_not_relays(queues, task_db) -> None:
    """Connected clients are the ones that execute: ordinary clients and leaves, never a relay identity."""
    task = Task.new(args='echo scheduled')
    task.server_id, task.server_host, task.schedule_time = INSTANCE, HOSTNAME, datetime.now().astimezone()
    Task.add(task)
    machine = monitor(queues)
    deliver(machine, Heartbeat.new(uuid=str(uuid()), host='plain-host'))
    deliver(machine, relay_beat(str(uuid()), [leaf(), leaf()]))
    deliver(machine, relay_beat(str(uuid()), [leaf()], site='site-b'))
    assert len(connected(clients())) == 6
    assert Client.count_connected() == 4


@mark.unit
def test_reregistration_refreshes_server(queues, task_db) -> None:
    """A relay or leaf row left by an earlier server comes back connected under this server."""
    relay, entry = str(uuid()), leaf()
    then = datetime.now().astimezone() - timedelta(hours=1)
    for id, host in ((relay, 'relay-host'), (entry.uuid, entry.host)):
        Client.add(Client.new(id=id, host=host, server_id=str(uuid()), server_host='old-server',
                              connected_at=then, disconnected_at=then, evicted=True))
    machine = monitor(queues)
    deliver(machine, relay_beat(relay, [entry]))
    rows = clients()
    for id in (relay, entry.uuid):
        assert (rows[id]['server_id'], rows[id]['server_host']) == (INSTANCE, HOSTNAME)
        assert rows[id]['disconnected_at'] is None and rows[id]['evicted'] is False
    assert (rows[entry.uuid]['site'], rows[entry.uuid]['relay']) == (SITE, relay)


@mark.unit
def test_beat_without_roster_fields_is_accepted(queues, task_db) -> None:
    """A client of an earlier release registers as before: no site, no relay, counted as connected."""
    machine = monitor(queues)
    now = datetime.now().astimezone()
    deliver(machine, serialize({'uuid': 'c0ffee00-0000-4000-8000-000000000001', 'host': 'old-host',
                                'time': str(now), 'state': ClientState.RUNNING.value}))
    row, = clients().values()
    assert (row['host'], row['site'], row['relay'], row['disconnected_at']) == ('old-host', None, None, None)
    assert machine.rosters == {}


@mark.unit
@pytest.mark.parametrize('options', [{'no_confirm': True}, {'in_memory': True}])
def test_relay_without_failover_logs_one_error(queues, task_db, options, caplog) -> None:
    """A relay that registers with a server running without confirmation or a database is one ERROR line."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    machine = monitor(queues, **options)
    relay = str(uuid())
    for _ in range(3):
        deliver(machine, relay_beat(relay, [leaf()]))
    errors = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1 and relay in errors[0], errors


# --- Schema ------------------------------------------------------------------------------------


@mark.unit
def test_pre_column_database_upgrades_in_place(tmp_path: Path) -> None:
    """A database from before the site and relay columns gains both in place; existing rows hold NULL."""
    path = tmp_path / 'legacy.db'
    shutil.copyfile(FIXTURE_283, path)
    with sqlite3.connect(path) as conn:
        before = conn.execute('SELECT id, host FROM client').fetchall()
    assert before
    engine = create_engine(f'sqlite:///{path}')
    try:
        changes = upgrade_schema(engine)
        assert {'Added column client.site', 'Added column client.relay'} <= set(changes)
        assert upgrade_schema(engine) == []
    finally:
        engine.dispose()
    with sqlite3.connect(path) as conn:
        after = conn.execute('SELECT id, host, site, relay FROM client').fetchall()
    assert after == [(id, host, None, None) for id, host in before]


# --- The relay side ----------------------------------------------------------------------------


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


def register_local(engine: Engine, server_id: str = INSTANCE, disconnected: bool = False) -> str:
    """A site client row as the relay-local server writes it."""
    id, now = uuid(), datetime.now().astimezone()
    with engine.begin() as conn:
        conn.execute(Client.__table__.insert().values(
            id=id, host=f'node-{id[:4]}', server_id=server_id, server_host=HOSTNAME, connected_at=now,
            disconnected_at=now if disconnected else None, evicted=False))
    return id


@mark.unit
def test_roster_is_the_local_servers_connected_clients(relay_db) -> None:
    """The roster lists this relay-local server's connected clients only, each running, with its host."""
    ours = sorted(register_local(relay_db) for _ in range(2))
    register_local(relay_db, disconnected=True)
    register_local(relay_db, server_id=str(uuid()))
    roster = store.roster(relay_db, INSTANCE)
    assert [entry.uuid for entry in roster] == ours
    assert all(entry.state is ClientState.RUNNING and entry.host == f'node-{entry.uuid[:4]}' for entry in roster)


def heartbeat(engine: Optional[Engine], site: Optional[str] = SITE, heartrate: int = 60) -> UpstreamHeartbeat:
    """A relay heartbeat machine on a fake upstream, reading its roster from `engine`."""
    reader = None if engine is None else (lambda: store.roster(engine, INSTANCE))
    return UpstreamHeartbeat(queue=SimpleNamespace(heartbeat=Queue()), identity=upstream_identity(None),
                             heartrate=heartrate, site=site, roster=reader)


def sent(machine: UpstreamHeartbeat) -> List[Heartbeat]:
    """Every heartbeat sent so far."""
    found = []
    while not machine.queue.heartbeat.empty():
        found.append(Heartbeat.unpack_or_none(machine.queue.heartbeat.get_nowait()))
    return found


@mark.unit
def test_relay_beat_carries_site_and_roster(relay_db) -> None:
    """Every beat carries the relay's site and its current roster, and nothing else beyond a plain beat."""
    leaves = sorted(register_local(relay_db) for _ in range(2))
    machine = heartbeat(relay_db)
    assert machine.submit() is HeartbeatState.WAIT
    beat, = sent(machine)
    assert beat.uuid == upstream_identity(None) and beat.site == SITE
    assert [entry.uuid for entry in beat.roster] == leaves
    raw = machine.queue.heartbeat
    machine.submit()
    assert set(deserialize(raw.get_nowait())) == {'uuid', 'host', 'time', 'state', 'site', 'roster'}


@mark.unit
def test_roster_change_beats_early(relay_db, monkeypatch) -> None:
    """A change in the roster is forwarded at the next wait step, not only when the heartrate is due."""
    monkeypatch.setattr(upstream_module, 'HEARTBEAT_WAIT_STEP', 0.01)
    register_local(relay_db)
    machine = heartbeat(relay_db)
    machine.submit()
    sent(machine)
    assert machine.wait() is HeartbeatState.WAIT
    added = register_local(relay_db)
    assert machine.wait() is HeartbeatState.SUBMIT
    machine.submit()
    beat, = sent(machine)
    assert added in {entry.uuid for entry in beat.roster}
    assert machine.wait() is HeartbeatState.WAIT


@mark.unit
def test_unreadable_roster_beats_without_one() -> None:
    """If the roster cannot be read the beat still goes, without a roster, so upstream keeps the last one."""
    def broken() -> List[RosterEntry]:
        raise OperationalError('SELECT', {}, Exception('database is locked'))
    machine = UpstreamHeartbeat(queue=SimpleNamespace(heartbeat=Queue()), identity=upstream_identity(None),
                                heartrate=60, site=SITE, roster=broken)
    assert machine.submit() is HeartbeatState.WAIT
    beat, = sent(machine)
    assert beat.site == SITE and beat.roster is None


@mark.unit
def test_relay_site_defaults_to_the_host() -> None:
    """The site is the one given (`[relay] site`), else the relay's host name."""
    assert relay_site(None) == HOSTNAME
    assert relay_site('') == HOSTNAME
    assert relay_site('cluster-a') == 'cluster-a'


# --- A relay serving several zones -----------------------------------------------------------------


ZONES = ('cpu', 'gpu')


def zoned_heartbeat(engine: Engine, remote: Any) -> UpstreamHeartbeat:
    """The heartbeat of a relay serving both `ZONES`, reading its roster from `engine`."""
    identities = [upstream_identity(zone) for zone in ZONES]
    return UpstreamHeartbeat(queue=remote, identity=identities[0], heartrate=60, site=SITE,
                             roster=lambda: store.roster(engine, INSTANCE), identities=identities)


@mark.unit
def test_every_identity_beats_and_the_holder_carries_the_roster(relay_db) -> None:
    """Each zone identity beats with the relay's site; only the first, the holder, carries the roster."""
    leaves = sorted(register_local(relay_db) for _ in range(3))
    machine = zoned_heartbeat(relay_db, SimpleNamespace(heartbeat=Queue()))
    assert machine.submit() is HeartbeatState.WAIT
    holder, other = sent(machine)
    assert (holder.uuid, other.uuid) == tuple(upstream_identity(zone) for zone in ZONES)
    assert holder.site == other.site == SITE
    assert [entry.uuid for entry in holder.roster] == leaves and other.roster is None
    machine.signal_finished()
    assert machine.submit() is HeartbeatState.FINAL
    assert [(beat.uuid, beat.state) for beat in sent(machine)] == \
        [(upstream_identity(zone), ClientState.FINISHED) for zone in ZONES]


def stamp_upstream(identity: str, zone: str) -> str:
    """An upstream task of `zone` confirmed to `identity`, not complete; return its id."""
    task = Task.new(args=f'echo {zone}', zone=zone_id(zone))
    task.schedule_time = datetime.now().astimezone()
    task.server_id, task.server_host, task.client_id, task.client_host = str(uuid()), 'upstream', identity, 'relay'
    Task.add(task)
    return task.id


@mark.unit
def test_failover_of_a_two_zone_relay_reverts_and_unstages_both_zones(queues, task_db, relay_db) -> None:
    """A two-zone relay that stops beating is evicted under each identity: both zones' tasks go back, both unstage.

    Upstream is the stock heart monitor and dispatcher. Each identity asked for its own zone, so each
    zone's pool is staged for it alone; once both identities are evicted, every task they held is
    reverted, and both zones, left with no client, are marked to unstage.
    """
    register_local(relay_db)
    machine = monitor(queues)
    dispatcher = queues.dispatcher
    for zone in ZONES:
        identity = upstream_identity(zone)
        dispatcher.request(request_text(identity, zone, size=2, wait=0))
        dispatcher.stage(zone_id(zone), [(str(uuid()), '{}')])
    held = {zone: stamp_upstream(upstream_identity(zone), zone) for zone in ZONES}

    beater = zoned_heartbeat(relay_db, SimpleNamespace(heartbeat=Queue()))
    beater.submit()
    for beat in sent(beater):  # The relay's last beats, received three eviction periods ago.
        deliver(machine, Heartbeat.new(uuid=beat.uuid, host=beat.host, state=beat.state, site=beat.site,
                                       roster=beat.roster))
        machine.received[beat.uuid] -= 3 * EVICT
    rows = clients()
    for zone in ZONES:
        identity = upstream_identity(zone)
        assert (rows[identity]['site'], rows[identity]['relay']) == (SITE, identity)

    machine.last_check = datetime.now().astimezone()
    assert machine.check_clients() is MonitorState.SWITCH
    rows = clients()
    assert all(rows[upstream_identity(zone)]['evicted'] is True for zone in ZONES)
    assert connected(rows) == set()  # The roster went with its holder.
    for zone, id in held.items():
        task = Task.from_id(id, caching=False)
        assert task.schedule_time is None and task.client_id is None, zone
    assert dispatcher.demand == {}
    assert len(dispatcher.take_unstaged()) == len(ZONES)
