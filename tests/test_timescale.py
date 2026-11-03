# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
TimescaleDB provider: the environment checks and the hypertable pass behind `hs initdb`.

Unit tests always run. Integration tests need a PostgreSQL server; they are skipped when
HS_TEST_POSTGRES is unset:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=community        # or: apache, or empty for a plain PostgreSQL server

HS_TEST_TIMESCALE names the edition the server must report. With it set, a server without
TimescaleDB, or reporting another edition, fails the tests rather than skipping them, so a
green run means every scenario ran. With it empty, only the scenarios for a server without
the extension run, and the server must not offer the extension.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Iterator, List, Optional, Tuple

# Standard libs
import os
import re
import uuid
import threading
from datetime import timedelta
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from cmdkit.config import ConfigurationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

# Internal libs
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data import timescale
from hypershell.data.timescale import (TimescaleError, Gates, Settings, gates, parse_version, extension_error,
                                       normalize_columns)
from tests import main, main_lines


ADMIN_URL: Optional[str] = os.getenv('HS_TEST_POSTGRES')
TIMESCALE: str = os.getenv('HS_TEST_TIMESCALE', '')
TEMPLATES = frozenset(name for name in os.getenv('HS_TEST_POSTGRES_TEMPLATES', '').split(',') if name)
EDITIONS = {'community': 'timescale', 'apache': 'apache'}  # HS_TEST_TIMESCALE -> timescaledb.license

needs_server = mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests')
with_timescale = mark.skipif(not TIMESCALE, reason='set HS_TEST_TIMESCALE to community or apache')
without_timescale = mark.skipif(bool(TIMESCALE), reason='needs a server without TimescaleDB')


def uuid7() -> str:
    """A time-ordered UUIDv7 (the `uuid7` extra, also in the dev dependencies)."""
    from uuid_utils import uuid7 as _uuid7
    return str(_uuid7())


@fixture(scope='module')
def admin() -> Iterator[Engine]:
    """Admin engine on the maintenance database (CREATE/DROP DATABASE need autocommit)."""
    engine = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
    yield engine
    engine.dispose()


@fixture
def ts_database(admin: Engine) -> Iterator[Callable[[Optional[str]], str]]:
    """Factory for a throwaway database, from `template1` or a named template; dropped afterwards."""
    created: List[str] = []

    def make(template: Optional[str] = None) -> str:
        if template not in (None, 'template0') and template not in TEMPLATES:
            pytest.fail(f'Template {template} not listed in HS_TEST_POSTGRES_TEMPLATES')
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


def database_engine(name: str, **options) -> Engine:
    """A private engine on one test database."""
    return create_engine(database_url(name), **options)


@fixture
def ts_site(tmp_path: Path) -> Iterator[Callable[..., Engine]]:
    """Point the command line at a test database under a provider; return a private engine on it."""
    engines: List[Engine] = []

    def use(name: str, schema: Optional[str] = None, provider: str = 'timescale') -> Engine:
        url = make_url(ADMIN_URL)
        os.environ.pop('HYPERSHELL_DATABASE_FILE', None)
        os.environ['HYPERSHELL_SITE'] = str(tmp_path)
        os.environ['HYPERSHELL_LOGGING_LEVEL'] = 'DEBUG'
        os.environ['HYPERSHELL_DATABASE_PROVIDER'] = provider
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


def sqlstate(error: DBAPIError) -> Optional[str]:
    """SQLSTATE of a driver error."""
    return getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)


# --------------------------------------------------------------------------------------------------
# The server under test


@mark.integration
@needs_server
def test_server_matches_entry(admin: Engine) -> None:
    """The server is what HS_TEST_TIMESCALE says: TimescaleDB of that edition, or no TimescaleDB at all."""
    with admin.connect() as conn:
        available = conn.execute(text('SELECT default_version FROM pg_available_extensions '
                                      'WHERE name = \'timescaledb\'')).scalar()
        if not TIMESCALE:
            assert available is None, f'server offers TimescaleDB {available}, but HS_TEST_TIMESCALE is empty'
            return
        assert TIMESCALE in EDITIONS, f'HS_TEST_TIMESCALE={TIMESCALE!r}: expected one of {sorted(EDITIONS)}'
        assert available is not None, 'HS_TEST_TIMESCALE is set but the server does not offer TimescaleDB'
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
        version = conn.execute(text('SELECT extversion FROM pg_extension WHERE extname = \'timescaledb\'')).scalar()
        license = conn.execute(text('SELECT current_setting(\'timescaledb.license\', true)')).scalar()
    assert version, 'TimescaleDB extension has no version'
    assert license == EDITIONS[TIMESCALE], f'server reports license {license!r}, expected {EDITIONS[TIMESCALE]!r}'


