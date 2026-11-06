# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's return path: completed site rows go back upstream through a durable outbox.

An upstream server's database is bound to the global session (as the server's own threads see it)
and the relay database is a separate file. A task is dispatched and confirmed upstream exactly as the
server does it, received and inserted by the relay, run at the site (its local row written as the
relay-local server writes a leaf's result), and returned by the returner machine, stepped one action
at a time against a fake upstream queue. The bytes the returner puts are then applied upstream through
the server's own result path, so every outcome (APPLIED, DUPLICATE, STALE) is the upstream server's.

The relay database keeps its datetimes in UTC, so a leaf's times go back upstream as the instants the
leaf reported, across a change of the relay host's zone and through the hour clocks repeat.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Standard libs
import os
import json
import time
import sqlite3
from pathlib import Path
from queue import Queue, Full as QueueFull
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.core.logging import INSTANCE, HOSTNAME
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, ResultOutcome, deserialize_tasks
from hypershell.server import Confirm
from hypershell.relay import store
from hypershell.relay.store import ensure_tables, RESULT_PENDING, RESULT_RETURNED
from hypershell.relay.upstream import UpstreamReturner, ReturnerState, upstream_identity


IDENTITY = upstream_identity(None)
LEAF = (str(uuid()), 'site-node-1')
OTHER_LEAF = (str(uuid()), 'site-node-2')
RELAY_SERVER = (str(uuid()), 'relay-host')  # The relay-local server's own stamp, which never leaves.
POLL = 0.01


@fixture
def upstream_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh upstream database, then restore it."""
    engine = create_engine(f'sqlite:///{tmp_path / "upstream.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


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


def dispatch_upstream(*args: str, group: int = 0) -> List[Dict[str, Any]]:
    """Submit and schedule tasks upstream; return the records a work request carries (decoded JSON)."""
    tasks = [Task.new(args=arg, group=group) for arg in args]
    Task.add_all(tasks)
    sent = Task.next(limit=len(tasks), group=group)
    return [json.loads(json.dumps(task.to_json())) for task in sent]


def confirm_upstream(ids: List[str], client: Tuple[str, str] = (IDENTITY, HOSTNAME)) -> None:
    """Confirm task ids upstream, as the upstream server's Confirm thread does."""
    machine = Confirm(queue=None)
    machine.client_info = ClientInfo(client_id=client[0], client_host=client[1], task_ids=ids)
    machine.update_info()


def receive(relay_db: Engine, records: List[Dict[str, Any]]) -> List[str]:
    """The relay takes the records and confirms them upstream under its identity."""
    ids = store.insert_received(relay_db, records, identity=IDENTITY)
    confirm_upstream(ids)
    return ids


def run_at_site(relay_db: Engine, task_id: str, exit_status: int = 0, leaf: Tuple[str, str] = LEAF) -> datetime:
    """Run a task at the site: the relay-local server stamps it, a leaf runs it and its result is written.

    Returns the completion time (aware, local).
    """
    scheduled = datetime.now().astimezone()
    started = scheduled + timedelta(seconds=1)
    completed = started + timedelta(seconds=2)
    table = Task.__table__
    with relay_db.begin() as conn:
        conn.execute(table.update().where(table.c.id == task_id).values(
            schedule_time=scheduled, server_id=RELAY_SERVER[0], server_host=RELAY_SERVER[1],
            client_id=leaf[0], client_host=leaf[1], command=f'run {task_id}', start_time=started,
            completion_time=completed, exit_status=exit_status, waited=1, duration=2))
    return completed


def returner(relay_db: Engine, completed: Optional[Any] = None, bundlesize: int = 100,
             bundlewait: float = 0, retention: float = 300) -> UpstreamReturner:
    """A returner on a fake upstream (only `completed` is used) and the relay database."""
    remote = SimpleNamespace(completed=completed if completed is not None else Queue())
    return UpstreamReturner(queue=remote, engine=relay_db, bundlesize=bundlesize, bundlewait=bundlewait,
                            poll=POLL, retention=retention)


def return_once(machine: UpstreamReturner) -> bytes:
    """Collect, send and mark one bundle; return the bytes put upstream."""
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.MARK
    assert machine.mark() is ReturnerState.COLLECT
    return machine.queue.completed.get_nowait()


def apply_upstream(payload: bytes) -> List[ResultOutcome]:
    """Apply a returned bundle upstream through the server's result path."""
    return Task.apply_results(deserialize_tasks(payload))


def upstream_row(task_id: str) -> Dict[str, Any]:
    """The upstream row, freshly read."""
    return Task.from_id(task_id, caching=False).to_dict()


def rows(engine: Engine, sql: str) -> List[tuple]:
    """Rows read on a new connection to a SQLite file, so only committed data is seen."""
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute(sql).fetchall()


# --- The returned record ------------------------------------------------------------------------


@mark.unit
def test_record_restores_the_upstream_stamp_and_gives_naive_times_the_relay_zone() -> None:
    """The record carries the upstream stamp verbatim and the leaf's result columns, with local offsets."""
    task = Task.new(args='echo record')
    row = task.to_dict()
    row.update(schedule_time=datetime(2026, 11, 5, 9, 0), server_id=RELAY_SERVER[0], server_host=RELAY_SERVER[1],
               client_id=LEAF[0], client_host=LEAF[1], start_time=datetime(2026, 11, 5, 9, 0, 1),
               completion_time=datetime(2026, 11, 5, 9, 0, 3), exit_status=0)
    stamp = {'schedule_time': '2026-11-05 14:30:00.123456+05:30', 'server_id': INSTANCE, 'server_host': 'upstream'}
    record = store.result_record(row, stamp)
    assert record['schedule_time'] == stamp['schedule_time']
    assert (record['server_id'], record['server_host']) == (INSTANCE, 'upstream')
    assert (record['client_id'], record['client_host']) == LEAF
    local = datetime(2026, 11, 5, 9, 0, 3).astimezone()
    assert datetime.fromisoformat(record['completion_time']) == local
    assert datetime.fromisoformat(record['completion_time']).utcoffset() == local.utcoffset()
    assert set(record) == set(Task.columns)


@mark.unit
def test_restored_record_is_applied_upstream_with_the_leaf_id(upstream_db: Engine, relay_db: Engine) -> None:
    """A returned result lands upstream: the leaf's attribution, the upstream stamp unchanged."""
    record, = dispatch_upstream('echo applied')
    stamp = upstream_row(record['id'])['schedule_time']
    receive(relay_db, [record])
    completed = run_at_site(relay_db, record['id'], exit_status=0)

    payload = return_once(returner(relay_db))
    assert apply_upstream(payload) == [ResultOutcome.APPLIED]
    row = upstream_row(record['id'])
    assert (row['client_id'], row['client_host']) == LEAF
    assert row['client_id'] != IDENTITY
    assert row['exit_status'] == 0
    assert row['command'] == f'run {record["id"]}'
    assert row['schedule_time'] == stamp
    assert (row['server_id'], row['server_host']) == (record['server_id'], record['server_host'])
    assert row['completion_time'].astimezone() == completed
    sent, = json.loads(payload)
    assert sent['schedule_time'] == record['schedule_time']
    assert RELAY_SERVER[0] not in json.dumps(sent) and RELAY_SERVER[1] not in json.dumps(sent)


@mark.unit
def test_replay_is_duplicate(upstream_db: Engine, relay_db: Engine) -> None:
    """The same returned bundle presented again (a resend after a crash) writes nothing upstream."""
    record, = dispatch_upstream('echo replay')
    receive(relay_db, [record])
    run_at_site(relay_db, record['id'])
    payload = return_once(returner(relay_db))
    assert apply_upstream(payload) == [ResultOutcome.APPLIED]
    before = upstream_row(record['id'])
    assert apply_upstream(payload) == [ResultOutcome.DUPLICATE]
    assert upstream_row(record['id']) == before


@mark.unit
def test_return_after_revert_is_stale(upstream_db: Engine, relay_db: Engine) -> None:
    """A result for an assignment upstream has since reverted (the relay was evicted) writes nothing."""
    record, = dispatch_upstream('echo reverted')
    receive(relay_db, [record])
    Task.revert_orphaned(IDENTITY)
    run_at_site(relay_db, record['id'])
    payload = return_once(returner(relay_db))
    assert apply_upstream(payload) == [ResultOutcome.STALE]
    row = upstream_row(record['id'])
    assert row['completion_time'] is None and row['client_id'] is None


@mark.unit
def test_result_from_a_superseded_assignment_is_dropped(upstream_db: Engine, relay_db: Engine) -> None:
    """A task received again (reverted and dispatched anew) drops the result held for its old assignment."""
    record, = dispatch_upstream('echo superseded')
    receive(relay_db, [record])
    run_at_site(relay_db, record['id'], exit_status=1, leaf=OTHER_LEAF)
    machine = returner(relay_db)
    assert machine.collect() is ReturnerState.SEND
    assert machine.send() is ReturnerState.MARK
    old_payload = machine.queue.completed.get_nowait()

    Task.revert_orphaned(IDENTITY)
    again = json.loads(json.dumps(Task.next(limit=1)[0].to_json()))
    assert again['schedule_time'] != record['schedule_time']
    receive(relay_db, [again])
    assert machine.mark() is ReturnerState.COLLECT  # The old send marks nothing now.
    assert rows(relay_db, 'SELECT count(*) FROM relay_result') == [(0, )]
    assert store.count_held(relay_db) == 1

    run_at_site(relay_db, again['id'], exit_status=0)
    payload = return_once(machine)
    assert apply_upstream(old_payload) == [ResultOutcome.STALE]
    assert apply_upstream(payload) == [ResultOutcome.APPLIED]
    row = upstream_row(record['id'])
    assert ((row['client_id'], row['client_host']), row['exit_status']) == (LEAF, 0)
    assert store.count_held(relay_db) == 0


# --- The outbox ---------------------------------------------------------------------------------


class CheckedQueue(Queue):
    """A completed queue that records, at each put, the outbox states a new connection reads."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.seen: List[List[tuple]] = []

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None) -> None:
        self.seen.append(rows(self.engine, 'SELECT state, count(*) FROM relay_result GROUP BY state'))
        super().put(item, block, timeout)


@mark.unit
def test_upstream_restart_drops_held_tasks_not_yet_started(upstream_db: Engine, relay_db: Engine) -> None:
    """Tasks stamped by a restarted upstream server (a new server id) drop the held tasks the earlier instance
    assigned that have not started here, since it put them all back when it started; one already started runs
    on, and one sent again is kept under its new assignment."""
    old = dispatch_upstream('echo a', 'echo b', 'echo c')
    receive(relay_db, old)
    started, again, dropped = (record['id'] for record in old)
    table = Task.__table__
    with relay_db.begin() as conn:  # The relay-local server has scheduled one of them.
        conn.execute(table.update().where(table.c.id == started).values(schedule_time=datetime.now().astimezone()))

    restarted = str(uuid())
    resent = {**old[1], 'server_id': restarted, 'schedule_time': datetime.now().astimezone().isoformat()}
    new, = dispatch_upstream('echo d')
    new['server_id'] = restarted
    store.insert_received(relay_db, [resent, new], identity=IDENTITY)
    held = {id for id, in rows(relay_db, 'SELECT id FROM task')}
    assert held == {started, again, new['id']} and dropped not in held
    stamps = dict(rows(relay_db, 'SELECT id, server_id FROM relay_task'))
    assert stamps == {started: old[0]['server_id'], again: restarted, new['id']: restarted}

    # Further tasks from the same instance drop nothing more: the started task runs on.
    more, = dispatch_upstream('echo e')
    more['server_id'] = restarted
    store.insert_received(relay_db, [more], identity=IDENTITY)
    assert {id for id, in rows(relay_db, 'SELECT id FROM task')} == {started, again, new['id'], more['id']}


@mark.unit
def test_completed_rows_move_to_the_outbox_and_are_marked_after_the_put(upstream_db: Engine,
                                                                         relay_db: Engine) -> None:
    """Completed rows leave the task table for the outbox; they are marked returned only once the put returns."""
    records = dispatch_upstream('echo a', 'echo b', 'echo c')
    receive(relay_db, records)
    for record in records[:2]:
        run_at_site(relay_db, record['id'])
    completed = CheckedQueue(relay_db)
    machine = returner(relay_db, completed=completed)
    assert store.count_held(relay_db) == 3

    assert machine.collect() is ReturnerState.SEND
    assert rows(relay_db, 'SELECT id FROM task') == [(records[2]['id'], )]
    assert rows(relay_db, 'SELECT state, count(*) FROM relay_result GROUP BY state') == [(RESULT_PENDING, 2)]
    assert machine.send() is ReturnerState.MARK
    assert completed.seen == [[(RESULT_PENDING, 2)]]
    assert machine.mark() is ReturnerState.COLLECT
    assert rows(relay_db, 'SELECT state, count(*) FROM relay_result GROUP BY state') == [(RESULT_RETURNED, 2)]
    assert store.count_held(relay_db) == 1
    assert apply_upstream(completed.get_nowait()) == [ResultOutcome.APPLIED] * 2


@mark.unit
@mark.parametrize('failure', [QueueFull, ConnectionResetError, EOFError])
def test_put_failure_leaves_the_row_pending(upstream_db: Engine, relay_db: Engine, failure: type) -> None:
    """A put that does not return (full queue, broken link) leaves the result pending, to be sent again."""

    class Refuses(Queue):
        def put(self, item, block=True, timeout=None):
            raise failure()

    record, = dispatch_upstream('echo refused')
    receive(relay_db, [record])
    run_at_site(relay_db, record['id'])
    machine = returner(relay_db, completed=Refuses())
    assert machine.collect() is ReturnerState.SEND
    if failure is QueueFull:
        assert machine.send() is ReturnerState.SEND
    else:
        with pytest.raises(failure):
            machine.send()
    assert rows(relay_db, 'SELECT state, returned_at FROM relay_result') == [(RESULT_PENDING, None)]
    assert store.count_held(relay_db) == 1

    machine.queue.completed = Queue()
    assert machine.send() is ReturnerState.MARK
    assert machine.mark() is ReturnerState.COLLECT
    assert apply_upstream(machine.queue.completed.get_nowait()) == [ResultOutcome.APPLIED]
    assert store.count_held(relay_db) == 0


@mark.unit
def test_bundles_follow_bundlesize_and_bundlewait(upstream_db: Engine, relay_db: Engine) -> None:
    """A full bundle goes at once; a partial one waits for the bundle wait, or goes on a flush."""
    records = dispatch_upstream(*[f'echo {n}' for n in range(5)])
    receive(relay_db, records)
    for record in records:
        run_at_site(relay_db, record['id'])
    machine = returner(relay_db, bundlesize=2, bundlewait=3600)
    for _ in range(2):
        payload = return_once(machine)
        assert len(json.loads(payload)) == 2
    assert machine.collect() is ReturnerState.COLLECT  # One left, and the bundle wait has not run out.
    machine.flush()
    payload = return_once(machine)
    assert len(json.loads(payload)) == 1
    assert machine.collect() is ReturnerState.FINAL
    assert store.count_held(relay_db) == 0


@mark.unit
def test_failed_local_row_does_not_hold_the_next_local_group(relay_db: Engine) -> None:
    """A task that failed at the site (local retries off) leaves the task table, so the next group starts."""
    first = Task.new(args='exit 1', group=0)
    second = Task.new(args='echo next', group=1)
    records = []
    for task in (first, second):
        task.server_id, task.server_host = INSTANCE, 'upstream'
        task.schedule_time = datetime.now(timezone.utc)
        records.append(json.loads(json.dumps(task.to_json())))
    store.insert_received(relay_db, records, identity=IDENTITY)
    run_at_site(relay_db, first.id, exit_status=1)
    Session.remove()
    Session.configure(bind=relay_db)
    try:
        held = Task.increment_group(0, attempts=1)
        assert held.value == 0 and not held.viable
        assert store.collect_results(relay_db) == 1
        moved = Task.increment_group(0, attempts=1)
        assert moved.viable and moved.value == 1
        assert [task.id for task in Task.next(limit=10, group=moved.value)] == [second.id]
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
    record = json.loads(rows(relay_db, f"SELECT record FROM relay_result WHERE id = '{first.id}'")[0][0])
    assert record['exit_status'] == 1 and record['client_id'] == LEAF[0]


@mark.unit
def test_retention_prune_keeps_the_file_size_stable(upstream_db: Engine, relay_db: Engine) -> None:
    """Returned results are pruned after the retention, with their upstream stamps, so the file stops growing."""
    path = Path(relay_db.url.database)
    machine = returner(relay_db, bundlesize=1000, retention=0)
    sizes = []
    for round_ in range(8):
        records = dispatch_upstream(*[f'echo {round_}-{n} ' + 'x' * 200 for n in range(50)])
        receive(relay_db, records)
        for record in records:
            run_at_site(relay_db, record['id'])
        assert apply_upstream(return_once(machine)) == [ResultOutcome.APPLIED] * 50
        assert store.prune_returned(relay_db, before=datetime.now().astimezone() + timedelta(seconds=1)) == 50
        assert rows(relay_db, 'SELECT count(*) FROM relay_result') == [(0, )]
        assert rows(relay_db, 'SELECT count(*) FROM relay_task') == [(0, )]
        assert rows(relay_db, 'SELECT count(*) FROM task') == [(0, )]
        sizes.append(path.stat().st_size)
    assert sizes[-1] <= sizes[1], sizes


@mark.unit
def test_prune_keeps_results_within_the_retention_and_pending_ones(upstream_db: Engine, relay_db: Engine) -> None:
    """Only returned results older than the retention are pruned; pending ones are never pruned."""
    records = dispatch_upstream('echo returned', 'echo pending')
    receive(relay_db, records)
    run_at_site(relay_db, records[0]['id'])
    return_once(returner(relay_db))
    run_at_site(relay_db, records[1]['id'])
    assert store.collect_results(relay_db) == 1
    assert store.prune_returned(relay_db, before=datetime.now().astimezone() - timedelta(seconds=60)) == 0
    assert store.prune_returned(relay_db, before=datetime.now().astimezone() + timedelta(seconds=1)) == 1
    assert rows(relay_db, 'SELECT id, state FROM relay_result') == [(records[1]['id'], RESULT_PENDING)]
    assert rows(relay_db, 'SELECT id FROM relay_task') == [(records[1]['id'], )]
    assert store.count_held(relay_db) == 1


# --- Times kept in UTC ---------------------------------------------------------------------------


EDT, EST = timezone(timedelta(hours=-4)), timezone(timedelta(hours=-5))
FIRST_HALF_PAST_ONE = datetime(2026, 11, 1, 1, 30, tzinfo=EDT)   # 05:30 UTC, before clocks go back
SECOND_HALF_PAST_ONE = datetime(2026, 11, 1, 1, 30, tzinfo=EST)  # 06:30 UTC, the same wall clock an hour on


@fixture
def local_zone() -> Iterator[Any]:
    """Set this process's local time zone (`TZ`); the original is restored at the end."""
    original = os.environ.get('TZ')

    def set_zone(name: str) -> None:
        os.environ['TZ'] = name
        time.tzset()

    try:
        yield set_zone
    finally:
        if original is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original
        time.tzset()


@fixture
def utc_relay_db(tmp_path: Path) -> Iterator[Engine]:
    """A fresh relay database keeping its datetimes in UTC, as a relay process does."""
    engine = create_engine(f'sqlite:///{tmp_path / "relay.db"}', connect_args={'check_same_thread': False})
    store.store_utc(engine)
    Entity.metadata.create_all(engine)
    ensure_tables(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def receive_one(engine: Engine) -> str:
    """Receive one task, stamped upstream; return its id."""
    task = Task.new(args='echo times')
    task.schedule_time = datetime.now().astimezone()
    record = json.loads(json.dumps(task.to_json()))
    store.insert_received(engine, [record], identity=IDENTITY)
    return task.id


@mark.unit
@mark.parametrize('stored_in, returned_in', [
    ('America/New_York', 'America/New_York'),
    ('America/New_York', 'UTC'),
    ('UTC', 'Asia/Kolkata'),
])
def test_leaf_times_return_as_the_instants_reported(utc_relay_db: Engine, local_zone: Any,
                                                    stored_in: str, returned_in: str) -> None:
    """Leaf times in the repeated hour come back as the same instants, whatever the relay's zone meanwhile."""
    local_zone(stored_in)
    task_id = receive_one(utc_relay_db)
    table = Task.__table__
    with utc_relay_db.begin() as conn:  # As the relay-local server writes the leaf's result
        conn.execute(table.update().where(table.c.id == task_id).values(
            client_id=LEAF[0], client_host=LEAF[1], exit_status=0,
            start_time=FIRST_HALF_PAST_ONE, completion_time=SECOND_HALF_PAST_ONE))
    with sqlite3.connect(utc_relay_db.url.database) as db:
        stored = db.execute('SELECT start_time, completion_time FROM task').fetchone()
    assert stored == ('2026-11-01 05:30:00.000000', '2026-11-01 06:30:00.000000')

    local_zone(returned_in)
    assert store.collect_results(utc_relay_db) == 1
    (_, text), = store.pending_results(utc_relay_db, limit=10)
    sent = json.loads(text)
    started, completed = (datetime.fromisoformat(sent[name]) for name in ('start_time', 'completion_time'))
    assert started == FIRST_HALF_PAST_ONE and completed == SECOND_HALF_PAST_ONE
    assert completed - started == timedelta(hours=1)
    assert completed.utcoffset() == SECOND_HALF_PAST_ONE.astimezone(ZoneInfo(returned_in)).utcoffset()


@mark.unit
def test_relay_database_matches_an_instant_in_any_offset(utc_relay_db: Engine, local_zone: Any) -> None:
    """A stamp matches the same instant given in any offset, and reads back aware, as the relay-local server needs."""
    local_zone('America/New_York')
    task_id = receive_one(utc_relay_db)
    stamp = datetime.now().astimezone()
    table = Task.__table__
    with utc_relay_db.begin() as conn:
        conn.execute(table.update().where(table.c.id == task_id).values(schedule_time=stamp))
    for given in (stamp, stamp.astimezone(timezone.utc), stamp.astimezone(ZoneInfo('Asia/Kolkata'))):
        with utc_relay_db.connect() as conn:
            found = conn.execute(select(table.c.id, table.c.schedule_time).where(table.c.schedule_time == given)).all()
        assert found == [(task_id, stamp)]
        assert found[0][1].tzinfo is not None


@mark.unit
def test_store_utc_refuses_an_engine_already_in_use(tmp_path: Path) -> None:
    """Once the engine has written a datetime its type is settled; switching then would mix two conventions."""
    engine = create_engine(f'sqlite:///{tmp_path / "relay.db"}')
    Entity.metadata.create_all(engine)
    ensure_tables(engine)
    receive_one(engine)
    with pytest.raises(RuntimeError, match='already bound'):
        store.store_utc(engine)
    engine.dispose()


# --- A late site confirmation ---------------------------------------------------------------------


@fixture
def site_session(relay_db: Engine) -> Iterator[Engine]:
    """Bind the global session to the relay database, as the relay-local server's threads see it."""
    Session.remove()
    Session.configure(bind=relay_db)
    try:
        yield relay_db
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)


UPSTREAM_SERVER = (str(uuid()), 'upstream-host')


def upstream_record(args: str) -> Dict[str, Any]:
    """A task record as one upstream server instance sends it, stamped by that server."""
    record = json.loads(json.dumps(Task.new(args=args, group=0).to_json()))
    record.update(schedule_time=datetime.now().astimezone().isoformat(),
                  server_id=UPSTREAM_SERVER[0], server_host=UPSTREAM_SERVER[1])
    return record


def leaf_result(task: Task, exit_status: int) -> Task:
    """The result a site client returns for a task the relay-local server dispatched to it."""
    result = Task.from_json(task.to_json())
    now = datetime.now().astimezone()
    result.client_id, result.client_host = LEAF
    result.command, result.start_time, result.completion_time = task.args, now, now
    result.exit_status, result.waited, result.duration = exit_status, 0, 0
    return result


@mark.unit
def test_site_confirmation_after_the_result_left_for_the_outbox(site_session: Engine) -> None:
    """A site client confirms a bundle; the relay-local Confirm thread is behind its Receiver, so one task's result
    is written first, and the returner moves the completed row to the outbox before the confirmation is processed.
    The confirmation must settle that task (its result is already held for return) and record the rest of its
    bundle, not end the relay-local server's Confirm thread with StaleDataError."""
    from hypershell.relay.site import site_cluster
    from tests.conftest import free_port
    from sqlalchemy.orm.exc import StaleDataError

    records = [upstream_record('exit 3'), upstream_record('sleep 1')]
    store.insert_received(site_session, records, identity=IDENTITY)
    dispatched = {task.args: task for task in Task.next(limit=2, group=0)}  # The relay-local scheduler.
    fast, slow = dispatched['exit 3'].id, dispatched['sleep 1'].id
    confirmation = ClientInfo(client_id=LEAF[0], client_host=LEAF[1], task_ids=[fast, slow])

    result = leaf_result(dispatched['exit 3'], exit_status=3)
    assert Task.apply_results([result]) == [ResultOutcome.APPLIED]  # The Receiver runs first,
    assert store.collect_results(site_session) == 1                 # then the returner,
    assert rows(site_session, 'SELECT id FROM task') == [(slow, )]

    machine = site_cluster(address=('127.0.0.1', free_port())).server.confirm.machine
    machine.client_info = confirmation
    try:
        machine.update_info()                                       # and the Confirm thread last.
    except StaleDataError as error:
        pytest.fail(f'Late site confirmation ended the Confirm thread: {error}')
    assert rows(site_session, 'SELECT id, client_id FROM task') == [(slow, LEAF[0])]
    assert rows(site_session, 'SELECT id, state FROM relay_result') == [(fast, RESULT_PENDING)]
    assert store.count_held(site_session) == 2


# --- A failed site task collected while the scheduler looks at its group --------------------------


@mark.unit
def test_group_check_while_a_failed_task_leaves_for_the_outbox(site_session: Engine) -> None:
    """The relay-local scheduler (no retries) checks its group as a site task's failure is collected.

    The failure is counted as one that has used its retries, and the returner moves the row to the
    outbox before the scheduler takes it as the example it names. The check must not fail on the
    vanished example: the group is held on the failure it saw, and once the row has gone the group
    no longer holds anything.
    """
    from sqlalchemy import event

    record = upstream_record('exit 3')
    store.insert_received(site_session, [record], identity=IDENTITY)
    task, = Task.next(limit=1, group=0)  # The relay-local scheduler.
    assert Task.apply_results([leaf_result(task, exit_status=3)]) == [ResultOutcome.APPLIED]  # The Receiver.

    seen, collected = [], []

    def counted(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: unused
        """Note the scheduler's first query for failures that have used their retries."""
        if 'attempt >=' in statement:
            seen.append(statement)

    def returner_between(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: unused
        """The returner runs before the scheduler's next statement once that query has run."""
        if seen and not collected:
            collected.append(None)  # The returner's own statements pass through here too.
            collected[0] = store.collect_results(site_session)

    event.listen(site_session, 'after_cursor_execute', counted)
    event.listen(site_session, 'before_cursor_execute', returner_between)
    try:
        held = Task.increment_group(0, attempts=1)  # The site server's scheduler (max_retries=0).
        after = Task.increment_group(0, attempts=1)
    finally:
        event.remove(site_session, 'after_cursor_execute', counted)
        event.remove(site_session, 'before_cursor_execute', returner_between)
    assert collected == [1]
    assert rows(site_session, 'SELECT id FROM task') == []
    assert held.value == 0 and not held.viable and record['id'] in held.reason
    assert after.viable and after.reason == 'no task groups remain'


@mark.unit
def test_starting_group_while_the_unscheduled_tasks_are_deleted(site_session: Engine) -> None:
    """The scheduler picks its starting group as every task, none yet scheduled, is deleted.

    In a relay the puller drops and inserts in one transaction, so the table is never left empty, but a
    task delete (`hs update --delete`) can empty it between the count and the read of the earliest
    group. The start must take the default group, not fail on the vanished row.
    """
    from sqlalchemy import event
    from hypershell.data.model import DEFAULT_TASK_GROUP

    store.insert_received(site_session, [upstream_record('echo unscheduled')], identity=IDENTITY)
    deleted = []

    def delete_before(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: unused
        """A task delete commits just before the scheduler reads the earliest group."""
        if not deleted and 'ORDER BY task."group"' in statement:
            deleted.append(None)
            with site_session.begin() as other:
                other.exec_driver_sql('DELETE FROM task')

    event.listen(site_session, 'before_cursor_execute', delete_before)
    try:
        info = Task.current_group()
    finally:
        event.remove(site_session, 'before_cursor_execute', delete_before)
    assert deleted == [None]
    assert rows(site_session, 'SELECT id FROM task') == []
    assert info.value == DEFAULT_TASK_GROUP


@mark.unit
def test_group_check_example_renders_for_postgresql(site_session: Engine) -> None:
    """The group check's example aggregate compiles, for PostgreSQL, over the id as text.

    On PostgreSQL the task id is a native `uuid`, which has no `min`; the statement must never take a
    bare `min(task.id)` there. Rendered only (no PostgreSQL server needed).
    """
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql

    store.insert_received(site_session, [upstream_record('exit 3')], identity=IDENTITY)
    task, = Task.next(limit=1, group=0)
    assert Task.apply_results([leaf_result(task, exit_status=3)]) == [ResultOutcome.APPLIED]
    rendered = []

    def render(state) -> None:
        """Compile each statement the group check runs as PostgreSQL would receive it."""
        rendered.append(str(state.statement.compile(dialect=postgresql.dialect())))

    session = Session()
    event.listen(session, 'do_orm_execute', render)
    try:
        held = Task.increment_group(0, attempts=1)
    finally:
        event.remove(session, 'do_orm_execute', render)
    assert not held.viable and task.id in held.reason
    example, = [sql for sql in rendered if 'attempt >=' in sql]
    assert 'min(task.id)' not in example
    assert 'min(CAST(task.id AS TEXT))' in example
