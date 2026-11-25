# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Every numeric key of the merged configuration is checked when it loads, from a file or a variable, exit 3.

The whole merged configuration is checked for every command (C2): a value out of range, or not a
number of its key's kind, stops the command before it starts work with one line naming the key, its
range, the value and where it came from. An `_env` or `_eval` form is checked when it resolves, and
pinned. `hs config` still runs on a bad file value, so the file can be repaired; a bad variable
still stops it. A file's numeric key given as text is read by its kind. The configuration is built
at import, so every case runs in a child.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, List, Tuple

# Standard libs
import os
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import create_taskfile
from tests.conftest import free_port
from tests.bounded_checks import LONG, assert_one_refusal, hs_cli, write_config
from tests.test_env_config import config_repr


LOOPBACK: Final[List[str]] = ['--no-tls', '-H', '127.0.0.1']
"""A local cluster binds the loopback address."""

FILE_REFUSED: Final[List[Tuple[str, str, List[str], str]]] = [
    ('client-ratelimit-1e-300-client', '[client]\nratelimit = 1e-300\n', ['hs', 'client', '--help'], 'client.ratelimit'),
    ('server-bundlesize-true', '[server]\nbundlesize = true\n', ['hs', 'list', '--count'], 'server.bundlesize'),
    ('server-evict-4300-digits', f'[server]\nevict = {LONG}\n', ['hs', 'list', '--count'], 'server.evict'),
    ('task-cores-100000', '[task]\ncores = 100000\n', ['hs', 'list', '--count'], 'task.cores'),
    ('relay-retention-list', '[relay]\nretention = [1, 2]\n', ['hs', 'list', '--count'], 'relay.retention'),
    ('zone-size-0', '[relay.zone.a]\nsize = 0\n', ['hs', 'list', '--count'], 'relay.zone.a.size'),
    ('server-attempts-text', '[server]\nattempts = "x"\n', ['hs', 'list', '--count'], 'server.attempts'),
    ('autoscale-factor-nan', '[autoscale]\nfactor = nan\n', ['hs', 'list', '--count'], 'autoscale.factor'),
    ('task-memory-8EB', '[task]\nmemory = "8EB"\n', ['hs', 'list', '--count'], 'task.memory'),
    ('relay-min-text', '[relay]\nmin = "none"\n', ['hs', 'list', '--count'], 'relay.min'),
    ('server-port-0-submit', '[server]\nport = 0\n', ['hs', 'submit', 'echo', 'a'], 'server.port'),
]
"""A file's value out of range or not of its key's kind, by test id: the file, the command, and the key named."""


@mark.integration
@mark.parametrize('text, argv, name', [pytest.param(text, argv, name, id=case) for case, text, argv, name in FILE_REFUSED])
def test_file_value_refused(temp_site: Path, text: str, argv: List[str], name: str) -> None:
    """A file's bad value stops the command at load, exit 3, naming the key and the file; no database is opened."""
    path, variables = write_config(temp_site, text)
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    rc, _, stderr = hs_cli(temp_site, argv, HYPERSHELL_SERVER_PORT=None, **variables)  # The file's port decides
    assert_one_refusal(rc, stderr, exit_status.bad_config, name, source=str(path))
    assert not database.exists(), 'the database was opened'


VARIABLE_REFUSED: Final[List[Tuple[str, str, str, str]]] = [
    ('server-queuesize-0', 'HYPERSHELL_SERVER_QUEUESIZE', '0', 'server.queuesize'),
    ('client-heartrate-2147483648', 'HYPERSHELL_CLIENT_HEARTRATE', '2147483648', 'client.heartrate'),
    ('task-cores-x', 'HYPERSHELL_TASK_CORES', 'x', 'task.cores'),
    ('client-memory-negative', 'HYPERSHELL_CLIENT_MEMORY', '-1', 'client.memory'),
    ('zone-min-negative', 'HYPERSHELL_RELAY_ZONE_A_MIN', '-1', 'relay.zone.a.min'),
]
"""A variable's value out of range or not of its key's kind, by test id: the variable, its value and the key."""


@mark.integration
@mark.parametrize('variable, value, name', [pytest.param(variable, value, name, id=case)
                                            for case, variable, value, name in VARIABLE_REFUSED])
