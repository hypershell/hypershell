# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration for HyperShell."""


# Type annotations
from __future__ import annotations
from typing import TypeVar, Union, List, Optional, Protocol, Final, Iterator, Dict

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
__all__ = [
    'config', 'update', 'default', 'ConfigurationError', 'Namespace', 'blame',
    'load', 'reload', 'reload_local', 'load_file', 'reload_file', 'load_env', 'reload_env', 'load_task_env',
    'PARAM_UNSET', 'DEFAULT_DATABASE_NAME', 'DEFAULT_DATABASE_FILE',
    'DEFAULT_LOGGING_LEVEL', 'DEFAULT_LOGGING_STYLE', 'LOGGING_STYLES', 'ACTIVE_CONFIG_VARS', 'SSH_GROUPS',
    'AUTH_ALLOWED_CHARS', 'AUTH_MINIMUM_LENGTH', 'find_available_ports',
    'RELAY_PROCESS', 'RELAY_MARKER', 'RELAY_PRAGMAS', 'DEFAULT_RELAY_PORT', 'default_relay_database',
    'relay_database_section',
]


# Partial logging (not yet configured - initialized afterward)
log = logging.getLogger(__name__)


DEFAULT_LOGGING_LEVEL: Final[str] = 'info'
DEFAULT_LOGGING_STYLE: Final[str] = 'default'

LOGGING_STYLES: Dict[str, Dict[str, str]] = {
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
    },
    'short': {
        'format': '%(ansi_level)s[%(elapsed).3f]%(ansi_reset)s %(message)s',
    }
}


# For TOML compatibility we cannot use an actual None
PARAM_UNSET: Final[str] = '<none>'


# Default secure key is not secure!
DEFAULT_AUTH: Final[str] = '<not-secure>'

# Basic security checks for user-provided auth keys.
# The character set admits hex, URL-safe tokens, and standard Base64 (``+``, ``/``, ``=``)
# so that common key generators (e.g. ``openssl rand -base64 48``) are accepted as-is.
AUTH_ALLOWED_CHARS: re.Pattern = re.compile(r'^[A-Za-z0-9._+/=-]+$')
AUTH_MINIMUM_LENGTH: Final[int] = 16


