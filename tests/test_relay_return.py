# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's return path: completed site rows go back upstream through a durable outbox.

An upstream server's database is bound to the global session (as the server's own threads see it)
and the relay database is a separate file. A task is dispatched and confirmed upstream exactly as the
server does it, received and inserted by the relay, run at the site (its local row written as the
relay-local server writes a leaf's result), and returned by the returner machine, stepped one action
at a time against a fake upstream queue. The bytes the returner puts are then applied upstream through
the server's own result path, so every outcome (APPLIED, DUPLICATE, STALE) is the upstream server's.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Standard libs
import json
import sqlite3
from pathlib import Path
from queue import Queue, Full as QueueFull
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine
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
