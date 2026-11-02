# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
TimescaleDB support for the `timescale` provider.

Before the schema step, `prepare` interrogates the server: the extension must be installed
(it is created in the database when possible) and recent enough to partition a table by a
UUIDv7 column. Every failure here leaves the database untouched.

After the schema step, `converge` brings the `task` table to its TimescaleDB shape in one
transaction: a hypertable partitioned by its UUIDv7 `id`, columnstore settings, and a
columnstore policy. Each part is probed first and changed only where it is missing or differs
from the settings, so a converged database costs only the probes. A part the server cannot
hold (the Apache edition, a release before the policy fix) is skipped with a warning, and a
plain `task` table that already holds rows is never migrated.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Mapping, Optional, Tuple

# Standard libs
import re
from dataclasses import dataclass

# External libs
from cmdkit.config import ConfigurationError
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection
from sqlalchemy.exc import DBAPIError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data.model import Task
from hypershell.data.upgrade import (_sqlstate, _message, _waiting, _waiting_for, _set_lock_timeout,
                                     _lock_postgres, WAIT_UPGRADE, WAIT_TABLES)

# Public interface
__all__ = ['prepare', 'converge', 'gates', 'parse_version', 'normalize_columns',
           'Settings', 'DEFAULTS', 'SETTINGS_KEYS', 'Gates', 'TimescaleError', 'MIN_VERSION', 'POLICY_VERSION', ]

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

# The only table converted; `client` and `source` stay plain tables.
TABLE: Final[str] = 'task'
PARTITION_COLUMN: Final[str] = 'id'

# An interval as configured: a positive whole number of one unit (e.g., '1 week', '12 hours').
INTERVAL_PATTERN: Final[re.Pattern] = re.compile(
    r'^\s*[1-9]\d*\s*(microsecond|millisecond|second|minute|hour|day|week|month)s?\s*$', re.IGNORECASE)

# One orderby item: a column, an optional direction, an optional NULLS placement.
ORDERBY_PATTERN: Final[re.Pattern] = re.compile(
    r'^\s*(\w+)(\s+(asc|desc))?(\s+nulls\s+(first|last))?\s*$', re.IGNORECASE)


class TimescaleError(SchemaUpgradeError):
    """The database cannot be brought to the TimescaleDB shape the provider promises."""


@dataclass(frozen=True)
class Settings:
    """The target shape of the `task` hypertable (`[database.timescale]` overrides each field)."""
    chunk_interval: str = '1 week'
    columnstore: bool = True
    segmentby: str = 'client_id'
    orderby: str = 'id DESC'
    policy: bool = True
    compress_after: str = '4 weeks'

    @classmethod
    def from_config(cls, table: Optional[Mapping[str, Any]],
                    columns: Optional[Tuple[str, ...]] = None) -> Settings:
        """
        Settings from the `[database.timescale]` configuration table, defaults for what it omits.
        Raises `ConfigurationError` naming the key, the value, and the accepted form.
        """
        if table is None:
            return cls()
        if not isinstance(table, Mapping):
            raise ConfigurationError(f'Invalid database.timescale = {table!r}: expected a table of settings '
                                     f'({", ".join(SETTINGS_KEYS)})')
        columns = columns if columns is not None else tuple(Task.__table__.columns.keys())
        values = {}
        for key, value in table.items():
            if key not in SETTINGS_KEYS:
                raise ConfigurationError(f'Unknown setting database.timescale.{key}: expected one of '
                                         f'{", ".join(SETTINGS_KEYS)}')
            values[key] = _check_setting(key, value, columns)
        settings = cls(**values)
        if settings.policy and not settings.columnstore:
            raise ConfigurationError('Invalid database.timescale: policy = true needs columnstore = true '
                                     '(the policy converts chunks to the columnstore); set policy = false')
        return settings


SETTINGS_KEYS: Final[Tuple[str, ...]] = ('chunk_interval', 'columnstore', 'segmentby', 'orderby',
                                         'policy', 'compress_after')


