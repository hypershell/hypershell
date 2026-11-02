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
from typing import Final, List, Optional, Tuple, FrozenSet

# Standard libs
import re
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
           'SQLITE_BUSY_TIMEOUT', 'POSTGRES_LOCK_TIMEOUT', ]

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

# How long the step waits for a peer's lock: the SQLite write lock (milliseconds), and any
# PostgreSQL lock the step needs (a PostgreSQL interval). Neither applies outside the step.
SQLITE_BUSY_TIMEOUT: int = 60_000
POSTGRES_LOCK_TIMEOUT: str = '30s'

# A server default the database can apply to existing rows: a number, a quoted string,
# NULL, TRUE, or FALSE (optionally in parentheses).
CONSTANT_DEFAULT: Final[re.Pattern] = re.compile(
    r"^\s*\(?\s*(-?\d+(\.\d+)?|'([^']|'')*'|NULL|TRUE|FALSE)\s*\)?\s*$", re.IGNORECASE
)

# Errors that mean a peer already applied the same change.
DUPLICATE_MESSAGES: Final[Tuple[str, ...]] = ('duplicate column name', 'already exists')
DUPLICATE_SQLSTATES: Final[FrozenSet[str]] = frozenset({'42701', '42P07', '23505'})


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
    with bind.connect() as conn:
        plan = _plan(conn, metadata)
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
        plan = _apply_sqlite(bind, metadata)
    else:
        plan = _apply_transactional(bind, metadata)
    messages = [step.message for step in plan.steps]
    for message in messages:
        if plan.existing:
            log.info(message)
        else:
            log.trace(message)
    if not messages:
        log.trace('Database schema is current (upgraded by another session)')
    return messages


def _apply_sqlite(bind: Engine, metadata: MetaData) -> Plan:
    """SQLite runs DDL in a transaction only under an explicit BEGIN, so the step issues its own."""
    with bind.connect() as conn:
        conn.execution_options(isolation_level='AUTOCOMMIT')
        previous_timeout = conn.exec_driver_sql('PRAGMA busy_timeout').scalar()
        conn.exec_driver_sql(f'PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT)}')
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
            conn.exec_driver_sql(f'PRAGMA busy_timeout = {int(previous_timeout)}')


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


def _apply_transactional(bind: Engine, metadata: MetaData) -> Plan:
    """Databases with transactional DDL: one transaction, serialized by an advisory lock on PostgreSQL."""
    with bind.connect() as conn:
        committed = False
        try:
            if bind.dialect.name == 'postgresql':
                _lock_postgres(conn)
            plan = _plan(conn, metadata)
            for step in plan.steps:
                conn.exec_driver_sql(step.sql)
            conn.commit()
            committed = True
            return plan
        finally:
            if not committed:
                conn.rollback()


def _lock_postgres(conn: Connection) -> None:
    """Bound every lock wait in this transaction, then serialize with other upgraders."""
    conn.execute(text('SELECT set_config(\'lock_timeout\', CAST(:timeout AS text), true)'),
                 {'timeout': str(POSTGRES_LOCK_TIMEOUT)})
    conn.execute(text('SELECT pg_advisory_xact_lock(CAST(:key AS bigint))'), {'key': SCHEMA_UPGRADE_LOCK_KEY})


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
    if _is_locked(error):
        return SchemaUpgradeError('Cannot upgrade database schema: another session holds a lock on it '
                                  '(an older server still running?). Stop it or retry. '
                                  'The schema is unchanged.')
    return SchemaUpgradeError(f'Cannot upgrade database schema: {_message(error)}. The schema is unchanged.')
