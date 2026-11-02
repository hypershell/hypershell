# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Zones in the data layer: the name-to-id mapping, the schema upgrade, and zone-filtered selection.

Every test binds the global session to a throwaway SQLite file and restores it afterwards.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Optional

# Standard libs
import os
import sys
import time
import shutil
import signal
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
from uuid import UUID as PyUUID

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.upgrade import upgrade_schema
from hypershell.data.model import (Entity, Task, Zone, zone_id, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME,
                                   DEFAULT_TASK_GROUP)
from tests import FIXTURE_283


def bind(path: Path) -> Engine:
    """Bind the global session to the SQLite file at `path`."""
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False})
    Session.remove()
    Session.configure(bind=engine)
    return engine


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file, then restore it."""
    engine = bind(tmp_path / 'tasks.db')
    Entity.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


def add_tasks(count: int, zone: Optional[str] = None, group: int = DEFAULT_TASK_GROUP,
              prefix: str = 'echo') -> List[str]:
    """Submit `count` tasks in `zone` and `group`, return their ids in submit order."""
    tasks = [Task.new(args=f'{prefix} {zone} {group} {n}', group=group, zone=zone) for n in range(count)]
    Task.add_all(tasks)
    return [task.id for task in tasks]


def finish(task_id: str, exit_status: int) -> None:
    """Mark a scheduled task as completed with `exit_status`."""
    now = datetime.now().astimezone()
    Task.update(task_id, exit_status=exit_status, completion_time=now)


@mark.unit
def test_zone_id_is_name_derived() -> None:
    """The id is a function of the name; the reserved names give the fixed default id."""
    assert zone_id(None) == zone_id('') == zone_id('default') == zone_id(DEFAULT_ZONE_NAME) == DEFAULT_ZONE_ID
    assert zone_id('gpu') == zone_id('gpu')
    assert zone_id('gpu') != zone_id('cpu')
    assert zone_id('gpu') != DEFAULT_ZONE_ID
    assert str(PyUUID(zone_id('gpu'))) == zone_id('gpu')
    assert str(PyUUID(DEFAULT_ZONE_ID)) == DEFAULT_ZONE_ID


@mark.unit
def test_ensure_is_idempotent(task_db: Engine) -> None:
    """Ensuring a name twice leaves one row with the derived id."""
    assert Zone.ensure('gpu') == zone_id('gpu')
    assert Zone.ensure('gpu') == zone_id('gpu')
    assert Zone.ensure(None) == DEFAULT_ZONE_ID
    assert Zone.ensure('default') == DEFAULT_ZONE_ID
    rows = sorted((zone.id, zone.name) for zone in Zone.query().all())
    assert rows == sorted([(zone_id('gpu'), 'gpu'), (DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME)])
    assert Zone.names_for_ids([zone_id('gpu'), DEFAULT_ZONE_ID]) == {zone_id('gpu'): 'gpu',
                                                                    DEFAULT_ZONE_ID: DEFAULT_ZONE_NAME}


ENSURE_WORKER = """
import os, sys, time
from pathlib import Path
from sqlalchemy import create_engine
from hypershell.data.core import Session
from hypershell.data.model import Zone
engine = create_engine('sqlite:///' + sys.argv[1], connect_args={'check_same_thread': False})
Session.remove()
Session.configure(bind=engine)
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.005)
for n in range(int(sys.argv[3])):
    Zone.ensure(f'zone-{n}')
    Zone.ensure('default')
    Zone.ensure(None)
print('ok')
"""


@mark.unit
def test_ensure_under_concurrent_processes(tmp_path: Path) -> None:
    """Six processes ensuring the same new names and the default at once all succeed, one row each."""
    db, go, rounds, count = tmp_path / 'zones.db', tmp_path / 'go', 20, 6
    engine = create_engine(f'sqlite:///{db}')
    Entity.metadata.create_all(engine)
    engine.dispose()
    procs = [subprocess.Popen([sys.executable, '-c', ENSURE_WORKER, str(db), str(go), str(rounds)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
             for _ in range(count)]
    try:
        time.sleep(1.0)  # let every worker import and wait at the line
        go.touch()
        results = [proc.communicate(timeout=120) for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    assert [proc.returncode for proc in procs] == [0] * count, [err.decode() for _, err in results]
    assert [out.decode().strip() for out, _ in results] == ['ok'] * count
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = conn.execute('SELECT id, name, count(*) FROM zone GROUP BY id, name').fetchall()
        total = conn.execute('SELECT count(*) FROM zone').fetchone()[0]
    finally:
        conn.close()
    expected = {(zone_id(f'zone-{n}'), f'zone-{n}', 1) for n in range(rounds)}
    expected.add((DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME, 1))
    assert set(rows) == expected
    assert total == rounds + 1


def zone_schema(path: Path) -> tuple:
    """What the zone feature adds to a schema: the table, the task column, and the index."""
    insp = inspect(create_engine(f'sqlite:///{path}'))
    return (insp.has_table('zone'),
            'zone' in [column['name'] for column in insp.get_columns('task')],
            'index_tasks_zone' in [index['name'] for index in insp.get_indexes('task')])


def make_pre_zone(path: Path) -> Path:
    """A database in the 2.9.0a2 shape: today's schema without the zone table, column and index."""
    engine = create_engine(f'sqlite:///{path}')
    Entity.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('DROP INDEX IF EXISTS index_tasks_zone')
        columns = [row[1] for row in conn.execute('PRAGMA table_info("task")')]
        if 'zone' in columns:
            conn.execute('ALTER TABLE task DROP COLUMN zone')
        conn.execute('DROP TABLE IF EXISTS zone')
        columns = [row[1] for row in conn.execute('PRAGMA table_info("task")')]
        now = datetime.now().astimezone().isoformat()
        for n in range(5):
            values = {'id': f'00000000-0000-4000-8000-00000000000{n}', 'group': 0, 'args': f'echo {n}',
                      'submit_id': '00000000-0000-4000-8000-0000000000ff', 'submit_time': now,
                      'attempt': 1, 'retried': 0, 'part': 0, 'tag': '{}'}
            names = [name for name in values if name in columns]
            conn.execute(f'INSERT INTO task ({", ".join(chr(34) + n + chr(34) for n in names)}) '
                         f'VALUES ({", ".join("?" for _ in names)})', [values[name] for name in names])
        conn.commit()
    finally:
        conn.close()
    return path