def _check_setting(key: str, value: Any, columns: Tuple[str, ...]) -> Any:
    """The validated value of one `[database.timescale]` setting."""
    def invalid(form: str) -> ConfigurationError:
        return ConfigurationError(f'Invalid database.timescale.{key} = {value!r}: expected {form}')
    if key in ('columnstore', 'policy'):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
            return value.strip().lower() == 'true'
        raise invalid('true or false')
    if not isinstance(value, str):
        raise invalid('a string')
    if key in ('chunk_interval', 'compress_after'):
        if INTERVAL_PATTERN.match(value) is None:
            raise invalid('a positive whole number and a unit, e.g. "1 week" or "12 hours" '
                          '(microseconds through months)')
        return ' '.join(value.split())
    if key == 'segmentby':
        names = [name.strip() for name in value.split(',')] if value.strip() else []
        if any(name not in columns for name in names):
            raise invalid(f'comma-separated task columns ({", ".join(columns)}), or "" for none')
        return ', '.join(names)
    items = value.split(',')
    for item in items:
        match = ORDERBY_PATTERN.match(item)
        if match is None or match.group(1) not in columns:
            raise invalid(f'comma-separated "<task column> [ASC|DESC] [NULLS FIRST|LAST]" items, '
                          f'e.g. "id DESC"; columns: {", ".join(columns)}')
    return ', '.join(' '.join(item.split()) for item in items)


DEFAULTS: Final[Settings] = Settings()


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


def normalize_columns(value: Optional[str]) -> str:
    """
    Canonical form of a segmentby or orderby list, for comparison: single spaces, lower case,
    and an explicit default direction or NULLS placement dropped ('id  DESC NULLS FIRST' and
    'id desc' compare equal; so do 'id ASC NULLS LAST' and 'id').
    """
    items = []
    for item in (value or '').split(','):
        words = item.lower().split()
        if not words:
            continue
        descending = 'desc' in words
        if words[-2:] == ['nulls', 'first' if descending else 'last']:
            words = words[:-2]
        words = [word for word in words if word != 'asc']
        items.append(' '.join(words))
    return ', '.join(items)


def nonempty_message(table: str) -> str:
    """A plain table that holds rows is left alone."""
    return (f'Table {table} holds rows and is a plain table; HyperShell will not convert it to a hypertable '
            f'(that would migrate every row under a long lock). See "Converting an existing table" in the '
            f'database documentation, then rerun hs initdb.')


def other_partition_message(table: str, column: str) -> str:
    """A hypertable partitioned by another column is left alone."""
    return (f'Table {table} is a hypertable partitioned by {column}, not {PARTITION_COLUMN}; HyperShell leaves '
            f'it as it is. Its chunk interval needs manual action.')


def columnstore_off_message(table: str) -> str:
    """Turning the columnstore off is left to the operator."""
    return (f'The columnstore is enabled on {table} but configured off; HyperShell does not disable it. '
            f'Decompress its chunks and run "ALTER TABLE {table} SET (timescaledb.enable_columnstore = false)" '
            f'by hand if that is intended.')


def policy_failed_message(table: str, job_id: int) -> str:
    """The policy exists, but its last run failed."""
    return (f'The columnstore policy on {table} (job {job_id}) failed on its last run; see '
            f'timescaledb_information.job_stats and the server log.')


def converge(engine: Engine, schema: Optional[str], settings: Settings = DEFAULTS) -> List[str]:
    """
    Bring the `task` table in `schema` to the TimescaleDB shape in `settings`, in one transaction
    serialized with schema upgrades. Returns the changes made (empty when already converged).
    Raises `TimescaleError`, with the pass rolled back, when a statement fails.
    """
    try:
        changes, warnings = _waiting(engine, lambda timeout: _converge(engine, schema, settings, timeout))
    except DBAPIError as error:
        message = _message(error)
        if 'must be preloaded' in message.lower():
            raise TimescaleError(preload_message()) from error
        raise TimescaleError(f'Cannot bring table {TABLE} to its TimescaleDB shape: {message}. The tables exist, '
                             f'but this pass changed nothing; fix the cause and rerun hs initdb.') from error
    for change in changes:
        log.info(change)
    for warning in warnings:
        log.warning(warning)
    if not changes:
        log.trace(f'Table {TABLE} has its TimescaleDB shape')
    return changes


def _converge(engine: Engine, schema: Optional[str], settings: Settings,
              timeout: float) -> Tuple[List[str], List[str]]:
    """One attempt at the pass, rolled back unless it completes."""
    with engine.connect() as conn:
        committed = False
        try:
            _set_lock_timeout(conn, timeout)
            with _waiting_for(WAIT_UPGRADE):
                _lock_postgres(conn)
            with _waiting_for(WAIT_TABLES):
                outcome = _Pass(conn, schema, settings).run()
            conn.commit()
            committed = True
            return outcome
        finally:
            if not committed:
                conn.rollback()


