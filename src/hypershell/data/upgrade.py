# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Bring an existing database schema up to the model declarations, in place.

The step only adds what the models declare and the database lacks: tables, columns, and
indexes. It never drops, renames, or retypes a table or column and never rewrites a row.
The one removal it makes is the retired retry-chain uniqueness on PostgreSQL (see
`RETIRED_UNIQUE`). A current schema costs one read-only reflection and takes no lock.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Final, Iterator, List, Optional, Tuple, FrozenSet, TypeVar

# Standard libs
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass

# External libs
from sqlalchemy import inspect, text, Column, Index, MetaData, Table
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable, CreateIndex, CreateColumn, DefaultClause
from sqlalchemy.sql.elements import TextClause

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data.model import Entity

# Public interface
__all__ = ['upgrade_schema', 'SCHEMA_UPGRADE_LOCK_KEY', 'RETIRED_UNIQUE',
           'LOCK_WAIT_SLICE', 'LOCK_WAIT_CEILING', ]

# Initialize logger
log = Logger.with_name(__name__)


# PostgreSQL advisory lock key that serializes concurrent upgrades. It is the first 8 bytes
# (big-endian, signed) of sha256(b'hypershell:schema-upgrade'), and must never change across
# releases, so that processes of different versions contend on the same lock.
SCHEMA_UPGRADE_LOCK_KEY: Final[int] = -4139901733377976503

# Retry-chain uniqueness the models no longer declare. These are the only constraints the
# step ever drops, and only on PostgreSQL: SQLite cannot drop a table constraint without
# rebuilding the table, so older SQLite databases keep them.
RETIRED_UNIQUE: Final[FrozenSet[Tuple[str, str]]] = frozenset({('task', 'previous_id'), ('task', 'next_id')})

# How the step waits on a lock another session holds (seconds). Every lock wait in the step is
# bounded by one slice. When a slice runs out the step rolls back, logs that it is still waiting,
# and tries again, so a peer's long upgrade of a large table is waited out. Past the ceiling it
# gives up with an error naming what it waited on. A lock dies with the session holding it, so
# waiting cannot outlive a crashed peer. Neither bound applies outside the step.
LOCK_WAIT_SLICE: float = 30.0
LOCK_WAIT_CEILING: float = 900.0

# What a lock wait is waiting on, for the log and for the error past the ceiling.
WAIT_SQLITE: Final[str] = 'another session to release the database, likely a concurrent schema upgrade'
WAIT_UPGRADE: Final[str] = 'a concurrent schema upgrade to finish'
WAIT_TABLES: Final[str] = 'another session to release its lock on the tables'

# A server default the database can apply to existing rows: a number, a quoted string,
# NULL, TRUE, or FALSE (optionally in parentheses).
CONSTANT_DEFAULT: Final[re.Pattern] = re.compile(
    r"^\s*\(?\s*(-?\d+(\.\d+)?|'([^']|'')*'|NULL|TRUE|FALSE)\s*\)?\s*$", re.IGNORECASE
)

# Errors that mean a peer already applied the same change.
DUPLICATE_MESSAGES: Final[Tuple[str, ...]] = ('duplicate column name', 'already exists')
DUPLICATE_SQLSTATES: Final[FrozenSet[str]] = frozenset({'42701', '42P07', '23505'})


T = TypeVar('T')


class LockWait(Exception):
    """A lock wait in the step ran out its slice; the attempt was rolled back."""

    def __init__(self, waiting_for: str) -> None:
        super().__init__(waiting_for)
        self.waiting_for = waiting_for


@dataclass(frozen=True)
class Step:
    """One schema change: the table it touches, a log message, and the statement."""
    table: str
    message: str
    sql: str


@dataclass(frozen=True)
class Plan:
    """The changes needed, and whether the database already held any model table."""
    steps: List[Step]
    existing: bool


def upgrade_schema(bind: Engine, metadata: MetaData = None) -> List[str]:
    """
    Create every table, column, and index in `metadata` that the database lacks.

    Returns the changes made (empty when the schema was already current). Raises
    `SchemaUpgradeError`, with the schema unchanged, for a column that cannot be added in
    place or when the change cannot be applied.
    """
    metadata = metadata if metadata is not None else Entity.metadata
    plan = _waiting(bind, lambda timeout: _check(bind, metadata, timeout), plain_first=True)
    if not plan.steps:
        log.trace('Database schema is current')
        return []
    try:
        return _apply(bind, metadata)
    except DBAPIError as error:
        if not _is_duplicate(error):
            raise _wrap(error) from error
        log.debug(f'Schema upgrade raced with another session, retrying ({_message(error)})')
    try:
        return _apply(bind, metadata)
    except DBAPIError as error:
        raise _wrap(error) from error


