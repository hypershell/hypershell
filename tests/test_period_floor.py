# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The scaling period must be greater than zero: `hs cluster`, `hs server` and `hs relay` refuse a
period of zero or less at start-up, on the command line, in `autoscale.period` and in its
environment variable, and read every positive period as before, a period below one second as one
second.

No test here starts a server, a relay or a client that a zero period could leave running: the
commands run in-process, or in a fresh interpreter (to read the key at import), with the serving
and launching entry points replaced by recorders. The one command-line drive runs with `--no-db`.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Tuple

# Standard libs
import os
import re
import sys
import json
import math
import secrets
from pathlib import Path
from types import SimpleNamespace
from subprocess import run, PIPE

# External libs
from pytest import mark, raises
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.core.config import config, AUTH_ENV
from hypershell.core.types import parse_period, UnitError
from hypershell.server import ServerApp
from hypershell.cluster import ClusterApp
from hypershell.relay import RelayApp
from tests import create_taskfile


DEADLINE: Final[float] = 120
"""Seconds any one command here may take."""

ZEROS: Final[List[str]] = ['0', '0s', '0m', '0:00', '0-0', '0.0']
"""A period of zero in each duration form."""

PERIOD_APPS: Final[Dict[str, type]] = {'server': ServerApp, 'cluster': ClusterApp}
"""Each command that takes -P/--period."""


def refusal_line(message: str) -> str:
    """The line of `message` that gives the floor (empty if none does)."""
    return next((line for line in message.splitlines() if 'greater than zero' in line), '')


def names_value(line: str, value: str) -> bool:
    """Whether `line` gives `value` as a whole word (so '0' is not found inside '0.0')."""
    return re.search(rf'(?<![\w.\-]){re.escape(value)}(?![\w.])', line) is not None


# --------------------------------------------------------------------------------------------------
# The option


@mark.unit
@mark.parametrize('spelling', ['short', 'long'])
@mark.parametrize('value', ZEROS)
@mark.parametrize('app', list(PERIOD_APPS))
def test_period_option_zero_refused(app: str, value: str, spelling: str) -> None:
    """A zero period on -P/--period is refused, naming the option, the value and the floor."""
    argv = ['-P', value] if spelling == 'short' else [f'--period={value}']
    with raises(ArgumentError) as exc_info:
        PERIOD_APPS[app].interface.parse_args(argv)
    line = refusal_line(str(exc_info.value))
    assert '-P/--period' in line and names_value(line, repr(value))


@mark.unit
@mark.parametrize('argv, expected', [(['-P', '1'], 1), (['-P', '0.5'], 1), (['--period=1s'], 1)])
@mark.parametrize('app', list(PERIOD_APPS))
def test_period_option_accepted(app: str, argv: List[str], expected: int) -> None:
    """A positive period on -P/--period reads as before, a fraction rounded up to a whole second."""
    period = PERIOD_APPS[app].interface.parse_args(argv).autoscaling_period
    assert period == expected and type(period) is int


# --------------------------------------------------------------------------------------------------
# The parser


PERIODS_REFUSED: Final[List[Any]] = [0, 0.0, -0.0, -5, -0.5, math.nan, -math.inf, False, '0', '0s', '0:00']
"""Each value that is not a period greater than zero, as a number and as a string."""


@mark.unit
@mark.parametrize('value', PERIODS_REFUSED, ids=[repr(value) for value in PERIODS_REFUSED])
def test_parse_period_refused(value: Any) -> None:
    """A value that is not greater than zero is refused, NaN included, naming the value and the floor."""
    with raises(UnitError) as exc_info:
        parse_period(value)
    message = str(exc_info.value)
    assert 'greater than zero' in message and names_value(message, repr(value))


PERIODS_ACCEPTED: Final[List[Tuple[Any, Any]]] = [(0.001, 1), (0.5, 1), (1, 1), (1.0, 1.0), (1.5, 1.5), (60, 60),
                                                  ('0.001', 1), ('0.5', 1), ('1.5', 2), ('5m', 300)]
