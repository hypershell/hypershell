# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs cluster` reads `server.evict` in every mode, and refuses a value `hs server` refuses before it starts.

Each reading runs `hs cluster` through the `hs` entry point in a child interpreter whose
`ServerThread` records the eviction period it is given and stops there, and whose database and
port-probe steps are recorded; so nothing binds, and nothing is launched, in any outcome.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List

# Standard libs
import sys
import json
from pathlib import Path
from datetime import timedelta
from subprocess import run, DEVNULL

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import create_taskfile
from tests.conftest import free_port
from tests.test_env_config import KEY, DEADLINE, child_env, run_bounded


EVICT_RECORDED: Final[str] = """\
import sys, json, inspect
import hypershell, hypershell.server, hypershell.cluster
steps, evict = [], []
for name in ('initdb', 'checkdb', 'find_available_ports'):
    def recorder(*args, _name=name, _original=getattr(hypershell.cluster, name), **options):
        steps.append(_name)
        return _original(*args, **options)
    setattr(hypershell.cluster, name, recorder)
default = inspect.signature(hypershell.server.ServerThread.__init__).parameters['evict_after'].default
def record(self, *args, **options):
    evict.append(options.get('evict_after', default))
    raise RuntimeError('recorded')
hypershell.server.ServerThread.__init__ = record
status = hypershell.main(sys.argv[1:])
print(json.dumps({'status': status, 'steps': steps, 'evict': evict}))
"""
"""Program a child runs: `hs` with every ServerThread recording its eviction period and stopping there."""

MODES: Final[Dict[str, List[str]]] = {
    'local': ['--no-db'],
    'launcher': ['--launcher=true', '--no-db'],
    'mpi': ['--mpi', '--no-db'],
    'ssh': ['--ssh', 'localhost', '--no-db'],
    'autoscaling': ['--autoscaling', 'fixed', '--launcher=true'],
}
"""Options of each `hs cluster` mode, beside the task file and `-H 127.0.0.1`."""


def recorded(site: Path, mode: str, **variables: Any) -> Dict[str, Any]:
    """Run `hs cluster` in `mode` in a recording child in `site`; its status, steps and periods."""
    create_taskfile(site, ['echo hi'], 't.in')
    argv = ['cluster', 't.in', '-H', '127.0.0.1', *MODES[mode]]
    result = run([sys.executable, '-c', EVICT_RECORDED, *argv], cwd=site, env=child_env(site, **variables),
                 stdin=DEVNULL, capture_output=True, text=True, timeout=DEADLINE)
    lines = result.stdout.strip().splitlines()
    assert result.returncode == 0 and lines, result.stderr
    return {**json.loads(lines[-1]), 'stderr': result.stderr}


def evict_layer(site: Path, layer: str, value: str) -> Dict[str, str]:
    """Variables that set `server.evict` to `value` from the environment, or from the site's file.

    In a file (`file`) a value of digits is a TOML integer and any other is a string; `toml` writes
    the value as TOML as given (e.g., `1e300`, a float).
    """
    if layer == 'env':
        return {'HYPERSHELL_SERVER_EVICT': value}
    path = site / 'config.toml'
    raw = layer == 'toml' or value.isdigit()
    path.write_text(f'[server]\nevict = {value}\n' if raw else f'[server]\nevict = {value!r}\n')
    return {'HYPERSHELL_CONFIG_FILE': str(path)}


@mark.integration
@mark.parametrize('mode', list(MODES))
@mark.parametrize('layer', ['env', 'file'])
def test_cluster_evict_reaches_server(temp_site, layer: str, mode: str) -> None:
    """`server.evict` (any duration form) is the period of the server `hs cluster` runs, in every mode."""
    site = Path(str(temp_site))
    seen = recorded(site, mode, **evict_layer(site, layer, '1m30s'))
    assert seen['evict'] == [90], seen['stderr']


@mark.integration
@mark.parametrize('value, seconds', [('10', 10), ('10.5', 10.5)])
def test_cluster_evict_exact(temp_site, value: str, seconds: float) -> None:
    """The period is used as given: the 10-second floor is inclusive, and a fraction is kept."""
    site = Path(str(temp_site))
    seen = recorded(site, 'local', HYPERSHELL_SERVER_EVICT=value)
    assert seen['evict'] == [seconds] and type(seen['evict'][0]) is type(seconds), seen['stderr']


@mark.integration
@mark.parametrize('mode', list(MODES))
def test_cluster_evict_default(temp_site, mode: str) -> None:
    """With no `server.evict` the server keeps the built-in 600 seconds."""
    site = Path(str(temp_site))
    seen = recorded(site, mode, HYPERSHELL_SERVER_EVICT=None)
    assert seen['evict'] == [600], seen['stderr']


EVICT_CAP: Final[int] = timedelta.max.days * 86_400
"""Longest period the heartbeat monitor's intervals hold, in whole days of seconds."""

