# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Task-row writes that wait on another transaction's row lock, on a server database.

Each test forces the race deterministically. A holder connection updates a `task` row and keeps its
transaction open, so the row lock is held. HyperShell's write then runs in a thread and blocks on
that lock; the test sees it blocked from the server (`pg_stat_activity` and `pg_blocking_pids`),
not from a sleep, and only then commits the holder. The write must behave as it does on PostgreSQL:
it lands if the row still qualifies once the holder commits, and is refused only for the reason it
would be refused without the wait. TimescaleDB 2.29 and later report 0 matched rows for such a
write on a hypertable, although the row exists and still qualifies.

The tests need a PostgreSQL server, as `tests/test_timescale.py` documents:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=community        # or: apache, or empty for a plain PostgreSQL server

With HS_TEST_TIMESCALE set, the database is initialized under the `timescale` provider and `task`
must be a hypertable; with it empty, under the `postgres` provider. They are skipped when
HS_TEST_POSTGRES is unset.

`hs update` is driven here too, as a subprocess on the same database: in the real race (its write
waits on the holder, as any other write does), with a row trigger that skips a chosen row's UPDATE or
DELETE a given number of times (fault injection in the server, the same miss TimescaleDB 2.28.1 to
2.31.0 make, on every server), and for combined changes, which must all land on PostgreSQL as on
SQLite.

Two sessions creating the reserved source row at once are here too, since they need the same
server: the race is forced by holding each at its INSERT until both have missed the row
(`tests/test_source.py` has the helper and the SQLite cases). The case on a database upgraded from
2.8.3 needs the `hs283` template (HS_TEST_POSTGRES_TEMPLATES, as `tests/test_upgrade_postgres.py`).
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

# Standard libs
import io
import re
import time
import uuid
import logging
import threading
from types import SimpleNamespace
from dataclasses import dataclass
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.core.types import to_json_type
from hypershell.data.model import Task, CANCEL_STATUS, STDIN_SOURCE_ID, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, ConfirmState, Receiver
from hypershell.task import TaskUpdateApp
from tests import main, main_lines
from tests.test_source import first_use_race, source_ids
from tests.test_timescale import (admin, ts_database, ts_site, database_engine, is_hypertable, uuid7,  # noqa: F401
                                  needs_server, TIMESCALE, TEMPLATES)


pytestmark = [mark.integration, needs_server]

CLIENT = (str(uuid.uuid4()), 'client-a')

POLL = 0.02      # Seconds between looks at the server for the blocked write.
DEADLINE = 30.0  # Seconds to see the write blocked, and again for it to finish once released.

# The statement a blocked HyperShell write is running, with or without a schema prefix.
WRITE_TASK = re.compile(r'^\s*(UPDATE|DELETE\s+FROM)\s+(\S+\.)?"?task"?\s', re.I)

# Every column a revert clears (`Task.revert_all`).
REVERTED = ('schedule_time', 'server_host', 'server_id', 'client_host', 'client_id', 'command', 'start_time',
            'completion_time', 'exit_status', 'outpath', 'errpath', 'waited', 'duration')

BY_ID = 'WHERE id = CAST(:id AS uuid)'


@fixture
def contended_db(ts_database, ts_site) -> Iterator[Engine]:
    """A database initialized by `hs initdb` under the entry's provider, with the in-process session bound to it.

    Yields a private engine for the holder, the observer and raw reads.
    """
    name = ts_database()
    engine = ts_site(name, provider='timescale' if TIMESCALE else 'postgres')
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    if rc != exit_status.success:
        pytest.fail(f'hs initdb failed ({rc}): {stderr}')
    if TIMESCALE and not is_hypertable(engine):
        pytest.fail('HS_TEST_TIMESCALE is set but task is not a hypertable')
    bound = database_engine(name)
    try:
        Session.remove()
        Session.configure(bind=bound)
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        bound.dispose()


def submit(args: str = 'echo contended', tag: Optional[Dict[str, Any]] = None) -> str:
    """Add one task with a time-ordered id (a hypertable partitioned by id refuses others); return its id."""
    task = Task.new(args=args, tag=tag)
    task.id = uuid7()
    Task.add(task)
    return task.id


def dispatch_one(task_id: str) -> Task:
    """Schedule exactly the one task `task_id`; return the copy a client receives through the wire."""
    copies = deserialize_tasks(serialize_tasks(Task.next(limit=1)))
    if [copy.id for copy in copies] != [task_id]:
        pytest.fail(f'Expected to dispatch task ({task_id}), got {[copy.id for copy in copies]}')
    return copies[0]


