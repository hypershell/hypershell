# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test durations with units on every option that takes one, its configuration and the launched client."""


# Type annotations
from __future__ import annotations
from typing import Final, List, Dict, Any

# Standard libs
import sys
import json
from pathlib import Path
from types import SimpleNamespace

# External libs
from pytest import mark, raises
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError

# Internal libs
from tests import main
from hypershell import client as client_module
from hypershell.core.types import parse_delay, parse_time, UnitError
from hypershell.client import ClientApp, ClientThread
from hypershell.submit import SubmitApp
from hypershell.server import ServerApp
from hypershell.cluster import ClusterApp
from hypershell.task import TaskWaitApp, TaskRunApp
from hypershell.cluster.launch import client_options


# A start-up delay keeps every bare number as float() read it, and takes the duration forms unrounded.
DELAY_ACCEPTED: Final[List[tuple[str, float]]] = [
    ('30', 30.0),
    ('0', 0.0),
    ('0.5', 0.5),
    ('-600', -600.0),
    ('-0.5', -0.5),
    ('600.25', 600.25),
    ('0.1', 0.1),
    ('30s', 30.0),
    ('1.5s', 1.5),
    ('10m', 600.0),
    ('-10m', -600.0),
    ('1h30m', 5400.0),
    ('-1h30m', -5400.0),
    ('0.1h', 360.0),
    ('1:30', 90.0),
    ('-1:30', -90.0),
    ('1:30:00', 5400.0),
    ('1-12', 129600.0),
    ('-1-12', -129600.0),
    (' -2s ', -2.0),
]

# Bare numbers that type=float read before: each still reads bit for bit the same.
DELAY_BARE_NUMBERS: Final[List[str]] = ['30', '0', '0.5', '-600', '-0.5', '600.25', '0.1', '-0', '-0.0',
                                        '123456789.123456789', '0.000001']

# Not a duration in full, or a sign other than one leading '-', or a spelling only float() took.
DELAY_REFUSED: Final[List[str]] = [
    '5MB', '1hr', '2hours', '1w', '30m1h', '--10m', '- 10m', '-', '', '+5', '+10m', '.5', '5.', '1e3', '1E3',
    'inf', '-inf', 'nan', '1_000', '1h 30m', '1-', '-1-',
]


@mark.unit
@mark.parametrize('value, expected', DELAY_ACCEPTED)
def test_delay_accepted(value: str, expected: float) -> None:
    """Each accepted delay is read as its exact number of seconds, sign kept, fraction kept."""
    assert parse_delay(value) == expected


@mark.unit
@mark.parametrize('value', DELAY_BARE_NUMBERS)
def test_delay_bare_number_unchanged(value: str) -> None:
    """A bare number reads exactly as float() reads it, negative zero included."""
    assert repr(parse_delay(value)) == repr(float(value))


@mark.unit
@mark.parametrize('value', DELAY_REFUSED)
def test_delay_refused_names_value_forms_and_random_form(value: str) -> None:
    """A refused delay gives the duration message for the same text, then names the random form."""
    with raises(UnitError) as exc_info:
        parse_delay(value)
    message = str(exc_info.value)
    with raises(UnitError) as time_info:
        parse_time(value)
    assert message.startswith(str(time_info.value))
    assert repr(value) in message and '1h30m' in message and '--delay-start=-10m' in message


@mark.unit
def test_delay_too_large_refused() -> None:
    """A delay too large for a float is refused, not raised as an overflow."""
    with raises(UnitError, match='--delay-start=-10m'):
        parse_delay('9' * 400)


@mark.unit
@mark.parametrize('value, expected', [('90', 90), ('1.5', 2), ('0.5s', 1), ('1h30m', 5400), ('1:30', 90)])
def test_time_still_rounds_up(value: str, expected: int) -> None:
    """The whole-second parser shares the matcher and still rounds up to whole seconds."""
    assert parse_time(value) == expected


DELAY_APPS: Final[List[type]] = [ClientApp, ClusterApp]


