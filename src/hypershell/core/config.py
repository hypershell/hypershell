# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration for HyperShell."""


# Type annotations
from __future__ import annotations
from typing import TypeVar, Union, List, Optional, Protocol, Final, Iterator

# Standard libs
import os
import re
import sys
import shutil
import tomlkit
import logging
import socket
import functools
import platform
from datetime import datetime

# External libs
from cmdkit.config import Namespace, Configuration, Environ, ConfigurationError
from cmdkit.app import exit_status

# Internal libs
from hypershell.core.platform import path, home, default_path
from hypershell.core.exceptions import write_traceback
from hypershell.core.sys import PATH_SEP

# Public interface
__all__ = ['config', 'update', 'default', 'ConfigurationError', 'Namespace', 'blame',
           'load', 'reload', 'reload_local', 'load_file', 'reload_file', 'load_env', 'reload_env', 'load_task_env',
           'DEFAULT_DATABASE_NAME', 'DEFAULT_DATABASE_FILE',
           'DEFAULT_LOGGING_STYLE', 'LOGGING_STYLES', 'ACTIVE_CONFIG_VARS', 'SSH_GROUPS',
           'find_available_ports']

# Partial logging (not yet configured - initialized afterward)
log = logging.getLogger(__name__)


DEFAULT_LOGGING_STYLE = 'default'
LOGGING_STYLES = {
    'default': {
        'format': ('%(ansi_bold)s%(ansi_level)s%(levelname)8s%(ansi_reset)s %(ansi_faint)s[%(name)s]%(ansi_reset)s'
                   ' %(message)s'),
    },
    'system': {
        'format': '%(asctime)s.%(msecs)03d %(hostname)s %(levelname)8s [%(app_id)s] [%(name)s] %(message)s',
    },
    'detailed': {
        'format': ('%(ansi_faint)s%(asctime)s.%(msecs)03d %(hostname)s %(ansi_reset)s'
                   '%(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s '
                   '%(ansi_faint)s[%(name)s]%(ansi_reset)s %(message)s'),
    },
    'detailed-compact': {
        'format': ('%(ansi_faint)s%(elapsed_hms)s [%(hostname_short)s] %(ansi_reset)s'
                   '%(ansi_level)s%(ansi_bold)s%(levelname)8s%(ansi_reset)s '
                   '%(ansi_faint)s[%(relative_name)s]%(ansi_reset)s %(message)s'),
    }
}


# Environment variables and configuration files are automatically depth-first merged with defaults
# A default value of 0 may mean "None" or "automatically chosen"
default = Namespace({

    'database': {
        'provider': 'sqlite',
    },

    'logging': {
        'color': True,
        'level': 'info',
        'datefmt': '%Y-%m-%d %H:%M:%S',
        'style': DEFAULT_LOGGING_STYLE,
        **LOGGING_STYLES.get(DEFAULT_LOGGING_STYLE),
    },

    'task': {
        'cwd': os.getcwd(),
        'cores': 0,          # Default cores allocated per task (default is unconstrained)
        'memory': 0,         # Default memory in bytes allocated per task (default is unconstrained)
        'timeout': 0,        # Default task-level walltime limit (default is unconstrained)
        'signalwait': 10,    # Wait time in seconds between signal escalation (INT, TERM, KILL)
    },

    'submit': {
        'bundlesize': 1,    # Size of task bundle to accumulate before committing
        'bundlewait': 5,    # Seconds to wait before committing regardless of size
    },

    'server': {
        'bind': 'localhost',   # Used by server
        'host': 'localhost',   # Used by clients
        'port': 50_001,
        'auth': '__HYPERSHELL__BAD__AUTHKEY__',  # DO NOT USE THIS
        'queuesize': 1,        # Only allow a single bundle (scheduler must wait)
        'bundlesize': 1,
        'bundlewait': 5,       # Seconds
        'attempts': 1,
        'eager': False,        # Prefer failed tasks to new tasks
        'poll': 5,             # Polling interval in seconds between database queries if no tasks
        'evict': 600,          # Seconds to wait before evicting client

        # Transport-layer TLS for queue connections. Disabled by default; when enabled,
        # '<auto>' values for cert/key/cafile are resolved against the site lib dir on
        # first server start (see hypershell.core.tls.ensure_default_materials).
        'tls': {
            'enabled': False,
            'cert': '<auto>',        # Path to server certificate or '<auto>'
            'key': '<auto>',         # Path to server private key or '<auto>'
            'cafile': '<auto>',      # Trust anchor for clients; '<auto>' mirrors the server cert
            'fingerprint': '<none>', # Pin peer fingerprint (e.g. 'SHA256:AB:CD:...')
            'insecure': False,       # Disable peer verification (logs a warning)
            'min_version': 'TLSv1.2',
            'ciphers': '<none>',     # OpenSSL cipher string
            'servername': '<none>',  # Override SNI / hostname check on client side
        },
    },

    'client': {
        'bundlesize': 1,    # Size of task bundle to accumulate before returning
        'bundlewait': 5,    # Seconds to wait before returning regardless of size
        'heartrate': 10,    # Seconds to wait between heartbeats
        'timeout': 0,       # Client-level timeout in seconds if no tasks available (never if 0/none)
        'cores': 0,         # Client-level resource constraint (all available resources if 0/none)
        'memory': 0,        # Client-level resource constraint (all available resources if 0/none)
        'ratelimit': 0,     # Maximum allowed tasks per second (no limit by default)
    },

    'ssh': {
        'config': os.path.join(home, '.ssh', 'config'),
        'nodelist': {}  # Populated by user configuration
    },

    'autoscale': {
        'policy': 'fixed',  # Either 'fixed' or 'dynamic'
        'factor': 1,
        'period': 60,       # Seconds to wait between checks
        'launcher': '',     # Empty means just 'hs client ...'
        'size': {
            'init': 1,
            'min': 0,
            'max': 2,
        },
    },

    'console': {
        'theme': 'monokai',
    },

    # NOTE: defining HYPERSHELL_EXPORT_XXX defines XXX within task env
    'export': {}
})


