# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Zones on PostgreSQL: the get-or-create upsert, zone-filtered selection, and the in-place upgrade;
also the scheduler's task-group checks, which aggregate over the native UUID task id.

These tests need a PostgreSQL server; each one creates and drops its own database:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres

They are skipped when HS_TEST_POSTGRES is unset.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Iterator, List, Optional

# Standard libs
import os
import uuid
import threading
from datetime import datetime

# External libs
from pytest import mark, fixture
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

# Internal libs
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.upgrade import upgrade_schema
from hypershell.data.model import Task, Zone, zone_id, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME, DEFAULT_TASK_GROUP


ADMIN_URL: Optional[str] = os.getenv('HS_TEST_POSTGRES')

pytestmark = [
    mark.integration,
    mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests'),
]


@fixture
def pg_engine() -> Iterator[Engine]:
    """A fresh database with today's schema, bound to the global session; dropped afterwards."""
    admin = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    name = f't_{uuid.uuid4().hex[:16]}'
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE {name}'))
    engine = create_engine(make_url(ADMIN_URL).set(database=name))
    try:
        upgrade_schema(engine)
        Session.remove()
        Session.configure(bind=engine)
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
        admin.dispose()


def add_tasks(count: int, zone: Optional[str] = None) -> List[str]:
    """Submit `count` tasks in `zone`, return their ids in submit order."""
    tasks = [Task.new(args=f'echo {zone} {n}', zone=zone) for n in range(count)]
    Task.add_all(tasks)
    return [task.id for task in tasks]


def test_ensure_upsert_under_concurrent_sessions(pg_engine: Engine) -> None:
    """Eight sessions ensuring the same new names and the default at once all succeed, one row each."""
    count, rounds = 8, 10
    start, errors = threading.Barrier(count), []

    def worker() -> None:
        try:
            start.wait(timeout=30)
            for n in range(rounds):
                assert Zone.ensure(f'zone-{n}') == zone_id(f'zone-{n}')
                assert Zone.ensure(None) == DEFAULT_ZONE_ID
        except Exception as error:  # noqa: broad (reported below)
            errors.append(repr(error))
        finally:
            Session.remove()

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    with pg_engine.connect() as conn:
        rows = conn.execute(text('SELECT CAST(id AS TEXT), name FROM zone')).all()
    expected = {(zone_id(f'zone-{n}'), f'zone-{n}') for n in range(rounds)}
    expected.add((DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME))
    assert sorted(tuple(row) for row in rows) == sorted(expected)


def test_zone_filtered_selection(pg_engine: Engine) -> None:
    """A zone selects only its tasks, the default id selects NULL rows, and a retry keeps its zone."""
    gpu = zone_id('gpu')
    default_ids, gpu_ids = add_tasks(3), add_tasks(2, gpu)
    assert [str(t.id) for t in Task.next(limit=10, zone=gpu, attempts=2)] == gpu_ids
    assert [str(t.id) for t in Task.next(limit=10, zone=DEFAULT_ZONE_ID, attempts=2)] == default_ids
    Task.update(gpu_ids[0], exit_status=1, completion_time=datetime.now().astimezone())
    assert Task.next(limit=10, zone=DEFAULT_ZONE_ID, attempts=2) == []
    retry, = Task.next(limit=10, zone=gpu, attempts=2)
    assert str(retry.previous_id) == gpu_ids[0]
    assert str(Task.from_id(retry.id, caching=False).zone) == gpu


def test_upgrade_adds_zone_in_place(pg_engine: Engine) -> None:
    """A schema without zones gains the table, column and index; a second run is a no-op."""
    add_tasks(2)
    Session.remove()
    with pg_engine.begin() as conn:
        conn.execute(text('DROP INDEX index_tasks_zone'))
        conn.execute(text('ALTER TABLE task DROP COLUMN zone'))
        conn.execute(text('DROP TABLE zone'))
    changes = upgrade_schema(pg_engine)
    assert 'Created table zone' in changes
    assert 'Added column task.zone' in changes
    assert 'Created index index_tasks_zone on task' in changes
    assert upgrade_schema(pg_engine) == []
    insp = inspect(pg_engine)
    assert {c['name']: str(c['type']) for c in insp.get_columns('task')}['zone'] == 'UUID'
    with pg_engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM task WHERE zone IS NULL')).scalar() == 2


def test_group_checks_on_native_uuid_ids(pg_engine: Engine) -> None:
    """The scheduler's group checks run on PostgreSQL: the start, an advance, and a hold on a spent failure.

    PostgreSQL has no `min` over `uuid`, so the example a held group names is taken over the id as text.
    """
    failed_id, done_id = add_tasks(2)
    assert Task.current_group().value == DEFAULT_TASK_GROUP  # Nothing scheduled: the earliest group.
    assert {str(t.id) for t in Task.next(limit=10, attempts=1)} == {failed_id, done_id}
    assert Task.current_group().value == DEFAULT_TASK_GROUP  # The most recently scheduled group.
    now = datetime.now().astimezone()
    Task.update(done_id, exit_status=0, completion_time=now)
    Task.update(failed_id, exit_status=3, completion_time=now)
    held = Task.increment_group(DEFAULT_TASK_GROUP, attempts=1)  # A site server's scheduler (no retries).
    assert held.value == DEFAULT_TASK_GROUP and not held.viable
    assert held.reason == f'at least 1 tasks exceeding allowed retries, example: {failed_id}'
    Task.update(failed_id, retried=True)
    after = Task.increment_group(DEFAULT_TASK_GROUP, attempts=1)
    assert after.viable and after.reason == 'no task groups remain'
