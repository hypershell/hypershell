# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A value inside its range but past its advisory limit is taken and runs as today, with one WARNING.

The warning names the setting, the value and the advisory limit, and says the command runs anyway;
the exit status is unchanged. It is logged at most once per setting per process, wherever the range
is checked: an option, the configuration at load (a file or a variable) and `hs config set`. A
value at the limit is not advised. (The resource tags' one summary per submission is tested with
the tags, `tests/test_bounded_tags.py`.)
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Tuple

# Standard libs
import logging
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
from hypershell.server import ServerApp
from tests import create_taskfile
from tests.conftest import free_port
from tests.bounded_checks import hs_cli, write_config
from tests.test_config_set_types import config_set, written
from tests.test_env_config import DIGIT_KEY, child_env, run_bounded, start_server, stop_server


LOOPBACK: Final[List[str]] = ['--no-tls', '-H', '127.0.0.1']
"""A local cluster binds the loopback address."""


def advice(stderr: str) -> List[str]:
    """The advisory WARNING lines of `stderr`."""
    return [line for line in stderr.splitlines() if 'WARNING' in line and ' advised ' in line]


def assert_advised_once(stderr: str, name: str, value: str, limit: str) -> None:
    """One advisory line, naming the setting, the value and the limit, and saying the command runs anyway."""
    lines = advice(stderr)
    assert len(lines) == 1, stderr[-4000:]
    assert f'{name}: {value} ' in lines[0] and limit in lines[0] and 'running anyway' in lines[0], lines[0]


def cluster_run(site: Path, *options: str, **variables: str) -> Tuple[int, str, str]:
    """`hs cluster` over one task, without a database, on the loopback address."""
    create_taskfile(Path(str(site)), ['echo hi'], 't.in')
    return hs_cli(site, ['hs', 'cluster', 't.in', '--no-db', *LOOPBACK, '-p', str(free_port()), *options], **variables)


CLUSTER_ADVISED: Final[List[Tuple[str, List[str], str, str, str]]] = [
    ('cluster-factor-101', ['-F', '101'], '-F/--factor', "'101'", '100'),
    ('cluster-num-threads-1025', ['-N', '1025'], '-N/--num-threads', "'1025'", '1,024'),
    ('cluster-retries-100', ['-r', '100'], '-r/--max-retries', "'100'", '99'),
    ('cluster-period-3601', ['-P', '3601'], '-P/--period', "'3601'", '3,600'),
    ('cluster-cores-1025', ['-c', '1025'], '-c/--cores', "'1025'", '1,024'),
    ('cluster-client-cores-1025', ['-C', '1025'], '-C/--client-cores', "'1025'", '1,024'),
]
"""Options past their advisory limit on a run that completes, by test id: the option, its name, the value shown
and the limit."""


@mark.integration
@mark.parametrize('options, name, value, limit',
                  [pytest.param(options, name, value, limit, id=case)
                   for case, options, name, value, limit in CLUSTER_ADVISED])
def test_option_advised_once_cluster(temp_site: Path, options: List[str], name: str, value: str, limit: str) -> None:
    """The run goes on and exits 0, with one WARNING naming the option, the value and the advisory limit."""
    rc, stdout, stderr = cluster_run(temp_site, *options)
    assert rc == exit_status.success, stderr[-4000:]
    # A task that needs more cores than the host has is refused for resources, as before; the run completes.
    assert stdout.split() == ([] if options[0] == '-c' else ['hi']), stdout
    assert_advised_once(stderr, name, value, limit)


@mark.integration
@mark.parametrize('case', ['client-num-threads-1025', 'cluster-factor-101'])
def test_option_advised_once(temp_site: Path, case: str) -> None:
    """`hs client -N 1025` and `hs cluster -F 101` run, exit 0, with one WARNING each."""
    site = Path(str(temp_site))
    if case.startswith('cluster'):
        rc, _, stderr = cluster_run(site, '-F', '101')
        assert rc == exit_status.success, stderr[-4000:]
        assert_advised_once(stderr, '-F/--factor', "'101'", '100')
        return
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY)
    taskfile = create_taskfile(site, ['echo one', 'echo two'])
    server = start_server(site, env, str(taskfile))
    try:
        rc, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1025'], env)
    finally:
        server_status = stop_server(server)
    assert rc == exit_status.success, stderr[-4000:]
    assert server_status == exit_status.success, (site / 'server.log').read_text()[-4000:]
    assert sorted(stdout.split()) == ['one', 'two']
    assert_advised_once(stderr, '-N/--num-threads', "'1025'", '1,024')


@mark.integration
def test_option_advised_once_given_twice(temp_site: Path) -> None:
    """An option given twice past its limit (the last wins, as today) is advised once."""
    rc, _, stderr = cluster_run(temp_site, '-F', '200', '-F', '101')
    assert rc == exit_status.success, stderr[-4000:]
    assert len(advice(stderr)) == 1, stderr[-4000:]