@mark.integration
@needs_server
@with_timescale
def test_v4_id_rejected(ts_database) -> None:
    """On a hypertable partitioned by a UUID, a v4 id fails with SQLSTATE 0A000 and aborts its whole INSERT."""
    engine = database_engine(ts_database(), isolation_level='AUTOCOMMIT')
    try:
        with engine.connect() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
            conn.execute(text('CREATE TABLE probe (id uuid PRIMARY KEY, n integer)'))
            conn.execute(text('SELECT create_hypertable(\'probe\', by_range(\'id\', INTERVAL \'1 week\'))'))
            conn.execute(text('INSERT INTO probe (id, n) VALUES (CAST(:id AS uuid), 1)'), {'id': uuid7()})
            with pytest.raises(DBAPIError) as caught:
                conn.execute(text('INSERT INTO probe (id, n) VALUES (CAST(:v7 AS uuid), 2), (CAST(:v4 AS uuid), 3)'),
                             {'v7': uuid7(), 'v4': str(uuid.uuid4())})
            assert sqlstate(caught.value) == '0A000', caught.value
            assert 'version 7' in str(caught.value.orig)
            assert conn.execute(text('SELECT n FROM probe ORDER BY n')).scalars().all() == [1]
    finally:
        engine.dispose()


def extension_installed(engine: Engine) -> Optional[str]:
    """The TimescaleDB extension's version in a database, or None."""
    with engine.connect() as conn:
        return conn.execute(text('SELECT extversion FROM pg_extension WHERE extname = \'timescaledb\'')).scalar()


PASS_LINE = re.compile(r'^(INFO|WARNING) \[hypershell\.data\.timescale\] ')
WEEK, FOUR_WEEKS = timedelta(days=7), timedelta(days=28)


def server_gates(engine: Engine) -> Gates:
    """What the server under test supports, from its extension version and license."""
    with engine.connect() as conn:
        version = conn.execute(text('SELECT extversion FROM pg_extension WHERE extname = \'timescaledb\'')).scalar()
        license = conn.execute(text('SELECT current_setting(\'timescaledb.license\', true)')).scalar()
    assert version, 'TimescaleDB extension missing from the test database'
    return gates(parse_version(version), license)


def shape(engine: Engine, schema: str = 'public') -> Dict[str, object]:
    """The TimescaleDB shape of `task`, read from the timescaledb_information views."""
    params = {'schema': schema, 'table': f'{schema}.task'}
    with engine.connect() as conn:
        enabled = conn.execute(text('SELECT compression_enabled FROM timescaledb_information.hypertables '
                                    'WHERE hypertable_schema = :schema AND hypertable_name = \'task\''),
                               params).first()
        if enabled is None:
            return {'hypertable': False}
        column, interval = conn.execute(text(
            'SELECT column_name, COALESCE(time_interval, integer_interval * INTERVAL \'1 microsecond\') '
            'FROM timescaledb_information.dimensions WHERE hypertable_schema = :schema '
            'AND hypertable_name = \'task\' AND dimension_number = 1'), params).one()
        settings = None
        if enabled[0]:  # Read only where the columnstore is on (an Apache-only server has none).
            settings = conn.execute(text('SELECT segmentby, orderby '
                                         'FROM timescaledb_information.hypertable_columnstore_settings '
                                         'WHERE hypertable = CAST(:table AS regclass)'), params).first()
        jobs = conn.execute(text('SELECT CAST(config->>\'compress_after\' AS interval) '
                                 'FROM timescaledb_information.jobs WHERE proc_name = \'policy_compression\' '
                                 'AND hypertable_schema = :schema AND hypertable_name = \'task\''),
                            params).scalars().all()
    return {'hypertable': True, 'column': column, 'interval': interval, 'columnstore': bool(enabled[0]),
            'segmentby': normalize_columns(settings[0]) if settings else None,
            'orderby': normalize_columns(settings[1]) if settings else None,
            'jobs': list(jobs)}


def assert_converged(engine: Engine, schema: str = 'public', interval: timedelta = WEEK,
                     after: timedelta = FOUR_WEEKS) -> Dict[str, object]:
    """`task` has every part of the shape the server supports, and nothing it does not."""
    allowed = server_gates(engine)
    state = shape(engine, schema)
    assert state['hypertable'], f'{schema}.task is not a hypertable'
    assert (state['column'], state['interval']) == ('id', interval), state
    assert state['columnstore'] == allowed.columnstore, state
    if allowed.columnstore:
        assert (state['segmentby'], state['orderby']) == ('client_id', 'id desc'), state
    assert state['jobs'] == ([after] if allowed.policy else []), state
    return state


def pass_lines(stderr: List[str], level: str = None) -> List[str]:
    """Lines the hypertable pass logged at INFO or WARNING (or only `level`)."""
    return [line for line in stderr if PASS_LINE.match(line) and (level is None or line.startswith(level))]


def assert_tier_warnings(engine: Engine, stderr: List[str]) -> None:
    """The edition and policy refusals are logged exactly when the server calls for them."""
    allowed = server_gates(engine)
    warnings = pass_lines(stderr, 'WARNING')
    assert any('Apache-only edition' in line for line in warnings) == (not allowed.columnstore), stderr
    assert any('fixed in TimescaleDB 2.25.0' in line for line in warnings) == (
        allowed.columnstore and not allowed.policy), stderr


def task_rows(engine: Engine, schema: str = 'public') -> List[Tuple[str, str]]:
    """Every task's id and args."""
    with engine.connect() as conn:
        return sorted((str(id), args) for id, args in conn.execute(text(f'SELECT id, args FROM {schema}.task')))


def is_hypertable(engine: Engine) -> bool:
    """True if `public.task` is a hypertable."""
    return shape(engine)['hypertable']


