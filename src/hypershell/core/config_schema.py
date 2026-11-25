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

Every numeric setting also has one range here (:data:`SETTINGS`): each numeric key, each zone key,
and the options that set no key. One parser per kind reads a value against its entry
(:func:`parse_setting`) and refuses it with one error (:class:`SettingError`), in one form wherever
the value came from. A few entries also have an advisory limit, past which a value is taken with one
warning (:func:`advise`).

This module imports only the standard library, so `hypershell.core.config` may import it at any
point of its own import.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Iterable, List, Mapping, Optional, Set, Tuple

# Standard libs
import re
import math
import logging
import tomllib
from enum import Enum
from decimal import Decimal
from fractions import Fraction
from dataclasses import dataclass

# Public interface
__all__ = ['Kind', 'TableKind', 'KEYS', 'TABLES', 'ZONE_KEYS', 'TIMESCALE_KEYS', 'PROCESS_VARIABLES',
           'PROSE_KEYS', 'SCALAR_TABLES', 'EMPTY_UNSET', 'VARIANTS', 'PREFIX', 'VariableError', 'read_value',
           'read_untyped', 'match_name', 'match_path', 'read_environment', 'variable_name',
           'Range', 'SETTINGS', 'SettingError', 'parse_setting', 'shown', 'advise', 'flush_advice',
           'ADVISED', 'PENDING', 'setting_for', 'VariableRefusal', 'NUMERIC', 'TIME_FORMS', 'MEMORY_FORMS', 'DELAY_RANDOM_FORM', 'match_seconds', 'match_bytes']


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
    'relay.launcher': T, 'relay.launcher_args': T, 'relay.remote_exe': T, 'relay.num_threads': I, 'relay.template': T,
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
    'relay.database', 'relay.bind', 'relay.advertise', 'relay.zone', 'relay.site', 'relay.remote_exe',
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


class VariableRefusal(VariableError):
    """A variable's value that is not a number of its key's kind, refused in the one form of a setting's refusal."""


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
        setting = setting_for('.'.join(path))
        try:
            if setting is not None and kind in (Kind.INT, Kind.NUMBER, Kind.INT_OR_NONE):
                values[path] = SETTINGS[setting].read(environ[name], '.'.join(path), source=name)
            else:
                values[path] = read_value(kind, environ[name])
        except SettingError as error:
            raise VariableRefusal(str(error)) from None
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


# The grammar of durations and sizes, shared by every option, key and task tag that takes one.

# A size is a number, then optionally K, M, G or T, alone or followed by B or iB, or a bare B.
# Every unit is a power of 1024, in any case.
MEMORY_PATTERN: Final[re.Pattern] = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT](?:I?B)?|B)?',
                                               re.IGNORECASE)
MEMORY_SCALES: Final[Dict[str, int]] = {
    'B': 1,
    'KB': 1024,
    'MB': 1024 * 1024,
    'GB': 1024 * 1024 * 1024,
    'TB': 1024 * 1024 * 1024 * 1024,
}
MEMORY_FORMS: Final[str] = ('a number of bytes, optionally with a unit K, M, G or T (powers of 1024), '
                            'alone or followed by B or iB, in any case (e.g., 2048, 512M, 16G, 16GB, 1.5GiB)')

# A duration is a bare number of seconds, unit components in the order d, h, m, s (each at most
# once, whole or fractional, in any case), or one of Slurm's --time forms with a colon or a dash.
TIME_NUMBER: Final[str] = r'\d+(?:\.\d+)?'
TIME_SECONDS_PATTERN: Final[re.Pattern] = re.compile(rf'(?P<s>{TIME_NUMBER})')
TIME_UNITS_PATTERN: Final[re.Pattern] = re.compile(
    rf'(?:(?P<d>{TIME_NUMBER})d)?(?:(?P<h>{TIME_NUMBER})h)?(?:(?P<m>{TIME_NUMBER})m)?(?:(?P<s>{TIME_NUMBER})s)?',
    re.IGNORECASE)