def _check(bind: Engine, metadata: MetaData, timeout: Optional[float]) -> Plan:
    """
    The lock-free plan. A read blocked by a peer's lock waits at most `timeout` seconds; with
    `timeout` None, SQLite keeps the connection's own busy timeout, so the uncontended check
    sends nothing but the reflection.
    """
    with bind.connect() as conn:
        if bind.dialect.name == 'sqlite':
            if timeout is None:
                with _waiting_for(WAIT_SQLITE):
                    return _plan(conn, metadata)
            with _sqlite_busy_timeout(conn, timeout), _waiting_for(WAIT_SQLITE):
                return _plan(conn, metadata)
        if bind.dialect.name == 'postgresql':
            _set_lock_timeout(conn, LOCK_WAIT_SLICE if timeout is None else timeout)
        with _waiting_for(WAIT_TABLES):
            return _plan(conn, metadata)


def _plan(conn: Connection, metadata: MetaData) -> Plan:
    """Diff the models against the live schema; refuse unsafe additions before any statement runs."""
    insp = inspect(conn)  # A fresh inspector per call; it caches reflection.
    dialect = conn.dialect
    prep = dialect.identifier_preparer
    tables, columns, drops, indexes = [], [], [], []
    existing = False
    for table in metadata.sorted_tables:
        qualified = prep.format_table(table)
        if not insp.has_table(table.name, schema=table.schema):
            tables.append(Step(table.name, f'Created table {table.name}',
                               _compile(CreateTable(table, if_not_exists=True), conn)))
            indexes.extend(_create_index(index, conn) for index in _sorted(table.indexes))
            continue
        existing = True
        present = {column['name'] for column in insp.get_columns(table.name, schema=table.schema)}
        for column in table.columns:
            if column.name not in present:
                _check_column(table, column)
                columns.append(Step(table.name, f'Added column {table.name}.{column.name}',
                                    f'ALTER TABLE {qualified} ADD COLUMN '
                                    f'{CreateColumn(column).compile(dialect=dialect)}'))
        if dialect.name == 'postgresql':
            for constraint in insp.get_unique_constraints(table.name, schema=table.schema):
                if _is_retired(table, constraint):
                    name = constraint['name']
                    drops.append(Step(table.name, f'Dropped unique constraint {name} on {table.name}',
                                      f'ALTER TABLE {qualified} DROP CONSTRAINT IF EXISTS {prep.quote(name)}'))
        present = {index['name'] for index in insp.get_indexes(table.name, schema=table.schema)}
        for index in _sorted(table.indexes):
            if index.name not in present:
                if index.unique:
                    raise SchemaUpgradeError(
                        f'Cannot upgrade table \'{table.name}\': index \'{index.name}\' is UNIQUE, so existing '
                        f'rows may violate it. Create it by hand after checking the existing rows.')
                indexes.append(_create_index(index, conn))
    return Plan(steps=tables + columns + drops + indexes, existing=existing)


def _check_column(table: Table, column: Column) -> None:
    """Raise `SchemaUpgradeError` if `column` cannot be added to existing rows in place."""
    if refusal := _refusal(column):
        reason, remedy = refusal
        raise SchemaUpgradeError(f'Cannot upgrade table \'{table.name}\': column \'{column.name}\' '
                                 f'{reason}. {remedy}')


def _refusal(column: Column) -> Optional[Tuple[str, str]]:
    """The reason and remedy when `column` cannot be added in place, else None."""
    if column.primary_key:
        return ('is part of the primary key, which an existing table cannot gain',
                'Declare it on a new table instead.')
    if column.unique:
        return ('is UNIQUE, which existing rows may violate',
                'Declare it without uniqueness and add a constraint by hand after checking the rows.')
    if column.foreign_keys:
        return ('has a foreign key, which cannot be added with the column',
                'Declare it without the foreign key and add the constraint by hand.')
    if column.computed is not None or column.identity is not None:
        return ('is a generated or identity column, which cannot be added in place',
                'Declare a plain column with a constant server_default.')
    if not column.nullable and column.server_default is None:
        return ('is NOT NULL with no server default, so existing rows have no value for it',
                'Declare a server_default or make it nullable.')
    if column.server_default is not None and not _is_constant(column.server_default):
        return ('has a server default that is not a constant, which existing rows cannot take',
                'Use a constant server_default (a number, quoted string, NULL, TRUE, or FALSE).')
    return None


