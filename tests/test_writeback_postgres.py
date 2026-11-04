# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Match a returned result to its dispatch on PostgreSQL.

A result is written only while its row still carries the `schedule_time` the result echoes. These
tests show that the value PostgreSQL returns for a dispatched row survives the bundle's JSON and
matches the conditional update exactly: timezone-aware values, any UTC offset, and every
microsecond, with a one-microsecond difference refused.

They need a PostgreSQL server, the same one the upgrade tests use:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres

They are skipped when HS_TEST_POSTGRES is unset.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, Optional

# Standard libs
import os
import uuid
import logging
from datetime import datetime, timedelta, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

# Internal libs
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, ResultOutcome, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, Receiver
from tests.test_writeback_statement import CASES, stamp, apply_twins, check_twins


ADMIN_URL: Optional[str] = os.getenv('HS_TEST_POSTGRES')

pytestmark = [
    mark.integration,
    mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests'),
]

CLIENT = (str(uuid.uuid4()), 'client-a')
RESULT_COLUMNS = ('client_id', 'client_host', 'command', 'start_time', 'completion_time', 'waited', 'duration',
                  'exit_status', 'outpath', 'errpath', 'csvpath', 'cores_max', 'memory_max')

# Session time zones: the server default in the CI image, and one with a non-zero, non-hour offset.
SESSION_ZONES = ['UTC', 'Asia/Kolkata']

# Stamps covering both ends of the microsecond field, a zero microsecond (which the wire omits),
# and offsets on both sides of UTC.
STAMPS = {
    'utc-usec-1': datetime(2026, 11, 2, 17, 17, 7, 1, tzinfo=timezone.utc),
    'east-usec-999999': datetime(2026, 11, 2, 22, 47, 7, 999999, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    'west-usec-0': datetime(2026, 11, 2, 10, 17, 7, 0, tzinfo=timezone(timedelta(hours=-7))),
    'west-usec-212078': datetime(2026, 3, 8, 1, 59, 59, 212078, tzinfo=timezone(timedelta(hours=-5))),
}


@fixture(scope='module')
def admin() -> Iterator[Engine]:
    """Admin engine on the maintenance database (CREATE/DROP DATABASE need autocommit)."""
    engine = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    yield engine
    engine.dispose()


@fixture(params=SESSION_ZONES)
def pg_tasks(request: pytest.FixtureRequest, admin: Engine) -> Iterator[Engine]:
    """Bind the global session to a throwaway database in one session time zone; drop it afterwards."""
    name = f't_{uuid.uuid4().hex[:16]}'
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE {name}'))
    url = make_url(ADMIN_URL).set(database=name)
    engine = create_engine(url, connect_args={'options': f'-c timezone={request.param}'})
    try:
        Entity.metadata.create_all(engine)
        Session.remove()
        Session.configure(bind=engine)
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))


def submit(args: str = 'echo writeback') -> str:
    """Add one task and return its id."""
    task = Task.new(args=args)
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
    copy.csvpath = f'task-{copy.id}.csv'
    copy.cores_max = 1.5
    copy.memory_max = 2048.0
    return copy


def returned(copy: Task) -> Task:
    """The finished copy as the server receives it, after the bundle's JSON."""
    return deserialize_tasks(serialize_tasks([copy]))[0]


def shifted(copy: Task, microseconds: int = 1) -> Task:
    """A returned copy whose echoed schedule_time is off by some microseconds."""
    other = returned(copy)
    other.schedule_time = other.schedule_time + timedelta(microseconds=microseconds)
    return other


def row(task_id: str) -> dict:
    """The task's current row, freshly read."""
    return Task.from_id(task_id, caching=False).to_dict()


def results_in(data: dict) -> dict:
    """The result columns of a row."""
    return {name: data[name] for name in RESULT_COLUMNS}


def results_of(copy: Task) -> dict:
    """The result columns a copy carries."""
    return {name: getattr(copy, name) for name in RESULT_COLUMNS}