TIME_CLOCK_PATTERN: Final[re.Pattern] = re.compile(r'(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)')
TIME_DAYS_PATTERN: Final[re.Pattern] = re.compile(r'(?P<d>\d+)-(?P<h>\d+)(?::(?P<m>\d+)(?::(?P<s>\d+))?)?')
TIME_SCALES: Final[Dict[str, int]] = {
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 60 * 60 * 24,
}
TIME_FORMS: Final[str] = ('a number of seconds, units d, h, m and s in that order, whole or fractional '
                          '(e.g., 90, 45s, 90m, 1h30m, 1.5h, 1d12h), or a Slurm time MM:SS, HH:MM:SS, D-HH, '
                          'D-HH:MM or D-HH:MM:SS (e.g., 90:00, 1:30:00, 1-12)')

# The forms are tried in this order; the first to match the whole text gives the duration.
TIME_PATTERNS: Final[Tuple[re.Pattern, ...]] = (TIME_SECONDS_PATTERN, TIME_UNITS_PATTERN,
                                                TIME_CLOCK_PATTERN, TIME_DAYS_PATTERN)

# A start-up delay takes the duration forms, and one leading '-' for a random wait up to that duration.
DELAY_RANDOM_FORM: Final[str] = 'a leading - waits a random time up to that duration (e.g., --delay-start=-10m)'


def match_seconds(text: str) -> Optional[Fraction]:
    """The exact number of seconds in a duration written in full, or None if it is not one.

    Raises ValueError for a number too wide to convert (thousands of digits).
    """
    for pattern in TIME_PATTERNS:
        if text and (match := pattern.fullmatch(text)):
            return sum(Fraction(num) * TIME_SCALES[unit] for unit, num in match.groupdict().items() if num is not None)
    return None


def match_bytes(text: str) -> Optional[int]:
    """The exact number of bytes (rounded down) in a size written in full, or None if it is not one.

    Raises ValueError for a number too wide to convert (thousands of digits).
    """
    if match := MEMORY_PATTERN.fullmatch(text):
        unit = (match.group('unit') or 'B').upper()
        return int(Fraction(match.group('num')) * MEMORY_SCALES[unit if unit == 'B' else f'{unit[0]}B'])
    return None


# The ranges: one per class of setting, each a floor taken from what the setting means and a ceiling
# taken from the narrowest place the value is stored or used, so no value inside can overflow there.

INT32_MAX: Final[int] = 2**31 - 1
"""A 32-bit signed integer: PostgreSQL's `INTEGER` and a C `int`; also the longest duration, in seconds."""

INT64_MAX: Final[int] = 2**63 - 1
"""A 64-bit signed integer: PostgreSQL's `BIGINT` and TOML's widest integer."""

INT16_MAX: Final[int] = 2**15 - 1
"""A 16-bit signed integer: PostgreSQL's `SMALLINT`."""

WIDEST: Final[int] = 30
"""Most significant digits a whole number may have before it is refused as out of range unread."""

NONE_WORD: Final[str] = '<none>'
"""The word an `int_or_none` key takes for none."""

KIND_FORMS: Final[Dict[Kind, str]] = {
    Kind.INT: 'a whole number',
    Kind.NUMBER: 'a number',
    Kind.INT_OR_NONE: 'a whole number or <none>',
    Kind.DURATION: 'a duration',
    Kind.SIZE: 'a size',
}
"""What a value of each numeric kind is called in a refusal."""

KIND_UNITS: Final[Dict[Kind, str]] = {Kind.DURATION: ' s', Kind.SIZE: ' bytes'}
"""The unit each bound of a duration or size is written with."""

KIND_DETAILS: Final[Dict[Kind, str]] = {
    Kind.DURATION: f'a duration is {TIME_FORMS}',
    Kind.SIZE: f'a size is {MEMORY_FORMS}',
}
"""The forms a refusal lists when the value is not of its kind at all."""


