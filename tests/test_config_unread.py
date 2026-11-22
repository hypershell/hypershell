# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`server.eager`, `server.attempts` and `client.ratelimit` reach the commands they configure, and `-R` takes a number.

The configuration is built at import, so each reading of a key runs in a child interpreter with the
file layer or a `HYPERSHELL_*` variable set. The option and launch checks run in process.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Optional, Tuple, Type

# Standard libs
import sys
import json
from pathlib import Path
from subprocess import run, DEVNULL

# External libs
import pytest
from pytest import mark
from cmdkit.app import Application, exit_status
from cmdkit.cli import ArgumentError

# Internal libs
from tests import create_taskfile
from tests.test_env_config import KEY, DEADLINE, child_env, config_repr, run_bounded
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
from hypershell.cluster.launch import client_options


def file_layer(site: Path, text: str) -> Dict[str, str]:
    """Write `text` as the site's configuration file; the variables that make a child read it."""
    path = Path(str(site)) / 'config.toml'
    path.write_text(text, encoding='utf-8')
    return {'HYPERSHELL_CONFIG_FILE': str(path)}


# --------------------------------------------------------------------------------------------------
# server.eager and server.attempts (R5, R6)

APPS: Final[str] = 'from hypershell.server import ServerApp\nfrom hypershell.cluster import ClusterApp'
"""Imports a child needs to build the two apps."""

RETRY_READ: Final[str] = ('[(a.from_cmdline(argv).eager_mode, a.from_cmdline(argv).max_retries) '
                          'for a in (ServerApp, ClusterApp) for argv in ([], ["--eager"], ["-r", "5"])]')
"""(eager_mode, max_retries) for `hs server` then `hs cluster`, with no option, `--eager` and `-r 5`."""

AS_TODAY: Final[List[Tuple[bool, int]]] = [(False, 0), (True, 0), (False, 5)] * 2
"""What both apps hold with neither key set (the built-in default, one attempt and no eager mode)."""


def read_retry(site: Path, **variables: str) -> List[Tuple[Any, int]]:
    """RETRY_READ in a child in `site` with `variables` set."""
    rc, seen, stderr = config_repr(site, RETRY_READ, APPS, **variables)
    assert rc == exit_status.success, stderr
    return eval(seen)


@mark.integration
@mark.parametrize('layer', ['file', 'env'])
def test_eager_attempts_read(temp_site, layer: str) -> None:
    """`server.eager` and `server.attempts` take effect in both commands; `--eager` and `-r` still apply (R5, R6)."""
    variables = (file_layer(temp_site, '[server]\neager = true\nattempts = 3\n') if layer == 'file' else
                 {'HYPERSHELL_SERVER_EAGER': 'true', 'HYPERSHELL_SERVER_ATTEMPTS': '3'})
    assert read_retry(temp_site, **variables) == [(True, 2), (True, 2), (True, 5)] * 2


@mark.integration
def test_eager_attempts_unset_as_today(temp_site) -> None:
    """With neither key set both commands behave as before (R5, R6)."""
    assert read_retry(temp_site) == AS_TODAY


@mark.integration
def test_eager_env_false_over_file(temp_site) -> None:
    """`HYPERSHELL_SERVER_EAGER=false` turns a file's `eager = true` off for one run (R5, change 4)."""
    variables = file_layer(temp_site, '[server]\neager = true\n')
    assert read_retry(temp_site, HYPERSHELL_SERVER_EAGER='false', **variables) == AS_TODAY


@mark.integration
@mark.parametrize('layer', ['file', 'env'])
def test_attempts_alone_cluster(temp_site, layer: str) -> None:
    """`server.attempts` alone gives `hs cluster` its retries, as `hs server` (R6)."""
    variables = (file_layer(temp_site, '[server]\nattempts = 3\n') if layer == 'file' else
                 {'HYPERSHELL_SERVER_ATTEMPTS': '3'})
    rc, seen, stderr = config_repr(temp_site, '(ClusterApp.from_cmdline([]).max_retries, '
                                              'ClusterApp.from_cmdline([]).eager_mode)', APPS, **variables)
    assert rc == exit_status.success, stderr
    assert eval(seen) == (2, False)