@mark.unit
@mark.parametrize('app', DELAY_APPS)
@mark.parametrize('argv, expected', [
    (['--delay-start=-10m'], -600.0),
    (['--delay-start=-1:30'], -90.0),
    (['--delay-start=1.5s'], 1.5),
    (['-d-10m'], -600.0),
    (['-d', '-600'], -600.0),
    (['--delay-start=-600'], -600.0),
    (['-d', '-0.5'], -0.5),
    (['-d', '0.5'], 0.5),
    (['-d', '30'], 30.0),
    (['-d', '30s'], 30.0),
    (['-d', '1:30'], 90.0),
    (['-d', '1-12'], 129600.0),
    ([], 0),
])
def test_delay_option_parsed(app: type, argv: List[str], expected: float) -> None:
    """The delay option of the client and the cluster reads the fixed and random forms."""
    assert app.interface.parse_args(argv).delay_start == expected


@mark.unit
@mark.skipif(sys.version_info < (3, 14), reason='argparse takes a separate -10m for an option before 3.14')
@mark.parametrize('app', DELAY_APPS)
def test_delay_option_random_form_two_words(app: type) -> None:
    """Where argparse passes it on, the random form also reads as a separate word."""
    assert app.interface.parse_args(['-d', '-10m']).delay_start == -600.0


@mark.unit
@mark.parametrize('app', DELAY_APPS)
@mark.parametrize('argv, value', [(['-d', '1hr'], '1hr'), (['-d', '5MB'], '5MB'), (['--delay-start=-1hr'], '-1hr'),
                                  (['-d', '1e3'], '1e3'), (['-d', '+5'], '+5'), (['-d', '.5'], '.5')])
def test_delay_option_refused(app: type, argv: List[str], value: str) -> None:
    """The delay option refuses a non-duration, naming the value, the forms and the random form."""
    with raises(ArgumentError) as exc_info:
        app.interface.parse_args(argv)
    message = str(exc_info.value)
    assert repr(value) in message and '1h30m' in message and '--delay-start=-10m' in message


@mark.unit
@mark.parametrize('text, sleeps, draws', [
    ('2s', 2.0, None),
    ('0.5', 0.5, None),
    ('1.5s', 1.5, None),
    ('--delay-start=-2s', 0.75, 2.0),
    ('--delay-start=-1:30', 0.75, 90.0),
    ('0', None, None),
])
def test_delay_waits(monkeypatch, text: str, sleeps: float, draws: float) -> None:
    """A fixed delay sleeps its value; a random one sleeps a draw between zero and its magnitude."""
    slept, drawn = [], []
    monkeypatch.setattr(client_module.time, 'sleep', slept.append)
    monkeypatch.setattr(client_module.random, 'uniform', lambda low, high: drawn.append((low, high)) or 0.75)
    argv = [text] if text.startswith('--') else ['-d', text]
    ClientThread.wait_start(SimpleNamespace(delay_start=ClientApp.interface.parse_args(argv).delay_start))
    assert slept == ([] if sleeps is None else [sleeps])
    assert drawn == ([] if draws is None else [(0, draws)])


# The options every launch passes besides the delay, as `hs cluster` defaults give them.
LAUNCH_ARGS: Final[Dict[str, Any]] = {
    'num_threads': 1, 'bundlesize': 1, 'bundlewait': 5, 'template': '{}', 'task_signalwait': 10,
    'no_confirm': False, 'capture': False, 'monitor': False, 'client_cores': None, 'client_memory': None,
    'client_timeout': None, 'task_timeout': None, 'ratelimit': None, 'tls_enabled': True, 'zone': None,
}


def launched_delay(delay_start: float) -> str:
    """The delay word on the client command line a launch builds."""
    words = client_options(('node-1', 50001), delay_start=delay_start, **LAUNCH_ARGS)
    return words[words.index('-d') + 1]


@mark.unit
@mark.parametrize('delay_start, word', [(-600.0, '-600.0'), (0.5, '0.5'), (1e-05, '0.00001'),
                                        (-1e-05, '-0.00001'), (1e16, '10000000000000000'), (0, '0'),
                                        (-0.0, '-0.0'), (30.0, '30.0'), (-1.5, '-1.5')])
def test_launched_delay_is_plain_decimal(delay_start: float, word: str) -> None:
    """The launched delay is a plain decimal that float() and the client option both read back exactly."""
    assert launched_delay(delay_start) == word
    assert 'e' not in word.lower()
    assert float(word) == delay_start
    assert ClientApp.interface.parse_args(['-d', word]).delay_start == delay_start


@mark.unit
@mark.parametrize('argv, word', [(['--delay-start=-10m'], '-600.0'), (['-d', '1.5s'], '1.5'),
                                 (['-d', '-600'], '-600.0'), ([], '0')])