def figure(value: int | float) -> str:
    """A bound as written in a message: thousands separated, and a decimal only when it has one."""
    if isinstance(value, float) and not value.is_integer():
        return f'{value:,.12f}'.rstrip('0')
    return f'{int(value):,}'


def shown(value: Any) -> str:
    """`value` cut to a short printable form: as given up to 32 characters, else its head, tail and length.

    An integer too wide to print is shown in scientific notation. Nothing it is given makes it raise.
    """
    try:
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 100:
            return f'{Decimal(value):.4e}'
        text = repr(value)
        if len(text) <= 32:
            return text
        return f'{text[:16]}…{text[-8:]} ({len(text)} characters)'
    except Exception:  # A value whose repr fails, or is too wide to convert.
        try:
            return f'<{type(value).__name__}>'
        except Exception:
            return '<value>'


class _Refused(Exception):
    """A value an entry does not take; `out_of_range` when it is a number of the kind outside the range."""

    def __init__(self: _Refused, out_of_range: bool) -> None:
        super().__init__()
        self.out_of_range = out_of_range


def _significant(text: str) -> int:
    """The number of significant digits in whole-number text."""
    return len(text.lstrip('+-').replace('_', '').lstrip('0'))


@dataclass(frozen=True)
class Range:
    """The range of a numeric setting, its advisory limit, and why.

    `low` and `high` are the bounds, inclusive (exclusive below when `low_open`); `zero` says what 0
    means where it means something; a rate takes 0 or a value from `least` up. A value inside the
    range but past `advise` (`('above'|'below', limit, reason)`) is taken with a warning. `rounds_to`
    is the least value a read gives (a period under one second reads as one), `signed` lets text carry
    one leading `-` (the start delay's random form), and `per` is the unit a number is counted in.
    """

    kind: Kind
    low: int | float
    high: int | float
    why: str
    zero: Optional[str] = None
    least: Optional[float] = None
    low_open: bool = False
    rounds_to: Optional[int] = None
    signed: bool = False
    per: str = ''
    advise: Optional[Tuple[str, int | float, str]] = None
    detail: Optional[str] = None

    @property
    def unit(self: Range) -> str:
        """The unit each bound is written with."""
        return KIND_UNITS.get(self.kind, self.per)

    @property
    def bounds(self: Range) -> str:
        """The range as a refusal and the documentation give it (`0 (unconstrained) to 32,767`)."""
        unit = self.unit
        if self.least is not None:
            return f'{figure(self.least)} to {figure(self.high)}{unit}, or 0 ({self.zero})'
        low = f'{figure(self.low)}{KIND_UNITS.get(self.kind, "")}'
        if self.zero is not None and self.low == 0:
            low += f' ({self.zero})'
        if self.low_open:
            low += ' (exclusive)'
        return f'{low} to {figure(self.high)}{unit}'

    @property
    def text(self: Range) -> str:
        """The range as one sentence of the documentation."""
        none = ', or <none>' if self.kind is Kind.INT_OR_NONE else ''
        return f'Range: {self.bounds}{none}.'

    @property
    def advice_text(self: Range) -> Optional[str]:
        """The advisory limit as one sentence of the documentation, or None."""
        if self.advise is None:
            return None
        direction, limit, _ = self.advise
        other = ', other than 0,' if direction == 'below' and self.low == 0 else ''
        return f'{direction.capitalize()} {figure(limit)}{self.unit}{other} it runs with a warning.'

    def refusal(self: Range, name: str, value: Any, source: Optional[str] = None, out_of_range: bool = True) -> str:
        """The one refusal: `<name>: expected <form> from <A> to <B>, given <V>`, and its source.

        A value not of the kind at all, refused without a source (an option, a task's tag), also lists
        the kind's forms; a configuration refusal ends with its source instead.
        """
        detail = '' if out_of_range or source is not None else self.detail or KIND_DETAILS.get(self.kind)
        message = f'{name}: expected {KIND_FORMS[self.kind]} from {self.bounds}, given {shown(value)}'
        message += f'; {detail}' if detail else ''
        return message + (f' (from: {source})' if source is not None else '')

    def read(self: Range, value: Any, name: str, source: Optional[str] = None, whole: bool = True) -> Any:
        """`value` read by this range's kind, the range left unchecked; :class:`SettingError` if it is not of the kind.

        A whole number too wide to convert is refused here too, as out of range.
        """
        try:
            return self._read(value, whole)
        except _Refused as refused:
            raise SettingError(name, self, value, source, out_of_range=refused.out_of_range) from None

    def parse(self: Range, value: Any, name: str, source: Optional[str] = None, whole: bool = True) -> Any:
        """`value` read by this range's kind, inside the range, else :class:`SettingError` naming `name`.

        A duration given as text is rounded up to whole seconds when `whole`, else exact; a number is
        kept as given. Raises :class:`SettingError` (a ValueError) in the one form.
        """
        try:
            read = self._read(value, whole)
        except _Refused as refused:
            raise SettingError(name, self, value, source, out_of_range=refused.out_of_range) from None
        if read == NONE_WORD and isinstance(read, str):
            return read
        if not self.inside(read):
            raise SettingError(name, self, value, source, out_of_range=True)
        if self.rounds_to is not None and read < self.rounds_to:
            return self.rounds_to
        return read

    def inside(self: Range, value: int | float) -> bool:
        """True when the number `value` lies inside the range."""
        if self.least is not None:
            return value == 0 or self.least <= value <= self.high
        if self.low_open:
            return self.low < value <= self.high
        return self.low <= value <= self.high

    def advised(self: Range, value: Any) -> bool:
        """True when `value` (as read) lies past the advisory limit; 0 is never past a lower one."""
        if self.advise is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        direction, limit, _ = self.advise
        return value > limit if direction == 'above' else (value != 0 and value < limit)

    def advice(self: Range, name: str, given: Any) -> str:
        """The advisory warning for the value `given` past the limit."""
        direction, limit, reason = self.advise
        return f'{name}: {shown(given)} is {direction} the advised {figure(limit)}{self.unit} ({reason}); running anyway'

    def _read(self: Range, value: Any, whole: bool) -> Any:
        """`value` as a number of this kind (or `<none>`), without the range; raise `_Refused` if it is not one."""
        kind = self.kind
        if isinstance(value, bool):
            raise _Refused(False)
        if isinstance(value, float) and not math.isfinite(value):
            raise _Refused(False)
        if kind in (Kind.INT, Kind.INT_OR_NONE):
            if kind is Kind.INT_OR_NONE and isinstance(value, str) and value == NONE_WORD:
                return value
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                if value.is_integer():
                    return int(value)
                raise _Refused(False)
            if isinstance(value, str) and INT_FORM.fullmatch(text := value.strip()):
                if _significant(text) > WIDEST:
                    raise _Refused(True)
                return int(text)
            raise _Refused(False)
        if isinstance(value, (int, float)):
            return value
        if not isinstance(value, str):
            raise _Refused(False)
        text = value.strip()
        if kind is Kind.NUMBER:
            if INT_FORM.fullmatch(text):
                if _significant(text) > WIDEST:
                    raise _Refused(True)
                return int(text)
            if NUMBER_FORM.fullmatch(text):
                number = float(text)
                if not math.isfinite(number):
                    raise _Refused(True)
                return number
            raise _Refused(False)
        if kind is Kind.SIZE:
            try:
                size = match_bytes(text)
            except ValueError:
                raise _Refused(True) from None
            if size is None:
                raise _Refused(False)
            return size
        negative = self.signed and text.startswith('-')
        try:
            seconds = match_seconds(text[1:] if negative else text)
        except ValueError:
            raise _Refused(True) from None
        if seconds is None:
            raise _Refused(False)
        if self.signed:
            if seconds > self.high:  # Before it is held as a float, which may not hold it
                raise _Refused(True)
            return -float(seconds) if negative else float(seconds)
        if whole:
            return math.ceil(seconds)
        return int(seconds) if seconds.denominator == 1 else float(seconds)