# --------------------------------------------------------------------------------------------------
# Environment checks before the schema step


@mark.unit
@mark.parametrize('value, expected', [
    ('2.31.0', (2, 31, 0)),
    ('2.22.0-dev', (2, 22, 0)),
    ('2.24', (2, 24, 0)),
    (' 3.0.1 ', (3, 0, 1)),
])
def test_parse_version(value: str, expected: tuple) -> None:
    """Extension versions parse to (major, minor, patch), ignoring suffixes."""
    assert parse_version(value) == expected


@mark.unit
@mark.parametrize('value', ['', 'garbage', 'v2.22.0', None])
def test_parse_version_garbage(value: Optional[str]) -> None:
    """An unreadable version is an actionable error, not a crash."""
    with pytest.raises(TimescaleError, match='Cannot read the TimescaleDB version'):
        parse_version(value)


@mark.unit
@mark.parametrize('version, edition, expected', [
    ((2, 21, 9), 'timescale', (False, False, False)),
    ((2, 21, 9), 'apache', (False, False, False)),
    ((2, 22, 0), 'timescale', (True, True, False)),
    ((2, 24, 9), 'timescale', (True, True, False)),
    ((2, 25, 0), 'timescale', (True, True, True)),
    ((2, 31, 0), 'timescale', (True, True, True)),
    ((2, 31, 0), None, (True, True, True)),
    ((2, 31, 0), 'apache', (True, False, False)),
    ((2, 22, 0), 'apache', (True, False, False)),
])
def test_gates(version: tuple, edition: Optional[str], expected: tuple) -> None:
    """Below 2.22.0 nothing; before 2.25.0 no policy; the Apache edition only the hypertable."""
    result = gates(version, edition)
    assert (result.hypertable, result.columnstore, result.policy) == expected
    assert bool(result.reasons) == (expected != (True, True, True))


@mark.unit
def test_gate_reasons() -> None:
    """Each refusal names its cause and its fix."""
    too_old, = gates((2, 21, 9), 'timescale').reasons
    assert '2.21.9' in too_old and '2.22.0' in too_old and 'ALTER EXTENSION timescaledb UPDATE' in too_old
    no_policy, = gates((2, 24, 0), 'timescale').reasons
    assert '2.24.0' in no_policy and '2.25.0' in no_policy and 'policy' in no_policy
    apache, = gates((2, 31, 0), 'apache').reasons
    assert 'apache' in apache and 'Community edition' in apache


@mark.unit
def test_messages_name_problem_and_fix() -> None:
    """The failure messages say what is wrong and what to do."""
    absent = timescale.absent_message('jobs')
    assert 'TimescaleDB is not installed' in absent and '\'jobs\'' in absent
    assert 'database.provider = "postgres"' in absent
    privilege = timescale.privilege_message('jobs')
    assert 'permission denied' in privilege and 'CREATE EXTENSION timescaledb' in privilege
    preload = timescale.preload_message()
    assert 'not preloaded' in preload and 'shared_preload_libraries' in preload and 'restart' in preload


@mark.unit
def test_error_is_schema_upgrade_error() -> None:
    """Every app maps the error to one critical line and runtime_error, with no traceback."""
    assert issubclass(TimescaleError, SchemaUpgradeError)


