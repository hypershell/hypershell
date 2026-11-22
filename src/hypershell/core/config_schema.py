# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The configuration keys HyperShell documents, and how `HYPERSHELL_*` environment variables reach them.

A variable names a key by its dotted path with each `.` written as `_`, in any letter case after
the `HYPERSHELL_` prefix. Since a key's own name may hold an underscore, a name is matched against
the declared keys and tables, not split at every underscore: an exact key first (or a key plus
`_ENV`/`_EVAL`), then the longest table the name continues, by that table's kind. The value is read
by the key's kind: text as written; whole numbers, numbers and booleans converted or refused;
durations and sizes left for the command that reads them.

This module imports only the standard library, so `hypershell.core.config` may import it at any
point of its own import.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Iterable, List, Mapping, Optional, Tuple

# Standard libs
import re
import math
import tomllib
from enum import Enum

# Public interface
__all__ = ['Kind', 'TableKind', 'KEYS', 'TABLES', 'ZONE_KEYS', 'TIMESCALE_KEYS', 'PROCESS_VARIABLES',
           'PROSE_KEYS', 'SCALAR_TABLES', 'EMPTY_UNSET', 'VARIANTS', 'PREFIX', 'VariableError', 'read_value',
           'read_untyped', 'match_name', 'match_path', 'read_environment', 'variable_name']


PREFIX: Final[str] = 'HYPERSHELL_'
"""Prefix of every variable that sets a configuration key (matched as written)."""


class Kind(Enum):
    """How the text of a variable is read for a key."""
    TEXT = 'text'                  # The text as written.
    BOOL = 'bool'                  # A boolean word.
    INT = 'int'                    # A whole number.
    NUMBER = 'number'              # A whole or decimal number, finite.
    INT_OR_NONE = 'int_or_none'    # A whole number, or exactly `<none>`.
    DURATION = 'duration'          # A number, or text the reading command parses as a duration.
    SIZE = 'size'                  # A number, or text the reading command parses as a size.
    LOGGING_FILE = 'logging_file'  # A word that enables the default file, or a path.
    UNTYPED = 'untyped'            # A TOML integer, float or boolean when the text is one, else the text.


class TableKind(Enum):
    """How the rest of a variable's name is read past a table's path."""
    CLOSED = 'closed'  # The rest is one key of that table, held as written if it names no declared key.
    OPEN = 'open'      # The rest is one entry name the user chose, lower-cased.
    ZONE = 'zone'      # The rest is a zone name and one of the zone keys.
    EXPORT = 'export'  # The rest is a task variable name, kept as written.


T, B, I, N = Kind.TEXT, Kind.BOOL, Kind.INT, Kind.NUMBER
D, S = Kind.DURATION, Kind.SIZE