class SettingError(ValueError):
    """A value a setting's range refuses, out of range or not a number of its kind.

    The message is the one form, `<name>: expected <form> from <A> to <B>, given <V>`, with
    ` (from: <source>)` for configuration. `name` is the option as defined or the key; `source` the
    file or variable the value came from, if any; `out_of_range` is False when the value is not a
    number of the kind at all.
    """

    def __init__(self: SettingError, name: str, entry: Range, value: Any, source: Optional[str] = None, *,
                 out_of_range: bool = True) -> None:
        """Initialize with the setting's `name`, its `entry`, the `value` given and its `source`."""
        self.name = name
        self.entry = entry
        self.value = value
        self.source = source
        self.out_of_range = out_of_range
        super().__init__(entry.refusal(name, value, source, out_of_range))


DAY: Final[int] = 24 * 60 * 60
"""Seconds in a day."""

THREADS: Final[Range] = Range(
    Kind.INT, 0, 4_194_304, zero='all cores',
    advise=('above', 1024, 'more executor threads than one client needs; a wider node runs more clients'),
    why='0 runs one thread per core. The ceiling is the most thread ids a 64-bit Linux host gives a process.')
COUNT: Final[Range] = Range(
    Kind.INT, 1, INT32_MAX,
    why='0 is an empty bundle, queue, page or site. The ceiling is a 32-bit signed integer.')