class DriverError(Exception):
    """Stand-in for a driver exception carrying a SQLSTATE."""

    def __init__(self, message: str, sqlstate: Optional[str] = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def driver_error(message: str, sqlstate: Optional[str] = None, invalidated: bool = False) -> DBAPIError:
    """A SQLAlchemy error wrapping a stub driver error."""
    return DBAPIError('CREATE EXTENSION IF NOT EXISTS timescaledb', {}, DriverError(message, sqlstate),
                      connection_invalidated=invalidated)


@mark.unit
def test_extension_error_mapping() -> None:
    """CREATE EXTENSION failures map to privilege, preload, a concurrent creator, or the driver's message."""
    error = extension_error(driver_error('permission denied to create extension', '42501'), 'jobs')
    assert isinstance(error, TimescaleError) and 'permission denied' in str(error)
    assert 'CREATE EXTENSION timescaledb' in str(error)
    error = extension_error(driver_error('server closed the connection unexpectedly', None, invalidated=True), 'jobs')
    assert isinstance(error, TimescaleError) and 'shared_preload_libraries' in str(error)
    error = extension_error(driver_error('FATAL: extension "timescaledb" must be preloaded', '0A000'), 'jobs')
    assert isinstance(error, TimescaleError) and 'shared_preload_libraries' in str(error)
    assert extension_error(driver_error('duplicate key value', '23505'), 'jobs') is None
    assert extension_error(driver_error('extension "timescaledb" already exists', '42710'), 'jobs') is None
    error = extension_error(driver_error('out of shared memory', '53200'), 'jobs')
    assert isinstance(error, TimescaleError) and 'out of shared memory' in str(error) and '\'jobs\'' in str(error)


class FakeResult:
    """The one value a probe returns."""

    def __init__(self, value: object) -> None:
        self.value = value

    def scalar(self) -> object:
        return self.value

    def first(self) -> object:
        return self.value


class FakeServer:
    """
    Stand-in engine for `prepare`: the version the server offers, the version created in the
    database, and whether the server preloads the TimescaleDB library. Records every statement.
    """

    url = make_url('postgresql+psycopg://user@localhost/jobs')

    def __init__(self, installed: Optional[str] = None, available: Optional[str] = None,
                 preloaded: bool = True) -> None:
        self.installed = installed
        self.available = available
        self.preloaded = preloaded
        self.statements: List[str] = []

    def connect(self) -> FakeServerConnection:
        return FakeServerConnection(self)

    def created(self) -> bool:
        return any(sql.startswith('CREATE EXTENSION') for sql in self.statements)


class FakeServerConnection:
    """One connection to a `FakeServer`."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server

    def __enter__(self) -> FakeServerConnection:
        return self

    def __exit__(self, *exc) -> None:
        pass

    def execution_options(self, **options) -> FakeServerConnection:
        return self

    def execute(self, statement, params=None) -> FakeResult:
        sql = str(statement)
        self.server.statements.append(sql)
        if 'FROM pg_extension' in sql:
            return FakeResult(self.server.installed)
        if 'pg_available_extension' in sql:
            return FakeResult(self.server.available)
        if 'timescaledb.max_background_workers' in sql:  # Defined only by the preloaded library.
            return FakeResult('16' if self.server.preloaded else None)
        if sql.startswith('CREATE EXTENSION'):
            self.server.installed = self.server.available
            return FakeResult(None)
        raise AssertionError(f'unexpected statement: {sql}')

    def invalidate(self) -> None:
        pass

    def close(self) -> None:
        pass


@mark.unit
def test_prepare_creates_offered_extension() -> None:
    """A usable server offering the extension gets it created in the database."""
    server = FakeServer(available='2.31.0')
    timescale.prepare(server)
    assert server.created() and server.installed == '2.31.0'


@mark.unit
def test_prepare_refuses_old_offer_before_creating() -> None:
    """A server offering only a release before 2.22.0 is refused, and nothing is created."""
    server = FakeServer(available='2.21.3')
    with pytest.raises(TimescaleError, match='2.21.3.*too old.*2.22.0'):
        timescale.prepare(server)
    assert not server.created() and server.installed is None


@mark.unit
def test_prepare_refuses_without_preload_before_creating() -> None:
    """A server that does not preload the library is refused before the extension is created."""
    server = FakeServer(available='2.31.0', preloaded=False)
    with pytest.raises(TimescaleError, match='shared_preload_libraries'):
        timescale.prepare(server)
    assert not server.created() and server.installed is None


@mark.unit
def test_prepare_refuses_created_without_preload() -> None:
    """An extension already created, on a server that does not preload it, is refused before the schema step."""
    server = FakeServer(installed='2.31.0', available='2.31.0', preloaded=False)
    with pytest.raises(TimescaleError, match='shared_preload_libraries'):
        timescale.prepare(server)
    assert not server.created()


@mark.integration
@needs_server
@without_timescale
def test_absent_fails_before_schema(ts_database, ts_site) -> None:
    """Without TimescaleDB on the server, initdb fails with guidance and creates nothing."""
    engine = ts_site(ts_database())
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.runtime_error, stderr
    output = '\n'.join(stderr)
    assert 'TimescaleError: TimescaleDB is not installed' in output
    assert 'database.provider = "postgres"' in output
    assert 'Traceback' not in output
    assert inspect(engine).get_table_names() == []


@mark.integration
@needs_server
@with_timescale
def test_extension_created(ts_database, ts_site) -> None:
    """In a database without the extension, initdb creates it, then the tables."""
    engine = ts_site(ts_database('template0'))
    assert extension_installed(engine) is None
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert extension_installed(engine) is not None
    assert any('Created extension timescaledb' in line for line in stderr), stderr
    assert_converged(engine)


# --------------------------------------------------------------------------------------------------
# The hypertable pass after the schema step


@mark.unit
@mark.parametrize('left, right', [
    ('id  desc', 'id DESC'),
    ('id DESC NULLS FIRST', 'id DESC'),
    ('id ASC NULLS LAST', 'id'),
    ('client_id,  id', 'client_id, id'),
    ('', None),
])
def test_normalize_columns_equal(left: str, right: Optional[str]) -> None:
    """Spacing, case, and explicit defaults do not count as drift."""
    assert normalize_columns(left) == normalize_columns(right)


@mark.unit
@mark.parametrize('left, right', [
    ('id DESC', 'id'),
    ('id DESC NULLS LAST', 'id DESC'),
    ('id NULLS FIRST', 'id'),
    ('client_id', 'client_id, id'),
])
def test_normalize_columns_differ(left: str, right: str) -> None:
    """A different column, direction, or NULLS placement is drift."""
    assert normalize_columns(left) != normalize_columns(right)


@mark.unit
def test_default_settings() -> None:
    """Zero configuration: one-week chunks, client_id/id DESC columnstore, policy after four weeks."""
    assert timescale.DEFAULTS == Settings(chunk_interval='1 week', columnstore=True, segmentby='client_id',
                                          orderby='id DESC', policy=True, compress_after='4 weeks')


@mark.integration
@needs_server
@with_timescale
def test_fresh_initdb(ts_database, ts_site) -> None:
    """A new database gets the whole shape the server supports, and the tier warnings it calls for."""
    engine = ts_site(ts_database())
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert_converged(engine)
    assert_tier_warnings(engine, stderr)
    assert any(line.endswith('Converted table task to a hypertable partitioned by id (1 week chunks)')
               for line in pass_lines(stderr, 'INFO')), stderr
    for name in ('client', 'source'):
        with engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM timescaledb_information.hypertables '
                                     'WHERE hypertable_name = :name'), {'name': name}).scalar() == 0
    assert not any('not yet implemented' in line for line in stderr)


@mark.integration
@needs_server
@with_timescale
def test_rerun_is_quiet(ts_database, ts_site) -> None:
    """Re-running initdb, and launching with --initdb, change nothing and log no change."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    state = assert_converged(engine)
    for argv in (['hs', 'initdb', '--yes'], ['hs', 'initdb', '--yes'],
                 ['hs', 'submit', '--initdb', 'echo', 'one'], ['hs', 'submit', '--initdb', 'echo', 'two']):
        rc, _, stderr = main_lines(argv)
        assert rc == exit_status.success, stderr
        assert pass_lines(stderr, 'INFO') == [], stderr
        assert_tier_warnings(engine, stderr)
        assert shape(engine) == state
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['2'])


