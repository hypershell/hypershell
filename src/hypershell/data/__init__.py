# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database interface, models, and methods."""


# Type annotations
from __future__ import annotations
from typing import Tuple, Final

# Standard libs
import os
import sys
import functools

# External libs
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import ConfigurationError
from sqlalchemy import inspect, text, type_coerce
from sqlalchemy.orm import close_all_sessions, sessionmaker, Session as SessionType
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import create_engine

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import config
from hypershell.core.exceptions import handle_exception, DatabaseUninitialized, get_shared_exception_mapping
from hypershell.core.pretty_print import format_bytes
from hypershell.data.core import Session, engine, in_memory, schema
from hypershell.data.model import Entity, Task, JSON

# Public interface
__all__ = [
    'initdb', 'truncatedb', 'checkdb', 'ensuredb', 'vacuumdb', 'rotatedb',
    'DATABASE_ENABLED', 'DATABASE_PROVIDER',
    'InitDBApp',
]

# Initialize logger
log = Logger.with_name(__name__)


DATABASE_ENABLED: Final[bool] = not in_memory
"""Set if database has been configured."""

DATABASE_PROVIDER: Final[str] = config.database.provider
"""Either sqlite or postgres."""

DATABASE_HOST: Final[str] = config.database.get('host', 'localhost')
"""Database server hostname (default: localhost)."""

DATABASE_SITE: Final[str] = DATABASE_HOST if DATABASE_PROVIDER != 'sqlite' else config.database.get('file', ':memory:')
"""Database server hostname (Postgres) or file path (SQLite)."""

DATABASE_INFO: Final[str] = f'{config.database.provider} ({DATABASE_SITE})'
"""Concise connection info for database."""


def initdb() -> None:
    """Initialize database tables."""
    Entity.metadata.create_all(engine)


def truncatedb() -> None:
    """Truncate database tables."""
    # NOTE: We still might hang here if other sessions exist outside this app instance
    close_all_sessions()
    log.trace('Dropping all tables')
    Entity.metadata.drop_all(engine)
    log.trace('Creating all tables')
    Entity.metadata.create_all(engine)
    log.warning(f'Truncated database')


def checkdb() -> None:
    """Ensure database connection and tables exist."""
    if not inspect(engine).has_table('task', schema=schema):
        raise DatabaseUninitialized('Use \'initdb\' to initialize the database')


def ensuredb(auto_init: bool = False) -> None:
    """
    Ensure database configuration before applying any operations.
    If SQLite and `auto_init` we run :meth:`initdb`, else :meth:`checkdb`.
    """
    db = config.database.get('file', None) or config.database.get('database', None)
    if config.database.provider == 'sqlite' and db in ('', ':memory:', None):
        raise ConfigurationError('Missing database configuration')
    if config.database.provider == 'sqlite' or auto_init is True:
        initdb()
    else:
        checkdb()


def vacuumdb(path: str = None) -> None:
    """Apply database vacuum (optionally into backup location for SQLite)."""
    if not path:
        log.info(f'Vacuuming database {DATABASE_SITE}')
        if DATABASE_PROVIDER == 'sqlite':
            size_before = os.path.getsize(DATABASE_SITE)
            Session.execute(text('VACUUM'))
            size_after = os.path.getsize(DATABASE_SITE)
            log.info(f'Cleaned {format_bytes(size_before - size_after)} from {DATABASE_SITE}')
        else:
            # VACUUM cannot run inside a transaction block for PostgreSQL
            autocommit_engine = engine.execution_options(isolation_level='AUTOCOMMIT')
            with SessionType(autocommit_engine) as session:
                session.execute(text('VACUUM'))
    else:
        if DATABASE_PROVIDER != 'sqlite':
            raise RuntimeError(f'{DATABASE_PROVIDER} cannot backup database into file ({path})')
        log.info(f'Backing up {DATABASE_SITE} into {path}')
        Session.execute(text(f'VACUUM INTO :path'), params={'path': path})


def rotatedb() -> None:
    """Split main database into next partition (SQLite only)."""

    if DATABASE_PROVIDER != 'sqlite':
        raise RuntimeError(f'Cannot rotate database with {DATABASE_PROVIDER} provider')

    part_id, part_path = next_rotate_path()
    log.info(f'Rotating database {DATABASE_SITE} into {part_path}')

    # Mark completed tasks as having part:N
    # We cannot simply drop completed tasks naively as more tasks may be updated in the
    # time between vacuuming to the new partition and the drop step
    (
        Session.query(Task)
            .filter(Task.exit_status.isnot(None))
            .update({Task.tag: text('json_set(task.tag, :k, :v)').params({'k': '$.part', 'v': part_id})})
    )
    Session.commit()

    # Clone entire database to new file
    # Previously marked tasks as part:N can then be dropped from main database
    log.debug(f'Vacuuming into {part_path}')
    Session.execute(text(f'VACUUM INTO :path'), params={'path': part_path})
    count_deleted = Session.query(Task).filter(Task.tag['part'] == type_coerce(part_id, JSON)).delete()
    Session.commit()
    log.debug(f'Dropped {count_deleted} completed tasks from main ({DATABASE_SITE})')

    # Now vacuum main database to reclaim space and optimize
    log.debug(f'Vacuuming main database ({DATABASE_SITE})')
    Session.execute(text('VACUUM'))

    # Now we can drop anything in the new partition not belonging to part:N
    with sessionmaker(bind=create_engine(f'sqlite:///{part_path}'))() as external_session:
        count_deleted = external_session.query(Task).filter(Task.tag['part'] != type_coerce(part_id, JSON)).delete()
        external_session.commit()
        external_session.execute(text('VACUUM'))
        log.debug(f'Dropped {count_deleted} remaining tasks from partition ({part_path})')