def test_variable_refused(temp_site: Path, variable: str, value: str, name: str) -> None:
    """A variable's bad value stops every command at load, exit 3, naming the key and the variable."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], **{variable: value})
    assert_one_refusal(rc, stderr, exit_status.bad_config, name, source=variable)


@mark.integration
def test_eval_value_refused(temp_site: Path) -> None:
    """An `_eval` or `_env` form is checked when it resolves, and names the form that gave it."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], HYPERSHELL_SERVER_PORT=None,
                           HYPERSHELL_SERVER_PORT_EVAL='echo 0')
    assert_one_refusal(rc, stderr, exit_status.bad_config, 'server.port', source='HYPERSHELL_SERVER_PORT_EVAL')
    path, variables = write_config(temp_site, '[server]\nbundlesize_env = "BUNDLE_SIZE"\n')
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], BUNDLE_SIZE='0', **variables)
    assert_one_refusal(rc, stderr, exit_status.bad_config, 'server.bundlesize', source=str(path))


@mark.integration
def test_config_command_runs_with_bad_file(temp_site: Path) -> None:
    """`hs config get` shows a bad file value and `hs config set` repairs it; a bad variable still stops it."""
    path, variables = write_config(temp_site, '[server]\nbundlesize = 0\n')
    rc, stdout, stderr = hs_cli(temp_site, ['hs', 'config', 'get', 'server.bundlesize', '-r'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert stdout.strip().splitlines()[-1].strip() == '0', stdout
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'which', 'server.bundlesize'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'set', 'server.bundlesize', '2', '--local'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert 'bundlesize = 2' in path.read_text()
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'get', 'server.queuesize'],
                           HYPERSHELL_SERVER_QUEUESIZE='0', **variables)
    assert_one_refusal(rc, stderr, exit_status.bad_config, 'server.queuesize', source='HYPERSHELL_SERVER_QUEUESIZE')


UNRESOLVED: Final[List[Tuple[str, str, str]]] = [
    ('env-not-set', '[client]\nratelimit_env = "HS_TEST_NOT_SET"\n', 'client.ratelimit'),
    ('eval-fails', '[server]\nbundlesize_eval = "exit 1"\n', 'server.bundlesize'),
]
"""A file's `_env` or `_eval` form that cannot resolve, by test id: the file and the key."""


@mark.integration
@mark.parametrize('text, key', [pytest.param(text, key, id=case) for case, text, key in UNRESOLVED])
def test_config_command_runs_with_unresolved_file(temp_site: Path, text: str, key: str) -> None:
    """`hs config` runs on a file whose `_env`/`_eval` cannot resolve, as on a bad file value; other commands exit 3."""
    path, variables = write_config(temp_site, text)
    form = text.split('\n')[1].split(' = ')[0]
    given = f'{key.rsplit(".", 1)[0]}.{form}'
    rc, stdout, stderr = hs_cli(temp_site, ['hs', 'config', 'get', given], HS_TEST_NOT_SET=None, **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert stdout.strip() == text.split(' = ')[1].strip().strip('"'), stdout
    rc, stdout, stderr = hs_cli(temp_site, ['hs', 'config', 'which', given], HS_TEST_NOT_SET=None, **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert str(path) in stdout, stdout
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'edit', '--local'], EDITOR='true', HS_TEST_NOT_SET=None,
                           **variables)
    assert rc == exit_status.success, stderr[-4000:]
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], HS_TEST_NOT_SET=None, **variables)
    assert rc == exit_status.bad_config and key in stderr and str(path) in stderr, stderr[-4000:]
    assert 'Traceback' not in stderr, stderr[-4000:]
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'set', key, '2', '--local'], HS_TEST_NOT_SET=None, **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert f'{key.rsplit(".", 1)[1]} = 2' in path.read_text(), path.read_text()


