# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database interface, models, and methods."""


# Type annotations
from __future__ import annotations
from typing import Final

# Standard libs
import sys
import functools

# External libs
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface
from cmdkit.config import ConfigurationError
from sqlalchemy import inspect
from sqlalchemy.orm import close_all_sessions
from sqlalchemy.exc import OperationalError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import config
from hypershell.core.exceptions import handle_exception, DatabaseUninitialized, get_shared_exception_mapping
from hypershell.data.core import engine, in_memory, schema
from hypershell.data.model import Entity, Task

# Public interface
__all__ = ['InitDBApp', 'initdb', 'truncatedb', 'checkdb', 'ensuredb', 'DATABASE_ENABLED', ]

# Initialize logger
log = Logger.with_name(__name__)


DATABASE_ENABLED: Final[bool] = not in_memory
"""Set if database has been configured."""


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


INITDB_PROGRAM = 'hs initdb'
INITDB_USAGE = f"""\
Usage:
  {INITDB_PROGRAM} [-h] [--truncate [--yes]]

  Initialize database (not needed for SQLite).
  Use --truncate to zero out the task metadata.\
"""

INITDB_HELP = f"""\
{INITDB_USAGE}

Options:
  -t, --truncate       Truncate database (task metadata will be lost).
  -y, --yes            Auto-confirm truncation (default will prompt).
  -h, --help           Show this message and exit.\
"""


class InitDBApp(Application):
    """Initialize database (not needed for SQLite)."""

    interface = Interface(INITDB_PROGRAM, INITDB_USAGE, INITDB_HELP)

    ALLOW_NOARGS = True

    truncate: bool = False
    interface.add_argument('-t', '--truncate', action='store_true')

    auto_confirm: bool = False
    interface.add_argument('-y', '--yes', action='store_true', dest='auto_confirm')

    exceptions = {
        OperationalError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__),
    }

    def run(self: InitDBApp) -> None:
        """Business logic for `initdb`."""
        if not DATABASE_ENABLED:
            raise ConfigurationError('No database configured')
        elif not self.truncate:
            initdb()
        elif self.auto_confirm:
            truncatedb()
        elif not sys.stdout.isatty():
            raise RuntimeError('Non-interactive prompt cannot confirm --truncate (see --yes).')
        else:
            if config.database.provider == 'sqlite':
                site = config.database.file
            else:
                site = config.database.get('host', 'localhost')
            print(f'Connected to: {config.database.provider} ({site})')
            response = input(f'Truncate database ({Task.count()} tasks)? [Y]es/no: ').strip()
            if response.lower() in ['', 'y', 'yes']:
                truncatedb()
            elif response.lower() in ['n', 'no']:
                print('Stopping')
            else:
                raise RuntimeError(f'Stopping (invalid response: "{response}")')