COUNT_OR_ZERO: Final[Range] = Range(
    Kind.INT, 0, INT32_MAX,
    why='0 is documented as none or never. The ceiling is a 32-bit signed integer.')
COUNT_OR_NONE: Final[Range] = Range(
    Kind.INT_OR_NONE, 0, INT32_MAX,
    why='0 is none; <none> leaves it unset. The ceiling is a 32-bit signed integer.')
ATTEMPTS: Final[Range] = Range(
    Kind.INT, 1, INT16_MAX,
    advise=('above', 100, 'a task that fails 100 times will not pass on the next'),
    why="A task's attempt is a 16-bit integer on PostgreSQL.")
RETRIES: Final[Range] = Range(
    Kind.INT, 0, INT16_MAX - 1,
    advise=('above', 99, 'a task that fails 100 times will not pass on the next'),
    why="Attempts less one: a task's attempt is a 16-bit integer on PostgreSQL.")
CORES: Final[Range] = Range(
    Kind.INT, 0, INT16_MAX, zero='unconstrained',
    advise=('above', 1024, 'no node a task runs on has more'),
    why="A task's cores are a 16-bit integer on PostgreSQL.")
PORT: Final[Range] = Range(Kind.INT, 1, 65_535, why='A TCP port.')
GROUP: Final[Range] = Range(Kind.INT, -INT32_MAX - 1, INT32_MAX, why="A task's group is a 32-bit integer.")
PART: Final[Range] = Range(Kind.INT, 0, INT32_MAX, why="A task's part is a 32-bit integer.")
DURATION: Final[Range] = Range(
    Kind.DURATION, 0, INT32_MAX,
    why="A task's timeout is a 32-bit integer of seconds (about 68 years), under the longest wait a thread blocks.")
SHORTEST: Final[Range] = Range(
    Kind.DURATION, 1, INT32_MAX,
    why='0 waits for nothing (a refused value, or a loop that never rests). The ceiling is the longest duration.')
HANDSHAKE: Final[Range] = Range(
    Kind.DURATION, 1, DAY,
    why='A TLS handshake deadline of at most one day, and more than none.')