KEYS: Final[Dict[str, Kind]] = {
    # [logging]
    'logging.level': T, 'logging.datefmt': T, 'logging.format': T, 'logging.style': T, 'logging.color': B,
    'logging.file': Kind.LOGGING_FILE,
    'logging.file.path': T, 'logging.file.level': T, 'logging.file.style': T, 'logging.file.rotate': T,
    'logging.file.compress': T, 'logging.file.keep': I, 'logging.file.format': T,
    # [database]
    'database': T,  # A SQLite file path in place of the table.
    'database.url': T, 'database.provider': T, 'database.file': T, 'database.database': T,
    'database.schema': T, 'database.host': T, 'database.port': I, 'database.user': T,
    'database.password': T, 'database.echo': B,
    'database.timescale.chunk_interval': T, 'database.timescale.columnstore': B,
    'database.timescale.segmentby': T, 'database.timescale.orderby': T, 'database.timescale.policy': B,
    'database.timescale.compress_after': T,
    # [server]
    'server.bind': T, 'server.host': T, 'server.advertise': T, 'server.port': I, 'server.auth': T,
    'server.queuesize': I, 'server.bundlesize': I, 'server.bundlemax': I, 'server.attempts': I,
    'server.eager': B, 'server.poll': D, 'server.evict': D,
    'server.tls.enabled': B, 'server.tls.cert': T, 'server.tls.key': T, 'server.tls.cafile': T,
    'server.tls.fingerprint': T, 'server.tls.insecure': B, 'server.tls.min_version': T,
    'server.tls.ciphers': T, 'server.tls.servername': T, 'server.tls.handshake': D,
    # [client]
    'client.bundlesize': I, 'client.bundlewait': D, 'client.heartrate': D, 'client.timeout': D,
    'client.cores': I, 'client.memory': S, 'client.ratelimit': N,
    # [submit]
    'submit.bundlesize': I, 'submit.bundlewait': D,
    # [task]
    'task.cwd': T, 'task.cores': I, 'task.memory': S, 'task.timeout': D, 'task.signalwait': D,
    # [ssh]
    'ssh.config': T, 'ssh.args': T,
    'ssh.nodelist': T,  # One host list in place of the table of groups.
    # [autoscale]
    'autoscale.policy': T, 'autoscale.factor': N, 'autoscale.period': D, 'autoscale.launcher': T,
    'autoscale.distribution': T, 'autoscale.sites': T, 'autoscale.failed_launches': I,
    'autoscale.size.init': I, 'autoscale.size.min': I, 'autoscale.size.max': I,
    # [relay]
    'relay.database': T, 'relay.bind': T, 'relay.advertise': T, 'relay.port': I, 'relay.bundlesize': I,
    'relay.bundlewait': D, 'relay.poll': D, 'relay.retention': D, 'relay.zone': T, 'relay.site': T,
    'relay.size': I, 'relay.min': Kind.INT_OR_NONE, 'relay.max': Kind.INT_OR_NONE, 'relay.init': Kind.INT_OR_NONE,
    'relay.launcher': T, 'relay.launcher_args': T, 'relay.num_threads': I, 'relay.template': T,
    'relay.timeout': D,
    'relay.upstream.host': T, 'relay.upstream.port': I, 'relay.upstream.auth': T,
    'relay.upstream.bundlesize': I, 'relay.upstream.heartrate': D, 'relay.upstream.deadline': D,
    'relay.upstream.backoff': D, 'relay.upstream.evict_hint': D,
    'relay.upstream.tls.enabled': B, 'relay.upstream.tls.cert': T, 'relay.upstream.tls.cafile': T,
    'relay.upstream.tls.fingerprint': T,
    # [console]
    'console.theme': T,
}
"""Every documented key by dotted path, and the kind its environment variable is read as."""

TABLES: Final[Dict[str, TableKind]] = {
    '': TableKind.CLOSED,
    'logging': TableKind.CLOSED, 'logging.file': TableKind.CLOSED,
    'database': TableKind.OPEN,  # Provider arguments, beneath the declared keys and tables.
    'database.connect_args': TableKind.OPEN, 'database.pragmas': TableKind.OPEN,
    'database.timescale': TableKind.CLOSED,
    'server': TableKind.CLOSED, 'server.tls': TableKind.CLOSED,
    'client': TableKind.CLOSED, 'submit': TableKind.CLOSED, 'task': TableKind.CLOSED,
    'ssh': TableKind.CLOSED, 'ssh.nodelist': TableKind.OPEN,
    'autoscale': TableKind.CLOSED, 'autoscale.size': TableKind.CLOSED,
    'relay': TableKind.CLOSED, 'relay.zone': TableKind.ZONE,
    'relay.upstream': TableKind.CLOSED, 'relay.upstream.tls': TableKind.CLOSED,
    'console': TableKind.CLOSED,
    'export': TableKind.EXPORT,
}
"""Every table by dotted path (the root is `''`), and how a name continuing past it is read."""

ZONE_KEYS: Final[Dict[str, Kind]] = {
    'launcher': T, 'launcher_args': T, 'size': I, 'min': I, 'max': I, 'init': I,
}
"""Keys a `[relay.zone.<name>]` table may hold, and their kinds."""

