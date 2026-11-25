# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Every numeric setting has one range, held in one table beside its kind (`SETTINGS`), and every option reads through it.

The table covers each numeric key of `KEYS`, each zone key and each option that sets no key; every
built-in default lies inside its range and its advisory limit; every option of every command that
takes a value either reads through the table (a `RangedOption` naming its entry) or is a text
option listed here. The table itself is imported inside each test, so this module collects on a
build without it.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Iterator, Tuple

# Standard libs
from argparse import Action, _HelpAction, _VersionAction

# External libs
import pytest
from pytest import mark
from cmdkit.app import ApplicationGroup

# Internal libs
from hypershell import HyperShellApp
from hypershell.core.config import default
from hypershell.core.config_schema import KEYS, ZONE_KEYS, Kind
from tests.test_config_schema import default_leaves


NUMERIC: Final[Tuple[Kind, ...]] = (Kind.INT, Kind.NUMBER, Kind.INT_OR_NONE, Kind.DURATION, Kind.SIZE)
"""The kinds of a numeric key."""

OPTION_ONLY: Final[Dict[str, Kind]] = {
    'option.max_retries': Kind.INT, 'option.group': Kind.INT, 'option.part': Kind.INT, 'option.limit': Kind.INT,
    'option.delay': Kind.DURATION, 'option.interval': Kind.DURATION,
    'option.client.timeout': Kind.DURATION, 'option.client.task_timeout': Kind.DURATION,
}
"""Entries for options that set no key, and their kinds (threads share `relay.num_threads`'s entry)."""

TEXT_OPTIONS: Final[frozenset] = frozenset({
    '--advertise', '--auth', '--autoscaling', '--backup', '--bind', '--database', '--delimiter', '--errors',
    '--extract', '--failures', '--format', '--from-json', '--host', '--launcher', '--launcher-args', '--order-by',
    '--output', '--remote-exe', '--remove-tag', '--signal', '--ssh', '--ssh-args', '--ssh-group', '--tag',
    '--task-file', '--template', '--tls-ca', '--tls-cert', '--tls-key', '--upstream-host', '--where', '--with-tag',
    '--zone',
})
"""Every option that takes a value that is not a number, by its first long flag (a new one forces a decision)."""

OPTIONS: Final[Dict[Tuple[str, str], Tuple[str, str]]] = {
    ('cluster', 'num_threads'): ('relay.num_threads', '-N/--num-threads'),
    ('cluster', 'bundlesize'): ('server.bundlesize', '-b/--bundlesize'),
    ('cluster', 'bundlewait'): ('submit.bundlewait', '-w/--bundlewait'),
    ('cluster', 'delay_start'): ('option.delay', '-d/--delay-start'),
    ('cluster', 'max_retries'): ('option.max_retries', '-r/--max-retries'),
    ('cluster', 'poll'): ('server.poll', '-Q/--poll'),
    ('cluster', 'port'): ('server.port', '-p/--port'),
    ('cluster', 'cores'): ('task.cores', '-c/--cores'),
    ('cluster', 'memory'): ('task.memory', '-m/--memory'),
    ('cluster', 'client_cores'): ('client.cores', '-C/--client-cores'),
    ('cluster', 'client_memory'): ('client.memory', '-M/--client-memory'),
    ('cluster', 'ratelimit'): ('client.ratelimit', '-R/--ratelimit'),
    ('cluster', 'task_timeout'): ('task.timeout', '-W/--task-timeout'),
    ('cluster', 'client_timeout'): ('client.timeout', '-T/--timeout'),
    ('cluster', 'task_signalwait'): ('task.signalwait', '-S/--signalwait'),
    ('cluster', 'autoscaling_factor'): ('autoscale.factor', '-F/--factor'),
    ('cluster', 'autoscaling_period'): ('autoscale.period', '-P/--period'),
    ('cluster', 'autoscaling_minimum'): ('autoscale.size.min', '-X/--min-size'),
    ('cluster', 'autoscaling_maximum'): ('autoscale.size.max', '-Y/--max-size'),
    ('cluster', 'autoscaling_initial'): ('autoscale.size.init', '-I/--init-size'),
    ('server', 'bundlesize'): ('server.bundlesize', '-b/--bundlesize'),
    ('server', 'bundlewait'): ('submit.bundlewait', '-w/--bundlewait'),
    ('server', 'task_cores'): ('task.cores', '-c/--task-cores'),
    ('server', 'task_memory'): ('task.memory', '-m/--task-memory'),
    ('server', 'task_timeout'): ('task.timeout', '-W/--task-timeout'),
    ('server', 'max_retries'): ('option.max_retries', '-r/--max-retries'),
    ('server', 'autoscaling_factor'): ('autoscale.factor', '-F/--factor'),
    ('server', 'autoscaling_period'): ('autoscale.period', '-P/--period'),
    ('server', 'poll'): ('server.poll', '-Q/--poll'),
    ('server', 'port'): ('server.port', '-p/--port'),
    ('client', 'num_threads'): ('relay.num_threads', '-N/--num-threads'),
    ('client', 'port'): ('server.port', '-p/--port'),
    ('client', 'bundlesize'): ('client.bundlesize', '-b/--bundlesize'),
    ('client', 'bundlewait'): ('client.bundlewait', '-w/--bundlewait'),
    ('client', 'delay_start'): ('option.delay', '-d/--delay-start'),
    ('client', 'client_timeout'): ('option.client.timeout', '-T/--timeout'),
    ('client', 'task_timeout'): ('option.client.task_timeout', '-W/--task-timeout'),
    ('client', 'ratelimit'): ('client.ratelimit', '-R/--ratelimit'),
    ('client', 'task_signalwait'): ('task.signalwait', '-S/--signalwait'),
    ('client', 'client_cores'): ('client.cores', '-C/--client-cores'),
    ('client', 'client_memory'): ('client.memory', '-M/--client-memory'),
    ('relay', 'upstream_port'): ('relay.upstream.port', '-p/--upstream-port'),
    ('relay', 'size'): ('relay.size', '-N/--size'),
    ('relay', 'port'): ('relay.port', '--port'),
    ('submit', 'bundlesize'): ('submit.bundlesize', '-b/--bundlesize'),
    ('submit', 'bundlewait'): ('submit.bundlewait', '-w/--bundlewait'),
    ('submit', 'cores'): ('task.cores', '-c/--cores'),
    ('submit', 'memory'): ('task.memory', '-m/--memory'),
    ('submit', 'timeout'): ('task.timeout', '-W/--timeout'),
    ('submit', 'group'): ('option.group', '-g/--group'),
    ('submit', 'queue_port'): ('server.port', '-p/--port'),
    ('wait', 'interval'): ('option.interval', '-n/--interval'),
    ('run', 'interval'): ('option.interval', '-n/--interval'),
    ('list', 'limit'): ('option.limit', '-l/--limit'),
    ('list', 'group_filter'): ('option.group', '-g/--group'),
    ('list', 'part_filter'): ('option.part', '--part'),
    ('update', 'limit'): ('option.limit', '-l/--limit'),
    ('update', 'group_filter'): ('option.group', '-g/--group'),
}
"""Every numeric option, by command (an alias of the task commands is the same class) and destination:
its table entry and its name in a refusal."""

