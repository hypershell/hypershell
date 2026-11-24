# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A numeric option out of its range, or not a number of its kind, stops the command with one line, exit 2.

Through the command line for the cases the GOAL names, and in process for every numeric option of
every command (and the task-command aliases, the same classes): the refusal names the option as
defined (`-N/--num-threads`), its range and the value, with no traceback and no `argument` prefix,
before anything starts. A value inside its range is taken as today.

Where the command would start work on a build that takes the value (`hs cluster`, `hs client`,
`hs server`, `hs wait`), a trailing `--help` ends it at once there, after the value is parsed: the
option is parsed where it stands, so the refusal still comes first.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Tuple

# Standard libs
import os
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError
from sqlalchemy import create_engine

# Internal libs
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
from hypershell.server import ServerApp
from hypershell.relay import RelayApp
from hypershell.submit import SubmitApp
from hypershell.task import TaskGroupApp, TaskSearchApp, TaskUpdateApp, TaskWaitApp, TaskRunApp
from tests.bounded_checks import LONG, SHORT, assert_one_refusal, hs_cli, refusal
from tests.input_checks import rows
from tests.test_bounded_table import OPTIONS


APPS: Final[Dict[str, type]] = {
    'cluster': ClusterApp, 'server': ServerApp, 'client': ClientApp, 'relay': RelayApp, 'submit': SubmitApp,
    'list': TaskSearchApp, 'update': TaskUpdateApp, 'wait': TaskWaitApp, 'run': TaskRunApp,
}
"""Each command with a numeric option, by name."""

ALIASES: Final[Dict[str, str]] = {'list': 'search', 'update': 'update', 'wait': 'wait', 'run': 'run'}
"""The backwards-compatible task-command name of each command that has one."""

HELP: Final[List[str]] = ['--help']
"""Ends a command that took the value at once (see the module's note)."""

REFUSED: Final[List[Tuple[str, List[str], str]]] = [
    ('submit-cores-100000', ['submit', '-c', '100000', 'echo', 'a'], '-c/--cores'),
    ('submit-memory-8388608TB', ['submit', '-m', '8388608TB', 'echo', 'a'], '-m/--memory'),
    ('submit-timeout-2147483648', ['submit', '-W', '2147483648', 'echo', 'a'], '-W/--timeout'),
    ('submit-bundlesize-0', ['submit', '-b', '0', 'echo', 'a'], '-b/--bundlesize'),
    ('list-limit-minus5', ['list', '--limit=-5'], '-l/--limit'),
    ('list-group-2147483648', ['list', '-g', '2147483648'], '-g/--group'),
    ('list-part-minus1', ['list', '--part=-1'], '--part'),
    ('cluster-num-threads-4194305', ['cluster', '--no-db', '-N', '4194305', *HELP], '-N/--num-threads'),
    ('cluster-factor-nan', ['cluster', '--no-db', '-F', 'nan', *HELP], '-F/--factor'),
    ('cluster-factor-0', ['cluster', '--no-db', '-F', '0', *HELP], '-F/--factor'),
    ('client-ratelimit-minus5', ['client', '--ratelimit=-5', *HELP], '-R/--ratelimit'),
    ('server-port-65536', ['server', '-p', '65536', *HELP], '-p/--port'),
    ('wait-interval-0', ['wait', '-n', '0', '00000000-0000-0000-0000-000000000000', *HELP], '-n/--interval'),
]
"""Values each command took before (or refused in another form), by test id: argv, and the option's name."""


@mark.integration
@mark.parametrize('argv, name', [pytest.param(argv, name, id=case) for case, argv, name in REFUSED])
def test_option_refused(temp_site: Path, argv: List[str], name: str) -> None:
    """The command exits 2 with one line naming the option, its range and the value; nothing is stored."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    rc, stdout, stderr = hs_cli(temp_site, ['hs', *argv], HYPERSHELL_DATABASE_FILE=str(database))
    assert_one_refusal(rc, stderr, exit_status.bad_argument, name)
    if database.exists():
        assert rows(create_engine(f'sqlite:///{database}'), "SELECT name FROM sqlite_master WHERE name = 'task'") == [] \
            or rows(create_engine(f'sqlite:///{database}'), 'SELECT count(*) FROM task') == [(0, )]


@mark.integration
def test_option_message_form(temp_site: Path) -> None:
    """The refusal reads `<name>: expected <form> from <A> to <B>, given <V>`, with the zero's meaning."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'submit', '-c', '100000', 'echo', 'a'])
    assert rc == exit_status.bad_argument, stderr[-4000:]
    assert '-c/--cores: expected a whole number from 0 (unconstrained) to 32,767, given \'100000\'' in stderr, stderr


@mark.integration
def test_option_message_printable_4300_digits(temp_site: Path) -> None:
    """A 4,300-digit value is refused with a short, printable line, exit 2."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'submit', '-b', LONG, 'echo', 'a'])
    assert_one_refusal(rc, stderr, exit_status.bad_argument, '-b/--bundlesize')
    assert LONG not in stderr


def out_of_range(setting: str) -> str:
    """A value one past the ceiling of `setting`'s entry, as text."""
    from hypershell.core.config_schema import SETTINGS
    entry = SETTINGS[setting]
    if setting.endswith('memory'):
        return '8388608TB'
    return str(int(entry.high) + 1)