TIMESCALE_KEYS: Final[Tuple[str, ...]] = tuple(path.rsplit('.', 1)[1] for path in KEYS
                                               if path.startswith('database.timescale.'))
"""Keys of `[database.timescale]`."""

PROCESS_VARIABLES: Final[Tuple[str, ...]] = ('SITE', 'CONFIG_FILE', 'PYTHONPATH', 'ZONE')
"""Names after the prefix that the process reads itself; they are not configuration keys."""

PROSE_KEYS: Final[Tuple[str, ...]] = ('logging.file.format', *(f'database.timescale.{name}' for name in TIMESCALE_KEYS))
"""Keys the parameter reference names in the text of their table's entry rather than as entries of their own."""

SCALAR_TABLES: Final[Tuple[str, ...]] = ('logging.file', 'ssh.nodelist', 'database')
"""Tables that may be given one value in place of the table (each is a key and a table here)."""

EMPTY_UNSET: Final[Tuple[str, ...]] = (
    'logging.file', 'logging.file.path', 'logging.file.compress', 'logging.file.format',
    'database', 'database.url', 'database.file', 'database.database', 'database.schema', 'database.host',
    'database.user', 'database.password',
    'server.bind', 'server.advertise', 'server.tls.cert', 'server.tls.key', 'server.tls.cafile',
    'server.tls.fingerprint', 'server.tls.ciphers', 'server.tls.servername',
    'task.cwd', 'ssh.args', 'ssh.nodelist', 'autoscale.sites',
    'relay.database', 'relay.bind', 'relay.advertise', 'relay.zone', 'relay.site',
    'relay.upstream.tls.cert', 'relay.upstream.tls.cafile', 'relay.upstream.tls.fingerprint',
)
"""Optional text keys, the bind addresses, the database host and the task directory: an empty variable
for one is not given.

The key's default applies, as it does when no variable is set (an empty bind, host or task directory
is the default one). Every other text key holds an empty variable as written, as it holds a file's `""`.
"""

VARIANTS: Final[Tuple[str, ...]] = ('_env', '_eval')
"""Suffixes by which a key is given as the name of a variable, or a command, that gives its value."""

del T, B, I, N, D, S


class VariableError(ValueError):
    """A variable's name or value the environment layer cannot hold; the message names the variable."""


BOOL_WORDS: Final[Dict[str, bool]] = {
    'true': True, 'yes': True, 'on': True, '1': True,
    'false': False, 'no': False, 'off': False, '0': False,
}
"""Words a boolean key takes, in any letter case."""

INT_FORM: Final[re.Pattern] = re.compile(r'[+-]?[0-9]+(?:_[0-9]+)*', re.ASCII)
NUMBER_FORM: Final[re.Pattern] = re.compile(
    r'[+-]?(?:[0-9]+(?:_[0-9]+)*(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', re.ASCII)
TOML_TOKEN: Final[re.Pattern] = re.compile(r'[^\s#="\'\[\]{},]+')

FORMS: Final[Dict[Kind, str]] = {
    Kind.BOOL: 'a boolean (true/false, yes/no, on/off, 1/0)',
    Kind.INT: 'a whole number',
    Kind.NUMBER: 'a number',
    Kind.INT_OR_NONE: 'a whole number or <none>',
    Kind.LOGGING_FILE: 'true, enabled, or a path',
}
"""What each kind that can refuse a value expects, for its message."""


def read_number(text: str) -> Optional[int | float]:
    """The whole number or finite number `text` spells (surrounding space allowed), or None."""
    text = text.strip()
    if INT_FORM.fullmatch(text):
        return int(text)
    if NUMBER_FORM.fullmatch(text):
        value = float(text)
        return value if math.isfinite(value) else None
    return None