def finish(copy: Task, exit_status: int = 0) -> Task:
    """Stamp the result columns on a copy, as a client does when it runs the task."""
    start_time = datetime.now().astimezone()
    copy.client_id, copy.client_host = CLIENT
    copy.command = copy.args
    copy.start_time = start_time
    copy.completion_time = start_time + timedelta(seconds=2)
    copy.waited = 1
    copy.duration = 2
    copy.exit_status = exit_status
    copy.outpath = f'task-{copy.id}.out'
    copy.errpath = f'task-{copy.id}.err'
    return copy


def confirm(task_ids: List[str]) -> ConfirmState:
    """Confirm task ids to the client, as the server's Confirm thread does; return its next state."""
    machine = Confirm(queue=None)
    machine.client_info = ClientInfo(client_id=CLIENT[0], client_host=CLIENT[1], task_ids=task_ids)
    return machine.update_info()


def receive(copies: List[Task], failures: Optional[io.StringIO] = None) -> None:
    """Return a bundle of finished copies through the wire and the server's Receiver."""
    machine = Receiver(queue=None, redirect_failures=failures)
    machine.tasks = deserialize_tasks(serialize_tasks(copies))
    machine.update_tasks()


def row(engine: Engine, task_id: str) -> Dict[str, Any]:
    """The task's committed row, read on a private connection."""
    with engine.connect() as conn:
        data = conn.execute(text(f'SELECT * FROM task {BY_ID}'), {'id': task_id}).mappings().one()
    return {name: str(value) if isinstance(value, uuid.UUID) else value for name, value in data.items()}


def warnings_with(caplog: pytest.LogCaptureFixture, phrase: str) -> List[str]:
    """WARNING messages logged by HyperShell that contain `phrase`."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and phrase in record.getMessage()]


@dataclass
class Outcome:
    """What the contended write returned or raised."""
    value: Any = None
    error: Optional[BaseException] = None


def blocked_by(engine: Engine, holder: int, worker: threading.Thread) -> Optional[str]:
    """Wait until the server shows an UPDATE or DELETE of task waiting on the holder's lock; else say why not."""
    query = text('SELECT wait_event_type, query FROM pg_stat_activity '
                 'WHERE datname = current_database() AND :holder = ANY(pg_blocking_pids(pid))')
    deadline = time.monotonic() + DEADLINE
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
        while time.monotonic() < deadline:
            for wait_type, statement in observer.execute(query, {'holder': holder}).all():
                if wait_type == 'Lock' and WRITE_TASK.match(statement or ''):
                    return None
            if not worker.is_alive():
                return 'The write finished without waiting on the holder\'s row lock'
            time.sleep(POLL)
    return f'The write was not seen waiting on the holder\'s row lock within {DEADLINE} seconds'


def contend(engine: Engine, holder_sql: str, params: Dict[str, Any], write: Callable[[], Any]) -> Outcome:
    """Run `write` while a holder transaction keeps the row locked; release it once the write waits on it."""
    outcome = Outcome()

    def work() -> None:
        try:
            outcome.value = write()
        except BaseException as error:  # noqa: B036 (the test inspects whatever the write raised)
            outcome.error = error
        finally:
            Session.remove()

    worker = threading.Thread(target=work, name='contended-write', daemon=True)
    holder = engine.connect()
    try:
        holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar()
        if holder.execute(text(holder_sql), params).rowcount != 1:
            pytest.fail(f'The holder matched no row: {holder_sql}')
        worker.start()
        problem = blocked_by(engine, holder_pid, worker)
        holder.commit()
    finally:
        holder.close()
    worker.join(DEADLINE)
    if worker.is_alive():
        pytest.fail(f'The write did not finish within {DEADLINE} seconds of the holder committing')
    if problem:
        pytest.fail(problem)
    return outcome


def test_rerun_after_contended_update_matches(contended_db) -> None:
    """A second run of an UPDATE that waited on a row lock matches the committed row."""
    task_id = submit()
    sql = f'UPDATE task SET client_host = :host {BY_ID}'

    def write() -> Tuple[int, int]:
        with contended_db.connect() as conn:
            first = conn.execute(text(sql), {'host': 'writer', 'id': task_id}).rowcount
            second = conn.execute(text(sql), {'host': 'writer', 'id': task_id}).rowcount
            conn.commit()
        return first, second

    outcome = contend(contended_db, sql, {'host': 'holder', 'id': task_id}, write)

    assert outcome.error is None, repr(outcome.error)
    first, second = outcome.value
    assert second == 1, f'the re-run matched {second} rows (the contended run matched {first})'
    assert row(contended_db, task_id)['client_host'] == 'writer'