# The start check of each app, as each command runs it before anything binds (`hs server` resolves its key
# first, as its run does): the eager value each then holds.
EAGER_CHECKED: Final[str] = ('[(lambda a: (setattr(a, "auth", resolve_auth("server")), a.check_args(), a.eager_mode)[2])'
                             '(ServerApp.from_cmdline(["t.in"])), '
                             '(lambda a: (a.check_arguments(), a.eager_mode)[1])'
                             '(ClusterApp.from_cmdline(["t.in", "--no-db"]))]')


@mark.integration
@mark.parametrize('text, expected', [pytest.param('"no"', False, id='no'), pytest.param('"off"', False, id='off'),
                                     pytest.param('"false"', False, id='false'), pytest.param('0', False, id='0'),
                                     pytest.param('"yes"', True, id='yes'), pytest.param('"On"', True, id='On'),
                                     pytest.param('1', True, id='1')])
def test_eager_file_words(temp_site, text: str, expected: bool) -> None:
    """A file's `eager` written as a word or 0/1 is read by the environment's boolean words (R5)."""
    variables = file_layer(temp_site, f'[server]\neager = {text}\n')
    create_taskfile(Path(str(temp_site)), ['echo hi'], 't.in')
    rc, seen, stderr = config_repr(temp_site, EAGER_CHECKED, APPS + '\nfrom hypershell.core.config import resolve_auth',
                                   HYPERSHELL_SERVER_AUTH=KEY, **variables)
    assert rc == exit_status.success, stderr
    assert eval(seen) == [expected, expected]


@mark.integration
@mark.parametrize('text', [pytest.param('"maybe"', id='maybe'), pytest.param('2.5', id='2.5'),
                            pytest.param('""', id='empty')])
def test_eager_unreadable_refused(temp_site, text: str) -> None:
    """A file's `eager` that is no boolean stops `hs cluster` at its start check, exit 3, naming the key (R5)."""
    variables = file_layer(temp_site, f'[server]\neager = {text}\n')
    site = Path(str(temp_site))
    create_taskfile(site, ['echo hi'], 't.in')
    status, _, stderr = run_bounded(site, ['hs', 'cluster', 't.in', '--no-db'], child_env(site, **variables))
    assert status == exit_status.bad_config, stderr
    assert 'server.eager' in stderr and 'config.toml' in stderr, stderr
    assert 'Traceback' not in stderr, stderr


# --------------------------------------------------------------------------------------------------
# client.ratelimit and -R (R8)

def parsed_rate(app: Type[Application], word: str) -> Any:
    """The rate `-R word` parses to on `app`, or the error text when it is refused."""
    try:
        return app.interface.parse_args(['-R', word]).ratelimit
    except ArgumentError as error:
        return f'error: {error}'


RATE_APPS: Final[Dict[str, Type[Application]]] = {'client': ClientApp, 'cluster': ClusterApp}


@mark.unit
@mark.parametrize('app', list(RATE_APPS))
@mark.parametrize('word, rate', [('0.5', 0.5), ('2.5', 2.5), ('1e-3', 0.001), ('5', 5), ('0', 0), ('-1', -1)])
def test_rate_option_takes_number(app: str, word: str, rate: Any) -> None:
    """`-R` takes a whole or decimal number on both commands; a whole word stays an integer (R8, change 8)."""
    value = parsed_rate(RATE_APPS[app], word)
    assert value == rate and type(value) is type(rate), value


@mark.unit
@mark.parametrize('app', list(RATE_APPS))
@mark.parametrize('word', ['abc', 'inf', 'nan', '1e400', ''])
def test_rate_option_refused(app: str, word: str) -> None:
    """`-R` refuses what is not a finite number, as a usage error naming the option (R8)."""
    value = parsed_rate(RATE_APPS[app], word)
    assert isinstance(value, str) and value.startswith('error: ') and '-R/--ratelimit' in value, value


LAUNCH_ARGS: Final[Dict[str, Any]] = {
    'num_threads': 1, 'bundlesize': 1, 'bundlewait': 5, 'template': '{}', 'task_signalwait': 10, 'delay_start': 0,
    'no_confirm': False, 'capture': False, 'monitor': False, 'client_cores': None, 'client_memory': None,
    'client_timeout': None, 'task_timeout': None, 'tls_enabled': True, 'zone': None,
}
"""Every other `client_options` argument, as a launch passes it."""


