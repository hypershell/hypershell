# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
TimescaleDB support for the `timescale` provider.

Before the schema step, `prepare` interrogates the server: the extension must be installed
(it is created in the database when possible) and recent enough to partition a table by a
UUIDv7 column. Every failure here leaves the database untouched.
"""


# Type annotations
from __future__ import annotations
from typing import Final, Optional, Tuple

# Standard libs
import re
from dataclasses import dataclass

# External libs
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.exc import DBAPIError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data.upgrade import _sqlstate, _message

# Public interface
__all__ = ['prepare', 'gates', 'parse_version', 'Gates', 'TimescaleError',
           'MIN_VERSION', 'POLICY_VERSION', ]

# Initialize logger
log = Logger.with_name(__name__)


# The first release that partitions a hypertable by a UUIDv7 column.
MIN_VERSION: Final[Tuple[int, int, int]] = (2, 22, 0)

# The first release whose columnstore policy runs on a UUID-partitioned hypertable. Earlier
# releases accept the policy, but every run of its job fails (fixed upstream in 2.25.0).
POLICY_VERSION: Final[Tuple[int, int, int]] = (2, 25, 0)

# The `timescaledb.license` setting of an Apache-only build: hypertables, no columnstore.
APACHE: Final[str] = 'apache'

# Errors that mean a concurrent session created the extension first.
RACE_SQLSTATES: Final[Tuple[str, ...]] = ('23505', '42710')

VERSION_PATTERN: Final[re.Pattern] = re.compile(r'^\s*(\d+)\.(\d+)(?:\.(\d+))?')


class TimescaleError(SchemaUpgradeError):
    """The database cannot be brought to the TimescaleDB shape the provider promises."""


def parse_version(value: str) -> Tuple[int, int, int]:
    """The (major, minor, patch) of an extension version string, ignoring any suffix (e.g., '-dev')."""
    match = VERSION_PATTERN.match(value or '')
    if match is None:
        raise TimescaleError(f'Cannot read the TimescaleDB version \'{value}\'')
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def format_version(version: Tuple[int, ...]) -> str:
    """Dotted version string."""
    return '.'.join(str(part) for part in version)


@dataclass(frozen=True)
class Gates:
    """Which parts of the TimescaleDB shape a server supports, and why any part is refused."""
    hypertable: bool
    columnstore: bool
    policy: bool
    reasons: Tuple[str, ...] = ()


def gates(version: Tuple[int, int, int], edition: Optional[str]) -> Gates:
    """The parts of the shape that TimescaleDB `version` of `edition` (its license setting) supports."""
    if version < MIN_VERSION:
        return Gates(hypertable=False, columnstore=False, policy=False,
                     reasons=(too_old_message(version), ))
    if edition == APACHE:
        return Gates(hypertable=True, columnstore=False, policy=False,
                     reasons=(apache_message(), ))
    if version < POLICY_VERSION:
        return Gates(hypertable=True, columnstore=True, policy=False,
                     reasons=(policy_refused_message(version), ))
    return Gates(hypertable=True, columnstore=True, policy=True)


def absent_message(database: str) -> str:
    """The server does not offer the extension."""
    return (f'TimescaleDB is not installed on the PostgreSQL server of database \'{database}\'. '
            f'Install the timescaledb extension on the server (or use a server image that ships it), '
            f'or set database.provider = "postgres" for plain PostgreSQL tables.')


def privilege_message(database: str) -> str:
    """The extension is available but this role cannot create it."""
    return (f'Cannot create the TimescaleDB extension in database \'{database}\': permission denied. '
            f'Ask an administrator to run "CREATE EXTENSION timescaledb" in database \'{database}\', '
            f'then rerun hs initdb.')


def preload_message() -> str:
    """The extension is installed but the server does not load its library at start."""
    return ('TimescaleDB is installed but not preloaded by the PostgreSQL server. '
            'Add timescaledb to shared_preload_libraries in postgresql.conf and restart the server, '
            'then rerun hs initdb.')


def too_old_message(version: Tuple[int, ...]) -> str:
    """The extension predates partitioning by a UUIDv7 column."""
    return (f'TimescaleDB {format_version(version)} is too old: partitioning the task table by its UUIDv7 id '
            f'needs TimescaleDB {format_version(MIN_VERSION)} or later. Install a newer release on the server '
            f'and run "ALTER EXTENSION timescaledb UPDATE" in the database, then rerun hs initdb.')


def apache_message() -> str:
    """The Apache-only build has hypertables but no columnstore."""
    return ('TimescaleDB is the Apache-only edition (timescaledb.license = \'apache\'): the task table '
            'is a hypertable, but columnstore compression and its policy need the Community edition '
            '(timescaledb.license = \'timescale\') and were skipped.')


def policy_refused_message(version: Tuple[int, ...]) -> str:
    """The columnstore policy fails on UUID hypertables before the upstream fix."""
    return (f'TimescaleDB {format_version(version)} cannot run a columnstore policy on a table partitioned '
            f'by a UUID (fixed in TimescaleDB {format_version(POLICY_VERSION)}); the policy was not added. '
            f'Update the extension to {format_version(POLICY_VERSION)} or later and rerun hs initdb.')


def extension_error(error: DBAPIError, database: str) -> Optional[TimescaleError]:
    """
    The actionable error for a failed CREATE EXTENSION, or None when a concurrent session
    created the extension first (the caller probes again).
    """
    state, message = _sqlstate(error), _message(error)
    if state in RACE_SQLSTATES:
        return None
    if state == '42501':
        return TimescaleError(privilege_message(database))
    if error.connection_invalidated or 'must be preloaded' in message.lower():
        return TimescaleError(preload_message())
    return TimescaleError(f'Cannot create the TimescaleDB extension in database \'{database}\': {message}')


def prepare(engine: Engine) -> None:
    """
    Make sure the server can hold the TimescaleDB shape before any table is created:
    the extension exists in the database (created here when available and permitted)
    and is recent enough. Raises `TimescaleError` with guidance otherwise.
    """
    database = engine.url.database
    with engine.connect() as conn:
        installed = _installed_version(conn)
        available = None if installed else _available_version(conn)
    log.debug(f'TimescaleDB extension: installed={installed} available={available}')
    if installed is None:
        if available is None:
            raise TimescaleError(absent_message(database))
        _create_extension(engine, database)
        with engine.connect() as conn:
            installed = _installed_version(conn)
        if installed is None:
            raise TimescaleError(absent_message(database))
        log.info(f'Created extension timescaledb {installed} in database {database}')
    version = parse_version(installed)
    if version < MIN_VERSION:
        raise TimescaleError(too_old_message(version))


def _installed_version(conn: Connection) -> Optional[str]:
    """The extension's version in this database, or None if it is not created here."""
    return conn.execute(text('SELECT extversion FROM pg_extension WHERE extname = \'timescaledb\'')).scalar()


def _available_version(conn: Connection) -> Optional[str]:
    """The version the server would install, or None if the server does not offer the extension."""
    return conn.execute(text('SELECT default_version FROM pg_available_extensions '
                             'WHERE name = \'timescaledb\'')).scalar()


def _create_extension(engine: Engine, database: str) -> None:
    """CREATE EXTENSION on its own autocommit connection, discarded if the statement fails."""
    conn = engine.connect().execution_options(isolation_level='AUTOCOMMIT')
    try:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
    except DBAPIError as error:
        conn.invalidate()
        if (mapped := extension_error(error, database)) is not None:
            raise mapped from error
        log.debug(f'TimescaleDB extension created by another session ({_message(error)})')
    finally:
        conn.close()