@mark.integration
@needs_server
@with_timescale
def test_truncate_recreates(ts_database, ts_site) -> None:
    """Truncating drops the hypertable and its policy; the pass recreates both, one policy only."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    assert main_lines(['hs', 'submit', 'echo', 'one'])[0] == exit_status.success
    rc, _, stderr = main_lines(['hs', 'initdb', '--truncate', '--yes'])
    assert rc == exit_status.success, stderr
    assert_converged(engine)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['0'])
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr
    assert_converged(engine)


@mark.integration
@needs_server
@with_timescale
def test_run_compress_list_vacuum(ts_database, ts_site) -> None:
    """Tasks run on the hypertable; with a chunk in the columnstore, list, vacuum, and initdb still work."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    for n in range(4):
        assert main_lines(['hs', 'submit', 'echo', f'task-{n}'])[0] == exit_status.success
    rc, stdout, stderr = main(['hs', 'cluster', '--restart', '-N2', '-b1', '-w1', '--no-tls'])
    assert rc == exit_status.success, stderr
    assert sorted(stdout.split()) == [f'task-{n}' for n in range(4)]
    if server_gates(engine).columnstore:
        with engine.connect() as conn:
            compressed = conn.execute(text('SELECT compress_chunk(c) FROM show_chunks(\'task\') c')).all()
            conn.commit()
        assert compressed
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['4'])
    assert main_lines(['hs', 'list', '--remaining', '--count'])[:2] == (exit_status.success, ['0'])
    assert main_lines(['hs', 'submit', 'echo', 'late'])[0] == exit_status.success
    rc, _, stderr = main_lines(['hs', 'initdb', '--vacuum', '--yes'])
    assert rc == exit_status.success, stderr
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr
    assert_converged(engine)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['5'])


@mark.integration
@needs_server
@with_timescale
def test_empty_plain_table_converted(ts_database, ts_site) -> None:
    """An empty plain task table from a postgres-provider initdb is converted."""
    name = ts_database()
    engine = ts_site(name, provider='postgres')
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    assert not is_hypertable(engine)
    ts_site(name)
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert_converged(engine)


@mark.integration
@needs_server
@with_timescale
def test_nonempty_plain_table_untouched(ts_database, ts_site) -> None:
    """A plain task table holding rows is left plain, with a warning, and initdb succeeds."""
    name = ts_database()
    engine = ts_site(name, provider='postgres')
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    assert main_lines(['hs', 'submit', 'echo', 'kept'])[0] == exit_status.success
    before = task_rows(engine)
    ts_site(name)
    for _ in range(2):
        rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
        assert rc == exit_status.success, stderr
        assert any('holds rows and is a plain table' in line for line in pass_lines(stderr, 'WARNING')), stderr
        assert pass_lines(stderr, 'INFO') == [], stderr
    assert not is_hypertable(engine)
    assert task_rows(engine) == before
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['1'])


@mark.integration
@needs_server
@with_timescale
def test_legacy_database_upgraded_not_converted(ts_database, ts_site) -> None:
    """A 2.8.3 database (UUIDv4 ids) opened under the timescale provider is upgraded, left plain, and works."""
    engine = ts_site(ts_database('hs283'))
    before = task_rows(engine)
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert any('holds rows and is a plain table' in line for line in pass_lines(stderr, 'WARNING')), stderr
    assert not is_hypertable(engine)
    assert task_rows(engine) == before
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM information_schema.columns '
                                 'WHERE table_name = \'task\' AND column_name = \'source\'')).scalar() == 1
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])