def under_floor(setting: str) -> str:
    """A value just under the floor of `setting`'s entry, as text (as `--opt=VALUE`, since it may be negative)."""
    from hypershell.core.config_schema import SETTINGS
    entry = SETTINGS[setting]
    return '0' if entry.low > 0 and isinstance(entry.low, float) else str(int(entry.low) - 1)


def flag_of(command: str, dest: str) -> str:
    """The first flag of the option of `command` whose destination is `dest`."""
    action, = [action for action in APPS[command].interface._actions if action.dest == dest and action.option_strings]
    return action.option_strings[-1] if action.option_strings[-1].startswith('--') else action.option_strings[0]


@mark.unit
@mark.parametrize('command, dest', sorted(OPTIONS), ids=[f'{command}-{dest}' for command, dest in sorted(OPTIONS)])
@mark.parametrize('bound', ['ceiling', 'floor', 'word'])
def test_option_refused_every_command(command: str, dest: str, bound: str) -> None:
    """Every numeric option of every command refuses one past each bound, and a word, in the one form."""
    setting, name = OPTIONS[(command, dest)]
    value = {'ceiling': out_of_range, 'floor': under_floor, 'word': lambda _: 'x'}[bound](setting)
    with pytest.raises(ArgumentError) as caught:
        APPS[command].from_cmdline([f'{flag_of(command, dest)}={value}'])
    message = str(caught.value)
    assert refusal(name).fullmatch(message), message
    assert len(message) <= SHORT


@mark.unit
@mark.parametrize('command', sorted(ALIASES))
def test_option_refused_task_alias(command: str) -> None:
    """The task-command alias of each command is the same class, so it refuses the same way."""
    assert TaskGroupApp.commands[ALIASES[command]] is APPS[command]


@mark.integration
def test_option_refused_through_alias(temp_site: Path) -> None:
    """`hs task search -l 0` is refused as `hs list -l 0` is."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'task', 'search', '-l', '0'])
    assert_one_refusal(rc, stderr, exit_status.bad_argument, '-l/--limit')


IN_RANGE: Final[List[Tuple[type, List[str], Dict[str, Any]]]] = [
    (ClusterApp, ['-N', '0', '-b', '2147483647', '-r', '32766', '-c', '32767', '-m', '8388607TB', '-W', '2147483647',
                  '-F', '1e9', '-P', '0.5', '-R', '1e-6', '-d', '-10m', '-p', '65535', '-X', '0', '-Y', '2147483647',
                  '-I', '0', '-C', '32767', '-M', '1T', '-T', '0', '-S', '2147483647', '-Q', '2147483647',
                  '-w', '2147483647'],
     {'num_threads': 0, 'autoscaling_period': 1, 'ratelimit': 1e-6, 'delay_start': -600.0, 'max_retries': 32766,
      'autoscaling_factor': 1e9}),
    (ClientApp, ['-N', '4194304', '-T', '1', '-W', '2147483647', '-R', '1e9', '-p', '1', '-d', '2147483647'],
     {'num_threads': 4194304, 'client_timeout': 1, 'task_timeout': 2147483647, 'ratelimit': 1e9}),
    (ServerApp, ['-r', '0', '-F', '1e-6', '-P', '2147483647', '-c', '0', '-m', '0'],
     {'max_retries': 0, 'autoscaling_factor': 1e-6, 'autoscaling_period': 2147483647}),
    (RelayApp, ['-N', '2147483647', '-p', '1', '--port', '65535'], {'size': 2147483647, 'upstream_port': 1}),
    (SubmitApp, ['-g', '-2147483648', '-b', '2147483647', '-c', '0', 'echo'], {'group': -2**31, 'bundlesize': 2**31 - 1}),
    (TaskSearchApp, ['-l', '2147483647', '-g', '2147483647', '--part', '0'], {'limit': 2**31 - 1}),
    (TaskUpdateApp, ['-l', '1', '-g', '-2147483648', 'x', 'y'], {'limit': 1}),
    (TaskWaitApp, ['-n', '1', 'id'], {'interval': 1}),
    (TaskRunApp, ['-n', '2147483647', 'echo'], {'interval': 2**31 - 1}),
]
"""Each bound inside its range, per command, and what the command then holds."""


@mark.unit
@mark.parametrize('app, argv, expected', IN_RANGE, ids=[app.__name__ for app, _, _ in IN_RANGE])
def test_option_in_range_unchanged(app: type, argv: List[str], expected: Dict[str, Any]) -> None:
    """Each bound inside its range is taken, and read as today (a period under one second as one, a random delay)."""
    held = app.from_cmdline(argv)
    for name, value in expected.items():
        assert getattr(held, name) == value, name


@mark.integration
def test_option_in_range_stored(temp_site: Path) -> None:
    """`hs submit` stores the largest cores, timeout and a large memory value as given."""
    database = os.environ['HYPERSHELL_DATABASE_FILE']
    rc, _, stderr = hs_cli(temp_site, ['hs', 'submit', '-c', '32767', '-W', '2147483647', '-m', '8388607TB',
                                       'echo', 'a'], HYPERSHELL_DATABASE_FILE=database)
    assert rc == 0, stderr[-4000:]
    stored = rows(create_engine(f'sqlite:///{database}'), 'SELECT cores, timeout, memory FROM task')
    assert stored == [(32767, 2147483647, 8388607 * 1024**4)]