def _is_constant(default: object) -> bool:
    """True when a server default is a literal the database can apply to existing rows."""
    if not isinstance(default, DefaultClause):
        return False
    if isinstance(default.arg, str):
        return True
    if isinstance(default.arg, TextClause):
        return CONSTANT_DEFAULT.match(default.arg.text) is not None
    return False


def _is_retired(table: Table, constraint: dict) -> bool:
    """True for a named single-column unique constraint the models no longer declare."""
    names = constraint.get('column_names') or []
    return (bool(constraint.get('name')) and len(names) == 1
            and (table.name, names[0]) in RETIRED_UNIQUE
            and names[0] in table.c and not table.c[names[0]].unique)


def _sorted(indexes: set) -> List[Index]:
    """Indexes in name order, for a stable plan."""
    return sorted(indexes, key=lambda index: index.name or '')


def _create_index(index: Index, conn: Connection) -> Step:
    """Step creating `index` if it does not exist."""
    return Step(index.table.name, f'Created index {index.name} on {index.table.name}',
                _compile(CreateIndex(index, if_not_exists=True), conn))


def _compile(element: object, conn: Connection) -> str:
    """Render a DDL element for this connection's dialect."""
    return str(element.compile(dialect=conn.dialect)).strip()


def _apply(bind: Engine, metadata: MetaData) -> List[str]:
    """Lock, re-plan, and apply the whole change in one transaction; log each change after commit."""
    if bind.dialect.name == 'sqlite':
        plan = _waiting(bind, lambda timeout: _apply_sqlite(bind, metadata, timeout))
    else:
        plan = _waiting(bind, lambda timeout: _apply_transactional(bind, metadata, timeout))
    messages = [step.message for step in plan.steps]
    for message in messages:
        if plan.existing:
            log.info(message)
        else:
            log.trace(message)
    if not messages:
        log.trace('Database schema is current (upgraded by another session)')
    return messages


def _apply_sqlite(bind: Engine, metadata: MetaData, timeout: float) -> Plan:
    """SQLite runs DDL in a transaction only under an explicit BEGIN, so the step issues its own."""
    with bind.connect() as conn:
        conn.execution_options(isolation_level='AUTOCOMMIT')
        with _sqlite_busy_timeout(conn, timeout), _waiting_for(WAIT_SQLITE):
            committed = False
            try:
                _begin_sqlite(conn)
                plan = _plan(conn, metadata)
                for step in plan.steps:
                    conn.exec_driver_sql(step.sql)
                conn.exec_driver_sql('COMMIT')
                committed = True
                return plan
            finally:
                if not committed:
                    _rollback_sqlite(conn)


@contextmanager
def _sqlite_busy_timeout(conn: Connection, timeout: float) -> Iterator[None]:
    """Bound this connection's lock waits by `timeout` seconds, restoring its own setting after."""
    previous = conn.exec_driver_sql('PRAGMA busy_timeout').scalar()
    conn.exec_driver_sql(f'PRAGMA busy_timeout = {_milliseconds(timeout)}')
    try:
        yield
    finally:
        conn.exec_driver_sql(f'PRAGMA busy_timeout = {int(previous)}')


def _begin_sqlite(conn: Connection) -> None:
    """Take the write lock up front; fall back to a plain BEGIN where IMMEDIATE is unsupported."""
    try:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
    except DBAPIError as error:
        if _is_locked(error):
            raise
        log.debug(f'BEGIN IMMEDIATE unsupported, using BEGIN ({_message(error)})')
        conn.exec_driver_sql('BEGIN')


def _rollback_sqlite(conn: Connection) -> None:
    """Roll back the step's transaction if one is still open."""
    if getattr(conn.connection.dbapi_connection, 'in_transaction', True):
        try:
            conn.exec_driver_sql('ROLLBACK')
        except DBAPIError as error:
            log.debug(f'Rollback after failed schema upgrade: {_message(error)}')