@mark.unit
@mark.parametrize('shape', ['2.9.0a2', '2.8.3'])
def test_upgrade_adds_zone_in_place(tmp_path: Path, shape: str) -> None:
    """An older database gains the zone table, column and index; a second run is a no-op."""
    path = tmp_path / 'old.db'
    if shape == '2.8.3':
        shutil.copyfile(FIXTURE_283, path)
    else:
        make_pre_zone(path)
    assert zone_schema(path) == (False, False, False)
    engine = create_engine(f'sqlite:///{path}')
    try:
        changes = upgrade_schema(engine)
        assert 'Created table zone' in changes
        assert 'Added column task.zone' in changes
        assert 'Created index index_tasks_zone on task' in changes
        assert upgrade_schema(engine) == []
    finally:
        engine.dispose()
    assert zone_schema(path) == (True, True, True)
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        rows, unzoned = conn.execute('SELECT count(*), count(*) - count(zone) FROM task').fetchone()
    finally:
        conn.close()
    assert rows > 0 and rows == unzoned


@mark.unit
def test_new_task_stores_default_zone_as_null(task_db: Engine) -> None:
    """The default zone is stored as NULL; a named zone keeps its id."""
    assert Task.new(args='echo a').zone is None
    assert Task.new(args='echo a', zone=DEFAULT_ZONE_ID).zone is None
    assert Task.new(args='echo a', zone=zone_id('gpu')).zone == zone_id('gpu')
    assert 'zone' in Task.columns
    assert list(Task.columns).index('zone') == list(Task.columns).index('fingerprint') + 1


@mark.unit
@mark.parametrize('eager', [False, True])
def test_next_selects_only_its_zone(task_db: Engine, eager: bool) -> None:
    """A zone selects only its tasks; the default id selects NULL rows; no zone selects all."""
    gpu, cpu = zone_id('gpu'), zone_id('cpu')
    default_ids, gpu_ids, cpu_ids = add_tasks(3), add_tasks(2, gpu), add_tasks(2, cpu)
    assert [t.id for t in Task.next(limit=10, zone=gpu, eager=eager)] == gpu_ids
    assert [t.id for t in Task.next(limit=10, zone=DEFAULT_ZONE_ID, eager=eager)] == default_ids
    assert [t.id for t in Task.next(limit=10, zone=cpu, eager=eager)] == cpu_ids
    assert Task.next(limit=10, zone=gpu, eager=eager) == []
    more = add_tasks(2) + add_tasks(1, gpu)
    assert sorted(t.id for t in Task.next(limit=10, eager=eager)) == sorted(more)


@mark.unit
@mark.parametrize('eager', [False, True])
def test_retry_keeps_zone(task_db: Engine, eager: bool) -> None:
    """A failed zoned task is retried only for its zone, and the retry row carries the zone."""
    gpu = zone_id('gpu')
    first, = add_tasks(1, gpu)
    assert [t.id for t in Task.next(limit=10, zone=gpu, attempts=2, eager=eager)] == [first]
    finish(first, 1)
    assert Task.next(limit=10, zone=DEFAULT_ZONE_ID, attempts=2, eager=eager) == []
    assert Task.next(limit=10, zone=zone_id('cpu'), attempts=2, eager=eager) == []
    # Eager selection can list a new retry row twice (pre-existing, zone-independent), so compare ids.
    retry, = {t.id: t for t in Task.next(limit=10, zone=gpu, attempts=2, eager=eager)}.values()
    assert retry.previous_id == first
    assert retry.attempt == 2
    assert Task.from_id(retry.id, caching=False).zone == gpu
    assert Task.from_id(first, caching=False).next_id == retry.id


@mark.unit
def test_group_gating_stays_global(task_db: Engine) -> None:
    """A group is held while any zone has incomplete work in it, and advances once all are done."""
    gpu = zone_id('gpu')
    default_ids = add_tasks(2, group=0)
    gpu_id, = add_tasks(1, gpu, group=0)
    later = add_tasks(2, group=1)
    for task in Task.next(limit=10, group=0, zone=DEFAULT_ZONE_ID):
        finish(task.id, 0)
    assert Task.next(limit=10, group=0, zone=DEFAULT_ZONE_ID) == []
    held = Task.increment_group(0, attempts=1)
    assert held.value == 0
    assert 'waiting on 1 tasks' in held.reason
    assert Task.count_remaining(group=0) == 1
    assert [t.id for t in Task.next(limit=10, group=0, zone=gpu)] == [gpu_id]
    assert Task.increment_group(0, attempts=1).value == 0
    finish(gpu_id, 0)
    assert Task.increment_group(0, attempts=1).value == 1
    assert Task.current_group().value == 0
    assert sorted(t.id for t in Task.next(limit=10, group=1, zone=DEFAULT_ZONE_ID)) == sorted(later)
    assert Task.current_group().value == 1
    assert len(default_ids) == 2
