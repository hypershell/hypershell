# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the in-place schema upgrade step."""


# Type annotations
from __future__ import annotations
from typing import List, Tuple

# Standard libs
import os
import sys
import time
import shutil
import logging
import sqlite3
import subprocess
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from sqlalchemy import (create_engine, event, inspect, text, Column, Computed, ForeignKey, Identity, Index,
                        Integer, MetaData, Text, DateTime, func)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# Internal libs
import hypershell.data
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data import upgrade
from hypershell.data.model import Entity
from hypershell.data.upgrade import upgrade_schema, Step, Plan
from tests import FIXTURE_283, main_lines, sqlite_schema, task_columns


@fixture
def legacy_path(tmp_path: Path) -> Path:
    """A private copy of the 2.8.3 database."""
    if not FIXTURE_283.is_file():
        pytest.fail(f'Missing fixture: {FIXTURE_283}')
    path = tmp_path / 'legacy.db'
    shutil.copyfile(FIXTURE_283, path)
    return path


def sqlite_engine(path: Path) -> Engine:
    """A private engine on a SQLite file."""
    return create_engine(f'sqlite:///{path}')


@fixture
def records() -> List[logging.LogRecord]:
    """Every record the upgrade step logs, at any level."""
    captured: List[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(upgrade.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


def copied_metadata() -> MetaData:
    """A private copy of the model metadata that a test may extend."""
    metadata = MetaData()
    for table in Entity.metadata.sorted_tables:
        table.to_metadata(metadata)
    return metadata


def count_ddl(engine: Engine) -> List[str]:
    """Record every ALTER/CREATE/DROP statement the engine sends."""
    statements: List[str] = []

    @event.listens_for(engine, 'before_cursor_execute')
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: unused
        if statement.lstrip().upper().startswith(('ALTER', 'CREATE', 'DROP')):
            statements.append(statement)

    return statements


@mark.unit
def test_upgrade_then_noop(legacy_path: Path, records: List[logging.LogRecord], monkeypatch) -> None:
    """A second run takes no lock, sends no DDL, logs nothing above trace, and changes nothing."""
    engine = sqlite_engine(legacy_path)
    changes = upgrade_schema(engine)
    assert 'Added column task.part' in changes
    assert all(record.levelno == logging.INFO for record in records)
    assert [record.getMessage() for record in records] == changes

    schema = sqlite_schema(legacy_path)
    records.clear()
    locks = []
    monkeypatch.setattr(upgrade, '_begin_sqlite', lambda conn: locks.append(conn))
    statements = count_ddl(engine)
    assert upgrade_schema(engine) == []
    assert locks == []
    assert statements == []
    assert all(record.levelno < logging.DEBUG for record in records)
    assert sqlite_schema(legacy_path) == schema


def spy_plans(monkeypatch) -> List[Tuple[Engine, int]]:
    """Record (engine, number of steps) for every plan."""
    calls: List[Tuple[Engine, int]] = []
    real_plan = upgrade._plan

    def _plan(conn, metadata):
        plan = real_plan(conn, metadata)
        calls.append((conn.engine, len(plan.steps)))
        return plan

    monkeypatch.setattr(upgrade, '_plan', _plan)
    return calls


@mark.unit
def test_race_replans_under_lock(legacy_path: Path, records: List[logging.LogRecord], monkeypatch) -> None:
    """A peer that upgrades between our first plan and our lock leaves us nothing to do, and no error."""
    first, second = sqlite_engine(legacy_path), sqlite_engine(legacy_path)
    plans = spy_plans(monkeypatch)
    real_begin = upgrade._begin_sqlite
    mark_after_peer = []

    def _begin(conn):
        if conn.engine is second and not mark_after_peer:
            assert upgrade_schema(first)  # The peer wins the race.
            mark_after_peer.append(len(records))
        real_begin(conn)

    monkeypatch.setattr(upgrade, '_begin_sqlite', _begin)
    assert upgrade_schema(second) == []

    second_plans = [steps for engine, steps in plans if engine is second]
    assert len(second_plans) == 2
    assert second_plans[0] > 0
    assert second_plans[1] == 0
    winner = records[:mark_after_peer[0]]
    assert winner and all(record.levelno == logging.INFO for record in winner)
    assert any(record.getMessage() == 'Added column task.source' for record in winner)
    assert all(record.levelno < logging.DEBUG for record in records[mark_after_peer[0]:])
    assert 'part' in task_columns(legacy_path)


@mark.unit
def test_race_without_replan_fails(legacy_path: Path, monkeypatch) -> None:
    """Mutation check: applying the stale lock-free plan under the lock collides with the peer."""
    first, second = sqlite_engine(legacy_path), sqlite_engine(legacy_path)
    real_plan, real_begin = upgrade._plan, upgrade._begin_sqlite
    stale = {}

    def _plan(conn, metadata):
        if conn.engine is second:
            if 'plan' not in stale:
                stale['plan'] = real_plan(conn, metadata)
            return stale['plan']
        return real_plan(conn, metadata)

    def _begin(conn):
        if conn.engine is second and not stale.get('raced'):
            stale['raced'] = upgrade_schema(first)
        real_begin(conn)

    monkeypatch.setattr(upgrade, '_plan', _plan)
    monkeypatch.setattr(upgrade, '_begin_sqlite', _begin)
    with pytest.raises(SchemaUpgradeError, match='duplicate column name'):
        upgrade_schema(second)


@mark.unit
def test_generic_column_add(legacy_path: Path) -> None:
    """A newly declared nullable or constant-defaulted column is added with no per-column code."""
    metadata = copied_metadata()
    task = metadata.tables['task']
    task.append_column(Column('note', Text, nullable=True))
    task.append_column(Column('weight', Integer, nullable=False, server_default=text('7')))
    changes = upgrade_schema(sqlite_engine(legacy_path), metadata)
    assert 'Added column task.note' in changes
    assert 'Added column task.weight' in changes
    conn = sqlite3.connect(str(legacy_path))
    try:
        assert conn.execute('SELECT note, weight FROM task').fetchall() == [(None, 7)] * 7
    finally:
        conn.close()


def refused_metadata(case: str) -> MetaData:
    """Model metadata extended with one column (or index) the step must refuse."""
    metadata = copied_metadata()
    task = metadata.tables['task']
    if case == 'unique-index':
        Index('index_tasks_unique_args', task.c.args, unique=True)
        return metadata
    task.append_column({
        'primary-key': lambda: Column('extra', Integer, primary_key=True),
        'unique': lambda: Column('extra', Text, unique=True),
        'foreign-key': lambda: Column('extra', Text, ForeignKey('client.id')),
        'computed': lambda: Column('extra', Integer, Computed('attempt + 1')),
        'identity': lambda: Column('extra', Integer, Identity()),
        'not-null': lambda: Column('extra', Integer, nullable=False),
        'function-default': lambda: Column('extra', DateTime, server_default=func.now()),
        'text-expression-default': lambda: Column('extra', DateTime, server_default=text('CURRENT_TIMESTAMP')),
    }[case]())
    return metadata


REFUSALS = ['primary-key', 'unique', 'foreign-key', 'computed', 'identity', 'not-null',
            'function-default', 'text-expression-default', 'unique-index']


@mark.unit
@mark.parametrize('case', REFUSALS)
def test_refusal_leaves_schema_unchanged(legacy_path: Path, case: str) -> None:
    """An unsafe addition is refused by name before any statement runs, even ones planned ahead of it."""
    before = sqlite_schema(legacy_path)
    with pytest.raises(SchemaUpgradeError) as error:
        upgrade_schema(sqlite_engine(legacy_path), refused_metadata(case))
    message = str(error.value)
    assert message.startswith('Cannot upgrade table \'task\': ')
    assert ('\'index_tasks_unique_args\'' if case == 'unique-index' else '\'extra\'') in message
    assert sqlite_schema(legacy_path) == before


@mark.unit
def test_failure_mid_step_rolls_back(legacy_path: Path, monkeypatch) -> None:
    """A statement failing partway leaves the SQLite schema exactly as it was."""
    real_plan = upgrade._plan

    def _plan(conn, metadata):
        plan = real_plan(conn, metadata)
        return Plan(steps=plan.steps + [Step('task', 'broken', 'CREATE INDEX broken ON no_such_table (x)')],
                    existing=plan.existing)

    monkeypatch.setattr(upgrade, '_plan', _plan)
    before = sqlite_schema(legacy_path)
    with pytest.raises(SchemaUpgradeError, match='no such table'):
        upgrade_schema(sqlite_engine(legacy_path))
    assert sqlite_schema(legacy_path) == before


@mark.unit
def test_fresh_schema_has_no_chain_uniqueness(tmp_path: Path, records: List[logging.LogRecord]) -> None:
    """A new database gets plain retry-chain indexes, no UNIQUE, and initialization logs only at trace."""
    engine = sqlite_engine(tmp_path / 'fresh.db')
    assert upgrade_schema(engine)
    assert all(record.levelno < logging.DEBUG for record in records)
    insp = inspect(engine)
    assert insp.get_unique_constraints('task') == []
    indexes = {index['name']: index for index in insp.get_indexes('task')}
    assert indexes['index_tasks_previous_id']['column_names'] == ['previous_id']
    assert indexes['index_tasks_next_id']['column_names'] == ['next_id']
    assert not indexes['index_tasks_previous_id']['unique']
    assert not indexes['index_tasks_next_id']['unique']

    other = sqlite_engine(tmp_path / 'create_all.db')
    Entity.metadata.create_all(other)
    assert inspect(other).get_unique_constraints('task') == []


@mark.unit
def test_repeat_check_does_not_wait(legacy_path: Path, monkeypatch) -> None:
    """With the schema current, checkdb takes no lock, so a pending write in this process cannot block it."""
    engine = sqlite_engine(legacy_path)
    upgrade_schema(engine)
    monkeypatch.setattr(hypershell.data, 'engine', engine)
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_SLICE', 0.1)
    with Session(engine) as session:
        session.execute(text('UPDATE task SET waited = 1'))  # Uncommitted: holds the write lock.
        start = time.monotonic()
        hypershell.data.checkdb()
        assert time.monotonic() - start < 2
        session.rollback()


@mark.integration
def test_refusal_on_command_line(temp_site: Path) -> None:
    """A refused upgrade prints one CRITICAL line, exits runtime_error, and writes no traceback file."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    shutil.copyfile(FIXTURE_283, database)
    before = sqlite_schema(database)
    script = ('import sys, hypershell\n'
              'from sqlalchemy import Column, Integer\n'
              'from hypershell.data.model import Task\n'
              'Task.__table__.append_column(Column(\'extra\', Integer, nullable=False))\n'
              'sys.exit(hypershell.main([\'list\', \'--count\']))\n')
    rc, stdout, stderr = main_lines([sys.executable, '-c', script])
    assert rc == exit_status.runtime_error
    assert stdout == ['']
    assert stderr == [
        'CRITICAL [hypershell.task] SchemaUpgradeError: Cannot upgrade table \'task\': column \'extra\' is '
        'NOT NULL with no server default, so existing rows have no value for it. '
        'Declare a server_default or make it nullable.'
    ]
    assert list(Path(temp_site).rglob('exception-*.log')) == []
    assert sqlite_schema(database) == before


@mark.integration
def test_truncate_gives_fresh_schema(temp_site: Path) -> None:
    """`hs initdb --truncate` recreates tables with no retry-chain uniqueness."""
    assert main_lines(['hs', 'submit', 'echo one'])[0] == exit_status.success
    assert main_lines(['hs', 'initdb', '--truncate', '--yes'])[0] == exit_status.success
    engine = sqlite_engine(Path(os.environ['HYPERSHELL_DATABASE_FILE']))
    assert inspect(engine).get_unique_constraints('task') == []
    names = {index['name'] for index in inspect(engine).get_indexes('task')}
    assert {'index_tasks_previous_id', 'index_tasks_next_id'} <= names


def row_shape(path: Path) -> tuple:
    """Schema and rows of a fixture with ids mapped to ordinals and times reduced to presence."""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        master = sorted(conn.execute('SELECT type, name, tbl_name, sql FROM sqlite_master'))
        tasks = conn.execute('SELECT id, args, attempt, retried, exit_status, previous_id, next_id, tag, "group", '
                             'submit_host, server_host, client_host, schedule_time IS NULL, '
                             'completion_time IS NULL FROM task ORDER BY args, attempt').fetchall()
        clients = conn.execute('SELECT host, server_host, evicted, connected_at IS NULL, '
                               'disconnected_at IS NULL FROM client').fetchall()
    finally:
        conn.close()
    ordinal = {row[0]: n for n, row in enumerate(tasks)}
    tasks = [(row[1:5] + (ordinal.get(row[5]), ordinal.get(row[6])) + row[7:]) for row in tasks]
    return master, tasks, clients


@mark.integration
@mark.skipif(not os.getenv('HS_TEST_REGEN_FIXTURE'),
             reason='set HS_TEST_REGEN_FIXTURE to a Python with hypershell==2.8.3 to regenerate the fixture')
def test_fixture_provenance(tmp_path: Path) -> None:
    """The generator, run with real hypershell 2.8.3, reproduces the committed fixture's schema and rows."""
    python = os.environ['HS_TEST_REGEN_FIXTURE']
    if not os.access(python, os.X_OK):
        pytest.fail(f'HS_TEST_REGEN_FIXTURE is not an executable Python: {python}')
    script = FIXTURE_283.parent / 'make-2.8.3-db.sh'
    proc = subprocess.run(['sh', str(script), python, str(tmp_path / 'out')], capture_output=True)
    if proc.returncode != 0:
        pytest.fail(f'Generator failed: {proc.stderr.decode()}')
    assert row_shape(tmp_path / 'out' / FIXTURE_283.name) == row_shape(FIXTURE_283)