@mark.parametrize('microseconds', [1, -1])
def test_dispatched_echo_matches(pg_tasks, microseconds: int) -> None:
    """The schedule_time of a real dispatch matches after the wire; one microsecond off does not."""
    task_id = submit()
    copy = finish(dispatch_one(task_id))
    if copy.schedule_time is None or copy.schedule_time.tzinfo is None:
        pytest.fail(f'Expected a timezone-aware schedule_time from PostgreSQL, got {copy.schedule_time!r}')
    before = row(task_id)
    if before['schedule_time'] != copy.schedule_time:
        pytest.fail('The dispatched copy does not carry the stored schedule_time')

    assert Task.apply_results([shifted(copy, microseconds)]) == [ResultOutcome.STALE]
    assert row(task_id) == before
    assert Task.apply_results([returned(copy)]) == [ResultOutcome.APPLIED]
    assert results_in(row(task_id)) == results_of(copy)
    assert row(task_id)['schedule_time'] == before['schedule_time']
    assert Task.apply_results([returned(copy)]) == [ResultOutcome.DUPLICATE]


@mark.parametrize('stamp', list(STAMPS.values()), ids=list(STAMPS))
def test_stamped_echo_matches(pg_tasks, stamp: datetime) -> None:
    """A stored schedule_time at any offset and microsecond round-trips and matches exactly."""
    task_id = submit()
    dispatch_one(task_id)
    Task.update(task_id, schedule_time=stamp)
    packed = Task.from_id(task_id, caching=False)  # the server packs a bundle from the reloaded row
    copy = finish(deserialize_tasks(serialize_tasks([packed]))[0])
    if copy.schedule_time is None or copy.schedule_time.tzinfo is None or copy.schedule_time != stamp:
        pytest.fail(f'Stored stamp {stamp!r} came back as {copy.schedule_time!r}')

    assert Task.apply_results([shifted(copy, 1)]) == [ResultOutcome.STALE]
    assert Task.apply_results([shifted(copy, -1)]) == [ResultOutcome.STALE]
    assert row(task_id)['completion_time'] is None
    assert Task.apply_results([returned(copy)]) == [ResultOutcome.APPLIED]
    assert results_in(row(task_id)) == results_of(copy)


def test_stale_echo_after_redispatch(pg_tasks, caplog: pytest.LogCaptureFixture) -> None:
    """After a revert and a new dispatch, the first dispatch's echo is refused through the Receiver."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    task_id = submit()
    first = finish(dispatch_one(task_id), exit_status=1)
    confirm = Confirm(queue=None)
    confirm.client_info = ClientInfo(client_id=CLIENT[0], client_host=CLIENT[1], task_ids=[task_id])
    confirm.update_info()
    Task.revert_orphaned(CLIENT[0])
    second = dispatch_one(task_id)
    if second.schedule_time == first.schedule_time:
        pytest.fail('Re-dispatch reused the first schedule_time')
    before = row(task_id)

    receiver = Receiver(queue=None)
    receiver.tasks = [returned(first)]
    receiver.update_tasks()

    assert row(task_id) == before
    stale = [record.getMessage() for record in caplog.records
             if record.levelno == logging.WARNING and 'stale' in record.getMessage()]
    assert len(stale) == 1 and task_id in stale[0] and CLIENT[0] in stale[0]

    receiver.tasks = [returned(finish(second))]
    receiver.update_tasks()
    assert row(task_id)['exit_status'] == 0


@mark.parametrize('name', list(CASES))
def test_applied_values_match_reference(pg_tasks, name: str) -> None:
    """An applied result stores what the per-result statement stores, through the same SQL.

    The raw values are read with a plain-text query, so psycopg's own loaders return them and
    SQLAlchemy's type processing plays no part in the comparison.
    """
    first_id, second_id = submit('echo twin'), submit('echo twin')
    copies = {copy.id: copy for copy in deserialize_tasks(serialize_tasks(Task.next(limit=2)))}
    if set(copies) != {first_id, second_id}:
        pytest.fail(f'Expected to dispatch both twins, got {list(copies)}')
    before = {task_id: row(task_id) for task_id in copies}
    client_id = str(uuid.uuid4())
    first, second = (returned(stamp(copies[task_id], CASES[name], client_id)) for task_id in (first_id, second_id))

    captured = apply_twins(pg_tasks, first, second)

    check_twins(first_id, second_id, before, captured)
