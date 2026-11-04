# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The result writeback reuses its statements and stores exactly what the per-result statement stored.

The server writes thousands of results a second on a busy site, so the writeback builds its
conditional UPDATE and its classifying SELECT once and binds only each result's values. One test
pins that reuse, deterministically, by the identity of the statements the session executes. The
others pin what reuse must not change: for each case, one task written through ``Task.apply_results``
and its twin written by a reference statement, built per result the way the writeback built it
before reuse, store the same raw values, and the UPDATE has the same SET list and WHERE clause.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple
from pathlib import Path

# Standard libs
import io
import re
import logging
from datetime import datetime, timedelta, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, ResultOutcome, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, Receiver


CLIENT_A = (uuid(), 'client-a')

# Result values for the reference-equivalence cases: UTC offsets on both sides of zero, both ends
# of the microsecond field, absent output paths, and a non-zero exit status.
CASES: Dict[str, Dict[str, Any]] = {
    'east-usec-999999': {'offset': timedelta(hours=5, minutes=30), 'microsecond': 999999},
    'west-usec-0': {'offset': timedelta(hours=-7), 'microsecond': 0},
    'utc': {'offset': timedelta(0), 'microsecond': 212078},
    'no-paths': {'paths': False},
    'exit-3': {'exit_status': 3},
}


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file, then restore it."""
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


def submit(args: str = 'echo writeback') -> str:
    """Add one task and return its id."""
    task = Task.new(args=args)
    Task.add(task)
    return task.id


def dispatch(limit: int = 1, attempts: int = 1) -> List[Task]:
    """Schedule tasks and return the copies a client receives, through the real bundle codec."""
    return deserialize_tasks(serialize_tasks(Task.next(limit=limit, attempts=attempts)))


def dispatch_one(task_id: str, attempts: int = 1) -> Task:
    """Schedule exactly the one task `task_id`; fail the test otherwise."""
    copies = dispatch(attempts=attempts)
    if [copy.id for copy in copies] != [task_id]:
        pytest.fail(f'Expected to dispatch task ({task_id}), got {[copy.id for copy in copies]}')
    return copies[0]


def confirm(client: Tuple[str, str], ids: List[str]) -> None:
    """Confirm task ids to a client, as the server's Confirm thread does."""
    machine = Confirm(queue=None)
    machine.client_info = ClientInfo(client_id=client[0], client_host=client[1], task_ids=ids)
    machine.update_info()


def finish(copy: Task, exit_status: int = 0, client: Tuple[str, str] = CLIENT_A) -> Task:
    """Stamp the result columns on a copy, as a client does when it runs the task."""
    start_time = datetime.now().astimezone()
    copy.client_id, copy.client_host = client
    copy.command = copy.args
    copy.start_time = start_time
    copy.completion_time = start_time + timedelta(seconds=2)
    copy.waited = 1
    copy.duration = 2
    copy.exit_status = exit_status
    copy.outpath = f'task-{copy.id}.out'
    copy.errpath = f'task-{copy.id}.err'
    copy.csvpath = f'task-{copy.id}.csv'
    copy.cores_max = 1.5
    copy.memory_max = 2048.0
    return copy


def receive(copies: List[Task], failures: Optional[io.StringIO] = None) -> None:
    """Return a bundle of finished copies through the wire and the server's Receiver."""
    machine = Receiver(queue=None, redirect_failures=failures)
    machine.tasks = deserialize_tasks(serialize_tasks(copies))
    machine.update_tasks()


def row(task_id: str) -> Dict[str, Any]:
    """The task's current row, freshly read."""
    return Task.from_id(task_id, caching=False).to_dict()


def stamp(copy: Task, case: Dict[str, Any], client_id: str) -> Task:
    """Stamp a copy with the result values of one reference-equivalence case."""
    zone = timezone(case.get('offset', timedelta(hours=-5)))
    start_time = datetime(2026, 11, 4, 9, 1, 2, case.get('microsecond', 345678), tzinfo=zone)
    copy.client_id, copy.client_host = client_id, 'client-ref'
    copy.command = 'echo reference'
    copy.start_time = start_time
    copy.completion_time = start_time + timedelta(seconds=7, microseconds=1)
    copy.waited = 3
    copy.duration = 7
    copy.exit_status = case.get('exit_status', 0)
    paths = case.get('paths', True)
    copy.outpath = 'reference.out' if paths else None
    copy.errpath = 'reference.err' if paths else None
    copy.csvpath = 'reference.csv' if paths else None
    copy.cores_max = 2.25
    copy.memory_max = 4096.5
    return copy


def reference_update(copy: Task) -> Any:
    """The conditional UPDATE built for one result, as the writeback built it per result."""
    return (
        update(Task)
        .where(Task.id == copy.id,
               Task.schedule_time == copy.schedule_time,
               Task.completion_time.is_(None))
        .values({name: getattr(copy, name) for name in Task.result_columns})
        .execution_options(synchronize_session=False)
    )


def raw_results(task_id: str) -> Tuple[Any, ...]:
    """The result columns as the database driver returns them, with no SQLAlchemy type processing."""
    names = ', '.join(Task.result_columns)
    return tuple(Session.execute(text(f'SELECT {names} FROM {Task.__table__.fullname} WHERE id = :id'),
                                 {'id': task_id}).one())


