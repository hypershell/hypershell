# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Upgrade PostgreSQL databases created by released hypershell versions in place.

These tests need a PostgreSQL server and the template databases that
`tests/fixtures/make-legacy-postgres.sh` builds with hypershell 2.8.3 and 2.9.0a1:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_POSTGRES_TEMPLATES=hs283,hs290a1

They are skipped when HS_TEST_POSTGRES is unset. With it set, a missing template fails.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# Standard libs
import os
import re
import uuid
import logging
import threading
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from sqlalchemy import create_engine, inspect, text, Column, Integer, MetaData
from sqlalchemy.engine import Engine, make_url

# Internal libs
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data import upgrade
from hypershell.data.model import Entity
from hypershell.data.upgrade import upgrade_schema, Step, Plan
from tests import main, main_lines, NO_OUTPUT, ORIGINAL_TASK_COLUMNS


ADMIN_URL: Optional[str] = os.getenv('HS_TEST_POSTGRES')
TEMPLATES = frozenset(name for name in os.getenv('HS_TEST_POSTGRES_TEMPLATES', '').split(',') if name)

pytestmark = [
    mark.integration,
    mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests'),
]

LEGACY = ['hs283', 'hs290a1']
FAIL_ARGS = "sh -c 'exit 3'"
NEW_TASK_COLUMNS = ('source', 'fingerprint', 'part')
NEW_INDEXES = ('index_tasks_source', 'index_tasks_previous_id', 'index_tasks_next_id')
UPGRADE_LINE = re.compile(r'^INFO \[hypershell\.data[\w.]*\] (Added column|Created index|Created table|Dropped) ')


@fixture(scope='module')
def admin() -> Iterator[Engine]:
    """Admin engine on the maintenance database (CREATE/DROP DATABASE need autocommit)."""
    engine = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    yield engine
    engine.dispose()


@fixture
def pg_database(admin: Engine) -> Iterator[Callable[[Optional[str]], str]]:
    """Factory for a throwaway database, empty or copied from a template; dropped afterwards."""
    created: List[str] = []

    def make(template: Optional[str] = None) -> str:
        if template is not None:
            if template not in TEMPLATES:
                pytest.fail(f'Template {template} not listed in HS_TEST_POSTGRES_TEMPLATES')
            with admin.connect() as conn:
                found = conn.execute(text('SELECT 1 FROM pg_database WHERE datname = :name'),
                                     {'name': template}).first()
            if found is None:
                pytest.fail(f'Template database {template} does not exist (run make-legacy-postgres.sh)')
        name = f't_{uuid.uuid4().hex[:16]}'
        clause = f' TEMPLATE {template}' if template else ''
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE {name}{clause}'))
        created.append(name)
        return name

    yield make
    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))


def database_url(name: str) -> str:
    """URL of a database on the admin server."""
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def database_engine(name: str) -> Engine:
    """A private engine on one test database."""
    return create_engine(database_url(name))


@fixture
def pg_site(tmp_path: Path) -> Callable[..., Engine]:
    """Point the command line at a test database; return a private engine on it."""
    engines: List[Engine] = []

    def use(name: str, schema: Optional[str] = None) -> Engine:
        url = make_url(ADMIN_URL)
        os.environ.pop('HYPERSHELL_DATABASE_FILE', None)
        os.environ['HYPERSHELL_SITE'] = str(tmp_path)
        os.environ['HYPERSHELL_LOGGING_LEVEL'] = 'DEBUG'
        os.environ['HYPERSHELL_DATABASE_PROVIDER'] = 'postgres'
        os.environ['HYPERSHELL_DATABASE_HOST'] = url.host or 'localhost'
        os.environ['HYPERSHELL_DATABASE_PORT'] = str(url.port or 5432)
        os.environ['HYPERSHELL_DATABASE_USER'] = url.username or 'postgres'
        os.environ['HYPERSHELL_DATABASE_PASSWORD'] = url.password or ''
        os.environ['HYPERSHELL_DATABASE_DATABASE'] = name
        if schema:
            os.environ['HYPERSHELL_DATABASE_SCHEMA'] = schema
        engine = database_engine(name)
        engines.append(engine)
        return engine

    yield use
    for engine in engines:
        engine.dispose()