def read_untyped(text: str) -> Any:
    """A TOML integer, float or boolean when `text` is exactly one, else `text` unchanged."""
    if not TOML_TOKEN.fullmatch(text):
        return text
    try:
        value = tomllib.loads(f'value = {text}')['value']
    except tomllib.TOMLDecodeError:
        return text
    return value if isinstance(value, (bool, int, float)) else text


def read_value(kind: Kind, text: str) -> Any:
    """Read `text` as `kind`; raise ValueError naming the form expected when it does not convert."""
    if kind is Kind.TEXT:
        return text
    if kind is Kind.UNTYPED:
        return read_untyped(text)
    if kind in (Kind.DURATION, Kind.SIZE):
        number = read_number(text)
        return text if number is None else number
    word = text.strip().lower()
    if kind is Kind.BOOL and word in BOOL_WORDS:
        return BOOL_WORDS[word]
    if kind is Kind.LOGGING_FILE:
        if word == 'enabled' or BOOL_WORDS.get(word) is True:
            return True
        if word not in BOOL_WORDS and text != '':
            return text
    if kind in (Kind.INT, Kind.INT_OR_NONE) and INT_FORM.fullmatch(text.strip()):
        return int(text.strip())
    if kind is Kind.INT_OR_NONE and text == '<none>':
        return text
    if kind is Kind.NUMBER and (number := read_number(text)) is not None:
        return number
    raise ValueError(FORMS[kind])


def variable_name(path: str) -> str:
    """The variable name the guide's rule gives a dotted `path` (upper-cased, `.` as `_`)."""
    return PREFIX + path.replace('.', '_').upper()


def _by_name() -> Dict[str, str]:
    """Declared keys by the part of their variable name after the prefix."""
    return {variable_name(path)[len(PREFIX):]: path for path in KEYS}


KEY_NAMES: Final[Dict[str, str]] = _by_name()
"""Declared keys by the upper-cased part of their variable name after the prefix."""

TABLE_NAMES: Final[List[Tuple[str, str]]] = sorted(
    ((variable_name(path)[len(PREFIX):] + '_' if path else '', path) for path in TABLES),
    key=lambda pair: len(pair[0]), reverse=True)
"""Each table's name prefix (after the variable prefix, ending in `_`), longest first."""

ZONE_SUFFIXES: Final[List[Tuple[str, str]]] = sorted(
    (('_' + name.upper(), name) for name in ZONE_KEYS), key=lambda pair: len(pair[0]), reverse=True)
"""Each zone key's name suffix, longest first."""


Match = Tuple[Tuple[str, ...], Kind]


def match_name(name: str) -> Optional[Match]:
    """The key path a variable `name` sets and the kind its value is read as; None if it sets no key.

    A process variable, and a name without the prefix, set no key. A `relay.zone` name that ends in
    no zone key raises :class:`VariableError`.
    """
    if not name.startswith(PREFIX) or name == PREFIX:
        return None
    given = name[len(PREFIX):]
    rest = given.upper()
    if rest in PROCESS_VARIABLES:
        return None
    if rest in KEY_NAMES:
        path = KEY_NAMES[rest]
        return tuple(path.split('.')), KEYS[path]
    for variant in VARIANTS:
        if rest.endswith(variant.upper()) and rest[:-len(variant)] in KEY_NAMES:
            *table, key = KEY_NAMES[rest[:-len(variant)]].split('.')
            return (*table, key + variant), Kind.TEXT
    for prefix, table in TABLE_NAMES:
        if not rest.startswith(prefix) or len(rest) == len(prefix):
            continue
        path = tuple(table.split('.')) if table else ()
        entry = given[len(prefix):]
        kind = TABLES[table]
        if kind is TableKind.EXPORT:
            return (*path, entry), Kind.TEXT
        if kind is TableKind.ZONE:
            for suffix, key in ZONE_SUFFIXES:
                if entry.upper().endswith(suffix) and len(entry) > len(suffix):
                    return (*path, entry[:-len(suffix)].lower(), key), ZONE_KEYS[key]
            raise VariableError(f'{name} names no zone key: a relay.zone variable ends in one of '
                                f'{", ".join(key.upper() for key in ZONE_KEYS)} after the zone name')
        if kind is TableKind.OPEN and table == 'ssh.nodelist':
            return (*path, entry.lower()), Kind.TEXT
        return (*path, entry.lower()), Kind.UNTYPED
    return None  # Unreachable: the root table matches every other name.


