# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The duration keys no option reads take every duration form, from a file or the environment.

A value that is not a duration stops the command that reads it at start, before it binds a port
or launches a client, with a configuration error naming the key, where it came from, and the forms
a duration takes. Each command runs in its own process group; one still running at the bound is
stopped and the test fails.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Optional, Tuple

# Standard libs
import shutil
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace

# Internal libs
from tests import create_taskfile
from tests.conftest import free_port
from tests.test_env_config import KEY, child_env, run_bounded, start_server, stop_server, assert_refused


BOUND: Final[float] = 30
"""Seconds a command refused at start may take."""


# The configuration modules are imported in each unit test below, so the command tests in this file
# collect, and can be checked red, against a build without `config_seconds`.
def layered(**server: Any) -> Configuration:
    """A configuration of the defaults with `server` keys set by the environment layer."""
    from hypershell.core.config import default
    return Configuration(default=Namespace(default), env=Namespace({'server': server}))


SECONDS: Final[Dict[str, Tuple[Any, bool, Any]]] = {
    'number': (600, False, 600),
    'fraction': (9.5, False, 9.5),
    'units': ('10m', False, 600),
    'fractional-units': ('1.5s', False, 1.5),
    'slurm': ('1:30:00', False, 5400),
    'spaces': (' 90 ', False, 90),
    'whole-rounds-up': ('1.2s', True, 2),
    'whole-units': ('5m', True, 300),
}
"""For each case: the value given, whether whole seconds are asked for, and the seconds read."""


@mark.unit
@mark.parametrize('case', list(SECONDS))
def test_config_seconds(case: str) -> None:
    """A number is used as given; text takes the duration forms, exact unless whole seconds are asked for."""
    from hypershell.core.config import config_seconds
    value, whole, seconds = SECONDS[case]
    result = config_seconds('server', 'evict', base=layered(evict=value), whole=whole)
    assert result == seconds and type(result) is type(seconds)


@mark.unit
@mark.parametrize('value', ['10x', '5MB', '', '-5m', 'true'])
def test_config_seconds_refused(value: str) -> None:
    """Text that is not a duration is refused, naming the key, the variable and the forms."""
    from hypershell.core.config import config_seconds, ConfigurationError
    with pytest.raises(ConfigurationError, match=r'server\.evict: expected a duration .*from 10 s to 2,147,483,647 s, '
                                                 r'given .* \(from: HYPERSHELL_SERVER_EVICT\)'):
        config_seconds('server', 'evict', base=layered(evict=value))


@mark.unit
def test_config_seconds_variable_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `_env` form of a duration key is read through the variable it names."""
    from hypershell.core.config import config_seconds, ConfigurationError
    monkeypatch.setenv('MY_EVICT', '2m')
    assert config_seconds('server', 'evict', base=layered(evict_env='MY_EVICT')) == 120
    monkeypatch.delenv('MY_EVICT')
    with pytest.raises(ConfigurationError, match='server.evict_env names MY_EVICT, which is not set'):
        config_seconds('server', 'evict', base=layered(evict_env='MY_EVICT'))


def write_config(site: Path, text: str) -> Path:
    """Write a configuration file in `site` and return its path."""
    filepath = site / 'config.toml'
    filepath.write_text(text, encoding='utf-8')
    return filepath


@mark.integration
@mark.parametrize('source', ['file', 'environment'])
def test_server_evict_units(temp_site: Path, source: str) -> None:
    """`server.evict` given with units runs a server and a client to the end."""
    site = Path(str(temp_site))
    if source == 'file':
        env = child_env(site, HYPERSHELL_SERVER_AUTH=KEY,
                        HYPERSHELL_CONFIG_FILE=str(write_config(site, '[server]\nevict = "10m"\n')))
    else:
        env = child_env(site, HYPERSHELL_SERVER_AUTH=KEY, HYPERSHELL_SERVER_EVICT='10m')
    server = start_server(site, env, '--no-db', str(create_taskfile(site, ['echo served'])))
    try:
        status, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1'], env)
    finally:
        server_status = stop_server(server)
    assert status == exit_status.success, stderr
    assert server_status == exit_status.success, (site / 'server.log').read_text()
    assert stdout == 'served'


def relay_env(site: Path, **variables: Optional[str]) -> Dict[str, str]:
    """Environment for an `hs relay` in `site` on a free port, with an upstream that is not there."""
    return child_env(site, HYPERSHELL_DATABASE_FILE=None,
                     HYPERSHELL_RELAY_BIND='127.0.0.1', HYPERSHELL_RELAY_PORT=str(free_port()),
                     HYPERSHELL_RELAY_UPSTREAM_HOST='127.0.0.1', HYPERSHELL_RELAY_UPSTREAM_PORT=str(free_port()),
                     HYPERSHELL_RELAY_UPSTREAM_AUTH=KEY, HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED='false',
                     HYPERSHELL_RELAY_SIZE='1', HYPERSHELL_RELAY_LAUNCHER=shutil.which('true'), **variables)


REFUSED: Final[Dict[str, Tuple[List[str], str, str, Optional[str]]]] = {
    'server.evict': (['hs', 'server', '--no-db'], 'HYPERSHELL_SERVER_EVICT', '10x', None),
    'client.heartrate': (['hs', 'client', '-N', '1'], 'HYPERSHELL_CLIENT_HEARTRATE', '10x', None),
    'relay.upstream.deadline': (['hs', 'relay'], 'HYPERSHELL_RELAY_UPSTREAM_DEADLINE', '10x', None),
    'relay.upstream.evict_hint': (['hs', 'relay'], '', '', '[relay.upstream]\nevict_hint = "5MB"\n'),
    'relay.poll': (['hs', 'relay'], 'HYPERSHELL_RELAY_POLL', '5MB', None),
}
"""For each key: the command that reads it, and the variable and value, or the file, that gives it."""


@mark.integration
@mark.parametrize('path', list(REFUSED), ids=list(REFUSED))
def test_duration_refused_at_start(temp_site: Path, path: str) -> None:
    """A value that is not a duration stops the command at start, naming the key and its source."""
    site = Path(str(temp_site))
    argv, name, value, text = REFUSED[path]
    variables = {name: value} if name else {
        'HYPERSHELL_CONFIG_FILE': str(write_config(site, text))}
    if argv[1] == 'relay':
        env = relay_env(site, **variables)
    else:
        env = child_env(site, HYPERSHELL_SERVER_AUTH=KEY, **variables)
    status, _, stderr = run_bounded(site, argv, env, bound=BOUND)
    source = name or str(site / 'config.toml')
    assert_refused(status, stderr, path, source, 'Duration')


RELAY_UNITS: Final[Dict[str, Tuple[str, str, Any]]] = {
    'server.evict': ('10m', 'evict', 600),
    'relay.bundlewait': ('1.5s', 'bundlewait', 1.5),
    'relay.poll': ('2.5s', 'poll', 2.5),
    'relay.retention': ('2h', 'retention', 7200),
    'relay.timeout': ('1.2s', 'client_timeout', 2),
    'relay.upstream.heartrate': ('1m30s', 'heartrate', 90),
    'relay.upstream.evict_hint': ('3m', 'evict_hint', 180),
    'relay.upstream.deadline': ('2.25s', 'deadline', 2.25),
    'relay.upstream.backoff': ('0.75s', 'backoff', 0.75),
}
"""For each relay duration key: the value given in units, the `RelayThread` argument it becomes, and its seconds.