class _Pass:
    """Probe-then-change steps of one converge attempt, on one connection inside one transaction."""

    def __init__(self, conn: Connection, schema: Optional[str], settings: Settings) -> None:
        self.conn = conn
        self.settings = settings
        prep = conn.dialect.identifier_preparer
        self.table = f'{prep.quote_schema(schema)}.{prep.quote(TABLE)}' if schema else prep.quote(TABLE)
        self.schema = schema or conn.execute(text('SELECT current_schema()')).scalar()
        self.changes: List[str] = []
        self.warnings: List[str] = []
        extension = conn.execute(text('SELECT e.extversion, n.nspname FROM pg_extension e '
                                      'JOIN pg_namespace n ON n.oid = e.extnamespace '
                                      'WHERE e.extname = \'timescaledb\'')).first()
        if extension is None:
            raise TimescaleError(absent_message(conn.engine.url.database))
        version, namespace = extension
        self.version = parse_version(version)
        self.api = prep.quote_schema(namespace)  # The extension's functions, wherever it was created.
        self.edition = conn.execute(text('SELECT current_setting(\'timescaledb.license\', true)')).scalar()

    def run(self) -> Tuple[List[str], List[str]]:
        """Every step the server supports, in order; returns (changes, warnings)."""
        allowed = gates(self.version, self.edition)
        if not allowed.hypertable:
            raise TimescaleError(allowed.reasons[0])
        if not self._hypertable():
            return self.changes, self.warnings
        if not allowed.columnstore:
            if self.settings.columnstore or self.settings.policy:
                self.warnings.extend(allowed.reasons)
            return self.changes, self.warnings
        self._columnstore()
        if not self.settings.policy:
            self._no_policy()
        elif not allowed.policy:
            self.warnings.extend(allowed.reasons)
        elif self.settings.columnstore:  # A policy needs the columnstore (validation refuses one without).
            self._policy()
        return self.changes, self.warnings

    def _params(self, **params) -> dict:
        return {'table': self.table, 'schema': self.schema, 'name': TABLE, **params}

    def _hypertable(self) -> bool:
        """Convert `task` if it is an empty plain table; fix its chunk interval if it is a hypertable."""
        dimension = self.conn.execute(text(
            'SELECT d.column_name, '
            '       CAST(COALESCE(d.time_interval, d.integer_interval * INTERVAL \'1 microsecond\') AS text) '
            '           AS current, '
            '       COALESCE(d.time_interval, d.integer_interval * INTERVAL \'1 microsecond\') '
            '           = CAST(:chunk AS interval) AS same '
            'FROM timescaledb_information.hypertables h '
            'LEFT JOIN timescaledb_information.dimensions d '
            '       ON d.hypertable_schema = h.hypertable_schema AND d.hypertable_name = h.hypertable_name '
            '      AND d.dimension_number = 1 '
            'WHERE h.hypertable_schema = :schema AND h.hypertable_name = :name'),
            self._params(chunk=self.settings.chunk_interval)).first()
        if dimension is None:
            return self._create()
        column, current, same = dimension
        if column != PARTITION_COLUMN:
            self.warnings.append(other_partition_message(TABLE, column))
            return False
        if not same:
            self.conn.execute(text(f'SELECT {self.api}.set_chunk_time_interval(CAST(:table AS regclass), '
                                   f'CAST(:chunk AS interval))'),
                              self._params(chunk=self.settings.chunk_interval))
            self.changes.append(f'Changed the chunk interval of {TABLE} from {current} to '
                                f'{self.settings.chunk_interval} (applies to new chunks only)')
        return True

    def _create(self) -> bool:
        """Convert an empty plain `task`; leave one holding rows, with a warning."""
        if self.conn.execute(text(f'SELECT EXISTS (SELECT 1 FROM {self.table})')).scalar():
            self.warnings.append(nonempty_message(TABLE))
            return False
        try:
            with self.conn.begin_nested():
                self.conn.execute(text(
                    f'SELECT * FROM {self.api}.create_hypertable(CAST(:table AS regclass), '
                    f'{self.api}.by_range(CAST(:column AS name), CAST(:chunk AS interval)), '
                    f'create_default_indexes => false, if_not_exists => true)'),
                    self._params(column=PARTITION_COLUMN, chunk=self.settings.chunk_interval))
        except DBAPIError as error:
            if _sqlstate(error) == '0A000' and 'is not empty' in _message(error):
                self.warnings.append(nonempty_message(TABLE))  # A row arrived since the check.
                return False
            raise
        self.changes.append(f'Converted table {TABLE} to a hypertable partitioned by {PARTITION_COLUMN} '
                            f'({self.settings.chunk_interval} chunks)')
        return True

    def _columnstore(self) -> None:
        """Enable the columnstore with the configured segmentby and orderby, or report what differs."""
        enabled = self.conn.execute(text(
            'SELECT compression_enabled FROM timescaledb_information.hypertables '
            'WHERE hypertable_schema = :schema AND hypertable_name = :name'), self._params()).scalar()
        if not self.settings.columnstore:
            if enabled:
                self.warnings.append(columnstore_off_message(TABLE))
            return
        segmentby, orderby = self.settings.segmentby, self.settings.orderby
        current = None
        if enabled:
            current = self.conn.execute(text(
                'SELECT segmentby, orderby FROM timescaledb_information.hypertable_columnstore_settings '
                'WHERE hypertable = CAST(:table AS regclass)'), self._params()).first()
            if (current is not None and normalize_columns(current[0]) == normalize_columns(segmentby)
                    and normalize_columns(current[1]) == normalize_columns(orderby)):
                return
        enable = '' if enabled else 'timescaledb.enable_columnstore = true, '
        self.conn.exec_driver_sql(
            f'ALTER TABLE {self.table} SET ({enable}'
            f'timescaledb.segmentby = {_literal(segmentby)}, timescaledb.orderby = {_literal(orderby)})')
        if not enabled:
            self.changes.append(f'Enabled the columnstore on {TABLE} (segmentby {segmentby}, orderby {orderby})')
        else:
            was = f'segmentby {current[0] if current else None}, orderby {current[1] if current else None}'
            self.changes.append(f'Changed the columnstore settings of {TABLE} from {was} to segmentby {segmentby}, '
                                f'orderby {orderby} (applies to future compression only)')

    def _policy_job(self) -> Optional[tuple]:
        """The columnstore policy job on `task`: (job_id, after, same, last_run_status), or None."""
        return self.conn.execute(text(
            'SELECT j.job_id, j.config->>\'compress_after\' AS after, '
            '       CAST(j.config->>\'compress_after\' AS interval) = CAST(:after AS interval) AS same, '
            '       s.last_run_status '
            'FROM timescaledb_information.jobs j '
            'LEFT JOIN timescaledb_information.job_stats s ON s.job_id = j.job_id '
            'WHERE j.proc_name = \'policy_compression\' '
            '  AND j.hypertable_schema = :schema AND j.hypertable_name = :name '
            'ORDER BY j.job_id'), self._params(after=self.settings.compress_after)).first()

    def _policy(self) -> None:
        """Add the columnstore policy, or replace one whose `after` differs; report a failed last run."""
        after = self.settings.compress_after
        job = self._policy_job()
        if job is None:
            self._add_policy()
            self.changes.append(f'Added a columnstore policy on {TABLE} (compress chunks older than {after})')
            return
        job_id, current, same, status = job
        if not same:
            self._remove_policy()
            self._add_policy()
            self.changes.append(f'Replaced the columnstore policy on {TABLE}: compress chunks older than '
                                f'{after} (was {current})')
        elif status == 'Failed':
            self.warnings.append(policy_failed_message(TABLE, job_id))

    def _no_policy(self) -> None:
        """Remove a columnstore policy the settings turn off."""
        if self._policy_job() is not None:
            self._remove_policy()
            self.changes.append(f'Removed the columnstore policy on {TABLE}')

    def _add_policy(self) -> None:
        self.conn.exec_driver_sql(
            f'CALL {self.api}.add_columnstore_policy({_literal(self.table)}::regclass, '
            f'after => {_literal(self.settings.compress_after)}::interval, if_not_exists => true)')

    def _remove_policy(self) -> None:
        self.conn.exec_driver_sql(
            f'CALL {self.api}.remove_columnstore_policy({_literal(self.table)}::regclass, if_exists => true)')


def _literal(value: str) -> str:
    """A SQL string literal (utility statements and CALL take no bound parameters here)."""
    return "'" + str(value).replace("'", "''") + "'"