def reload_file(filepath: str) -> Namespace:
    """Force reloading configuration file."""
    if not os.path.exists(filepath):
        return Namespace({})
    try:
        return Namespace.from_toml(filepath)
    except Exception as err:
        raise ConfigurationError(f'(from file: {filepath}) {err.__class__.__name__}: {err}')


@functools.lru_cache(maxsize=None)
def load_file(filepath: str) -> Namespace:
    """Load configuration file."""
    return reload_file(filepath)


def reload_env() -> Environ:
    """Force reloading environment variables and expanding hierarchy as namespace."""
    return Environ(prefix='HYPERSHELL').expand()


@functools.lru_cache(maxsize=None)
def load_env() -> Environ:
    """Load environment variables and expand hierarchy as namespace."""
    return reload_env()


def partial_load(system: Optional[str] = path.system.config,
                 user: Optional[str] = path.user.config,
                 local: Optional[str] = path.local.config,
                 **preload: Namespace) -> Configuration:
    """Load configuration from files and merge environment variables."""
    return Configuration(**{
        'default': default, **preload,
        'system': {} if not system else load_file(system),
        'user': {} if not user else load_file(user),
        'local': {} if not user else load_file(local),
        'env': load_env(),
    })


def partial_reload(system: Optional[str] = path.system.config,
                   user: Optional[str] = path.user.config,
                   local: Optional[str] = path.local.config,
                   **preload: Namespace) -> Configuration:
    """Force reload configuration from files and merge environment variables."""
    return Configuration(**{
        'default': default, **preload,
        'system': {} if not system else reload_file(system),
        'user': {} if not user else reload_file(user),
        'local': {} if not local else reload_file(local),
        'env': reload_env(),
    })


def blame(base: Configuration, *varpath: str) -> Optional[str]:
    """Construct filename or variable assignment string based on precedent of `varpath`."""
    source = base.which(*varpath)
    if not source:
        return None
    if source in ('system', 'user', 'local'):
        return f'from: {path.get(source).config}'
    elif source == 'env':
        return 'from: HYPERSHELL_' + '_'.join([node.upper() for node in varpath])
    else:
        return f'from: <{source}>'


def get_logging_style(base: Configuration) -> str:
    """Get and check valid on `config.logging.style`."""
    style = base.logging.style
    label = blame(base, 'logging', 'style')
    if not isinstance(style, str):
        raise ConfigurationError(f'Expected string for `logging.style` ({label})')
    style = style.lower()
    if style in LOGGING_STYLES:
        return style
    else:
        raise ConfigurationError(f'Unrecognized `logging.style` \'{style}\' ({label})')


DEFAULT_DATABASE_NAME: Final[str] = 'Main.db' if platform.system() in ['Windows', 'Darwin'] else 'main.db'
"""Either Main.db (Windows/macOS) or main.db on Linux/POSIX."""

DEFAULT_DATABASE_FILE: Final[str] = os.path.join(default_path.lib, DEFAULT_DATABASE_NAME)
"""Default local SQLite database file path if none is provided."""