@pytest.fixture
def live_advice(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """A fresh record of what this process has advised, logged at once, and the records captured."""
    from hypershell.core import config_schema
    monkeypatch.setattr(config_schema, 'ADVISED', set())
    monkeypatch.setattr(config_schema, 'PENDING', None)
    caplog.set_level(logging.DEBUG, logger='hypershell')
    return caplog


def warnings(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Messages of the advisory WARNING records captured."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and ' advised ' in record.getMessage()]


AT_LIMIT: Final[List[Tuple[type, List[str]]]] = [
    (ClientApp, ['-N', '1024']), (ClientApp, ['-N', '0']), (ClientApp, ['-C', '1024']), (ClientApp, ['-R', '0.001']),
    (ClientApp, ['-R', '0']), (ClusterApp, ['-r', '99']), (ClusterApp, ['-c', '1024']), (ClusterApp, ['-F', '100']),
    (ClusterApp, ['-P', '3600']), (ClusterApp, ['-P', '1h']), (ServerApp, ['-r', '99']), (ServerApp, ['-F', '100']),
]
"""Options at their advisory limit (or 0 where 0 has its own meaning): none is advised."""


@mark.unit
@mark.parametrize('app, argv', AT_LIMIT, ids=[f'{app.__name__}{"".join(argv)}' for app, argv in AT_LIMIT])
def test_option_not_advised_at_limit(live_advice: pytest.LogCaptureFixture, app: type, argv: List[str]) -> None:
    """A value at the advisory limit is taken without a warning."""
    app.from_cmdline(argv)
    assert warnings(live_advice) == []


PAST_LIMIT: Final[List[Tuple[type, List[str], str]]] = [
    (ClientApp, ['-N', '1025'], '-N/--num-threads'), (ClientApp, ['-C', '1025'], '-C/--client-cores'),
    (ClientApp, ['-R', '0.0009'], '-R/--ratelimit'), (ClusterApp, ['-r', '100'], '-r/--max-retries'),
    (ClusterApp, ['-c', '1025'], '-c/--cores'), (ClusterApp, ['-F', '100.5'], '-F/--factor'),
    (ClusterApp, ['-P', '3601'], '-P/--period'), (ServerApp, ['-r', '100'], '-r/--max-retries'),
]
"""Options just past their advisory limit, and the name each warning gives."""


@mark.unit
@mark.parametrize('app, argv, name', PAST_LIMIT, ids=[f'{app.__name__}{"".join(argv)}' for app, argv, _ in PAST_LIMIT])
def test_option_advised_past_limit(live_advice: pytest.LogCaptureFixture, app: type, argv: List[str], name: str) -> None:
    """A value just past the advisory limit is taken, with one warning; the same again in the process, none."""
    held = app.from_cmdline(argv)
    app.from_cmdline(argv)
    messages = warnings(live_advice)
    assert len(messages) == 1 and messages[0].startswith(f'{name}: '), messages
    assert held is not None


FILE_ADVISED: Final[List[Tuple[str, str, str, str, str]]] = [
    ('server-attempts-101', '[server]\nattempts = 101\n', 'server.attempts', '101', '100'),
    ('autoscale-period-3601', '[autoscale]\nperiod = "3601s"\n', 'autoscale.period', "'3601s'", '3,600'),
    ('autoscale-factor-101', '[autoscale]\nfactor = 101\n', 'autoscale.factor', '101', '100'),
    ('client-ratelimit-0.0005', '[client]\nratelimit = 0.0005\n', 'client.ratelimit', '0.0005', '0.001'),
    ('relay-num-threads-2000', '[relay]\nnum_threads = 2000\n', 'relay.num_threads', '2000', '1,024'),
    ('task-cores-1025', '[task]\ncores = 1025\n', 'task.cores', '1025', '1,024'),
]
"""A file's value past its advisory limit, by test id: the file, the key, the value shown and the limit."""


@mark.integration
@mark.parametrize('text, name, value, limit',
                  [pytest.param(text, name, value, limit, id=case) for case, text, name, value, limit in FILE_ADVISED])
def test_file_value_advised_once(temp_site: Path, text: str, name: str, value: str, limit: str) -> None:
    """A file's value past its advisory limit is taken: the command runs (exit 0), with one WARNING."""
    _, variables = write_config(temp_site, text)
    rc, stdout, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert stdout.strip() == '0'
    assert_advised_once(stderr, name, value, limit)


@mark.integration
def test_file_value_at_limit_not_advised(temp_site: Path) -> None:
    """A file's values at their advisory limits are taken without a warning."""
    _, variables = write_config(temp_site, '[server]\nattempts = 100\n[autoscale]\nfactor = 100\nperiod = 3600\n'
                                           '[client]\nratelimit = 0.001\ncores = 1024\n[relay]\nnum_threads = 1024\n')
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], **variables)
    assert rc == exit_status.success, stderr[-4000:]
    assert advice(stderr) == [], stderr[-4000:]


@mark.integration
def test_variable_value_advised_once(temp_site: Path) -> None:
    """A variable's value past its advisory limit is taken with one WARNING; with the option past it too, still one."""
    rc, _, stderr = hs_cli(temp_site, ['hs', 'list', '--count'], HYPERSHELL_SERVER_ATTEMPTS='101')
    assert rc == exit_status.success, stderr[-4000:]
    assert_advised_once(stderr, 'server.attempts', '101', '100')
    rc, _, stderr = cluster_run(temp_site, '-F', '101', HYPERSHELL_AUTOSCALE_FACTOR='150')
    assert rc == exit_status.success, stderr[-4000:]
    assert len(advice(stderr)) == 1, stderr[-4000:]


@mark.integration
def test_config_set_advised(temp_site: Path) -> None:
    """`hs config set` writes a value past its advisory limit, with one WARNING; at the limit, none."""
    site = Path(str(temp_site))
    rc, stderr = config_set(site, 'server.attempts', '101')
    assert rc == exit_status.success, stderr[-4000:]
    assert written(site / 'config.toml', 'server.attempts') == 101
    assert_advised_once(stderr, 'server.attempts', '101', '100')
    rc, stderr = config_set(site, 'autoscale.factor', '100')
    assert rc == exit_status.success and advice(stderr) == [], stderr[-4000:]