@mark.integration
@needs_server
@with_timescale
def test_drift_restored(ts_database, ts_site) -> None:
    """A chunk interval, columnstore setting, or policy changed by hand is put back, with INFO naming each."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    allowed = server_gates(engine)
    with engine.connect() as conn:
        conn.execute(text('SELECT set_chunk_time_interval(\'task\', INTERVAL \'1 day\')'))
        if allowed.columnstore:
            conn.execute(text('ALTER TABLE task SET (timescaledb.orderby = \'id ASC\')'))
        if allowed.policy:
            conn.execute(text('CALL remove_columnstore_policy(\'task\')'))
            conn.execute(text('CALL add_columnstore_policy(\'task\', after => INTERVAL \'2 weeks\')'))
        conn.commit()
    drifted = shape(engine)
    assert drifted['interval'] == timedelta(days=1)
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    info = pass_lines(stderr, 'INFO')
    assert any('Changed the chunk interval of task from' in line and 'to 1 week' in line for line in info), stderr
    assert any('Changed the columnstore settings of task' in line for line in info) == allowed.columnstore, stderr
    assert any('Replaced the columnstore policy on task' in line for line in info) == allowed.policy, stderr
    assert_converged(engine)
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr


@mark.integration
@needs_server
@with_timescale
def test_named_schema(ts_database, ts_site) -> None:
    """With database.schema set, the hypertable is made there and nothing is created in public."""
    name = ts_database()
    with database_engine(name, isolation_level='AUTOCOMMIT').connect() as conn:
        conn.execute(text('CREATE SCHEMA jobs'))
    engine = ts_site(name, schema='jobs')
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert_converged(engine, schema='jobs')
    assert inspect(engine).get_table_names(schema='public') == []
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr


@mark.integration
@needs_server
@with_timescale
def test_concurrent_initdb(ts_database, ts_site) -> None:
    """Concurrent initdb runs all succeed; exactly one converts the table, and one policy results."""
    engine = ts_site(ts_database())
    count = 3
    barrier = threading.Barrier(count)
    results: List[Optional[Tuple[int, str, str]]] = [None] * count

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = main(['hs', 'initdb', '--yes'])

    threads = [threading.Thread(target=worker, args=(i, )) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    if any(result is None for result in results):
        pytest.fail('a worker did not finish')
    assert [rc for rc, _, _ in results] == [exit_status.success] * count, results
    assert len([stderr for _, _, stderr in results if 'Converted table task' in stderr]) == 1, results
    assert_converged(engine)


# --------------------------------------------------------------------------------------------------
# The [database.timescale] configuration table


@mark.unit
@mark.parametrize('table, expected', [
    (None, Settings()),
    ({}, Settings()),
    ({'chunk_interval': '1  day', 'compress_after': '2 weeks'},
     Settings(chunk_interval='1 day', compress_after='2 weeks')),
    ({'chunk_interval': '12 HOURS'}, Settings(chunk_interval='12 HOURS')),
    ({'segmentby': 'client_id, submit_host', 'orderby': 'id desc, submit_time ASC NULLS FIRST'},
     Settings(segmentby='client_id, submit_host', orderby='id desc, submit_time ASC NULLS FIRST')),
    ({'segmentby': '"client_id"', 'orderby': '"id"  DESC'}, Settings(segmentby='client_id', orderby='id DESC')),
    ({'segmentby': ''}, Settings(segmentby='')),
    ({'policy': False}, Settings(policy=False)),
    ({'policy': 'false', 'columnstore': 'false'}, Settings(policy=False, columnstore=False)),
])
def test_settings_from_config(table, expected: Settings) -> None:
    """Zero configuration gives the defaults; each key overrides one of them."""
    assert Settings.from_config(table) == expected


class FakeColumnstore:
    """
    Stand-in connection for the pass on a `task` hypertable that TimescaleDB 2.31.0 already holds
    (partitioned by `id`, policy in place), modelling the columnstore settings as upstream does:

    - `ALTER TABLE … SET (timescaledb.segmentby = …, timescaledb.orderby = …)` parses each option as
      raw SQL (`GROUP BY <text>`, `ORDER BY <text>`), so a bare reserved word is a syntax error, and
      refuses a column named twice (`src/with_clause/alter_table_with_clause.c`).
    - A partition column named in neither list is appended to orderby, descending, nulls first
      (`add_time_to_order_by_if_not_included`, `tsl/src/compression/create.c`).
    - `timescaledb_information.hypertable_columnstore_settings` shows segmentby as the bare names
      joined by ',' and orderby items as `format('%I%s%s', …)` joined by ',' (`sql/views.sql`).
    """

    dialect = postgresql.dialect()

    def __init__(self) -> None:
        self.enabled = False
        self.segmentby: List[str] = []
        self.orderby: List[Tuple[str, bool, bool]] = []  # (column, descending, nulls first)
        self.statements: List[str] = []

    def alters(self) -> List[str]:
        return [sql for sql in self.statements if sql.startswith('ALTER TABLE')]

    def execute(self, statement, params=None) -> FakeResult:
        sql = ' '.join(str(statement).split())
        self.statements.append(sql)
        if 'FROM pg_extension' in sql:
            return FakeResult(('2.31.0', 'public'))
        if 'timescaledb.license' in sql:
            return FakeResult('timescale')
        if 'timescaledb_information.dimensions' in sql:
            return FakeResult(('id', '7 days', True))
        if 'SELECT compression_enabled' in sql:
            return FakeResult(self.enabled)
        if 'hypertable_columnstore_settings' in sql:
            return FakeResult(self.view())
        if 'timescaledb_information.jobs' in sql:
            return FakeResult((1000, '28 days', True, 'Success'))
        raise AssertionError(f'unexpected statement: {sql}')

    def exec_driver_sql(self, sql: str) -> None:
        self.statements.append(sql)
        if not sql.startswith('ALTER TABLE'):
            raise AssertionError(f'unexpected statement: {sql}')
        segmentby = [self.parse(item, sql)[0] for item in self.option('segmentby', sql)]
        orderby = [self.parse(item, sql) for item in self.option('orderby', sql)]
        for names in (segmentby, [name for name, _, _ in orderby]):
            if len(set(names)) != len(names):
                raise DBAPIError(sql, {}, DriverError('duplicate column name', '42701'))
        if 'id' not in segmentby and 'id' not in [name for name, _, _ in orderby]:
            orderby.append(('id', True, True))
        self.enabled, self.segmentby, self.orderby = True, segmentby, orderby

    @staticmethod
    def option(name: str, sql: str) -> List[str]:
        match = re.search(rf"timescaledb\.{name} = '((?:[^']|'')*)'", sql)
        text_ = match.group(1).replace("''", "'") if match else ''
        return [item for item in text_.split(',') if item.strip()]

    def parse(self, item: str, sql: str) -> Tuple[str, bool, bool]:
        words = item.split()
        name = words[0]
        if name.startswith('"'):
            name = name.strip('"')
        elif name.lower() in self.dialect.identifier_preparer.reserved_words:
            raise DBAPIError(sql, {}, DriverError(f'syntax error at or near "{name}"', '42601'))
        rest = [word.lower() for word in words[1:]]
        descending = 'desc' in rest
        nulls_first = rest[-1] == 'first' if 'nulls' in rest else descending
        return name, descending, nulls_first

    def view(self) -> Optional[Tuple[str, str]]:
        if not self.enabled:
            return None
        quote = self.dialect.identifier_preparer.quote
        orderby = []
        for name, descending, nulls_first in self.orderby:
            nulls = (' NULLS FIRST' if nulls_first and not descending else
                     ' NULLS LAST' if descending and not nulls_first else '')
            orderby.append(quote(name) + (' DESC' if descending else '') + nulls)
        return ','.join(self.segmentby), ','.join(orderby)


def run_pass(conn: FakeColumnstore, settings: Settings) -> List[str]:
    """The real pass on the fake connection; returns its changes."""
    changes, warnings = timescale._Pass(conn, 'public', settings).run()
    assert warnings == []
    return changes


@mark.unit
def test_default_columnstore_converges() -> None:
    """The default settings are applied once; the next pass finds them in place."""
    conn = FakeColumnstore()
    assert len(run_pass(conn, timescale.DEFAULTS)) == 1
    assert run_pass(conn, timescale.DEFAULTS) == []
    assert len(conn.alters()) == 1


@mark.unit
def test_orderby_without_id_converges() -> None:
    """An orderby that omits id is compared as TimescaleDB stores it (id DESC appended): one ALTER, then none."""
    conn = FakeColumnstore()
    settings = Settings.from_config({'orderby': 'submit_time DESC'})
    assert len(run_pass(conn, settings)) == 1
    assert conn.view() == ('client_id', 'submit_time DESC,id DESC')
    assert run_pass(conn, settings) == []
    assert run_pass(conn, settings) == []
    assert len(conn.alters()) == 1


@mark.unit
def test_group_column_emitted_quoted() -> None:
    """The task column `group` (a reserved word) is accepted and emitted as a quoted identifier."""
    settings = Settings.from_config({'segmentby': 'client_id, group', 'orderby': 'group DESC'})
    assert settings.segmentby == 'client_id, group' and settings.orderby == 'group DESC'
    conn = FakeColumnstore()
    assert len(run_pass(conn, settings)) == 1
    alter, = conn.alters()
    assert 'timescaledb.segmentby = \'client_id, "group"\'' in alter, alter
    assert 'timescaledb.orderby = \'"group" DESC' in alter, alter
    assert conn.view() == ('client_id,group', '"group" DESC,id DESC')
    assert run_pass(conn, settings) == []
    assert len(conn.alters()) == 1


@mark.unit
@mark.parametrize('table, named', [
    ({'segmentby': 'client_id, client_id'}, 'database.timescale.segmentby'),
    ({'segmentby': 'client_id, "client_id"'}, 'database.timescale.segmentby'),
    ({'orderby': 'id DESC, id'}, 'database.timescale.orderby'),
    ({'orderby': 'submit_time, "submit_time" DESC'}, 'database.timescale.orderby'),
])
def test_settings_duplicate_column(table, named: str) -> None:
    """A column named twice in one list is refused at validation, before any connection."""
    with pytest.raises(ConfigurationError, match='expected') as caught:
        Settings.from_config(table)
    assert named in str(caught.value) and 'once' in str(caught.value)


@mark.unit
@mark.parametrize('table, named', [
    ({'segmentby': 'client_id, client'}, 'database.timescale.segmentby'),
    ({'segmentby': '"nope"'}, 'database.timescale.segmentby'),
    ({'segmentby': 'client_id DESC'}, 'database.timescale.segmentby'),
    ({'orderby': 'id DESC, nope'}, 'database.timescale.orderby'),
    ({'orderby': '"Group" DESC'}, 'database.timescale.orderby'),
])
def test_settings_unknown_column(table, named: str) -> None:
    """A name that is not a task column is refused at validation, before any connection."""
    with pytest.raises(ConfigurationError, match='expected') as caught:
        Settings.from_config(table)
    assert named in str(caught.value) and 'task column' in str(caught.value)


@mark.unit
@mark.parametrize('table, named', [
    ({'chunk_interval': 'soon'}, 'database.timescale.chunk_interval'),
    ({'chunk_interval': '0 days'}, 'database.timescale.chunk_interval'),
    ({'chunk_interval': '1 fortnight'}, 'database.timescale.chunk_interval'),
    ({'compress_after': 7}, 'database.timescale.compress_after'),
    ({'segmentby': 'nope'}, 'database.timescale.segmentby'),
    ({'orderby': 'id sideways'}, 'database.timescale.orderby'),
    ({'orderby': ''}, 'database.timescale.orderby'),
    ({'orderby': 'id; DROP TABLE task'}, 'database.timescale.orderby'),
    ({'columnstore': 'maybe'}, 'database.timescale.columnstore'),
    ({'policy': 1}, 'database.timescale.policy'),
    ({'chunk': '1 day'}, 'database.timescale.chunk'),
    ({'columnstore': False}, 'policy = true needs columnstore = true'),
    ('1 day', 'database.timescale'),
])
def test_settings_invalid(table, named: str) -> None:
    """An invalid value fails with the key, the value, and the accepted form."""
    with pytest.raises(ConfigurationError, match='expected|needs') as caught:
        Settings.from_config(table)
    assert named in str(caught.value)


def write_config(path: Path, lines: List[str]) -> None:
    """Use `lines` as the only configuration file."""
    path.write_text('\n'.join(['[database.timescale]', *lines, '']))
    os.environ['HYPERSHELL_CONFIG_FILE'] = str(path)


@mark.integration
@needs_server
def test_invalid_setting_exits(ts_database, ts_site, tmp_path: Path) -> None:
    """An invalid setting stops every command at startup with bad_config, naming the key, before connecting."""
    engine = ts_site(ts_database())
    write_config(tmp_path / 'config.toml', ['chunk_interval = "soon"'])
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.bad_config, stderr
    output = '\n'.join(stderr)
    assert 'database.timescale.chunk_interval' in output and 'soon' in output
    assert 'Traceback' not in output
    assert inspect(engine).get_table_names() == []


@mark.integration
@needs_server
@with_timescale
def test_configured_fresh(ts_database, ts_site, tmp_path: Path) -> None:
    """Configured chunk interval and policy interval are applied to a new database."""
    engine = ts_site(ts_database())
    write_config(tmp_path / 'config.toml', ['chunk_interval = "1 day"', 'compress_after = "2 weeks"'])
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    assert_converged(engine, interval=timedelta(days=1), after=timedelta(days=14))
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr


@mark.integration
@needs_server
@with_timescale
def test_configured_drift(ts_database, ts_site, tmp_path: Path) -> None:
    """Changing the configuration later is applied as drift, with INFO naming each change."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    assert_converged(engine)
    allowed = server_gates(engine)
    write_config(tmp_path / 'config.toml', ['chunk_interval = "1 day"', 'compress_after = "2 weeks"'])
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    info = pass_lines(stderr, 'INFO')
    assert any('Changed the chunk interval of task' in line and 'to 1 day' in line for line in info), stderr
    assert any('Replaced the columnstore policy on task: compress chunks older than 2 weeks' in line
               for line in info) == allowed.policy, stderr
    assert_converged(engine, interval=timedelta(days=1), after=timedelta(days=14))