"""Each positive period and its seconds: below one second is one second, a string is rounded up."""


@mark.unit
@mark.parametrize('value, expected', PERIODS_ACCEPTED, ids=[repr(value) for value, _ in PERIODS_ACCEPTED])
def test_parse_period_accepted(value: Any, expected: Any) -> None:
    """A positive period reads as one second at the least; a number of one or more is kept as given."""
    seconds = parse_period(value)
    assert seconds == expected and type(seconds) is type(expected)


# --------------------------------------------------------------------------------------------------
# The key, read at import, in a fresh interpreter


# Runs one command through the `hs` entry point with its serving and launching entry points replaced by recorders.
RUN_RECORDED: Final[str] = """\
import sys, json
from types import SimpleNamespace
import hypershell, hypershell.server, hypershell.cluster, hypershell.cluster.topology, hypershell.relay
recorded = []
def recorder(name):
    def record(*args, **options):
        recorded.append([name, options.get('period')])
        return SimpleNamespace(start=lambda: None, stop=lambda: None, release=lambda: None)
    return record
for module, names in [(hypershell.server, ['serve_from', 'serve_forever']),
                      (hypershell.cluster.topology, ['serve_with_sites']),
                      (hypershell.cluster, ['run_local', 'run_cluster', 'run_ssh']),
                      (hypershell.relay, ['RelayThread'])]:
    for name in names:
        setattr(module, name, recorder(name))
hypershell.relay.RelayApp.bind_database = lambda self: None
for name in ['lock_database', 'store_utc', 'ensuredb', 'ensure_tables', 'join_waking',
             'install_drain_handlers', 'install_terminate_handler']:
    setattr(hypershell.relay, name, lambda *args, **options: None)
status = hypershell.main(sys.argv[1:])
print(json.dumps({'status': status, 'recorded': recorded}))
"""

KEY_LAUNCHES: Final[Dict[str, Tuple[str, List[str]]]] = {
    'server-file': ('server', ['--no-tls', '{taskfile}', '--no-db']),
    'server-autoscaling': ('server', ['--no-tls', '--autoscaling', 'fixed']),
    'cluster-file': ('cluster', ['{taskfile}', '--no-db', '--no-tls']),
    'relay': ('relay', ['--no-tls']),
}
"""Each launch the key is tried with: the command and its arguments (the relay's site fleet recorded unbuilt)."""

KEY_ROUTES: Final[Dict[str, Tuple[str, str, str, str]]] = {
    'env-0': ('env', '0', 'autoscale.period', '0'),
    'env-neg5': ('env', '-5', 'autoscale.period', '-5'),
    'env-0s': ('env', '0s', '-P/--period', "'0s'"),
    'file-0': ('file', '0', 'autoscale.period', '0'),
    'file-neg5': ('file', '-5', 'autoscale.period', '-5'),
    'file-0s': ('file', '"0s"', '-P/--period', "'0s'"),
    'file-0.0': ('file', '0.0', 'autoscale.period', '0.0'),
    'env-nan': ('env', 'nan', '-P/--period', "'nan'"),
    'env-neginf': ('env', '-inf', '-P/--period', "'-inf'"),
    'env-neg0.0': ('env', '-0.0', 'autoscale.period', '-0.0'),
    'file-nan': ('file', 'nan', 'autoscale.period', 'nan'),
    'file-neginf': ('file', '-inf', 'autoscale.period', '-inf'),
    'file-neg0.0': ('file', '-0.0', 'autoscale.period', '-0.0'),
}
"""Each route a period not greater than zero takes: where it is set, as what, the name and value the error gives.

The configuration loader reads a number as a number, which no option type converts, so the error
names the key; a string reaches the option's type as its default, so the error names the option.
The relay has no option, so its error names the key either way. NaN, `-inf` and `-0.0` are not
greater than zero, so a floor that tests `seconds <= 0` fails here. A variable's value is text, and
`nan` and `-inf` as text are not durations: the refusal says so rather than giving the floor.
"""