RANGES: Final[Dict[str, Tuple[Any, Any, Any]]] = {
    # (floor, ceiling, advisory limit or None), as the GOAL's table, with TECH.md's changes under R1.
    'relay.num_threads': (0, 4_194_304, ('above', 1024)),
    'server.bundlesize': (1, 2**31 - 1, None), 'relay.size': (1, 2**31 - 1, None),
    'relay.zone.*.size': (1, 2**31 - 1, None), 'option.limit': (1, 2**31 - 1, None),
    'logging.file.keep': (0, 2**31 - 1, None), 'autoscale.failed_launches': (0, 2**31 - 1, None),
    'autoscale.size.max': (0, 2**31 - 1, None), 'relay.zone.*.init': (0, 2**31 - 1, None),
    'relay.min': (0, 2**31 - 1, None),
    'server.attempts': (1, 32_767, ('above', 100)), 'option.max_retries': (0, 32_766, ('above', 99)),
    'task.cores': (0, 32_767, ('above', 1024)), 'client.cores': (0, 32_767, ('above', 1024)),
    'server.port': (1, 65_535, None), 'database.port': (1, 65_535, None),
    'option.group': (-2**31, 2**31 - 1, None), 'option.part': (0, 2**31 - 1, None),
    'task.timeout': (0, 2**31 - 1, None), 'server.poll': (0, 2**31 - 1, None),
    'server.tls.handshake': (1, 86_400, None), 'option.interval': (1, 2**31 - 1, None),
    'option.client.timeout': (1, 2**31 - 1, None), 'option.client.task_timeout': (1, 2**31 - 1, None),
    'server.evict': (10, 2**31 - 1, None), 'autoscale.period': (0, 2**31 - 1, ('above', 3600)),
    'option.delay': (-(2**31 - 1), 2**31 - 1, None),
    'task.memory': (0, 2**63 - 1, None), 'client.memory': (0, 2**63 - 1, None),
    'client.ratelimit': (0, 1e9, ('below', 0.001)), 'autoscale.factor': (1e-6, 1e9, ('above', 100)),
}
"""A sample of entries, one or more per class of the GOAL's table, by floor, ceiling and advisory limit."""


