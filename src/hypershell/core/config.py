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
import argparse
import shutil
import tomlkit
import logging
import socket
import functools
import platform
import subprocess
from datetime import datetime

# External libs
from cmdkit.config import Namespace, Configuration, Environ, ConfigurationError
from cmdkit.app import exit_status
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.platform import path, home, default_path
from hypershell.core.exceptions import write_traceback, display_critical
from hypershell.core.sys import PATH_SEP

# Public interface
__all__ = [
    'config', 'update', 'default', 'ConfigurationError', 'Namespace', 'blame',
    'resolve_auth', 'check_auth_forms', 'AUTH_FORMS', 'AUTH_SECTIONS', 'AUTH_ENV',
    'KEY_OPTION_STRINGS', 'SERVER_KEY_SOURCES', 'RefuseKeyOption', 'refuse_key_option',
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
        'distribution': 'spread',  # Relay sites only: either 'spread' or 'fill'
        'sites': PARAM_UNSET,      # Relay sites only: preferred site order for 'fill' (list or comma string)
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
        'poll': 0.1,              # Max polling interval of the relay-local scheduler in seconds
        'retention': 300,         # Seconds a returned result is kept in the relay database
        'zone': PARAM_UNSET,      # Zone(s) served: a name, a list, or one [relay.zone.<name>] table each
        'site': PARAM_UNSET,      # Site name the relay gives upstream (default: the relay's host name)
        'size': 1,                # Number of site clients the relay keeps launched (at least 1)
        'min': PARAM_UNSET,       # Fewest clients per zone a topology assigns (default: 0)
        'max': PARAM_UNSET,       # Most clients per zone a topology assigns (default: size)
        'init': PARAM_UNSET,      # Clients per zone a topology assigns at start (default: min)
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
            'deadline': 30,         # Seconds an upstream call may take before the link counts as down
            'backoff': 60,          # Longest wait in seconds between attempts to reach upstream again
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


AUTH_FORMS: Final[tuple[str, ...]] = ('auth', 'auth_env', 'auth_eval')
"""The forms an authkey may take: literal, from an environment variable, or from a command."""

AUTH_SECTIONS: Final[tuple[tuple[str, ...], ...]] = (('server', ), ('relay', 'upstream'))
"""The sections whose `auth` key is an authkey (a section no layer has is simply absent)."""


def _layer_label(name: str) -> str:
    """Name a configuration layer for a message: its file path, `env`, or `<name>`."""
    if name in ('system', 'user', 'local'):
        return path.get(name).config
    return name if name == 'env' else f'<{name}>'


def _layer_section(layer: dict, section: tuple[str, ...]) -> dict:
    """The table at `section` within one configuration `layer`, or empty if the layer has none."""
    for name in section:
        layer = layer.get(name, {}) if isinstance(layer, dict) else {}
    return layer if isinstance(layer, dict) else {}


def check_auth_forms(base: Configuration, sections: tuple[tuple[str, ...], ...] = AUTH_SECTIONS) -> None:
    """Refuse a configuration layer that gives an authkey in more than one form.

    Every layer is checked, not only the one that decides the key, since the merged view hides
    a lower layer's duplicate. This reads no environment variable and runs no command.
    """
    for section in sections:
        key = '.'.join((*section, 'auth'))
        for name, layer in base.namespaces.items():
            table = _layer_section(layer, section)
            given = [form for form in AUTH_FORMS if form in table]
            if len(given) > 1:
                raise ConfigurationError(f'More than one form of {key} ({", ".join(given)}) '
                                         f'in one configuration layer ({_layer_label(name)})')


def resolve_auth(*section: str, base: Configuration = None) -> str:
    """The authkey of `section` (e.g., 'server'), from whichever form the deciding layer gives.

    The highest configuration layer that sets `auth`, `auth_env` or `auth_eval` decides.
    An unset variable, a failed command, or two forms in one layer raise `ConfigurationError`
    naming the key; no message carries the key itself, and nothing stands in for it.
    """
    base = config if base is None else base
    key = '.'.join((*section, 'auth'))
    check_auth_forms(base, (section, ))
    table = base
    try:
        for name in section:
            table = table[name]
    except (KeyError, TypeError):
        raise ConfigurationError(f'"{key}" not found in configuration') from None
    given = [form for form in AUTH_FORMS if isinstance(table, dict) and form in table]
    if not given:
        raise ConfigurationError(f'"{key}" not found in configuration')
    form, = given
    label = blame(base, *section, form)
    try:
        value = getattr(table, 'auth')
    except subprocess.CalledProcessError as error:
        raise ConfigurationError(f'{key}_eval command exited with status {error.returncode} ({label})') from None
    except Exception as error:
        # Only the error's type is named: its text can carry part of the command's output.
        raise ConfigurationError(f'{key}{form[4:]} could not be resolved: {type(error).__name__} ({label})') from None
    if value is None and form == 'auth_env':
        raise ConfigurationError(f'{key}_env names {table[form]}, which is not set ({label})')
    if value is None:
        raise ConfigurationError(f'"{key}" not found in configuration')
    return str(value)


AUTH_ENV: Final[str] = 'HYPERSHELL_SERVER_AUTH'
"""Environment variable that gives `server.auth`; `hs cluster` launchers hand the key over in it."""

KEY_OPTION_STRINGS: Final[tuple[str, ...]] = ('-k', '--auth')
"""Option strings registered on the commands only to be refused (see :func:`refuse_key_option`)."""

KEY_OPTION_REFUSAL: Final[str] = ('{option} is not accepted: '
                                   'a key on a command line is visible to every user on the host.')
"""First sentence of the refusal, naming only the option string the parser matched."""

SERVER_KEY_SOURCES: Final[str] = (f'Give the key in the {AUTH_ENV} environment variable '
                                  f'or as server.auth in a configuration file.')
"""Where the commands that read `server.auth` take their key from instead."""


class RefuseKeyOption(argparse.Action):
    """Refuse a key option the moment the parser meets it, saying where the key goes instead.

    The message is built from the matched option string and fixed text only. The value is never
    read, so no message, and no log record of it, can hold the key. Since the parser stops at
    the refusal, nothing else on the command line is reported either.
    """

    def __init__(self: RefuseKeyOption, option_strings: List[str], dest: str, advice: str = SERVER_KEY_SOURCES,
                 **options) -> None:
        super().__init__(option_strings, dest, **options)
        self.advice = advice

    def __call__(self: RefuseKeyOption, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
                 values: Optional[str], option_string: Optional[str] = None) -> None:
        option = option_string or self.option_strings[0]
        raise ArgumentError(' '.join([KEY_OPTION_REFUSAL.format(option=option), self.advice.format(option=option)]))


def refuse_key_option(interface: Interface, advice: str = SERVER_KEY_SOURCES) -> None:
    """Register :const:`KEY_OPTION_STRINGS` on `interface`, refused with `advice` (see :class:`RefuseKeyOption`).

    The option takes an optional value, so that a bare option, or one followed by another option,
    meets the same refusal; and it leaves nothing on the parsed namespace. An `{option}` field in
    `advice` is the option string given.
    """
    interface.add_argument(*KEY_OPTION_STRINGS, action=RefuseKeyOption, advice=advice, nargs='?',
                           default=argparse.SUPPRESS, dest='key_option')


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
    check_auth_forms(config)
except ConfigurationError as error:
    display_critical(error, module=__name__)
    sys.exit(exit_status.bad_config)
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

DEFAULT_RELAY_PORT: Final[int] = config.relay.port
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