@mark.integration
def test_config_command_stops_on_unresolved_variable(temp_site: Path) -> None:
    """A variable's `_env` form that cannot resolve still stops `hs config`, exit 3, naming the variable."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'config', 'get', 'client.bundlesize'],
                           HYPERSHELL_CLIENT_RATELIMIT_ENV='HS_TEST_NOT_SET', HS_TEST_NOT_SET=None)
    assert rc == exit_status.bad_config, stderr[-4000:]
    assert 'client.ratelimit' in stderr and 'HYPERSHELL_CLIENT_RATELIMIT_ENV' in stderr, stderr[-4000:]
    assert 'Traceback' not in stderr, stderr[-4000:]


@mark.integration
def test_text_attempts_runs_three_attempts(temp_site: Path) -> None:
    """A file's `attempts = "3"` reads as 3: a failing task runs three times, instead of every command failing."""
    site = Path(str(temp_site))
    _, variables = write_config(site, '[server]\nattempts = "3"\n')
    create_taskfile(site, ['exit 1'], 't.in')
    rc, _, stderr = hs_cli(site, ['hs', 'cluster', 't.in', *LOOPBACK, '-p', str(free_port())], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    rc, stdout, stderr = hs_cli(site, ['hs', 'list', '--count'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert stdout.strip() == '3', stdout


def cluster_refused(site: Path, text: str, name: str) -> None:
    """`hs cluster --no-db` with the file `text` exits 3, naming `name` and the file."""
    path, variables = write_config(site, text)
    create_taskfile(Path(str(site)), ['echo a'], 't.in')
    rc, _, stderr = hs_cli(site, ['hs', 'cluster', 't.in', '--no-db', *LOOPBACK, '-p', str(free_port())], **variables)
    assert_one_refusal(rc, stderr, exit_status.bad_config, name, source=str(path))


@mark.integration
def test_cluster_period_key_exits_3(temp_site: Path) -> None:
    """A file's `autoscale.period = 0` stops `hs cluster` with exit 3 (it exited 2)."""
    cluster_refused(temp_site, '[autoscale]\nperiod = 0\n', 'autoscale.period')


@mark.integration
def test_cluster_ratelimit_key_exits_3(temp_site: Path) -> None:
    """A file's `client.ratelimit = 1e-300` stops `hs cluster` with exit 3 (it exited 2)."""
    cluster_refused(temp_site, '[client]\nratelimit = 1e-300\n', 'client.ratelimit')


@mark.integration
def test_evict_key_exits_3(temp_site: Path) -> None:
    """A file's `server.evict = 5` stops `hs cluster` with exit 3 (it exited 5)."""
    cluster_refused(temp_site, '[server]\nevict = 5\n', 'server.evict')


CHECKED: Final[str] = '''
from hypershell.core.config_schema import KEYS, ZONE_KEYS, Kind, match_path, VARIANTS
NUMERIC = (Kind.INT, Kind.NUMBER, Kind.INT_OR_NONE, Kind.DURATION, Kind.SIZE)
def problems():
    found = []
    def walk(tree, path):
        for name, value in tree.items():
            here = (*path, name)
            dotted = '.'.join(here)
            if isinstance(value, dict) and match_path(dotted) not in NUMERIC:
                walk(value, here)
                continue
            if any(dotted.endswith(variant) and match_path(dotted[:-len(variant)]) in NUMERIC for variant in VARIANTS):
                found.append(('variant left', dotted))
            kind = match_path(dotted)
            if kind in (Kind.INT, Kind.NUMBER, Kind.INT_OR_NONE):
                if isinstance(value, bool) or not isinstance(value, (int, float)) and value != '<none>':
                    found.append(('not a number', dotted, value))
    for layer in config.namespaces.values():
        walk(layer, ())
    return found
'''
"""A child's check that no layer holds a numeric `_env`/`_eval` form, or a whole number or number as text."""


@mark.integration
def test_loaded_numeric_keys_checked(temp_site: Path) -> None:
    """After load, every numeric key holds a checked value, whatever form gave it, and no `_env`/`_eval` form is left."""
    _, variables = write_config(temp_site, '\n'.join([
        '[server]', 'port = "50011"', 'bundlesize_env = "BUNDLE_SIZE"', 'queuesize_eval = "echo 4"', 'evict = "10m"',
        '[client]', 'ratelimit = "0.5"', '[relay.zone.a]', 'size = "2"', 'min = 1', '']))
    expr = ('(problems(), config.server.port, config.server.bundlesize, config.server.queuesize, config.server.evict, '
            'config.client.ratelimit, config.relay.zone.a.size, config.task.cores, config.autoscale.factor)')
    rc, seen, stderr = config_repr(temp_site, expr, CHECKED, HYPERSHELL_SERVER_PORT=None, BUNDLE_SIZE='3',
                                   HYPERSHELL_TASK_CORES='4', HYPERSHELL_AUTOSCALE_FACTOR_EVAL='echo 2', **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert eval(seen) == ([], 50011, 3, 4, '10m', 0.5, 2, 4, 2), seen


@mark.integration
def test_eval_runs_once(temp_site: Path) -> None:
    """A numeric key's `_eval` runs once per process, when the configuration loads."""
    counter = Path(str(temp_site)) / 'runs'
    command = f'echo x >> {counter}; echo 7'
    rc, seen, stderr = config_repr(temp_site, '(config.server.bundlesize, config.server.bundlesize)',
                                   'import hypershell', HYPERSHELL_SERVER_BUNDLESIZE_EVAL=command)
    assert rc == exit_status.success, stderr[-4000:]
    assert eval(seen) == (7, 7)
    assert counter.read_text().count('x') == 1


@mark.integration
def test_api_default_reads_checked_value(temp_site: Path) -> None:
    """A Python API default that comes from configuration reads the checked value (`attempts = "3"` gives 2 retries)."""
    _, variables = write_config(temp_site, '[server]\nattempts = "3"\n')
    rc, seen, stderr = config_repr(temp_site, 'configured_retries()', 'from hypershell.server import configured_retries',
                                   **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert seen == '2'


@mark.unit
@mark.parametrize('evict', [9, 2**31, 10**30, 10**400, float('inf'), float('nan'), -1.5],
                  ids=['9', '2**31', '10**30', '10**400', 'inf', 'nan', 'minus-1.5'])
def test_heartmonitor_evict_refused(evict: float) -> None:
    """`HeartMonitor`'s `evict_after` outside `server.evict`'s range raises the entry's refusal, however wide."""
    from types import SimpleNamespace
    from hypershell.server import HeartMonitor
    with pytest.raises(RuntimeError, match=r'^evict_after: expected a duration from 10 s to 2,147,483,647 s, given '):
        HeartMonitor(queue=SimpleNamespace(), evict_after=evict)


@mark.unit
def test_heartmonitor_evict_in_range() -> None:
    """`HeartMonitor` takes `evict_after` at both bounds of `server.evict`."""
    from types import SimpleNamespace
    from datetime import timedelta
    from hypershell.server import HeartMonitor
    for evict in (10, 2**31 - 1):
        assert HeartMonitor(queue=SimpleNamespace(), evict_after=evict).evict_after == timedelta(seconds=evict)


IN_RANGE: Final[str] = '\n'.join([
    '[server]', 'bundlesize = 2147483647', 'attempts = 32767', 'evict = 2147483647', 'port = 65535',
    '[autoscale]', 'factor = 1e9', '[client]', 'ratelimit = 0.000001',
    '[task]', 'cores = 32767', 'memory = "8388607TB"', 'timeout = 2147483647', ''])
"""A file holding the bounds of several ranges, each accepted."""


@mark.integration
def test_file_value_in_range_unchanged(temp_site: Path) -> None:
    """A file's values at the bounds of their ranges are taken and read as today."""
    _, variables = write_config(temp_site, IN_RANGE)
    expr = ('(lambda a: (a.bundlesize, a.max_retries, a.autoscaling_factor, a.ratelimit, a.cores, a.memory, a.task_timeout))'
            '(ClusterApp.from_cmdline(["--no-db"]))')
    rc, seen, stderr = config_repr(temp_site, expr, 'from hypershell.cluster import ClusterApp',
                                   HYPERSHELL_SERVER_PORT=None, **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert eval(seen) == (2147483647, 32766, 1e9, 1e-6, 32767, 8388607 * 1024**4, 2147483647), seen
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], HYPERSHELL_SERVER_PORT=None, **variables)
    assert rc == exit_status.success, stderr[-4000:]