def test_contended_confirm_records_client(contended_db) -> None:
    """A confirmation that waited on another write to its open row stamps the client."""
    task_id = submit()
    dispatch_one(task_id)
    holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_id': str(uuid.uuid4()), 'client_host': 'holder', 'id': task_id},
                      lambda: confirm([task_id]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value is ConfirmState.UNLOAD
    data = row(contended_db, task_id)
    assert (data['client_id'], data['client_host']) == CLIENT
    assert data['completion_time'] is None


def test_contended_late_confirm_on_complete_row_is_settled(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A confirmation that waited on the result write leaves the result's client and is settled, not contended."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    dispatch_one(task_id)
    completed = datetime.now().astimezone()
    leaf = (str(uuid.uuid4()), 'leaf-a')
    holder_sql = (f'UPDATE task SET completion_time = :completed, exit_status = 0, duration = 2, '
                  f'client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}')

    outcome = contend(contended_db, holder_sql,
                      {'completed': completed, 'client_id': leaf[0], 'client_host': leaf[1], 'id': task_id},
                      lambda: confirm([task_id]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value is ConfirmState.UNLOAD
    data = row(contended_db, task_id)
    assert (data['client_id'], data['client_host']) == leaf
    assert data['completion_time'] == completed
    assert data['exit_status'] == 0
    assert warnings_with(caplog, 'Contended confirm') == []
    settled = [record.getMessage() for record in caplog.records
               if record.levelno == logging.DEBUG and 'already complete' in record.getMessage()]
    assert len(settled) == 1 and task_id in settled[0], settled


def test_contended_result_applies(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A result that waited on a confirmation is applied, with the failure written to the failures file."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit('echo fails')
    copy = dispatch_one(task_id)
    confirm([task_id])
    finished = finish(copy, exit_status=3)
    failures = io.StringIO()
    holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                      lambda: receive([finished], failures))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert data['completion_time'] == finished.completion_time
    assert data['exit_status'] == 3
    assert (data['command'], data['duration']) == (finished.command, finished.duration)
    assert failures.getvalue().splitlines() == ['echo fails']
    assert warnings_with(caplog, 'Refused stale result') == []


def test_contended_result_refused_when_reverted(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A result that waited on a revert of its row is refused as stale and writes nothing."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    copy = dispatch_one(task_id)
    confirm([task_id])
    finished = finish(copy, exit_status=0)
    holder_sql = 'UPDATE task SET ' + ', '.join(f'{name} = NULL' for name in REVERTED) + f' {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'id': task_id}, lambda: receive([finished]))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert data['schedule_time'] is None
    assert {name: data[name] for name in Task.result_columns} == {name: None for name in Task.result_columns}
    stale = warnings_with(caplog, 'Refused stale result')
    assert len(stale) == 1 and task_id in stale[0], stale


def test_contended_orphan_revert_lands(contended_db) -> None:
    """Evicting a client reverts its task even when the revert waited on a late confirmation."""
    task_id = submit()
    dispatch_one(task_id)
    confirm([task_id])
    holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                      lambda: Task.revert_orphaned(CLIENT[0]))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert data['schedule_time'] is None
    assert data['client_id'] is None


def test_contended_staged_revert_lands(contended_db) -> None:
    """A staged revert that waited on another write to its still-pending row reverts it."""
    task_id = submit()
    dispatch_one(task_id)
    holder_sql = f'UPDATE task SET client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_host': 'holder', 'id': task_id},
                      lambda: Task.revert_staged([task_id]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == 1
    data = row(contended_db, task_id)
    assert {name: data[name] for name in REVERTED} == {name: None for name in REVERTED}


def test_contended_staged_revert_keeps_cancel(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A staged revert that waited on a cancel of its row leaves the row cancelled."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    dispatch_one(task_id)
    cancelled = datetime.now().astimezone()
    holder_sql = ('UPDATE task SET schedule_time = :cancelled, completion_time = :cancelled, '
                  f'exit_status = :status {BY_ID}')

    outcome = contend(contended_db, holder_sql, {'cancelled': cancelled, 'status': CANCEL_STATUS, 'id': task_id},
                      lambda: Task.revert_staged([task_id]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == 0
    data = row(contended_db, task_id)
    assert (data['schedule_time'], data['completion_time']) == (cancelled, cancelled)
    assert data['exit_status'] == CANCEL_STATUS
    assert warnings_with(caplog, 'staged revert') == []


def test_handback_revert_matches_the_dispatch(contended_db) -> None:
    """A relay's hand-back, naming the schedule time as the relay received it, reverts its assignment on a
    server database, and a stamp of another dispatch reverts nothing."""
    task_id = submit()
    stamp = to_json_type(dispatch_one(task_id).schedule_time)
    confirm([task_id])
    later = to_json_type(datetime.now().astimezone() + timedelta(seconds=1))

    assert Task.revert_handed_back(CLIENT[0], [(task_id, later)]) == []
    assert row(contended_db, task_id)['schedule_time'] is not None
    assert Task.revert_handed_back(CLIENT[0], [(task_id, stamp)]) == [task_id]
    data = row(contended_db, task_id)
    assert {name: data[name] for name in REVERTED} == {name: None for name in REVERTED}


def test_contended_handback_revert_lands(contended_db) -> None:
    """A hand-back revert that waited on the relay's own late confirmation reverts the row."""
    task_id = submit()
    stamp = to_json_type(dispatch_one(task_id).schedule_time)
    holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                      lambda: Task.revert_handed_back(CLIENT[0], [(task_id, stamp)]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == [task_id]
    data = row(contended_db, task_id)
    assert {name: data[name] for name in REVERTED} == {name: None for name in REVERTED}


def test_contended_handback_revert_keeps_a_result(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A hand-back revert that waited on the result of its row leaves the result, settled and not contended."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    copy = dispatch_one(task_id)
    stamp = to_json_type(copy.schedule_time)
    confirm([task_id])
    completed = datetime.now().astimezone()
    holder_sql = f'UPDATE task SET completion_time = :completed, exit_status = 0, duration = 2 {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'completed': completed, 'id': task_id},
                      lambda: Task.revert_handed_back(CLIENT[0], [(task_id, stamp)]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == []
    data = row(contended_db, task_id)
    assert (data['schedule_time'], data['completion_time']) == (copy.schedule_time, completed)
    assert (data['client_id'], data['exit_status']) == (CLIENT[0], 0)
    assert warnings_with(caplog, 'hand-back') == []


def test_contended_handback_revert_skips_another_client(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A hand-back revert that waited on the row being confirmed to another client leaves it with that client."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    stamp = to_json_type(dispatch_one(task_id).schedule_time)
    other = str(uuid.uuid4())
    holder_sql = f'UPDATE task SET client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_id': other, 'client_host': 'other', 'id': task_id},
                      lambda: Task.revert_handed_back(CLIENT[0], [(task_id, stamp)]))

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == []
    data = row(contended_db, task_id)
    assert data['client_id'] == other and data['schedule_time'] is not None
    assert warnings_with(caplog, 'hand-back') == []


# A result, a cancel and a delete of one row, each as another writer's statement.
RESULT_SQL = (f'UPDATE task SET completion_time = :completed, exit_status = 0, duration = 2, '
              f'client_id = CAST(:client_id AS uuid), client_host = :client_host {BY_ID}')
CANCEL_SQL = f'UPDATE task SET schedule_time = :cancelled, completion_time = :cancelled, exit_status = :status {BY_ID}'
DELETE_SQL = f'DELETE FROM task {BY_ID}'


def count(engine: Engine, task_id: str) -> int:
    """How many committed rows have the id (0 or 1)."""
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT count(*) FROM task {BY_ID}'), {'id': task_id}).scalar()


def dispatched_and_confirmed() -> str:
    """Submit, dispatch and confirm one task to the client; return its id."""
    task_id = submit()
    dispatch_one(task_id)
    confirm([task_id])
    return task_id


def dispatched_ids() -> List[str]:
    """Dispatch the next task (at most one); return the ids dispatched."""
    return [task.id for task in Task.next(limit=1)]


def test_contended_orphan_revert_keeps_result(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """An eviction revert that waited on the result of its row keeps the result, settled and not contended."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = dispatched_and_confirmed()
    completed = datetime.now().astimezone()

    outcome = contend(contended_db, RESULT_SQL,
                      {'completed': completed, 'client_id': CLIENT[0], 'client_host': CLIENT[1], 'id': task_id},
                      lambda: Task.revert_orphaned(CLIENT[0]))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert (data['completion_time'], data['exit_status']) == (completed, 0)
    assert data['client_id'] == CLIENT[0] and data['schedule_time'] is not None
    assert warnings_with(caplog, 'revert') == []


def test_contended_orphan_revert_keeps_cancel(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """An eviction revert that waited on a cancel of its row leaves the row cancelled."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = dispatched_and_confirmed()
    cancelled = datetime.now().astimezone()

    outcome = contend(contended_db, CANCEL_SQL, {'cancelled': cancelled, 'status': CANCEL_STATUS, 'id': task_id},
                      lambda: Task.revert_orphaned(CLIENT[0]))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert (data['schedule_time'], data['completion_time']) == (cancelled, cancelled)
    assert data['exit_status'] == CANCEL_STATUS
    assert warnings_with(caplog, 'revert') == []


def test_contended_orphan_revert_skips_deleted_row(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """An eviction revert that waited on a delete of its row skips it without an error."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = dispatched_and_confirmed()

    outcome = contend(contended_db, DELETE_SQL, {'id': task_id}, lambda: Task.revert_orphaned(CLIENT[0]))

    assert outcome.error is None, repr(outcome.error)
    assert count(contended_db, task_id) == 0
    assert warnings_with(caplog, 'revert') == []


def test_contended_dispatch_lands(contended_db) -> None:
    """A dispatch stamp that waited on another write to its still-unscheduled row stamps it."""
    task_id = submit()
    holder_sql = f'UPDATE task SET client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_host': 'holder', 'id': task_id}, dispatched_ids)

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == [task_id]
    data = row(contended_db, task_id)
    assert data['schedule_time'] is not None and data['server_id'] is not None
    assert data['client_host'] == 'holder'


def test_contended_dispatch_skips_cancelled_row(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A dispatch stamp that waited on a cancel of its row does not dispatch it, and the row stays cancelled."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    cancelled = datetime.now().astimezone()

    outcome = contend(contended_db, CANCEL_SQL, {'cancelled': cancelled, 'status': CANCEL_STATUS, 'id': task_id},
                      dispatched_ids)

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == []
    data = row(contended_db, task_id)
    assert (data['schedule_time'], data['completion_time']) == (cancelled, cancelled)
    assert data['exit_status'] == CANCEL_STATUS and data['server_id'] is None
    assert warnings_with(caplog, 'dispatch') == []


def test_contended_dispatch_skips_deleted_row(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A dispatch stamp that waited on a delete of its row skips it without an error."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()

    outcome = contend(contended_db, DELETE_SQL, {'id': task_id}, dispatched_ids)

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == []
    assert count(contended_db, task_id) == 0
    assert warnings_with(caplog, 'dispatch') == []


# --- The retry mark, the cancel, and the reserved source row ----------------------------------------


def failed_row(engine: Engine) -> str:
    """Submit, dispatch and confirm one task, and write back its failure; return its id."""
    task_id = submit('false')
    copy = dispatch_one(task_id)
    confirm([task_id])
    receive([finish(copy, exit_status=1)], io.StringIO())
    if row(engine, task_id)['exit_status'] != 1:
        pytest.fail(f'The failure of task ({task_id}) was not written back')
    return task_id


def retries_of(engine: Engine, task_id: str) -> List[Dict[str, Any]]:
    """The committed rows that name the task as their previous attempt (id and schedule time)."""
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT id, schedule_time FROM task WHERE previous_id = CAST(:id AS uuid)'),
                            {'id': task_id}).mappings().all()
    return [{'id': str(data['id']), 'schedule_time': data['schedule_time']} for data in rows]


def test_contended_retry_mark_lands(contended_db) -> None:
    """A retry mark that waited on another write to its failed row lands, and the row has exactly one retry."""
    task_id = failed_row(contended_db)
    holder_sql = f'UPDATE task SET client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_host': 'holder', 'id': task_id},
                      lambda: [(task.id, task.previous_id) for task in Task.next(limit=1, attempts=2)])

    assert outcome.error is None, repr(outcome.error)
    retries = retries_of(contended_db, task_id)
    assert len(retries) == 1, retries
    assert outcome.value == [(retries[0]['id'], task_id)]
    assert retries[0]['schedule_time'] is not None
    data = row(contended_db, task_id)
    assert data['retried'] is True and data['next_id'] == retries[0]['id']
    assert data['client_host'] == 'holder'


def test_contended_retry_mark_skips_cancelled_row(contended_db, caplog: pytest.LogCaptureFixture) -> None:
    """A retry mark that waited on a cancel of its failed row does not mark it, and the row gets no retry."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = failed_row(contended_db)
    cancelled = datetime.now().astimezone()

    outcome = contend(contended_db, CANCEL_SQL, {'cancelled': cancelled, 'status': CANCEL_STATUS, 'id': task_id},
                      lambda: [(task.id, task.previous_id) for task in Task.next(limit=1, attempts=2)])

    assert outcome.error is None, repr(outcome.error)
    assert outcome.value == []
    assert retries_of(contended_db, task_id) == []
    data = row(contended_db, task_id)
    assert data['exit_status'] == CANCEL_STATUS and data['completion_time'] == cancelled
    assert not data['retried'] and data['next_id'] is None
    assert warnings_with(caplog, 'retry mark') == []


def test_contended_cancel_lands(contended_db) -> None:
    """A cancel that waited on another write to its row cancels it."""
    task_id = submit()
    holder_sql = f'UPDATE task SET client_host = :client_host {BY_ID}'

    outcome = contend(contended_db, holder_sql, {'client_host': 'holder', 'id': task_id},
                      lambda: Task.cancel_all([task_id]))

    assert outcome.error is None, repr(outcome.error)
    data = row(contended_db, task_id)
    assert data['exit_status'] == CANCEL_STATUS
    assert data['schedule_time'] is not None and data['completion_time'] is not None
    assert data['client_host'] == 'holder'


needs_hs283 = mark.skipif('hs283' not in TEMPLATES,
                          reason='set HS_TEST_POSTGRES_TEMPLATES to include hs283 (a 2.8.3 database template)')


@mark.parametrize('database', ['fresh', pytest.param('upgraded', marks=needs_hs283)])
def test_reserved_source_concurrent_first_use(ts_database, ts_site, database: str) -> None:
    """Two sessions that both miss the reserved source row before either inserts it both use the one row:
    on a fresh database under the entry's provider, and on a 2.8.3 database upgraded under `postgres`."""
    if database == 'upgraded':
        name = ts_database('hs283')
        engine = ts_site(name, provider='postgres')
    else:
        name = ts_database()
        engine = ts_site(name, provider='timescale' if TIMESCALE else 'postgres')
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    if rc != exit_status.success:
        pytest.fail(f'hs initdb failed ({rc}): {stderr}')
    if source_ids(engine):
        pytest.fail(f'The {database} database already has a source row')
    bound = database_engine(name)
    try:
        Session.remove()
        Session.configure(bind=bound)
        results = first_use_race(bound, STDIN_SOURCE_ID)
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        bound.dispose()

    assert results == [STDIN_SOURCE_ID, STDIN_SOURCE_ID], results
    assert source_ids(engine) == [STDIN_SOURCE_ID]


# --- hs update ---------------------------------------------------------------------------------------


# Enough skips that a write re-run any bounded number of times never gets through.
ALWAYS = 400

# The holder's write: another column of the same row, so the row still matches every filter used here.
HOLD_SQL = f'UPDATE task SET client_host = :client_host {BY_ID}'

# The "no longer matched" warning when one of four tasks counted was left as it is.
NOT_MATCHED = '1 of the 4 tasks counted no longer matched and were left as they are'

# A row trigger that skips the UPDATE or DELETE of a row while `skip_once` gives it times left, leaving
# it out of the statement's count although it still matches: the miss TimescaleDB 2.28.1 to 2.31.0
# make on a contended row, forced on every server. The skips run in the writer's transaction, so a
# write that is rolled back gives them back.
SKIP_TRIGGER = """
CREATE TABLE skip_once (id uuid PRIMARY KEY, times int NOT NULL);
CREATE FUNCTION skip_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE skip_once SET times = times - 1 WHERE id = OLD.id AND times > 0;
  IF FOUND THEN RETURN NULL; END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER skip_row BEFORE UPDATE OR DELETE ON task FOR EACH ROW EXECUTE FUNCTION skip_row();
"""


@fixture
def skipping(contended_db) -> Callable[[str, int], None]:
    """Install the skip trigger on `task`; return a function that makes it skip a row's next `times` writes."""
    with contended_db.begin() as conn:
        conn.execute(text(SKIP_TRIGGER))

    def skip(task_id: str, times: int) -> None:
        with contended_db.begin() as conn:
            conn.execute(text('INSERT INTO skip_once (id, times) VALUES (CAST(:id AS uuid), :times)'),
                         {'id': task_id, 'times': times})

    return skip


def hs_update(*argv: str) -> Tuple[int, str, str]:
    """Run `hs update ARGS...` as a subprocess on the test database; return its exit status, stdout and stderr."""
    return main(['hs', 'update', *argv])


def tasks(engine: Engine) -> Dict[str, Dict[str, Any]]:
    """Every committed task's group, exit status, client host and tag, by id."""
    with engine.connect() as conn:
        found = conn.execute(text('SELECT id, "group", exit_status, client_host, tag FROM task')).mappings().all()
    return {str(data['id']): {'group': data['group'], 'exit_status': data['exit_status'],
                              'client_host': data['client_host'], 'tag': data['tag']} for data in found}


def groups(engine: Engine) -> Dict[str, int]:
    """Every committed task's group, by id."""
    return {task_id: data['group'] for task_id, data in tasks(engine).items()}


def contended_error(stderr: str, task_id: str) -> List[str]:
    """Lines at ERROR or above that name the task and say the update was contended and nothing was written."""
    return [line for line in stderr.splitlines()
            if re.match(r'^\s*(ERROR|CRITICAL)\b', line) and task_id in line
            and 'contended' in line.lower() and 'nothing was written' in line]


def test_update_filtered_skipped_row_written(contended_db, skipping) -> None:
    """A filtered update that missed a row the first time writes it, and counts it."""
    ids = [submit(f'echo {n}') for n in range(4)]
    skipping(ids[1], 1)

    rc, _, stderr = hs_update('-f', '-R', 'group=5')

    assert rc == exit_status.success, stderr
    assert groups(contended_db) == dict.fromkeys(ids, 5)
    assert 'Updated 4 tasks' in stderr
    assert 'no longer matched' not in stderr


def test_update_filtered_skipped_row_exhausted_writes_nothing(contended_db, skipping) -> None:
    """A filtered update that never gets a row written writes none of the rows, says so, and exits 5."""
    ids = [submit(f'echo {n}') for n in range(4)]
    skipping(ids[1], ALWAYS)

    rc, _, stderr = hs_update('-f', '-R', 'group=5')

    assert rc == exit_status.runtime_error
    assert groups(contended_db) == dict.fromkeys(ids, 0)
    assert contended_error(stderr, ids[1]), stderr
    assert 'traceback written' not in stderr


def test_update_delete_filtered_skipped_row_deleted(contended_db, skipping) -> None:
    """A filtered delete that missed a row the first time deletes it, and counts it."""
    ids = [submit(f'echo {n}') for n in range(4)]
    skipping(ids[2], 1)

    rc, _, stderr = hs_update('-f', '-R', '--delete')

    assert rc == exit_status.success, stderr
    assert tasks(contended_db) == {}
    assert 'Deleted 4 tasks' in stderr


# Combined changes, each case one update of four untagged tasks.
COMBINED_CASES = {
    'limit': ['--limit', '2', '-f', 'group=7', 'color:red'],
    'moved': ['-f', 'group=7', 'color:blue', '-w', 'group==0'],
    'cancel': ['-f', '-R', '--cancel', 'color:green'],
}


@mark.parametrize('case', list(COMBINED_CASES))
def test_update_combined_changes_pg(contended_db, case: str) -> None:
    """A field change, or a cancel, with a tag change: every task counted as updated gets both."""
    ids = [submit(f'echo {n}') for n in range(4)]

    rc, _, stderr = hs_update(*COMBINED_CASES[case])

    assert rc == exit_status.success, stderr
    after = tasks(contended_db)
    state = {task_id: (data['group'], data['exit_status'], data['tag']) for task_id, data in after.items()}
    if case == 'limit':
        written = [task_id for task_id in ids if state[task_id][0] == 7]
        assert len(written) == 2, state
        assert {task_id: state[task_id] for task_id in written} == dict.fromkeys(written, (7, None, {'color': 'red'}))
        assert {task_id: state[task_id] for task_id in ids if task_id not in written} == \
            {task_id: (0, None, {}) for task_id in ids if task_id not in written}
        assert 'Updated 2 tasks' in stderr
    elif case == 'moved':
        assert state == dict.fromkeys(ids, (7, None, {'color': 'blue'}))
        assert 'Updated 4 tasks' in stderr
    else:
        assert state == dict.fromkeys(ids, (0, CANCEL_STATUS, {'color': 'green'}))
        assert 'Updated 4 tasks' in stderr


# Several tag keys, several removals, and a set with a removal, each under a tag filter the change takes
# the tasks out of: (tag of the four matching tasks, the update, the tag every one of them is left
# with). A fifth task, tagged color:green, never matches.
TAG_CASES = {
    'keys': ({'color': 'red'}, ['color:blue', 'size:big', '-f', '-t', 'color:red'], {'color': 'blue', 'size': 'big'}),
    'removals': ({'color': 'red', 'size': 'big'}, ['-f', '--remove-tag', 'color', 'size', '-t', 'color:red'], {}),
    'set_and_remove': ({'color': 'red', 'size': 'big'}, ['color:blue', '--remove-tag', 'size', '-f', '-t', 'color:red'],
                       {'color': 'blue'}),
}


@mark.parametrize('case', list(TAG_CASES))
def test_update_tag_keys_and_removals_pg(contended_db, case: str) -> None:
    """Every tag key set and every tag name removed lands on every task counted as updated."""
    tag, argv, expected = TAG_CASES[case]
    ids = [submit(f'echo {n}', tag=dict(tag)) for n in range(4)]
    other = submit('echo other', tag={'color': 'green'})

    rc, _, stderr = hs_update(*argv)

    assert rc == exit_status.success, stderr
    after = tasks(contended_db)
    assert {task_id: after[task_id]['tag'] for task_id in ids} == {task_id: expected for task_id in ids}
    assert after[other]['tag'] == {'color': 'green'}
    assert 'Updated 4 tasks' in stderr


# A statement on the `task` table.
ON_TASK = re.compile(r'\btask\b', re.I)


def test_update_filtered_uncontended_statements_pg(contended_db, monkeypatch, tmp_path) -> None:
    """An uncontended filtered field update is one UPDATE of `task`, with as many statements on `task` for 30
    rows as for 5."""
    bound = Session.get_bind()
    monkeypatch.setattr('hypershell.task.ensuredb', lambda: None)
    monkeypatch.setattr('hypershell.core.exceptions.default_path', SimpleNamespace(log=str(tmp_path)))
    where = next(action for action in TaskUpdateApp.interface._actions  # noqa: SLF001
                 if action.dest == 'where_clauses')
    executed: List[str] = []

    def record(conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool) -> None:
        if ON_TASK.search(statement):
            executed.append(' '.join(statement.split()))

    counts = {}
    event.listen(bound, 'before_cursor_execute', record)
    try:
        for count in (5, 30):
            ids = [submit(f'echo {n}') for n in range(count)]
            executed.clear()
            monkeypatch.setattr(where, 'default', [])
            try:
                assert TaskUpdateApp.main(['group=7', '-f', '-w', 'group==0']) == exit_status.success
            finally:
                Session.remove()
            assert len([statement for statement in executed if statement.upper().startswith('UPDATE')]) == 1, executed
            assert all(groups(contended_db)[task_id] == 7 for task_id in ids)
            counts[count] = len(executed)
    finally:
        event.remove(bound, 'before_cursor_execute', record)

    assert counts[5] == counts[30], counts


def update_while_held(engine: Engine, task_id: str, *argv: str, holder_sql: str = HOLD_SQL,
                      params: Optional[Dict[str, Any]] = None) -> Tuple[int, str, str]:
    """Run `hs update ARGS...` while a holder keeps task `task_id` locked; release it once the update waits."""
    outcome = contend(engine, holder_sql, {'client_host': 'holder', **(params or {}), 'id': task_id},
                      lambda: hs_update(*argv))
    assert outcome.error is None, repr(outcome.error)
    return outcome.value


def test_contended_update_legacy_lands(contended_db) -> None:
    """`hs update ID FIELD VALUE` that waited on another write to the task writes it, exit 0."""
    task_id = submit()

    rc, _, stderr = update_while_held(contended_db, task_id, task_id, 'group', '3', '-f')

    assert rc == exit_status.success, stderr
    data = tasks(contended_db)[task_id]
    assert (data['group'], data['client_host']) == (3, 'holder')


def test_contended_update_filtered_lands(contended_db) -> None:
    """A filtered update that waited on another write to one of its tasks writes every task."""
    ids = [submit(f'echo {n}') for n in range(4)]

    rc, _, stderr = update_while_held(contended_db, ids[1], '-f', '-R', 'group=5')

    assert rc == exit_status.success, stderr
    assert groups(contended_db) == dict.fromkeys(ids, 5)


def test_contended_update_limit_lands(contended_db) -> None:
    """A `--limit` update that waited on another write to one of its tasks writes every task, exit 0."""
    ids = [submit(f'echo {n}') for n in range(4)]

    rc, _, stderr = update_while_held(contended_db, ids[1], '--limit', '4', '-f', '-R', 'group=5')

    assert rc == exit_status.success, stderr
    assert groups(contended_db) == dict.fromkeys(ids, 5)


def test_contended_update_delete_lands(contended_db) -> None:
    """A filtered delete that waited on another write to one of its tasks deletes every task."""
    ids = [submit(f'echo {n}') for n in range(4)]

    rc, _, stderr = update_while_held(contended_db, ids[1], '-f', '-R', '--delete')

    assert rc == exit_status.success, stderr
    assert tasks(contended_db) == {}


def test_contended_update_delete_limit_lands(contended_db) -> None:
    """A `--delete --limit` that waited on another write to one of its tasks deletes every task."""
    ids = [submit(f'echo {n}') for n in range(4)]

    rc, _, stderr = update_while_held(contended_db, ids[1], '--limit', '4', '-f', '-R', '--delete')

    assert rc == exit_status.success, stderr
    assert tasks(contended_db) == {}


def test_contended_update_leaves_row_moved_out_of_filter(contended_db) -> None:
    """A filtered update that waited on another writer completing one of its tasks leaves that task as it is,
    does not count it, and says so; exit 0."""
    ids = [submit(f'echo {n}') for n in range(4)]
    holder_sql = f'UPDATE task SET client_host = :client_host, exit_status = 0 {BY_ID}'

    rc, _, stderr = update_while_held(contended_db, ids[1], '-f', '-R', 'group=5', holder_sql=holder_sql)

    assert rc == exit_status.success, stderr
    after = tasks(contended_db)
    assert (after[ids[1]]['group'], after[ids[1]]['exit_status']) == (0, 0)
    assert [after[task_id]['group'] for task_id in ids if task_id != ids[1]] == [5, 5, 5]
    assert 'Updated 3 tasks' in stderr, stderr
    assert NOT_MATCHED in stderr, stderr