def table() -> Any:
    """The module that holds the table (imported when a test runs, not at collection)."""
    from hypershell.core import config_schema
    return config_schema


def numeric_keys() -> set:
    """Every numeric key of `KEYS`, and each zone key as `relay.zone.*.<key>`."""
    return ({path for path, kind in KEYS.items() if kind in NUMERIC} |
            {f'relay.zone.*.{name}' for name, kind in ZONE_KEYS.items() if kind in NUMERIC})


def leaf_apps(app: type = HyperShellApp, path: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], type]]:
    """Every command class below `app`, with its command path (the task aliases included)."""
    if issubclass(app, ApplicationGroup):
        for name, sub in app.commands.items():
            yield from leaf_apps(sub, (*path, name))
    else:
        yield path, app


def value_options() -> Iterator[Tuple[Tuple[str, ...], Action]]:
    """Every option of every command that takes a value."""
    for path, app in leaf_apps():
        for action in app.interface._actions:
            if action.option_strings and action.nargs != 0 and not isinstance(action, (_HelpAction, _VersionAction)):
                yield path, action


def long_name(action: Action) -> str:
    """The option's first long flag, or its only flag."""
    return next((flag for flag in action.option_strings if flag.startswith('--')), action.option_strings[0])


@mark.unit
def test_no_plain_numeric_option_type() -> None:
    """No option of any command is typed as a plain `int` or `float`: each numeric option reads through the table."""
    plain = sorted(f'hs {" ".join(path)} {"/".join(action.option_strings)}'
                   for path, action in value_options() if action.type in (int, float))
    assert plain == [], plain


@mark.unit
def test_settings_cover_numeric_keys() -> None:
    """`SETTINGS` holds every numeric key, every zone key and each option-only entry, and nothing else."""
    assert set(table().SETTINGS) == numeric_keys() | set(OPTION_ONLY)


@mark.unit
def test_settings_kinds_match_keys() -> None:
    """Each entry's kind is its key's kind in `KEYS` (or `ZONE_KEYS`), so the table cannot drift from it."""
    kinds = {**{path: kind for path, kind in KEYS.items() if kind in NUMERIC},
             **{f'relay.zone.*.{name}': kind for name, kind in ZONE_KEYS.items() if kind in NUMERIC}, **OPTION_ONLY}
    assert {name: entry.kind for name, entry in table().SETTINGS.items()} == kinds


@mark.unit
@mark.parametrize('setting', sorted(RANGES))
def test_settings_ranges(setting: str) -> None:
    """Each class of the GOAL's table has its floor, ceiling and advisory limit."""
    entry = table().SETTINGS[setting]
    low, high, advise = RANGES[setting]
    assert (entry.low, entry.high) == (low, high)
    assert (entry.advise[:2] if entry.advise else None) == advise
    assert entry.why, f'{setting} has no justification'


@mark.unit
def test_advisory_limits_inside_ranges() -> None:
    """Every advisory limit lies inside its range, and has a reason."""
    for name, entry in table().SETTINGS.items():
        if entry.advise is not None:
            direction, limit, reason = entry.advise
            assert direction in ('above', 'below') and reason, name
            assert entry.low <= limit <= entry.high, name


@mark.unit
def test_defaults_in_range() -> None:
    """Every built-in default of a numeric key reads into its range, inside any advisory limit."""
    schema = table()
    checked = set()
    for path, value in default_leaves(default):
        if KEYS.get(path) in NUMERIC:
            entry = schema.SETTINGS[path]
            parsed = entry.parse(value, path)
            assert not entry.advised(parsed), (path, value)
            checked.add(path)
    assert checked == {path for path, kind in KEYS.items() if kind in NUMERIC} - {'database.port'}


@mark.unit
def test_every_value_option_ranged_or_text() -> None:
    """Every option that takes a value is a `RangedOption` on its entry, by command and destination, or a text option."""
    schema = table()
    from hypershell.core.types import RangedOption
    ranged, text = {}, set()
    for path, action in value_options():
        if isinstance(action.type, RangedOption):
            command = path[-1] if path[0] != 'task' else {'search': 'list'}.get(path[-1], path[-1])
            ranged[(command, action.dest)] = (action.type.setting, action.type.name)
            assert action.type.setting in schema.SETTINGS
        else:
            assert action.type is None, f'hs {" ".join(path)} {long_name(action)} has type {action.type!r}'
            text.add(long_name(action))
    assert ranged == OPTIONS
    assert text == TEXT_OPTIONS


@mark.unit
def test_ranged_option_defaults_in_range() -> None:
    """Each numeric option's default (as the built-in configuration gives it) lies inside its entry's range."""
    schema = table()
    for path, action in value_options():
        if action.type is not None and action.default is not None:
            entry = schema.SETTINGS[action.type.setting]
            entry.parse(action.default, action.type.name)