def task_columns(engine: Engine, schema: str = None) -> Dict[str, str]:
    """Column name to data type for `task`, from the information schema."""
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT column_name, data_type FROM information_schema.columns '
                                 'WHERE table_name = \'task\' AND table_schema = :schema'),
                            {'schema': schema or 'public'}).all()
    return dict(rows)


def schema_signature(engine: Engine, schema: str = None) -> tuple:
    """Every column, index, and constraint in a schema, for exact before/after comparison."""
    params = {'schema': schema or 'public'}
    with engine.connect() as conn:
        columns = conn.execute(text('SELECT table_name, column_name, data_type, is_nullable, column_default '
                                    'FROM information_schema.columns WHERE table_schema = :schema '
                                    'ORDER BY 1, 2'), params).all()
        indexes = conn.execute(text('SELECT tablename, indexname, indexdef FROM pg_indexes '
                                    'WHERE schemaname = :schema ORDER BY 1, 2'), params).all()
        constraints = conn.execute(text('SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) '
                                        'FROM pg_constraint WHERE connamespace = CAST(:schema AS regnamespace) '
                                        'ORDER BY 1, 2'), params).all()
    return tuple(columns), tuple(indexes), tuple(constraints)


def snapshot(engine: Engine, schema: str = None) -> Dict[str, tuple]:
    """The original task columns of every row, keyed by id."""
    columns = ', '.join(f'"{name}"' for name in ORIGINAL_TASK_COLUMNS)
    table = f'{schema}.task' if schema else 'task'
    with engine.connect() as conn:
        return {str(row[0]): tuple(row) for row in conn.execute(text(f'SELECT {columns} FROM {table}'))}


def unique_constraints(engine: Engine, schema: str = None) -> List[Tuple[str, List[str]]]:
    """Name and columns of each unique constraint on `task`."""
    return sorted((uc['name'], uc['column_names'])
                  for uc in inspect(engine).get_unique_constraints('task', schema=schema))


def index_names(engine: Engine, schema: str = None) -> set:
    """Names of the indexes on `task`."""
    return {index['name'] for index in inspect(engine).get_indexes('task', schema=schema)}


def assert_schema_current(engine: Engine, schema: str = None) -> None:
    """Every new column, table, and index is present, and no retry-chain uniqueness remains."""
    columns = task_columns(engine, schema)
    for name in NEW_TASK_COLUMNS:
        assert name in columns, f'task.{name} missing'
    assert inspect(engine).has_table('source', schema=schema)
    assert set(NEW_INDEXES) <= index_names(engine, schema)
    assert unique_constraints(engine, schema) == []


def upgrade_lines(stderr: List[str]) -> List[str]:
    """Lines logged by the upgrade step."""
    return [line for line in stderr if UPGRADE_LINE.match(line)]


R1_COMMANDS: Dict[str, Tuple[List[str], str]] = {
    # name: (argv, expected `hs list --count` afterwards)
    'submit': (['hs', 'submit', 'echo r1'], '8'),
    'list': (['hs', 'list', '--all'], '7'),
    'info': (['hs', 'info', '{done}'], '7'),
    'search': (['hs', 'task', 'search', 'args'], '7'),
    'update': (['hs', 'update', 'exit_status=0', '-t', 'n:99', '--no-confirm'], '7'),
    'wait': (['hs', 'wait', '{done}', '-n', '1'], '7'),
    'initdb': (['hs', 'initdb', '--yes'], '7'),
}