def test_cluster_delay_launched_as_seconds(argv: List[str], word: str) -> None:
    """The cluster hands a launched client its delay as bare seconds, a random delay as a negative number."""
    assert launched_delay(ClusterApp.interface.parse_args(argv).delay_start) == word


# Each whole-second duration option as (app, argv before the option, option, namespace attribute).
SECONDS_OPTIONS: Final[List[tuple[type, List[str], str, str]]] = [
    (SubmitApp, [], '-w', 'bundlewait'),
    (SubmitApp, [], '--bundlewait', 'bundlewait'),
    (ServerApp, [], '-w', 'bundlewait'),
    (ServerApp, [], '-P', 'autoscaling_period'),
    (ServerApp, [], '-Q', 'poll'),
    (ClientApp, [], '-w', 'bundlewait'),
    (ClientApp, [], '-S', 'task_signalwait'),
    (ClusterApp, [], '-w', 'bundlewait'),
    (ClusterApp, [], '-S', 'task_signalwait'),
    (ClusterApp, [], '-P', 'autoscaling_period'),
    (ClusterApp, [], '-Q', 'poll'),
    (TaskWaitApp, ['01a1d8c0-2df4-7ee2-89f0-891d777efb5d'], '-n', 'interval'),
    (TaskRunApp, ['echo', 'a'], '-n', 'interval'),
]


@mark.unit
@mark.parametrize('app, args, option, dest', SECONDS_OPTIONS)
@mark.parametrize('value, expected', [('30s', 30), ('5m', 300), ('1h30m', 5400), ('1.5h', 5400), ('1:30', 90),
                                      ('1:30:00', 5400), ('1-12', 129600), ('90', 90), ('0.5', 1), ('0', 0)])
def test_seconds_option_parsed(app: type, args: List[str], option: str, dest: str, value: str, expected: int) -> None:
    """Every whole-second duration option reads seconds, units and Slurm times, rounded up."""
    assert getattr(app.interface.parse_args([*args, option, value]), dest) == expected


@mark.unit
@mark.parametrize('app, args, option, dest', SECONDS_OPTIONS)
@mark.parametrize('value', ['5MB', '1hr', '2hours', '1w', '30m1h', '-5', '+60', '-5m'])
def test_seconds_option_refused(app: type, args: List[str], option: str, dest: str, value: str) -> None:
    """Every whole-second duration option refuses a non-duration or a signed value, naming it and the forms."""
    if option.startswith('--'):
        argv = [f'{option}={value}']
    elif value == '-5m':
        argv = [f'{option}{value}']  # Attached, since a separate -5m is taken for an option before Python 3.14.
    else:
        argv = [option, value]
    with raises(ArgumentError) as exc_info:
        app.interface.parse_args([*args, *argv])
    message = str(exc_info.value)
    assert repr(value) in message and '1h30m' in message


# Each configuration key behind an option, its environment variable, and what reads it: (app, args, dest).
SECONDS_KEYS: Final[List[tuple[str, str, str, List[tuple[str, List[str], str]]]]] = [
    ('autoscale', 'period', '5m', [('ServerApp', [], 'autoscaling_period'),
                                   ('ClusterApp', [], 'autoscaling_period')]),
    ('submit', 'bundlewait', '30s', [('SubmitApp', [], 'bundlewait'), ('ServerApp', [], 'bundlewait'),
                                     ('ClientApp', [], 'bundlewait'), ('ClusterApp', [], 'bundlewait')]),
    ('task', 'signalwait', '5s', [('ClientApp', [], 'task_signalwait'), ('ClusterApp', [], 'task_signalwait')]),
    ('server', 'poll', '1m', [('ServerApp', [], 'poll'), ('ClusterApp', [], 'poll')]),
]

# Defaults are read when the app modules are imported, so each read runs in a fresh interpreter.
READ_DEFAULTS: Final[str] = """\
import sys, json
from hypershell.submit import SubmitApp
from hypershell.server import ServerApp
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
reads = json.loads(sys.argv[1])
print(json.dumps([getattr(globals()[app].interface.parse_args(args), dest) for app, args, dest in reads]))
"""


def read_defaults(reads: List[tuple[str, List[str], str]]) -> tuple[int, Any, str]:
    """The parsed value of each (app, args, dest) under the current site and environment."""
    rc, stdout, stderr = main([sys.executable, '-c', READ_DEFAULTS, json.dumps(reads)])
    return rc, (json.loads(stdout) if rc == exit_status.success else None), stderr