NOT_DURATIONS: Final[List[str]] = ['env-nan', 'env-neginf']
"""The routes refused as not a duration rather than as not greater than zero."""


def not_duration_line(message: str) -> str:
    """The line of `message` that refuses a value as not a duration (empty if none does)."""
    return next((line for line in message.splitlines() if 'is not valid' in line), '')


def set_period(temp_site: Path, monkeypatch, where: str, text: str) -> None:
    """Set `autoscale.period` to `text` in the environment or in the site configuration file."""
    if where == 'env':
        monkeypatch.setenv('HYPERSHELL_AUTOSCALE_PERIOD', text)
    else:
        config_file = Path(temp_site) / 'config.toml'
        config_file.write_text(f'[autoscale]\nperiod = {text}\n', encoding='utf-8')
        monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))


def run_recorded(temp_site: Path, monkeypatch, app: str, args: List[str]) -> Tuple[int, Dict[str, Any], str]:
    """Run `app` with `args` in a fresh interpreter; its exit status, what it reports, and its log."""
    taskfile = create_taskfile(Path(temp_site), ['echo a'])
    monkeypatch.setenv(AUTH_ENV, secrets.token_hex(32))
    argv = [arg.format(taskfile=taskfile) for arg in args]
    proc = run([sys.executable, '-c', RUN_RECORDED, app, *argv],
               stdout=PIPE, stderr=PIPE, env=os.environ, timeout=DEADLINE)
    stdout, stderr = proc.stdout.decode('utf-8'), proc.stderr.decode('utf-8')
    report = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    return proc.returncode, report, stderr


@mark.integration
@mark.parametrize('route', list(KEY_ROUTES))
@mark.parametrize('launch', list(KEY_LAUNCHES))
def test_period_key_refused(temp_site: Path, monkeypatch, launch: str, route: str) -> None:
    """A period not greater than zero, or not a duration, stops the command before it serves or launches anything."""
    where, text, name, value = KEY_ROUTES[route]
    set_period(temp_site, monkeypatch, where, text)
    app, args = KEY_LAUNCHES[launch]
    rc, report, log = run_recorded(temp_site, monkeypatch, app, args)
    assert rc == exit_status.success, log  # The interpreter itself exits normally.
    assert report['status'] == exit_status.bad_argument, log
    assert report['recorded'] == []
    line = refusal_line(log) if route not in NOT_DURATIONS else not_duration_line(log)
    assert (name if app != 'relay' else 'autoscale.period') in line and names_value(line, value), log


AUTOSCALING_LAUNCHES: Final[Dict[str, Tuple[str, List[str], str]]] = {
    'server': ('server', ['--no-tls', '--autoscaling', 'fixed'], 'serve_with_sites'),
    'cluster': ('cluster', ['{taskfile}', '--no-tls', '--autoscaling', 'fixed'], 'run_cluster'),
    'relay': ('relay', ['--no-tls'], 'RelayThread'),
}
"""Each autoscaling launch: the command, its arguments, and the entry point that takes the period."""

KEY_PERIODS: Final[Dict[str, Tuple[str, Any]]] = {
    '0.001': ('0.001', 1),
    '0.5': ('0.5', 1),
    '1': ('1', 1),
    '1.5': ('1.5', 1.5),
    '2': ('2', 2),
}
"""Each positive number in the key and the seconds the autoscaler gets: one at the least, else as given."""