# Environment variables and configuration files are automatically depth-first merged with defaults
# A default value of 0 may mean "None" or "automatically chosen"
default = Namespace({

    'database': {
        'provider': 'sqlite',
    },

    'logging': {
        'color': True,
        'datefmt': '%Y-%m-%d %H:%M:%S',
        'level': DEFAULT_LOGGING_LEVEL,
        'style': DEFAULT_LOGGING_STYLE,
        **LOGGING_STYLES.get(DEFAULT_LOGGING_STYLE),

        # We allow logging.file = {<path> | 'enabled' | True} for default configuration.
        # A <path> stands in for logging.file.path with otherwise default values for everything else.
        # At least one parameter must be set to enable logging to file.
        'file': {
            'path': PARAM_UNSET,
            'level': 'trace',
            'style': 'system',
            'rotate': 'never',       # Size-like ('512MB') or cron-like ('@midnight', '0 1 * * 0')
            'compress': PARAM_UNSET, # Either 'gzip', 'bzip', 'lzma', or 'zstd' (requires 'zstandard' package)
            'keep': 0,               # Number of uncompressed files to leave on disk (0 = none)
        },
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
        'auth': DEFAULT_AUTH,  # Initializes to fixed default value (DO NOT USE THIS)
        'queuesize': 1,        # Only allow a single bundle (scheduler must wait)
        'bundlesize': 1,
        'bundlemax': 1000,     # Most tasks in one reply to a client's request (never below bundlesize)
        'attempts': 1,
        'eager': False,        # Prefer failed tasks to new tasks
        'poll': 30,            # Max polling interval in seconds between database queries if no tasks
        'evict': 600,          # Seconds to wait before evicting client

        # Transport-layer TLS for queue connections. Enabled by default; '<auto>' values
        # for cert/key/cafile are resolved against the site lib dir on first server start
        # (see hypershell.core.tls.ensure_default_materials).
        'tls': {
            'enabled': True,            # Use --no-tls or other mechanisms to disable (not recommended)
            'cert': '<auto>',           # Path to server certificate or '<auto>'
            'key': '<auto>',            # Path to server private key or '<auto>'
            'cafile': '<auto>',         # Trust anchor for clients; '<auto>' mirrors the server cert
            'fingerprint': PARAM_UNSET, # Pin peer fingerprint (e.g. 'SHA256:AB:CD:...')
            'insecure': False,          # Disable peer verification (logs a warning)
            'min_version': 'TLSv1.2',
            'ciphers': PARAM_UNSET,     # OpenSSL cipher string
            'servername': PARAM_UNSET,  # Override SNI / hostname check on client side
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
            'max': 1,       # Typically this would be the only thing to modify
        },
    },

    'console': {
        'theme': 'monokai',
    },

    # The relay is a per-site service: a client upstream and a local server and fleet at its site.
    # Its own database is always a private SQLite file (see `relay_database_section`).
    'relay': {
        'database': PARAM_UNSET,  # Path to the relay database (default: <lib>/relay/<host>.db)
        'bind': '0.0.0.0',        # Bind address of the relay-local server (site clients connect here)
        'port': 50_002,           # Port of the relay-local server (distinct from the server default)
        'bundlesize': 1,          # Bundle size of the relay-local server
        'bundlewait': 5,          # Seconds the relay waits before returning results regardless of size
        'poll': 1,                # Max polling interval of the relay-local scheduler in seconds
        'retention': 300,         # Seconds a returned result is kept in the relay database
        'zone': PARAM_UNSET,      # Zone served by the relay (default zone if unset)
        'size': 1,                # Number of site clients the relay keeps launched
        'launcher': '',           # Command prefix to launch each site client (empty means 'hs client ...')
        'launcher_args': '',      # Extra arguments for the launcher
        'num_threads': 1,         # Executor threads per site client
        'template': '{}',         # Command template for site clients
        'timeout': 0,             # Site client idle timeout in seconds (never if 0)
        'upstream': {
            'host': 'localhost',    # Upstream server hostname
            'port': 50_001,         # Upstream server port
            'auth': DEFAULT_AUTH,   # Upstream key (use auth_env or auth_eval to keep it out of files)
            'bundlesize': 1_000,    # Bundle size the relay advertises upstream
            'heartrate': 10,        # Seconds between upstream heartbeats
            'evict_hint': 600,      # Upstream eviction period the relay assumes during an outage
            'tls': {
                'enabled': True,            # Use --no-tls to disable on the upstream hop (not recommended)
                'cert': '<auto>',           # Upstream server certificate or '<auto>'
                'cafile': '<auto>',         # Trust anchor for the upstream server; '<auto>' mirrors the cert
                'fingerprint': PARAM_UNSET, # Pin the upstream server fingerprint (e.g. 'SHA256:AB:CD:...')
            },
        },
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
        'local': {} if not local else load_file(local),
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


RELAY_MARKER: Final[str] = '_HYPERSHELL_RELAY'
"""Private environment variable that marks a process started to run `hs relay`.

It sits outside the `HYPERSHELL_` prefix, so it never reaches the configuration, and it is
removed as soon as it is read, so no child process inherits it.
"""

RELAY_PRAGMAS: Final[Dict[str, str]] = {'synchronous': 'FULL'}
"""SQLite pragmas for the relay database: a committed insert survives a power loss."""

DEFAULT_RELAY_PORT: Final[int] = default.relay.port
"""Default port of the relay-local server."""


def default_relay_database() -> str:
    """Default relay database path, scoped by host since home directories are shared between hosts."""
    return os.path.join(default_path.lib, 'relay', f'{socket.gethostname().split(".", 1)[0]}.db')


def relay_database_section(base: Namespace) -> Namespace:
    """The database section a relay process uses in place of the configured one.

    Always SQLite, whatever provider, URL or schema the configured section names, so a relay never
    opens (or needs a driver for) the configured primary database. The file is `relay.database`,
    or a host-scoped default under the site lib directory.
    """
    path_ = base.get('relay', {}).get('database', None)
    if not path_ or path_ == PARAM_UNSET:
        path_ = default_relay_database()
    return Namespace({'provider': 'sqlite', 'file': os.path.abspath(os.path.expanduser(str(path_))),
                      'pragmas': dict(RELAY_PRAGMAS)})


def _relay_requested(argv: List[str] = None) -> bool:
    """True if this process runs `hs relay`: the private marker is set, or the command line names it."""
    if os.environ.pop(RELAY_MARKER, None):
        return True
    argv = sys.argv if argv is None else argv
    program = os.path.splitext(os.path.basename(argv[0]))[0] if argv else ''
    return program in ('hs', 'hyper-shell') and len(argv) > 1 and argv[1] == 'relay'


RELAY_PROCESS: Final[bool] = _relay_requested()
"""Set in a process that runs `hs relay`; its database section is the relay database.

The swap happens here, before `hypershell.data` builds its engine from this configuration, so
every module that reads the database section sees the relay database. One relay per process.
"""

if RELAY_PROCESS:
    config['database'] = relay_database_section(config)


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
                         end: int = default.server.port + 1_000,
                         bind: str = '0.0.0.0') -> Iterator[int]:
    """Yield available ports by testing each in turn."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((bind, port))
                yield port
            except socket.error:
                pass


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