def match_path(path: str) -> Kind:
    """The kind a value written at the dotted `path` is read as, the path matched as written.

    A documented key is read by its own kind, and its `_env` and `_eval` forms as text. One entry past
    an `[export]` table or `ssh.nodelist` is text, and a key of a `relay.zone.<name>` table is read by
    its zone key's kind. Every other path (an entry of an open table, an undocumented key, a path
    below a key or in another letter case) has no type of its own.
    """
    if path in KEYS:
        return KEYS[path]
    for variant in VARIANTS:
        if path.endswith(variant) and path[:-len(variant)] in KEYS:
            return Kind.TEXT
    *table, entry = path.split('.')
    parent = '.'.join(table)
    if TABLES.get(parent) is TableKind.EXPORT or parent == 'ssh.nodelist':
        return Kind.TEXT
    if len(table) > 1 and TABLES.get('.'.join(table[:-1])) is TableKind.ZONE:
        return ZONE_KEYS.get(entry, Kind.UNTYPED)
    return Kind.UNTYPED


def _set(tree: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    """Set `value` at `path` in the nested dictionary `tree`."""
    for name in path[:-1]:
        tree = tree.setdefault(name, {})
    tree[path[-1]] = value


def _below(path: Tuple[str, ...], paths: Iterable[Tuple[str, ...]]) -> bool:
    """True if any of `paths` lies beneath `path`."""
    return any(len(other) > len(path) and other[:len(path)] == path for other in paths)


def read_environment(environ: Mapping[str, str]) -> Tuple[Dict[str, Any], Dict[Tuple[str, ...], str]]:
    """The environment layer that `environ` gives, and the variable that set each key path.

    An empty variable for a key of :data:`EMPTY_UNSET` sets nothing. Raises :class:`VariableError`,
    naming the variables, for a value that does not convert to its key's kind, a `relay.zone` name
    with no zone key, `HYPERSHELL_RELAY_ZONE` together with a zone-table variable, and two variables
    that set one key.
    """
    values: Dict[Tuple[str, ...], Any] = {}
    variables: Dict[Tuple[str, ...], str] = {}
    for name in sorted(environ):
        if (match := match_name(name)) is None:
            continue
        path, kind = match
        if environ[name] == '' and '.'.join(path) in EMPTY_UNSET:
            continue
        if path in variables:
            raise VariableError(f'{variables[path]} and {name} both set {".".join(path)}')
        try:
            values[path] = read_value(kind, environ[name])
        except ValueError as error:
            raise VariableError(f'{".".join(path)}: expected {error}, '
                                f'given {environ[name]!r} (from: {name})') from None
        variables[path] = name

    zone = ('relay', 'zone')
    if zone in values and _below(zone, values):
        tables = sorted(variables[path] for path in values if len(path) > len(zone) and path[:2] == zone)
        raise VariableError(f'{variables[zone]} cannot be set together with zone tables '
                            f'({", ".join(tables)}): name the zones in one form')

    # A logging.file word or path set beside its table's entries becomes part of that table.
    file = ('logging', 'file')
    if file in values and _below(file, values):
        given, variable = values.pop(file), variables.pop(file)
        if given is not True:
            if (*file, 'path') in values:
                raise VariableError(f'{variable} and {variables[(*file, "path")]} both set logging.file.path')
            values[(*file, 'path')], variables[(*file, 'path')] = given, variable

    # Any other value set beside entries beneath it gives way to them.
    for path in [path for path in values if _below(path, values)]:
        del values[path], variables[path]

    tree: Dict[str, Any] = {}
    for path, value in values.items():
        _set(tree, path, value)
    return tree, variables
