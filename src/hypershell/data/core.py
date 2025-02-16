# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Core interface for database engine and session manager."""


# Type annotations
from __future__ import annotations
from typing import Any, Type

# Standard libs
import sys
import logging
from urllib.parse import urlencode

# External libs
from cmdkit.app import exit_status
from cmdkit.config import Namespace, ConfigurationError
from sqlalchemy.engine import create_engine, Engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import ArgumentError

# Internal libs
from hypershell.core.config import config
from hypershell.core.logging import handler
from hypershell.core.exceptions import display_critical, write_traceback

# Public interface
__all__ = ['DatabaseURL', 'engine', 'Session', 'config', 'in_memory', 'schema', ]


class DatabaseURL(dict):
    """
    Dataclass-like representation for database URL.
    Standard arguments apply. Extra arguments are encoded as URL parameters.

    Example:
        >>> url = DatabaseURL(provider='postgresql', database='mine')
        >>> url.encode()
        'postgresql://localhost/mine'
    """

    def __init__(self: DatabaseURL, **fields) -> None:
        """Initialize fields."""
        try:
            super().__init__(provider=fields.pop('provider'),
                             database=fields.pop('database', None),
                             file=fields.pop('file', None),
                             user=fields.pop('user', None),
                             password=fields.pop('password', None),
                             host=fields.pop('host', None),
                             port=fields.pop('port', None),
                             parameters=fields)
        except KeyError as _error:
            raise AttributeError('Missing \'provider\'') from _error
        self._validate()

    def __getattr__(self: DatabaseURL, field: str) -> Any:
        return self.get(field)

    def __repr__(self: DatabaseURL) -> str:
        """Interactive representation."""
        masked = self.__class__(**self)
        masked['password'] = None if self.password is None else '****'
        value = '<DatabaseURL('
        value += ', '.join([field + '=' + repr(masked.get(field))
                            for field in ('provider', 'database', 'file', 'user', 'password', 'host', 'port')
                            if masked.get(field) is not None])
        if self.parameters:
            value += ', ' + ', '.join([field + '=' + repr(value)
                                       for field, value in self.parameters.items()])
        return value + ')>'

    def _validate(self: DatabaseURL) -> None:
        """Validate provided arguments."""
        if self.provider == 'sqlite':
            self._validate_for_sqlite()
        else:
            self._validate_database()
            self._validate_user_and_password()

    def _validate_user_and_password(self: DatabaseURL) -> None:
        if self.user is not None and self.password is None:
            raise AttributeError('Must provide \'password\' if \'user\' provided')
        if self.user is None and self.password is not None:
            raise AttributeError('Must provide \'user\' if \'password\' provided')

    def _validate_for_sqlite(self: DatabaseURL) -> None:
        if self.file is not None and self.database is not None:
            raise AttributeError('Cannot provide both \'file\' and \'database\' for SQLite')
        for field in ('user', 'password', 'host', 'port'):
            if self.get(field) is not None:
                raise AttributeError(f'Cannot provide \'{field}\' for SQLite')

    def _validate_database(self: DatabaseURL) -> None:
        if self.file:
            raise AttributeError('Cannot provide \'file\' if not SQLite')
        if not self.database:
            raise AttributeError('Must provide \'database\' if not SQLite')

    def encode(self: DatabaseURL) -> str:
        """Construct URL string with encoded parameters."""
        return ''.join([
            f'{self.provider}://',
            self._format_user_and_password(),
            self._format_host_and_port(),
            self._format_database_or_file(),
            self._format_parameters(),
        ])

    def _format_parameters(self: DatabaseURL) -> str:
        if self.parameters:
            return '?' + urlencode(self.parameters)
        else:
            return ''

    def _format_database_or_file(self: DatabaseURL) -> str:
        if self.database:
            return f'/{self.database}'
        elif self.file:
            return f'/{self.file}'
        else:
            return ''

    def _format_host_and_port(self: DatabaseURL) -> str:
        if self.host and self.port:
            return f'{self.host}:{self.port}'
        elif self.host and not self.port:
            return f'{self.host}'
        elif self.port and not self.host:
            return f'localhost:{self.port}'
        else:
            if self.user or self.password:
                return 'localhost'
            else:
                return ''

    def _format_user_and_password(self: DatabaseURL) -> str:
        if self.user and self.password:
            return f'{self.user}:{self.password}@'
        else:
            return ''

    def __str__(self: DatabaseURL) -> str:
        return self.encode()

    @staticmethod
    def _strip_endings(value: str, *endings: str):
        """Removing instances of each possible `endings` from `value`."""
        r = str(value)
        for ending in endings:
            pos = len(ending)
            if r[-pos:] == ending:
                r = r[:-pos]
        return r

    @classmethod
    def from_namespace(cls: Type[DatabaseURL], ns: Namespace) -> DatabaseURL:
        fields = {}
        for key in ns.keys():
            key_ = cls._strip_endings(key, '_env', '_eval')
            fields[key_] = getattr(ns, key_)
        return cls(**fields)


# Allowed database providers
# Mapping translates from name to library/implementation name
# NOTE: mysql/mariadb and other providers not yet working
providers = {
    'sqlite': 'sqlite',
    'postgres': 'postgresql',
    'postgresql': 'postgresql',
}


config = Namespace(config.database.copy())
schema = config.pop('schema', None)
engine_echo = config.pop('echo', False)
connect_args = config.pop('connect_args', {})


# additional parameters for engine creation
engine_config = {}


# sqlite specific configuration
in_memory = False
if config.provider == 'sqlite':
    in_memory = (config.get('file', None) or config.get('database', None)) in ('', ':memory:', None)
    if in_memory:
        engine_config['poolclass'] = StaticPool
    if 'check_same_thread' not in connect_args:
        connect_args['check_same_thread'] = False


def get_url() -> DatabaseURL:
    """Wraps parsing within function."""
    if config.provider not in providers:
        raise ConfigurationError(f'Unsupported database \'{config.provider}\'')
    try:
        params = Namespace({**config, 'provider': providers[config.provider]})
        return DatabaseURL.from_namespace(params)
    except AttributeError as err:
        raise ConfigurationError(str(err)) from err


def get_engine() -> Engine:
    """Wraps engine creation."""
    try:
        if engine_echo:
            logging.getLogger('sqlalchemy.engine').addHandler(handler)
            logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
        return create_engine(get_url().encode(), connect_args=connect_args, **engine_config)
    except ArgumentError as err:
        raise ConfigurationError(f'DatabaseURL: {err}') from err


try:
    engine = get_engine()
    factory = sessionmaker(bind=engine)
    Session = scoped_session(factory)
except ModuleNotFoundError as error:
    if 'psycopg2' in error.args[0]:
        display_critical(f'Missing optional dependency "psycopg2" needed for PostgreSQL', module=__name__)
        sys.exit(exit_status.runtime_error)
    else:
        write_traceback(error, module=__name__)
        sys.exit(exit_status.bad_config)
except Exception as error:
    write_traceback(error, module=__name__)
    sys.exit(exit_status.bad_config)
