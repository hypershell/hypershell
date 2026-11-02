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
from typing import Callable, Iterator, List, Optional

# Standard libs
import os
import uuid
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError


ADMIN_URL: Optional[str] = os.getenv('HS_TEST_POSTGRES')
TIMESCALE: str = os.getenv('HS_TEST_TIMESCALE', '')
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