def write_config(temp_site: Path, monkeypatch, text: str) -> None:
    """Write the site configuration file and read it (the suite reads none by default)."""
    config_file = Path(temp_site) / 'config.toml'
    config_file.write_text(text, encoding='utf-8')
    monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))


@mark.integration
@mark.parametrize('section, key, value, reads', SECONDS_KEYS)
def test_seconds_key_from_environment(temp_site: Path, monkeypatch, section: str, key: str, value: str,
                                      reads: List[tuple[str, List[str], str]]) -> None:
    """A duration string in the environment reaches every command that reads the key, as seconds."""
    monkeypatch.setenv(f'HYPERSHELL_{section.upper()}_{key.upper()}', value)
    rc, values, stderr = read_defaults(reads)
    assert rc == exit_status.success, stderr
    assert values == [parse_time(value)] * len(reads)


@mark.integration
@mark.parametrize('section, key, value, reads', SECONDS_KEYS)
def test_seconds_key_from_config_file(temp_site: Path, monkeypatch, section: str, key: str, value: str,
                                      reads: List[tuple[str, List[str], str]]) -> None:
    """A duration string in a configuration file reaches every command that reads the key, as seconds."""
    write_config(temp_site, monkeypatch, f'[{section}]\n{key} = "{value}"\n')
    rc, values, stderr = read_defaults(reads)
    assert rc == exit_status.success, stderr
    assert values == [parse_time(value)] * len(reads)


@mark.integration
@mark.parametrize('number', ['7', '0.5'])
@mark.parametrize('section, key, value, reads', SECONDS_KEYS)
def test_seconds_key_number_unchanged(temp_site: Path, monkeypatch, section: str, key: str, value: str,
                                      reads: List[tuple[str, List[str], str]], number: str) -> None:
    """A number in the environment or a configuration file keeps its value and type."""
    expected = json.loads(number)
    monkeypatch.setenv(f'HYPERSHELL_{section.upper()}_{key.upper()}', number)
    rc, values, stderr = read_defaults(reads)
    assert rc == exit_status.success, stderr
    assert values == [expected] * len(reads) and all(type(v) is type(expected) for v in values)
    monkeypatch.delenv(f'HYPERSHELL_{section.upper()}_{key.upper()}')
    write_config(temp_site, monkeypatch, f'[{section}]\n{key} = {number}\n')
    rc, values, stderr = read_defaults(reads)
    assert rc == exit_status.success, stderr
    assert values == [expected] * len(reads) and all(type(v) is type(expected) for v in values)


@mark.integration
@mark.parametrize('section, key, value, reads', SECONDS_KEYS)
def test_seconds_key_refused(temp_site: Path, monkeypatch, section: str, key: str, value: str,
                             reads: List[tuple[str, List[str], str]]) -> None:
    """A bad duration string in the environment stops the command with the value and the forms."""
    monkeypatch.setenv(f'HYPERSHELL_{section.upper()}_{key.upper()}', '5MB')
    rc, _, stderr = main(['hs', 'cluster', str(Path(temp_site) / 'missing.in'), '--no-db'])
    assert rc == exit_status.bad_argument
    assert "'5MB'" in stderr and '1h30m' in stderr


@mark.unit
def test_cluster_launches_whole_seconds() -> None:
    """The cluster hands a launched client bare whole seconds, read alike by earlier and current clients."""
    args = ClusterApp.interface.parse_args(['--delay-start=-10m', '-w', '1m', '-S', '5s'])
    words = client_options(('node-1', 50001), num_threads=1, bundlesize=1, bundlewait=args.bundlewait,
                           template='{}', delay_start=args.delay_start, task_signalwait=args.task_signalwait,
                           **{k: v for k, v in LAUNCH_ARGS.items()
                              if k not in ('num_threads', 'bundlesize', 'bundlewait', 'template', 'task_signalwait')})
    pairs = {words[i]: words[i + 1] for i in range(len(words) - 1) if words[i] in ('-w', '-S', '-d')}
    assert pairs == {'-w': '60', '-S': '5', '-d': '-600.0'}
    assert (int(pairs['-w']), int(pairs['-S']), float(pairs['-d'])) == (60, 5, -600.0)
    client = ClientApp.interface.parse_args(words[words.index('-H'):])
    assert (client.bundlewait, client.task_signalwait, client.delay_start) == (60, 5, -600.0)