WIDE: Final[str] = '1' + '0' * 400
"""A whole number of seconds wider than a float."""

REFUSALS: Final[Dict[str, Any]] = {
    'abc': ('env', 'abc', exit_status.bad_config, "server.evict: Duration 'abc' is not valid"),
    'under-floor': ('env', '5', exit_status.runtime_error, 'Evict period must be greater than 10 seconds: given 5'),
    'fraction-under': ('env', '9.5s', exit_status.runtime_error,
                       'Evict period must be greater than 10 seconds: given 9.5'),
    'file-under-floor': ('file', '5', exit_status.runtime_error,
                         'Evict period must be greater than 10 seconds: given 5'),
    'over-cap': ('env', str(EVICT_CAP + 1), exit_status.runtime_error,
                 f'Evict period must be at most {EVICT_CAP} seconds: given {EVICT_CAP + 1}'),
    'file-float-huge': ('toml', '1e300', exit_status.runtime_error,
                        f'Evict period must be at most {EVICT_CAP} seconds: given 1e+300'),
    'int-huge': ('env', WIDE, exit_status.runtime_error, f'Evict period must be at most {EVICT_CAP} seconds: given {WIDE}'),
    'file-int-huge': ('file', WIDE, exit_status.runtime_error,
                      f'Evict period must be at most {EVICT_CAP} seconds: given {WIDE}'),
    'days-huge': ('env', f'{WIDE}d', exit_status.runtime_error,
                  f'Evict period must be at most {EVICT_CAP} seconds: given {int(WIDE) * 86_400}'),
}
"""A value `hs server` refuses: its layer, value, exit status and message."""


@mark.integration
@mark.parametrize('mode', list(MODES))
@mark.parametrize('case', list(REFUSALS))
def test_cluster_evict_refused(temp_site, case: str, mode: str) -> None:
    """A value `hs server` refuses stops `hs cluster` with its message and status before any database,
    port or server."""
    site = Path(str(temp_site))
    layer, value, status, message = REFUSALS[case]
    seen = recorded(site, mode, HYPERSHELL_SERVER_PORT=None, **evict_layer(site, layer, value))
    assert seen['status'] == status, seen['stderr']
    assert message in seen['stderr'], seen['stderr']
    assert seen['steps'] == [] and seen['evict'] == [], seen
    assert 'Traceback' not in seen['stderr'], seen['stderr']


@mark.integration
@mark.parametrize('case', list(REFUSALS))
def test_server_evict_refused(temp_site, case: str) -> None:
    """`hs server` refuses each of these values with that message and status, and never listens."""
    site = Path(str(temp_site))
    layer, value, status, message = REFUSALS[case]
    create_taskfile(site, ['echo hi'], 't.in')
    env = child_env(site, HYPERSHELL_SERVER_AUTH=KEY, **evict_layer(site, layer, value))
    code, _, stderr = run_bounded(site, ['hs', 'server', 't.in', '--no-db', '--no-tls', '-H', '127.0.0.1',
                                         '-p', str(free_port())], env, bound=30)
    assert code == status and message in stderr, stderr
    assert 'Server listening' not in stderr, stderr


@mark.integration
def test_cluster_evict_floor_cli(temp_site) -> None:
    """A real `hs cluster` with `server.evict` under the floor exits as `hs server` does, never listening."""
    site = Path(str(temp_site))
    create_taskfile(site, ['echo hi'], 't.in')
    env = child_env(site, HYPERSHELL_SERVER_EVICT='5')
    code, _, stderr = run_bounded(site, ['hs', 'cluster', 't.in', '--no-db', '--no-tls', '-H', '127.0.0.1',
                                         '-p', str(free_port())], env, bound=60)
    assert code == exit_status.runtime_error, stderr
    assert 'Evict period must be greater than 10 seconds: given 5' in stderr, stderr
    assert 'Server listening' not in stderr, stderr


@mark.integration
@mark.parametrize('command', ['server', 'cluster'])
def test_evict_over_cap_cli(temp_site, command: str) -> None:
    """A file's `evict = 1e300` stops a real `hs server` or `hs cluster` with exit 5 and the refusal,
    no traceback written, never listening."""
    site = Path(str(temp_site))
    create_taskfile(site, ['echo hi'], 't.in')
    env = child_env(site, HYPERSHELL_SERVER_AUTH=KEY, **evict_layer(site, 'toml', '1e300'))
    code, _, stderr = run_bounded(site, ['hs', command, 't.in', '--no-db', '--no-tls', '-H', '127.0.0.1',
                                         '-p', str(free_port())], env, bound=60)
    assert code == exit_status.runtime_error, stderr
    assert f'Evict period must be at most {EVICT_CAP} seconds: given 1e+300' in stderr, stderr
    assert 'Server listening' not in stderr and 'Traceback' not in stderr, stderr
    assert not list(site.rglob('exception-*.log')), stderr