def _apply_transactional(bind: Engine, metadata: MetaData, timeout: float) -> Plan:
    """Databases with transactional DDL: one transaction, serialized by an advisory lock on PostgreSQL."""
    with bind.connect() as conn:
        committed = False
        try:
            if bind.dialect.name == 'postgresql':
                _set_lock_timeout(conn, timeout)
                with _waiting_for(WAIT_UPGRADE):
                    _lock_postgres(conn)
            with _waiting_for(WAIT_TABLES):
                plan = _plan(conn, metadata)
                for step in plan.steps:
                    conn.exec_driver_sql(step.sql)
                conn.commit()
            committed = True
            return plan
        finally:
            if not committed:
                conn.rollback()


def _set_lock_timeout(conn: Connection, timeout: float) -> None:
    """Bound every PostgreSQL lock wait in this transaction by `timeout` seconds."""
    conn.execute(text('SELECT set_config(\'lock_timeout\', CAST(:timeout AS text), true)'),
                 {'timeout': f'{_milliseconds(timeout)}ms'})


def _lock_postgres(conn: Connection) -> None:
    """Serialize with other upgraders until this transaction ends."""
    conn.execute(text('SELECT pg_advisory_xact_lock(CAST(:key AS bigint))'), {'key': SCHEMA_UPGRADE_LOCK_KEY})


def _milliseconds(timeout: float) -> int:
    """A lock timeout in whole milliseconds, never zero (zero disables the timeout)."""
    return max(1, int(timeout * 1000))


@contextmanager
def _waiting_for(what: str) -> Iterator[None]:
    """Report a lock wait that ran out its slice as `LockWait`, naming what it waited on."""
    try:
        yield
    except DBAPIError as error:
        if _is_locked(error):
            raise LockWait(what) from error
        raise


def _waiting(bind: Engine, attempt: Callable[[Optional[float]], T], plain_first: bool = False) -> T:
    """
    Run `attempt` with lock waits bounded by one slice, again after each slice that runs out,
    logging each time, until `LOCK_WAIT_CEILING`; past it, raise `SchemaUpgradeError`.
    With `plain_first`, the first attempt is given no timeout and keeps the connection's own.
    """
    start = time.monotonic()
    plain = plain_first
    while True:
        remaining = LOCK_WAIT_CEILING - (time.monotonic() - start)
        timeout = None if plain else min(LOCK_WAIT_SLICE, max(remaining, 0))
        plain = False
        try:
            return attempt(timeout)
        except LockWait as wait:
            elapsed = time.monotonic() - start
            if elapsed >= LOCK_WAIT_CEILING:
                raise SchemaUpgradeError(_gave_up(bind, wait.waiting_for)) from wait
            log.info(f'Waiting for {wait.waiting_for} ({elapsed:.0f}s elapsed)')


def _gave_up(bind: Engine, what: str) -> str:
    """The error past the wait ceiling: what was waited on, and how to find the holder."""
    if bind.dialect.name == 'sqlite':
        hint = f'Check which processes have {bind.url.database} open (e.g., with lsof).'
    elif what == WAIT_UPGRADE:
        hint = ('Find the upgrading session in pg_stat_activity, joined with pg_locks '
                'where locktype = \'advisory\'.')
    else:
        hint = ('Find the session holding the lock in pg_stat_activity, joined with pg_locks '
                '(e.g., one idle in transaction).')
    return (f'Cannot upgrade database schema: gave up after {_duration(LOCK_WAIT_CEILING)} waiting for '
            f'{what}. {hint} The schema is unchanged; retry once the lock is released.')


def _duration(seconds: float) -> str:
    """A wait bound for a message: minutes from one minute up, else seconds."""
    return f'{seconds / 60:g} min' if seconds >= 60 else f'{seconds:g} s'


def _sqlstate(error: DBAPIError) -> Optional[str]:
    """SQLSTATE of a driver error, when the driver reports one."""
    return getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)


def _message(error: DBAPIError) -> str:
    """The driver's own message, on one line."""
    return ' '.join(str(error.orig).split())


def _is_duplicate(error: DBAPIError) -> bool:
    """True when a peer already made the change this statement makes."""
    message = _message(error).lower()
    return _sqlstate(error) in DUPLICATE_SQLSTATES or any(text in message for text in DUPLICATE_MESSAGES)


def _is_locked(error: DBAPIError) -> bool:
    """True when another session held a lock past the step's timeout."""
    message = _message(error).lower()
    return _sqlstate(error) == '55P03' or 'database is locked' in message or 'database is busy' in message


def _wrap(error: DBAPIError) -> SchemaUpgradeError:
    """An actionable error for a failed upgrade; the transaction was rolled back."""
    return SchemaUpgradeError(f'Cannot upgrade database schema: {_message(error)}. The schema is unchanged.')