EVICT: Final[Range] = Range(
    Kind.DURATION, 10, INT32_MAX,
    why='Under 10 s a client between heartbeats is taken for lost. The ceiling is the longest duration.')
PERIOD: Final[Range] = Range(
    Kind.DURATION, 0, INT32_MAX, low_open=True, rounds_to=1,
    advise=('above', 3600, 'a scaling check less often than hourly leaves new work unanswered for an hour'),
    why='A scaling period greater than 0; under one second reads as one.')
DELAY: Final[Range] = Range(
    Kind.DURATION, -INT32_MAX, INT32_MAX, signed=True,
    detail=f'{TIME_FORMS}; {DELAY_RANDOM_FORM}',
    why='The longest duration, either sign; a leading - waits a random time up to it.')
SIZE: Final[Range] = Range(
    Kind.SIZE, 0, INT64_MAX, zero='unconstrained',
    why="A task's memory is a 64-bit integer of bytes on PostgreSQL.")
RATE: Final[Range] = Range(
    Kind.NUMBER, 0, 1e9, least=1e-6, zero='no limit', per=' tasks per second',
    advise=('below', 0.001, 'under one task per 1,000 s a throttle is a schedule, likely a rate meant per '
                            'minute or hour'),
    why='Under 0.000001 a wait between tasks overflows; at 1,000,000,000 a client waits a nanosecond per task.')
FACTOR: Final[Range] = Range(
    Kind.NUMBER, 1e-6, 1e9,
    advise=('above', 100, 'past 100 scaling hardly adds a client at all'),
    why='0 divides by zero in the scaling pressure; the same decimal span as a rate.')
EXIT_STATUS: Final[Range] = Range(
    Kind.INT, -INT16_MAX - 1, INT16_MAX,
    why="A task's exit status is a 16-bit integer on PostgreSQL: 0 to 255, a signal's negative number, "
        "-1 for cancelled, and -1001 and -1002 for a task that never ran.")
PEAK_CORES: Final[Range] = Range(
    Kind.NUMBER, 0, INT16_MAX,
    why="The peak cores a client measured for a task; the ceiling is a task's cores, as no host it runs on has more.")
PEAK_MEMORY: Final[Range] = Range(
    Kind.NUMBER, 0, INT64_MAX, per=' bytes',
    why="The peak memory a client measured for a task, in bytes; the ceiling is a task's memory.")

