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
from typing import Dict, Final, List, Optional, Tuple

# Standard libs
import shutil
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import create_taskfile
from tests.conftest import free_port
from tests.test_env_config import KEY, child_env, run_bounded, start_server, stop_server, assert_refused


BOUND: Final[float] = 30
"""Seconds a command refused at start may take."""


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
    'client.heartrate': (['hs', 'client'], 'HYPERSHELL_CLIENT_HEARTRATE', '10x', None),
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