@mark.parametrize('template', LEGACY)
@mark.parametrize('command', list(R1_COMMANDS))
def test_first_use_without_initdb(pg_database, pg_site, template: str, command: str) -> None:
    """Each command upgrades a 2.8 (or half-upgraded) database on first use, with no --initdb."""
    engine = pg_site(pg_database(template))
    with engine.connect() as conn:
        done = str(conn.execute(text('SELECT id FROM task WHERE args = \'echo done-0\'')).scalar_one())
    argv, count = R1_COMMANDS[command]
    rc, _, stderr = main([arg.format(done=done) for arg in argv])
    assert rc == exit_status.success, stderr
    assert_schema_current(engine)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, [count])


@mark.parametrize('template', LEGACY)
def test_rows_preserved(pg_database, pg_site, template: str) -> None:
    """Every row keeps its values; new columns read NULL, NULL, and 0; source is a native uuid."""
    engine = pg_site(pg_database(template))
    before = snapshot(engine)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    assert snapshot(engine) == before
    with engine.connect() as conn:
        values = conn.execute(text('SELECT source, fingerprint, part FROM task')).all()
    assert [tuple(row) for row in values] == [(None, None, 0)] * 7
    assert task_columns(engine)['source'] == 'uuid'


def test_chain_constraints_dropped(pg_database, pg_site) -> None:
    """The retry-chain UNIQUE constraints are dropped by their reflected names and replaced by plain indexes."""
    engine = pg_site(pg_database('hs283'))
    constraints = unique_constraints(engine)
    assert [columns for _, columns in constraints] == [['next_id'], ['previous_id']]
    rc, _, stderr = main_lines(['hs', 'list', '--count'])
    assert rc == exit_status.success
    for name, _ in constraints:
        assert f'INFO [hypershell.data.upgrade] Dropped unique constraint {name} on task' in stderr
    assert unique_constraints(engine) == []
    assert {'index_tasks_previous_id', 'index_tasks_next_id'} <= index_names(engine)

    signature = schema_signature(engine)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['7'], NO_OUTPUT)
    assert schema_signature(engine) == signature


@mark.parametrize('template', LEGACY)
def test_upgrade_logged_once(pg_database, pg_site, template: str) -> None:
    """The first command logs each change at INFO naming the table; the second is silent."""
    engine = pg_site(pg_database(template))
    rc, stdout, stderr = main_lines(['hs', 'list', '--count'])
    assert (rc, stdout) == (exit_status.success, ['7'])
    lines = upgrade_lines(stderr)
    for message in ('Added column task.source', 'Added column task.fingerprint', 'Added column task.part',
                    'Created index index_tasks_source on task'):
        assert len([line for line in lines if line.endswith(message)]) == 1, stderr
    assert len([line for line in lines if line.endswith('Created table source')]) == (1 if template == 'hs283' else 0)
    signature = schema_signature(engine)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['7'], NO_OUTPUT)
    assert schema_signature(engine) == signature