@mark.integration
@mark.parametrize('period', list(KEY_PERIODS))
@mark.parametrize('where', ['env', 'file'])
@mark.parametrize('launch', list(AUTOSCALING_LAUNCHES))
def test_period_key_accepted(temp_site: Path, monkeypatch, launch: str, where: str, period: str) -> None:
    """A positive number in the key starts and reaches the autoscaler as given, one second at the least."""
    app, args, entry = AUTOSCALING_LAUNCHES[launch]
    text, expected = KEY_PERIODS[period]
    set_period(temp_site, monkeypatch, where, text)
    rc, report, log = run_recorded(temp_site, monkeypatch, app, args)
    assert rc == exit_status.success, log
    assert report['status'] == exit_status.success, log
    assert report['recorded'] == [[entry, expected]]
    assert type(report['recorded'][0][1]) is type(expected)


# --------------------------------------------------------------------------------------------------
# The command line, driven for real


@mark.integration
def test_period_cli_zero_refused(temp_site: Path) -> None:
    """`hs cluster -P 0` exits with a usage error naming the option and the floor, and runs no task."""
    taskfile = create_taskfile(Path(temp_site), ['a'])
    proc = run(['hs', 'cluster', str(taskfile), '--no-db', '--no-tls', '-t', 'echo {}', '-P', '0'],
               stdout=PIPE, stderr=PIPE, env=os.environ, timeout=DEADLINE)
    log = proc.stderr.decode('utf-8')
    assert proc.returncode == exit_status.bad_argument, log
    assert proc.stdout.decode('utf-8').split() == []
    line = refusal_line(log)
    assert '-P/--period' in line and names_value(line, "'0'"), log


# --------------------------------------------------------------------------------------------------
# The relay


RELAY_ZEROS: Final[List[Any]] = [0, 0.0, -0.0, -5, math.nan, -math.inf, '0', '0s', '0:00']
"""A zero or negative autoscale.period, as a number and as a string."""


@mark.unit
@mark.parametrize('period', RELAY_ZEROS, ids=[repr(period) for period in RELAY_ZEROS])
def test_relay_period_zero_refused(monkeypatch, tmp_path: Path, period: Any) -> None:
    """The relay refuses a zero or negative period before building anything, naming the key, value and floor."""
    import hypershell.relay as relay_module
    monkeypatch.setitem(config['autoscale'], 'period', period)
    with raises(ArgumentError) as exc_info:
        RelayApp.from_cmdline([]).check_args()
    message = str(exc_info.value)
    assert message.startswith('autoscale.period: ') and 'greater than zero' in message
    assert names_value(message, repr(period))
    built: List[Dict[str, Any]] = []
    thread = SimpleNamespace(start=lambda: None, stop=lambda: None, release=lambda: None)
    monkeypatch.setitem(config['database'], 'file', str(tmp_path / 'relay.db'))
    monkeypatch.setattr(RelayApp, 'bind_database', lambda self: None)
    for name in ('lock_database', 'store_utc', 'ensuredb', 'ensure_tables', 'join_waking',
                 'install_drain_handlers', 'install_terminate_handler'):
        monkeypatch.setattr(relay_module, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(relay_module, 'RelayThread', lambda **options: built.append(options) or thread)
    assert RelayApp.main([]) == exit_status.bad_argument
    assert built == []


RELAY_PERIODS: Final[List[Tuple[Any, Any]]] = [(1, 1), (1.5, 1.5), (0.5, 1), (0.001, 1), ('1s', 1), ('0.5', 1)]
"""Each positive autoscale.period and the seconds the relay reads."""


@mark.unit
@mark.parametrize('period, expected', RELAY_PERIODS, ids=[repr(period) for period, _ in RELAY_PERIODS])
def test_relay_period_accepted(monkeypatch, period: Any, expected: Any) -> None:
    """The relay reads a positive period as one second at the least: a string rounded up, a larger number as given."""
    monkeypatch.setitem(config['autoscale'], 'period', period)
    app = RelayApp.from_cmdline([])
    app.check_args()
    assert app.site_period == expected and type(app.site_period) is type(expected)