def build_preloads(base: Configuration) -> Namespace:
    """Build 'preload' namespace from base configuration."""
    return Namespace({'logging': LOGGING_STYLES.get(get_logging_style(base)),
                      'database': {'file': DEFAULT_DATABASE_FILE},})


class LoaderImpl(Protocol):
    """Loader interface for building configuration."""
    def __call__(self: LoaderImpl,
                 system: Optional[str] = path.system.config,
                 user: Optional[str] = path.user.config,
                 local: Optional[str] = path.local.config,
                 **preload: Namespace) -> Configuration: ...


def build_configuration(loader: LoaderImpl) -> Configuration:
    """Construct full configuration."""
    return loader(preload=build_preloads(base=loader()))


def load() -> Configuration:
    """Load configuration from files and merge environment variables."""
    return build_configuration(loader=partial_load)


def reload() -> Configuration:
    """Load configuration from files and merge environment variables."""
    return build_configuration(loader=partial_reload)


def reload_local(filepath: Optional[str] = None) -> Configuration:
    """Load configuration but only include one file."""
    loader = functools.partial(partial_reload, system=None, user=None, local=filepath)
    return build_configuration(loader=loader)


try:
    if (local_config := os.getenv('HYPERSHELL_CONFIG_FILE', None)) is not None:
        path.local.config = local_config  # Modified as to not lie to the user
        config = reload_local(local_config)
    else:
        config = load()
except Exception as error:
    write_traceback(error, module=__name__)
    sys.exit(exit_status.bad_config)


ACTIVE_CONFIG_VARS: Final[List[str]] = [
    re.sub(r'_(?!(env|eval))', r'.', name.lower())
    for name in Namespace(config).to_env().flatten()
]


SSH_GROUPS = []
try:
    if isinstance(config.ssh.nodelist, dict):
        SSH_GROUPS = list(config.ssh.nodelist)
except KeyError:
    pass


def find_available_ports(start: int = default.server.port,
                         end: int = default.server.port + 1_000) -> Iterator[int]:
    """Yield available ports by testing each in turn."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            try:
                sock.bind(("0.0.0.0", port))
                yield port
            except socket.error:
                pass
    else:
        raise RuntimeError(f'Could not find available port in range {start}-{end}')


DEFAULT_CONFIG_HEADERS: Final[str] = f"""\
# File automatically created on {datetime.now()}
# Settings here are merged automatically with defaults and environment variables
"""


def update(scope: str, partial: dict) -> None:
    """Extend the current configuration and commit it to disk."""
    config_path = path[scope].config
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    if os.path.exists(config_path):
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        config_backup_path = os.path.join(os.path.dirname(config_path), f'.config.{timestamp}.toml')
        shutil.copy(config_path, config_backup_path)
        shutil.copystat(config_path, config_backup_path)
        log.debug(f'Created backup file ({config_backup_path})')
    else:
        with open(config_path, mode='w') as stream:
            stream.write(DEFAULT_CONFIG_HEADERS)
    with open(config_path, mode='r') as stream:
        new_config = tomlkit.parse(stream.read())
    _inplace_update(new_config, partial)
    with open(config_path, mode='w') as stream:
        tomlkit.dump(new_config, stream)


# Re-implemented from `cmdkit.config.Namespace` (but works with `tomlkit`)
def _inplace_update(original: dict, partial: dict) -> dict:
    """
    Like normal `dict.update` but if values in both are mappable, descend
    a level deeper (recursive) and apply updates there instead.
    """
    for key, value in partial.items():
        if isinstance(value, dict) and isinstance(original.get(key), dict):
            original[key] = _inplace_update(original.get(key, {}), value)
        else:
            original[key] = value
    return original


T = TypeVar('T')


def __collapse_if_list_impl(value: Union[T, List[str]]) -> Union[T, str]:
    """If `value` is a list, collapse it to a path-like list (with ':' or ';')."""
    return value if not isinstance(value, list) else PATH_SEP.join([str(member) for member in value])


def __collapse_lists(ns: Namespace) -> Namespace:
    """Collapse member values if they are a list, recursively."""
    result = Namespace()
    for key, value in ns.items():
        if isinstance(value, Namespace):
            result[key] = __collapse_lists(value)
        else:
            result[key] = __collapse_if_list_impl(value)
    return result


@functools.cache
def load_task_env() -> Environ:
    """Export environment defined in `config.export`."""
    return __collapse_lists(config.export).to_env().flatten()