def test_concurrent_first_use(pg_database, pg_site) -> None:
    """Concurrent processes opening one 2.8 database all succeed; exactly one upgrades it."""
    engine = pg_site(pg_database('hs283'))
    count = 4
    barrier = threading.Barrier(count)
    results: List[Tuple[int, str, str]] = [None] * count

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = main(['hs', 'list', '--count'])

    threads = [threading.Thread(target=worker, args=(i, )) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    if any(result is None for result in results):
        pytest.fail('a worker did not finish')
    assert [rc for rc, _, _ in results] == [exit_status.success] * count, results
    assert [stdout for _, stdout, _ in results] == ['7'] * count
    assert len([stderr for _, _, stderr in results if 'Added column task.source' in stderr]) == 1, results
    assert_schema_current(engine)


def test_race_replans_under_lock(pg_database, monkeypatch) -> None:
    """A peer that upgrades between our first plan and our lock leaves us nothing to do, and no error."""
    name = pg_database('hs283')
    first, second = database_engine(name), database_engine(name)
    real_plan, real_lock = upgrade._plan, upgrade._lock_postgres
    plans: List[Tuple[Engine, int]] = []

    def _plan(conn, metadata):
        plan = real_plan(conn, metadata)
        plans.append((conn.engine, len(plan.steps)))
        return plan

    def _lock(conn):
        if conn.engine is second and not any(engine is first for engine, _ in plans):
            assert upgrade_schema(first)  # The peer wins the race.
        real_lock(conn)

    monkeypatch.setattr(upgrade, '_plan', _plan)
    monkeypatch.setattr(upgrade, '_lock_postgres', _lock)
    try:
        assert upgrade_schema(second) == []
        assert [steps > 0 for engine, steps in plans if engine is second] == [True, False]
        assert_schema_current(second)
    finally:
        first.dispose()
        second.dispose()


def test_refusal_rolls_back(pg_database) -> None:
    """An unsafe column is refused by name and nothing is changed."""
    engine = database_engine(pg_database('hs283'))
    metadata = MetaData()
    for table in Entity.metadata.sorted_tables:
        table.to_metadata(metadata)
    metadata.tables['task'].append_column(Column('extra', Integer, nullable=False))
    try:
        before = schema_signature(engine)
        with pytest.raises(SchemaUpgradeError, match='Cannot upgrade table \'task\': column \'extra\''):
            upgrade_schema(engine, metadata)
        assert schema_signature(engine) == before
    finally:
        engine.dispose()


def test_failure_mid_step_rolls_back(pg_database, monkeypatch) -> None:
    """A statement failing partway rolls back every earlier change (transactional DDL)."""
    engine = database_engine(pg_database('hs283'))
    real_plan = upgrade._plan

    def _plan(conn, metadata):
        plan = real_plan(conn, metadata)
        return Plan(steps=plan.steps + [Step('task', 'broken', 'CREATE INDEX broken ON no_such_table (x)')],
                    existing=plan.existing)

    monkeypatch.setattr(upgrade, '_plan', _plan)
    try:
        before = schema_signature(engine)
        with pytest.raises(SchemaUpgradeError, match='no_such_table'):
            upgrade_schema(engine)
        assert schema_signature(engine) == before
    finally:
        engine.dispose()


WAIT_MESSAGE = re.compile(r'^Waiting for .+ \(\d+s elapsed\)$')


@fixture
def upgrade_log() -> Iterator[List[Tuple[int, str]]]:
    """(level, message) of every record the upgrade step logs."""
    captured: List[Tuple[int, str]] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append((record.levelno, record.getMessage()))

    logger = logging.getLogger(upgrade.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


def test_lock_timeout_is_actionable(pg_database, monkeypatch) -> None:
    """A third session holding `task` past the wait ceiling fails the step with an accurate error, schema unchanged."""
    engine = database_engine(pg_database('hs283'))
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_SLICE', 0.2)
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_CEILING', 1)
    holder = engine.connect()
    try:
        before = schema_signature(engine)
        holder.execute(text('SELECT 1 FROM task LIMIT 1'))  # Open transaction holding a lock on task.
        with pytest.raises(SchemaUpgradeError, match='gave up after 1 s waiting for another session to release '
                                                     'its lock on the tables. Find the session'):
            upgrade_schema(engine)
        holder.rollback()
        assert schema_signature(engine) == before
    finally:
        holder.close()
        engine.dispose()


def test_waits_for_slow_peer_upgrade(pg_database, monkeypatch, upgrade_log) -> None:
    """A peer upgrade that outlasts many wait slices is waited out, with one INFO line per slice; no error."""
    engine = database_engine(pg_database('hs283'))
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_SLICE', 0.25)
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_CEILING', 60)
    peer = engine.connect()
    try:
        upgrade._lock_postgres(peer)
        steps = upgrade._plan(peer, Entity.metadata).steps
        assert steps
        for step in steps:
            peer.exec_driver_sql(step.sql)
        timer = threading.Timer(2, peer.commit)
        timer.start()
        assert upgrade_schema(engine) == []
        timer.join()
        waits = [message for level, message in upgrade_log if WAIT_MESSAGE.match(message)]
        assert len(waits) >= 2, upgrade_log
        assert all(level == logging.INFO for level, message in upgrade_log if WAIT_MESSAGE.match(message))
        assert_schema_current(engine)
    finally:
        peer.close()
        engine.dispose()


def test_gives_up_past_ceiling(pg_database, monkeypatch, upgrade_log) -> None:
    """A peer holding the upgrade lock past the wait ceiling fails the step with an accurate error, schema unchanged."""
    engine = database_engine(pg_database('hs283'))
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_SLICE', 0.2)
    monkeypatch.setattr(upgrade, 'LOCK_WAIT_CEILING', 1)
    peer = engine.connect()
    try:
        before = schema_signature(engine)
        upgrade._lock_postgres(peer)
        with pytest.raises(SchemaUpgradeError, match='gave up after 1 s waiting for a concurrent schema upgrade '
                                                     'to finish. Find the upgrading session'):
            upgrade_schema(engine)
        assert [level for level, message in upgrade_log if WAIT_MESSAGE.match(message)]
        peer.rollback()
        assert schema_signature(engine) == before
    finally:
        peer.close()
        engine.dispose()


def test_fresh_schema(pg_database, pg_site) -> None:
    """A new database has plain retry-chain indexes and no UNIQUE constraints; initialization is quiet."""
    engine = pg_site(pg_database())
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success
    assert upgrade_lines(stderr) == []
    assert unique_constraints(engine) == []
    assert {'index_tasks_previous_id', 'index_tasks_next_id'} <= index_names(engine)


def test_schema_qualified(pg_database, pg_site, admin) -> None:
    """A 2.8 database in a named schema is upgraded there, and nothing is created elsewhere."""
    name = pg_database('hs283')
    with create_engine(database_url(name), isolation_level='AUTOCOMMIT').connect() as conn:
        conn.execute(text('ALTER SCHEMA public RENAME TO legacy'))
        conn.execute(text('CREATE SCHEMA public'))
    engine = pg_site(name, schema='legacy')
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    assert_schema_current(engine, schema='legacy')
    assert inspect(engine).get_table_names(schema='public') == []


def test_end_to_end(pg_database, pg_site, tmp_path: Path) -> None:
    """A 2.8.3 database takes new tasks, completes pending ones with a retry, and lists them all."""
    engine = pg_site(pg_database('hs283'))
    before = snapshot(engine)
    rc, _, stderr = main(['hs', 'submit', '-t', 'kind:new', '--', 'echo', 'new-0'])
    assert rc == exit_status.success, stderr
    rc, stdout, stderr = main(['hs', 'cluster', '--restart', '-r', '2', '-N2', '-b1', '-w1', '--no-tls'])
    assert rc == exit_status.success, stderr
    assert sorted(stdout.split()) == ['new-0', 'pending-0', 'pending-1']
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['9'])
    assert main_lines(['hs', 'list', '--remaining', '--count'])[:2] == (exit_status.success, ['0'])

    after = snapshot(engine)
    columns = list(ORIGINAL_TASK_COLUMNS)
    second = [id for id, row in before.items() if row[columns.index('args')] == FAIL_ARGS
              and row[columns.index('attempt')] == 2][0]
    retries = [dict(zip(columns, row)) for id, row in after.items() if id not in before
               and row[columns.index('args')] == FAIL_ARGS]
    assert len(retries) == 1
    assert retries[0]['attempt'] == 3
    assert str(retries[0]['previous_id']) == second
    old = dict(zip(columns, after[second]))
    assert old['retried'] is True
    assert str(old['next_id']) == str(retries[0]['id'])
