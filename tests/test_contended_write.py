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
from dataclasses import dataclass
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.core.types import to_json_type
from hypershell.data.model import Task, CANCEL_STATUS, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, ConfirmState, Receiver
from tests import main_lines
from tests.test_timescale import (admin, ts_database, ts_site, database_engine, is_hypertable, uuid7,  # noqa: F401
                                  needs_server, TIMESCALE)


pytestmark = [mark.integration, needs_server]

CLIENT = (str(uuid.uuid4()), 'client-a')

POLL = 0.02      # Seconds between looks at the server for the blocked write.
DEADLINE = 30.0  # Seconds to see the write blocked, and again for it to finish once released.

# The statement a blocked HyperShell write is running, with or without a schema prefix.
UPDATE_TASK = re.compile(r'^\s*UPDATE\s+(\S+\.)?"?task"?\s', re.I)

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


def submit(args: str = 'echo contended') -> str:
    """Add one task with a time-ordered id (a hypertable partitioned by id refuses others); return its id."""
    task = Task.new(args=args)
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
    """Wait until the server shows an UPDATE of task waiting on the holder's lock; else say why not."""
    query = text('SELECT wait_event_type, query FROM pg_stat_activity '
                 'WHERE datname = current_database() AND :holder = ANY(pg_blocking_pids(pid))')
    deadline = time.monotonic() + DEADLINE
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
        while time.monotonic() < deadline:
            for wait_type, statement in observer.execute(query, {'holder': holder}).all():
                if wait_type == 'Lock' and UPDATE_TASK.match(statement or ''):
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