def launched_rate(rate: Optional[float]) -> Optional[str]:
    """The word after `-R` on the client command line a launch builds, or None if there is no `-R`."""
    words = client_options(('node-1', 50001), ratelimit=rate, **LAUNCH_ARGS)
    return words[words.index('-R') + 1] if '-R' in words else None


@mark.unit
@mark.parametrize('rate, word', [(5, '5'), (5.0, '5'), (0, '0'), (0.5, '0.5'), (2.5, '2.5'), (1e-05, '0.00001'),
                                 (1 / 3, '0.3333333333333333')])
def test_launched_rate_word(rate: float, word: str) -> None:
    """A whole rate is launched as an integer word (a 2.9.0a4 client reads it); any other as a plain decimal (R8)."""
    assert launched_rate(rate) == word
    assert 'e' not in word.lower() and float(word) == rate
    if float(rate).is_integer():
        assert int(word) == rate


@mark.unit
@mark.parametrize('rate', [0.5, 1e-05, 1 / 3])
def test_launched_rate_read_back(rate: float) -> None:
    """A launched client of this release reads a fractional rate back exactly (R8)."""
    assert parsed_rate(ClientApp, launched_rate(rate)) == rate


@mark.unit
def test_launch_omits_unset_rate() -> None:
    """With no rate a launch passes no `-R`, so each client reads its own configuration."""
    assert launched_rate(None) is None


RATE_READ: Final[str] = """\
import sys, json
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
argv = json.loads(sys.argv[1])
print(json.dumps([ClusterApp.interface.parse_args(argv).ratelimit, ClientApp.interface.parse_args(argv).ratelimit]))
"""
"""Program a child runs to print the `-R` value `hs cluster` and `hs client` hold for the given options."""


def read_rates(site: Path, argv: List[str], **variables: str) -> Tuple[int, Any, str]:
    """[cluster, client] rates in a child in `site` with `variables` set and `argv` given to both."""
    result = run([sys.executable, '-c', RATE_READ, json.dumps(argv)], cwd=site, env=child_env(site, **variables),
                 stdin=DEVNULL, capture_output=True, text=True, timeout=DEADLINE)
    lines = result.stdout.strip().splitlines()
    return result.returncode, (json.loads(lines[-1]) if result.returncode == 0 and lines else None), result.stderr


@mark.integration
@mark.parametrize('layer, text, rate', [('env', '5', 5), ('env', '0.5', 0.5), ('file', '2.5', 2.5),
                                        ('file', '5', 5)])
def test_cluster_reads_client_ratelimit(temp_site, layer: str, text: str, rate: float) -> None:
    """`hs cluster` takes its `-R` default from `client.ratelimit`, as `hs client` does (R8)."""
    variables = (file_layer(temp_site, f'[client]\nratelimit = {text}\n') if layer == 'file' else
                 {'HYPERSHELL_CLIENT_RATELIMIT': text})
    rc, rates, stderr = read_rates(Path(str(temp_site)), [], **variables)
    assert rc == exit_status.success, stderr
    assert rates == [rate, rate]


@mark.integration
@mark.parametrize('layer', ['file', 'env'])
def test_cluster_ignores_task_ratelimit(temp_site, layer: str) -> None:
    """`hs cluster` no longer reads the undocumented `task.ratelimit` (R8, change 7)."""
    variables = (file_layer(temp_site, '[task]\nratelimit = 7\n') if layer == 'file' else
                 {'HYPERSHELL_TASK_RATELIMIT': '7'})
    rc, rates, stderr = read_rates(Path(str(temp_site)), [], **variables)
    assert rc == exit_status.success, stderr
    assert rates == [None, None]


@mark.integration
def test_rate_zero_is_no_limit(temp_site) -> None:
    """A `client.ratelimit` of 0 is no limit for both commands (R8)."""
    rc, rates, stderr = read_rates(Path(str(temp_site)), [], HYPERSHELL_CLIENT_RATELIMIT='0')
    assert rc == exit_status.success, stderr
    assert rates == [None, None]


@mark.integration
def test_rate_option_over_key(temp_site) -> None:
    """`-R` overrides `client.ratelimit` on both commands, a fraction included (R8)."""
    rc, rates, stderr = read_rates(Path(str(temp_site)), ['-R', '0.5'], HYPERSHELL_CLIENT_RATELIMIT='5')
    assert rc == exit_status.success, stderr
    assert rates == [0.5, 0.5]