SETTINGS: Final[Dict[str, Range]] = {
    # [logging]
    'logging.file.keep': COUNT_OR_ZERO,
    # [database]
    'database.port': PORT,
    # [server]
    'server.port': PORT, 'server.queuesize': COUNT, 'server.bundlesize': COUNT, 'server.bundlemax': COUNT,
    'server.attempts': ATTEMPTS, 'server.poll': DURATION, 'server.evict': EVICT, 'server.tls.handshake': HANDSHAKE,
    # [client]
    'client.bundlesize': COUNT, 'client.bundlewait': DURATION, 'client.heartrate': DURATION,
    'client.timeout': DURATION, 'client.cores': CORES, 'client.memory': SIZE, 'client.ratelimit': RATE,
    # [submit]
    'submit.bundlesize': COUNT, 'submit.bundlewait': DURATION,
    # [task]
    'task.cores': CORES, 'task.memory': SIZE, 'task.timeout': DURATION, 'task.signalwait': DURATION,
    # [autoscale]
    'autoscale.factor': FACTOR, 'autoscale.period': PERIOD, 'autoscale.failed_launches': COUNT_OR_ZERO,
    'autoscale.size.init': COUNT_OR_ZERO, 'autoscale.size.min': COUNT_OR_ZERO, 'autoscale.size.max': COUNT_OR_ZERO,
    # [relay]
    'relay.port': PORT, 'relay.bundlesize': COUNT, 'relay.bundlewait': DURATION, 'relay.poll': DURATION,
    'relay.retention': DURATION, 'relay.size': COUNT, 'relay.min': COUNT_OR_NONE, 'relay.max': COUNT_OR_NONE,
    'relay.init': COUNT_OR_NONE, 'relay.num_threads': THREADS, 'relay.timeout': DURATION,
    'relay.upstream.port': PORT, 'relay.upstream.bundlesize': COUNT, 'relay.upstream.heartrate': DURATION,
    'relay.upstream.deadline': DURATION, 'relay.upstream.backoff': DURATION, 'relay.upstream.evict_hint': DURATION,
    # [relay.zone.<name>]
    'relay.zone.*.size': COUNT, 'relay.zone.*.min': COUNT_OR_ZERO, 'relay.zone.*.max': COUNT_OR_ZERO,
    'relay.zone.*.init': COUNT_OR_ZERO,
    # Options that set no key (threads take relay.num_threads's entry)
    'option.max_retries': RETRIES, 'option.group': GROUP, 'option.part': PART, 'option.limit': COUNT,
    'option.delay': DELAY, 'option.interval': SHORTEST,
    'option.client.timeout': SHORTEST, 'option.client.task_timeout': SHORTEST,
    # Task columns `hs update` writes that no key or option reads
    'column.exit_status': EXIT_STATUS, 'column.cores_max': PEAK_CORES, 'column.memory_max': PEAK_MEMORY,
}
"""The range of every numeric setting: each numeric key of :data:`KEYS`, each zone key as
`relay.zone.*.<key>`, each option that sets no key as `option.<name>`, and each task column that
`hs update` writes and nothing else reads as `column.<name>`."""


NUMERIC: Final[Tuple[Kind, ...]] = (Kind.INT, Kind.NUMBER, Kind.INT_OR_NONE, Kind.DURATION, Kind.SIZE)
"""The kinds of a numeric setting."""


def setting_for(path: str) -> Optional[str]:
    """The entry of :data:`SETTINGS` for the dotted key `path` (a zone key's by its zone key), or None if not numeric.

    An option's or a column's entry is not a key's, so it is never one.
    """
    if path in SETTINGS and not path.startswith(('option.', 'column.')):
        return path
    parts = path.split('.')
    if len(parts) == 4 and parts[:2] == ['relay', 'zone'] and ZONE_KEYS.get(parts[3]) in NUMERIC:
        return f'relay.zone.*.{parts[3]}'
    return None


def parse_setting(setting: str, value: Any, name: Optional[str] = None, source: Optional[str] = None,
                  whole: bool = True) -> Any:
    """`value` read by the entry `setting` of :data:`SETTINGS`, refused by :class:`SettingError` named `name`.

    The refusal names `name` (by default the setting itself) and `source` when given.
    """
    return SETTINGS[setting].parse(value, setting if name is None else name, source, whole)


log = logging.getLogger(__name__)

ADVISED: Set[str] = set()
"""The settings this process has warned of, so each warns at most once."""

PENDING: Optional[List[str]] = []
"""Warnings made before logging is set up, logged by :func:`flush_advice`; None once it is."""


def advise(setting: str, value: Any, name: Optional[str] = None, given: Any = None) -> bool:
    """Warn once per process when `value` (as read) of `setting` is past its advisory limit; True if it is.

    The warning names `name` (by default the setting) and shows `given` (by default `value`).
    """
    entry = SETTINGS[setting]
    if not entry.advised(value):
        return False
    if setting not in ADVISED:
        ADVISED.add(setting)
        message = entry.advice(setting if name is None else name, value if given is None else given)
        if PENDING is None:
            log.warning(message)
        else:
            PENDING.append(message)
    return True


def flush_advice() -> None:
    """Log the warnings made before logging was set up, and log any later one at once."""
    global PENDING
    pending, PENDING = PENDING or [], None
    for message in pending:
        log.warning(message)