def set_and_where(sql: str) -> Tuple[set, str]:
    """The column names in an UPDATE's SET list, and its WHERE clause."""
    match = re.match(r'\s*UPDATE\s+\S+\s+SET\s+(.*?)\s+WHERE\s+(.*)$', sql, re.S | re.I)
    if not match:
        pytest.fail(f'Not an UPDATE with a WHERE clause: {sql}')
    return {item.split('=', 1)[0].strip() for item in match.group(1).split(',')}, match.group(2)


def assert_where_fences_assignment(where: str) -> None:
    """The WHERE clause matches the row's id and dispatch, and only while it has no completion time."""
    table = Task.__table__.name
    assert re.search(rf'\b{table}\.id = ', where), where
    assert re.search(rf'\b{table}\.schedule_time = ', where), where
    assert re.search(rf'\b{table}\.completion_time IS NULL\b', where), where


def apply_twins(engine: Engine, first: Task, second: Task) -> List[str]:
    """Write `first` through the writeback and `second` through the reference; return the writeback's UPDATEs."""
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith('UPDATE'):
            captured.append(statement)

    event.listen(engine, 'before_cursor_execute', capture)
    try:
        outcomes = Task.apply_results([first])
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert outcomes == [ResultOutcome.APPLIED]
    matched = Session.execute(reference_update(second)).rowcount
    Session.commit()
    if matched != 1:
        pytest.fail(f'The reference statement matched {matched} rows')
    return captured


def check_twins(first_id: str, second_id: str, before: Dict[str, Dict[str, Any]], captured: List[str]) -> None:
    """Both twins store the same result values, nothing else changed, and the UPDATE kept its shape."""
    assert raw_results(first_id) == raw_results(second_id)
    after = {task_id: row(task_id) for task_id in (first_id, second_id)}
    assert ({name: after[first_id][name] for name in Task.result_columns} ==
            {name: after[second_id][name] for name in Task.result_columns})
    for task_id, data in after.items():
        for name in Task.columns:
            if name not in Task.result_columns:
                assert data[name] == before[task_id][name], name
    assert len(captured) == 1, captured
    columns, where = set_and_where(captured[0])
    assert columns == set(Task.result_columns)
    assert_where_fences_assignment(where)


@mark.unit
@mark.parametrize('name', list(CASES))
def test_applied_values_match_reference(task_db, name: str) -> None:
    """An applied result stores what the per-result statement stores, through the same SQL."""
    first_id, second_id = submit('echo twin'), submit('echo twin')
    copies = {copy.id: copy for copy in dispatch(limit=2)}
    if set(copies) != {first_id, second_id}:
        pytest.fail(f'Expected to dispatch both twins, got {list(copies)}')
    before = {task_id: row(task_id) for task_id in copies}
    client_id = uuid()
    first, second = (deserialize_tasks(serialize_tasks([stamp(copies[task_id], CASES[name], client_id)]))[0]
                     for task_id in (first_id, second_id))

    captured = apply_twins(task_db, first, second)

    check_twins(first_id, second_id, before, captured)


@mark.unit
def test_writeback_reuses_its_statements(task_db, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    """The writeback executes one UPDATE object and at most one SELECT object for every result.

    This guards the writeback's per-result CPU cost by the mechanism that removed it: statements
    built once and bound per result. A redesign of the write path (a batched executemany, a
    different statement cache) changes what is cheap to observe, and should update this test with it.
    """
    caplog.set_level(logging.DEBUG, logger='hypershell')
    statements = []
    recording = False
    execute = Session.execute

    def recorded(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if recording:
            statements.append(statement)
        return execute(statement, *args, **kwargs)

    def writeback(copies: List[Task]) -> None:
        nonlocal recording
        recording = True
        try:
            receive(copies)
        finally:
            recording = False

    monkeypatch.setitem(vars(Session), 'execute', recorded)
    ids = [submit(f'echo {i}') for i in range(10)]
    copies = {copy.id: copy for copy in dispatch(limit=10)}
    if set(copies) != set(ids):
        pytest.fail(f'Expected to dispatch all {len(ids)} tasks, got {len(copies)}')
    confirm(CLIENT_A, ids)
    first = [finish(copies[task_id]) for task_id in ids]

    writeback(first[:5])
    writeback(first[:1] + first[5:8])
    Task.revert_orphaned(CLIENT_A[0])
    again = dispatch(limit=10)
    if sorted(copy.id for copy in again) != sorted(ids[8:]):
        pytest.fail(f'Expected to re-dispatch the two reverted tasks, got {[copy.id for copy in again]}')
    writeback(first[8:9] + [finish(copy) for copy in again])

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    completed = [record.getMessage() for record in caplog.records if record.getMessage().startswith('Completed task')]
    if (len(completed), sum('duplicate' in w for w in warnings), sum('stale' in w for w in warnings)) != (10, 1, 1):
        pytest.fail(f'Expected 10 applied, 1 duplicate and 1 stale result, got {completed} and {warnings}')
    updates = [statement for statement in statements if getattr(statement, 'is_update', False)]
    selects = [statement for statement in statements if getattr(statement, 'is_select', False)]
    if len(updates) < 8 or not selects:
        pytest.fail(f'Expected 8 or more UPDATEs and some SELECTs, got {len(updates)} and {len(selects)}')

    distinct_updates = len({id(statement) for statement in updates})
    distinct_selects = len({id(statement) for statement in selects})
    assert distinct_updates == 1, f'{distinct_updates} UPDATE objects over {len(updates)} executions'
    assert distinct_selects <= 1, f'{distinct_selects} SELECT objects over {len(selects)} executions'