def next_rotate_path() -> Tuple[int, str]:
    """
    Choose next file path (increment suffix by one).
    We enumerate files in the containing directory and pick the next integer.
    Suffixes are dropped (e.g., main.db -> main.1, main.2, ...).
    """
    dirname = os.path.dirname(config.database.file)
    filename, _ = os.path.splitext(os.path.basename(config.database.file))
    n = 1
    while os.path.isfile(rotated_filename := os.path.join(dirname, f'{filename}.{n}')):
        n += 1
    return n, rotated_filename


INITDB_PROGRAM = 'hs initdb'
INITDB_USAGE = f"""\
Usage:
  {INITDB_PROGRAM} [-h] [--truncate | --vacuum | --rotate | --backup PATH] [--yes]

  Initialize database.\
"""

INITDB_HELP = f"""\
{INITDB_USAGE}
  For SQLite this happens automatically.
  See also --initdb for the `hs cluster` command.
  
  The available special actions are mutually exclusive.
  The --rotate operation migrates completed tasks to the next database partition,
  and applies a special purpose `part:N` tag to the new partition and remaining tasks.

Actions:
      --vacuum             Vacuum an existing database.
      --backup     PATH    Vacuum into backup file (SQLite only).
      --rotate             Rotate completed tasks to new database (SQLite only).
  -t, --truncate           Truncate database (task metadata will be lost).

Options:
  -y, --yes                Auto-confirm action (default will prompt).
  -h, --help               Show this message and exit.\
"""


class InitDBApp(Application):
    """Initialize database (not needed for SQLite)."""

    interface = Interface(INITDB_PROGRAM, INITDB_USAGE, INITDB_HELP)
    ALLOW_NOARGS = True

    truncate_mode: bool = False
    vacuum_mode: bool = False
    rotate_mode: bool = False
    backup_path: str | None = None
    action_interface = interface.add_mutually_exclusive_group()
    action_interface.add_argument('-t', '--truncate', action='store_true', dest='truncate_mode')
    action_interface.add_argument('-v', '--vacuum', action='store_true', dest='vacuum_mode')
    action_interface.add_argument('-r', '--rotate', action='store_true', dest='rotate_mode')
    action_interface.add_argument('-b', '--backup', dest='backup_path')

    auto_confirm: bool = False
    interface.add_argument('-y', '--yes', action='store_true', dest='auto_confirm')

    exceptions = {
        OperationalError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__),
    }

    def run(self: InitDBApp) -> None:
        """Run database operations."""
        self.check_arguments()
        if self.vacuum_mode:
            if self.confirm_action(f'Vacuum {DATABASE_INFO}'):
                vacuumdb()
        elif self.backup_path:
            if self.confirm_action(f'Backup {DATABASE_INFO} into \'{self.backup_path}\''):
                vacuumdb(self.backup_path)
        elif self.rotate_mode:
            part_id, part_path = next_rotate_path()
            if self.confirm_action(f'Rotate {DATABASE_INFO} to \'{part_path}\''):
                rotatedb()
        elif self.truncate_mode:
            if self.confirm_action(f'Truncate {DATABASE_INFO}: {Task.count()} tasks'):
                truncatedb()
        else:
            if config.database.provider == 'sqlite':
                log.info('SQLite database initialized automatically')
                return
            if self.confirm_action(f'Initialize {DATABASE_INFO}'):
                initdb()

    def check_arguments(self: InitDBApp) -> None:
        """Check configuration and given command-line arguments."""
        if not DATABASE_ENABLED:
            raise ConfigurationError('No database configured')
        if config.database.provider != 'sqlite' and self.backup_path:
            raise ArgumentError('Can only backup SQLite')
        if config.database.provider != 'sqlite' and self.rotate_mode:
            raise ArgumentError('Can only rotate SQLite')
        if self.backup_path and os.path.exists(self.backup_path):
            raise RuntimeError(f'Backup path already exists ({self.backup_path})')
        if not sys.stdout.isatty():
            raise RuntimeError('Non-interactive prompt cannot confirm (see --yes).')

    def confirm_action(self: InitDBApp, message: str) -> bool:
        """True if okay to proceed, else False."""
        if self.auto_confirm:
            return True
        response = input(f'{message}? [Y]es/no: ').strip()
        if response.lower() in ['', 'y', 'yes']:
            print('Ok')
            return True
        if response.lower() in ['n', 'no']:
            print('Stopping')
            return False
        else:
            raise RuntimeError(f'Stopping (invalid response: "{response}")')