@mark.integration
@needs_server
@with_timescale
def test_policy_off_removes_job(ts_database, ts_site, tmp_path: Path) -> None:
    """With policy = false, an existing columnstore policy is removed and none is added again."""
    engine = ts_site(ts_database())
    assert main_lines(['hs', 'initdb', '--yes'])[0] == exit_status.success
    allowed = server_gates(engine)
    write_config(tmp_path / 'config.toml', ['policy = false'])
    for run in range(2):
        rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
        assert rc == exit_status.success, stderr
        removed = any(line.endswith('Removed the columnstore policy on task') for line in pass_lines(stderr, 'INFO'))
        assert removed == (allowed.policy and run == 0), stderr
        assert shape(engine)['jobs'] == []
    state = shape(engine)
    assert state['hypertable'] and state['columnstore'] == allowed.columnstore
    rc, _, stderr = main_lines(['hs', 'initdb', '--truncate', '--yes'])
    assert rc == exit_status.success, stderr
    assert shape(engine)['jobs'] == []


@mark.integration
@needs_server
@with_timescale
def test_configured_columns_converge(ts_database, ts_site, tmp_path: Path) -> None:
    """The column `group` and an orderby without id are applied once; the next run changes nothing."""
    engine = ts_site(ts_database())
    write_config(tmp_path / 'config.toml', ['segmentby = "client_id, group"', 'orderby = "submit_time DESC"'])
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr
    allowed = server_gates(engine)
    state = shape(engine)
    assert state['hypertable'] and state['columnstore'] == allowed.columnstore, state
    if allowed.columnstore:
        assert (state['segmentby'], state['orderby']) == ('client_id, group', 'submit_time desc, id desc'), state
    rc, _, stderr = main_lines(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success and pass_lines(stderr, 'INFO') == [], stderr