Only `relay.timeout` is rounded up to whole seconds; the others keep fractions.
"""

RELAY_RECORDER: Final[str] = '''\
import json
from types import SimpleNamespace
import hypershell.relay as relay_module
from hypershell.relay import RelayApp

built = []
thread = SimpleNamespace(start=lambda: None, stop=lambda: None, release=lambda: None)
RelayApp.bind_database = lambda self: None
for name in ('lock_database', 'store_utc', 'ensuredb', 'ensure_tables', 'join_waking',
             'install_drain_handlers', 'install_terminate_handler'):
    setattr(relay_module, name, lambda *args, **kwargs: None)
relay_module.RelayThread = lambda **options: built.append(options) or thread
RelayApp.from_cmdline([]).run()
print(json.dumps([{{name: options.get(name) for name in {names!r}}} for options in built]))
'''
"""Program a child interpreter runs to start `RelayApp` with `RelayThread` recorded, not built (nothing is served)."""


def relay_units_file(site: Path) -> Path:
    """Write a configuration file giving every relay duration key of `RELAY_UNITS` in units."""
    tables: Dict[str, List[str]] = {}
    for path, (value, _, _) in RELAY_UNITS.items():
        *section, name = path.split('.')
        tables.setdefault('.'.join(section), []).append(f'{name} = "{value}"')
    return write_config(site, ''.join(f'[{section}]\n' + '\n'.join(lines) + '\n'
                                      for section, lines in tables.items()))


@mark.integration
@mark.parametrize('source', ['file', 'environment'])
def test_relay_passes_duration_seconds(temp_site: Path, source: str) -> None:
    """The relay hands its parts the seconds of each duration key given in units, not the text as read."""
    import json
    import sys
    site = Path(str(temp_site))
    if source == 'file':
        variables = {'HYPERSHELL_CONFIG_FILE': str(relay_units_file(site))}
    else:
        variables = {'HYPERSHELL_' + path.replace('.', '_').upper(): value
                     for path, (value, _, _) in RELAY_UNITS.items()}
    env = relay_env(site, **variables)
    names = [name for _, name, _ in RELAY_UNITS.values()]
    status, stdout, stderr = run_bounded(site, [sys.executable, '-c', RELAY_RECORDER.format(names=names)],
                                         env, bound=BOUND)
    assert status == exit_status.success, stderr
    built = json.loads(stdout.splitlines()[-1])
    assert len(built) == 1
    expected = {name: seconds for _, name, seconds in RELAY_UNITS.values()}
    assert built[0] == expected
    assert {name: type(value) for name, value in built[0].items()} == {
        name: type(seconds) for name, seconds in expected.items()}
