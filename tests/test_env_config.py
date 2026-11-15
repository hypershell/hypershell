# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Configuration keys set by `HYPERSHELL_*` environment variables.

A variable names a key by its dotted path with each `.` written as `_`, in any letter case, and
the key holds the value as written when it takes text, or the converted value when it takes a
whole number, a number or a boolean. The configuration is built at import, so every test here runs
a child interpreter or the `hs` command with the variables set, and asserts its exit status first.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Optional, Tuple

# Standard libs
import os
import sys
import time
from pathlib import Path
from signal import SIGTERM, SIGKILL
from subprocess import Popen, PIPE, DEVNULL, TimeoutExpired, run

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import create_taskfile


KEY: Final[str] = 'Xk3v9QpL2mN7bR4tW8yZ'
"""A well-formed key."""

DIGIT_KEY: Final[str] = '0012345678901234'
"""A well-formed key of sixteen digits, two of them leading zeros."""

DEADLINE: Final[int] = 60
"""Seconds a command may take before the test stops it and fails."""

MISSING: Final[str] = '<missing>'
"""What `key()` gives in a child for a path the configuration does not hold."""

CHILD: Final[str] = '''\
import sys
{imports}

MISSING = {missing!r}


def key(path):
    """The value at dotted `path`, read entry by entry (no variant is resolved), or MISSING."""
    node = config
    for name in path.split('.'):
        if not isinstance(node, dict) or name not in node:
            return MISSING
        node = node[name]
    return node


print(repr(eval(sys.argv[1])))
'''
"""Program a child interpreter runs to print `repr` of one expression over the configuration."""


def child_env(site: Path, **variables: Optional[str]) -> Dict[str, str]:
    """The test's environment with `variables` set (a value of None unsets it), for a child in `site`."""
    env = {**os.environ, 'HYPERSHELL_SITE': str(site), 'HYPERSHELL_CONFIG_FILE': '', **variables}
    return {name: value for name, value in env.items() if value is not None}


def config_repr(site: Path, expr: str, imports: str = '', **variables: Optional[str]) -> Tuple[int, str, str]:
    """Run a child interpreter in `site` with `variables` set; return its status, `repr(expr)` and stderr.

    The child imports `config` from `hypershell.core.config` (plus `imports`) and defines `key(path)`
    and `MISSING`, so `expr` may use them.
    """
    program = CHILD.format(imports='\n'.join(['from hypershell.core.config import config', imports]),
                           missing=MISSING)
    result = run([sys.executable, '-c', program, expr], cwd=site, env=child_env(site, **variables),
                 stdin=DEVNULL, capture_output=True, text=True, timeout=DEADLINE)
    lines = result.stdout.strip().splitlines()
    return result.returncode, lines[-1] if lines else '', result.stderr


def hs(site: Path, argv: List[str], env: Optional[Dict[str, str]] = None, **variables: Optional[str]
       ) -> Tuple[int, str, str]:
    """Run `argv` in `site` with `variables` set; return its status, stdout and stderr (stripped)."""
    result = run(argv, cwd=site, env=env or child_env(site, **variables), stdin=DEVNULL, capture_output=True,
                 text=True, timeout=DEADLINE)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_bounded(site: Path, argv: List[str], env: Dict[str, str], bound: float = DEADLINE
                ) -> Tuple[int, str, str]:
    """Run `argv` in its own process group; fail the test if it is still running after `bound` seconds.

    A command still running at the bound is stopped (SIGTERM to its group, then SIGKILL) before
    the test fails, so nothing it started is left behind.
    """
    out, err = Path(site) / 'bounded.out', Path(site) / 'bounded.err'
    with open(out, mode='w') as stdout, open(err, mode='w') as stderr:
        proc = Popen(argv, cwd=site, env=env, stdin=DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
    try:
        status = proc.wait(timeout=bound)
    except TimeoutExpired:
        status = None
        stop_group(proc)
    if status is None:
        pytest.fail(f'{" ".join(argv)} still running after {bound} seconds: {err.read_text()[-2000:]}')
    return status, out.read_text().strip(), err.read_text().strip()


def stop_group(proc: Popen) -> None:
    """Stop the process group of `proc`: SIGTERM, then SIGKILL if it does not exit in a few seconds."""
    for signal in (SIGTERM, SIGKILL):
        try:
            os.killpg(proc.pid, signal)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
            return
        except TimeoutExpired:
            continue


def assert_refused(rc: int, stderr: str, *names: str) -> None:
    """The command exited with a configuration error naming each of `names`, with no traceback."""
    assert rc == exit_status.bad_config, stderr
    for name in names:
        assert name in stderr, stderr
    assert 'traceback' not in stderr.lower(), stderr


# --- Underscore keys and letter case --------------------------------------------------------------------------

UNDERSCORE_KEYS: Final[Dict[str, Tuple[str, str, Any]]] = {
    'server.tls.min_version': ('HYPERSHELL_SERVER_TLS_MIN_VERSION', 'TLSv1.3', 'TLSv1.3'),
    'database.timescale.chunk_interval': ('HYPERSHELL_DATABASE_TIMESCALE_CHUNK_INTERVAL', '1 day', '1 day'),
    'database.timescale.compress_after': ('HYPERSHELL_DATABASE_TIMESCALE_COMPRESS_AFTER', '7 days', '7 days'),
    'relay.launcher_args': ('HYPERSHELL_RELAY_LAUNCHER_ARGS', '--mem 4G', '--mem 4G'),
    'relay.num_threads': ('HYPERSHELL_RELAY_NUM_THREADS', '4', 4),
    'relay.upstream.evict_hint': ('HYPERSHELL_RELAY_UPSTREAM_EVICT_HINT', '120', 120),
    'autoscale.failed_launches': ('HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES', '5', 5),
}
"""Keys whose own name has an underscore: the variable, its value, and the value the key holds."""


@mark.integration
@mark.parametrize('path', list(UNDERSCORE_KEYS), ids=list(UNDERSCORE_KEYS))
def test_underscore_key(temp_site: Path, path: str) -> None:
    """A variable spelling a key whose name has an underscore sets that key."""
    name, value, expected = UNDERSCORE_KEYS[path]
    rc, seen, stderr = config_repr(temp_site, f'key({path!r})', **{name: value})
    assert rc == exit_status.success, stderr
    assert seen == repr(expected)


@mark.integration
def test_key_any_letter_case(temp_site: Path) -> None:
    """The name after the prefix may be written in any letter case."""
    rc, seen, stderr = config_repr(temp_site, "key('server.tls.min_version')",
                                   HYPERSHELL_server_tls_min_version='TLSv1.3')
    assert rc == exit_status.success, stderr
    assert seen == repr('TLSv1.3')


@mark.integration
def test_launcher_args_leaves_launcher(temp_site: Path) -> None:
    """`relay.launcher_args` is its own key, and `relay.launcher` keeps the value its variable gives."""
    rc, seen, stderr = config_repr(temp_site, "(key('relay.launcher'), key('relay.launcher_args'))",
                                   HYPERSHELL_RELAY_LAUNCHER='srun', HYPERSHELL_RELAY_LAUNCHER_ARGS='--mem=4G')
    assert rc == exit_status.success, stderr
    assert seen == repr(('srun', '--mem=4G'))


@mark.integration
def test_config_get_underscore_key(temp_site: Path) -> None:
    """`hs config get` shows the key a variable set."""
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', '-r', 'server.tls.min_version'],
                            HYPERSHELL_SERVER_TLS_MIN_VERSION='TLSv1.3')
    assert rc == exit_status.success, stderr
    assert stdout == 'TLSv1.3'


@mark.integration
def test_unknown_name_held_whole(temp_site: Path) -> None:
    """A name matching no key is held as one key in the deepest table it names, beside that table's keys."""
    rc, seen, stderr = config_repr(temp_site, "(key('server.port_x'), type(key('server.port')).__name__)",
                                   HYPERSHELL_SERVER_PORT_X='1')
    assert rc == exit_status.success, stderr
    assert seen == repr((1, 'int'))


# --- Open tables and zones ------------------------------------------------------------------------------------

OPEN_ENTRIES: Final[Dict[str, Tuple[str, str, Any]]] = {
    'database.pragmas.journal_mode': ('HYPERSHELL_DATABASE_PRAGMAS_JOURNAL_MODE', 'WAL', 'WAL'),
    'database.connect_args.check_same_thread': ('HYPERSHELL_DATABASE_CONNECT_ARGS_CHECK_SAME_THREAD', 'false', False),
    'ssh.nodelist.my_group': ('HYPERSHELL_SSH_NODELIST_MY_GROUP', 'a[0-3]', 'a[0-3]'),
}
"""Entries of tables whose entries the user names: the variable, its value, and the entry's value."""


@mark.integration
@mark.parametrize('path', list(OPEN_ENTRIES), ids=list(OPEN_ENTRIES))
def test_open_table_entry(temp_site: Path, path: str) -> None:
    """The rest of a name past an open table's path is one entry name, underscores kept."""
    name, value, expected = OPEN_ENTRIES[path]
    rc, seen, stderr = config_repr(temp_site, f'key({path!r})', **{name: value})
    assert rc == exit_status.success, stderr
    assert seen == repr(expected)


@mark.integration
def test_provider_argument_kept_whole(temp_site: Path) -> None:
    """A `[database]` name that is no key of its own is one provider argument, underscores kept."""
    rc, seen, stderr = config_repr(temp_site, "key('database.ssl_mode')", HYPERSHELL_DATABASE_SSL_MODE='require')
    assert rc == exit_status.success, stderr
    assert seen == repr('require')


@mark.integration
def test_zone_table_key(temp_site: Path) -> None:
    """A `relay.zone` name ends in a zone key, and the part before it is the zone name."""
    rc, seen, stderr = config_repr(temp_site, "key('relay.zone.gpu_a100.launcher_args')",
                                   HYPERSHELL_RELAY_ZONE_GPU_A100_LAUNCHER_ARGS='--gpus=1')
    assert rc == exit_status.success, stderr
    assert seen == repr('--gpus=1')


@mark.integration
def test_zone_variable_without_zone_key_refused(temp_site: Path) -> None:
    """A `relay.zone` variable that ends in no zone key is a configuration error naming it."""
    rc, _, stderr = hs(temp_site, ['hs', 'config', 'get', 'relay.zone'], HYPERSHELL_RELAY_ZONE_GPU='x')
    assert_refused(rc, stderr, 'HYPERSHELL_RELAY_ZONE_GPU')


@mark.integration
def test_zone_name_and_tables_refused(temp_site: Path) -> None:
    """`HYPERSHELL_RELAY_ZONE` and a zone-table variable together are a configuration error naming both."""
    rc, _, stderr = hs(temp_site, ['hs', 'config', 'get', 'relay.zone'],
                       HYPERSHELL_RELAY_ZONE='cpu', HYPERSHELL_RELAY_ZONE_GPU_SIZE='2')
    assert_refused(rc, stderr, 'HYPERSHELL_RELAY_ZONE', 'HYPERSHELL_RELAY_ZONE_GPU_SIZE')


# --- _ENV and _EVAL -------------------------------------------------------------------------------------------

VARIANTS: Final[Dict[str, Tuple[str, str, str]]] = {
    'server.auth_env': ('HYPERSHELL_SERVER_AUTH_ENV', 'ENV_CONFIG_TEST_KEY', 'server.auth'),
    'relay.upstream.auth_eval': ('HYPERSHELL_RELAY_UPSTREAM_AUTH_EVAL', f'echo {KEY}', 'relay.upstream.auth'),
}
"""Variant variables: the variable, its value, and the key `hs config get` resolves to `KEY`."""


@mark.integration
@mark.parametrize('variant', list(VARIANTS), ids=list(VARIANTS))
def test_variant_from_environment(temp_site: Path, variant: str) -> None:
    """`HYPERSHELL_<KEY>_ENV` and `_EVAL` resolve the key as `<key>_env` and `_eval` do in a file."""
    name, value, path = VARIANTS[variant]
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', '-r', path],
                            **{name: value, 'ENV_CONFIG_TEST_KEY': KEY})
    assert rc == exit_status.success, stderr
    assert stdout == KEY


@mark.integration
def test_variant_database_password_env(temp_site: Path) -> None:
    """`HYPERSHELL_DATABASE_PASSWORD_ENV` gives `database.password` the named variable's value."""
    rc, seen, stderr = config_repr(temp_site, 'config.database.password',
                                   HYPERSHELL_DATABASE_PASSWORD_ENV='ENV_CONFIG_TEST_PASSWORD',
                                   ENV_CONFIG_TEST_PASSWORD='s3cret')
    assert rc == exit_status.success, stderr
    assert seen == repr('s3cret')


@mark.integration
def test_variant_two_forms_refused(temp_site: Path) -> None:
    """Two forms of the key from the environment stop every command, as two forms in one file do."""
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', 'logging.level'], HYPERSHELL_SERVER_AUTH=KEY,
                            HYPERSHELL_SERVER_AUTH_ENV='ENV_CONFIG_TEST_KEY', ENV_CONFIG_TEST_KEY=KEY)
    assert_refused(rc, stderr, 'server.auth', 'auth, auth_env')
    assert KEY not in stdout and KEY not in stderr


# --- Text stays text ------------------------------------------------------------------------------------------

TEXT_KEPT: Final[Dict[str, Tuple[str, str, str]]] = {
    'autoscale.launcher-false': ('autoscale.launcher', 'HYPERSHELL_AUTOSCALE_LAUNCHER', 'false'),
    'relay.launcher-true': ('relay.launcher', 'HYPERSHELL_RELAY_LAUNCHER', 'true'),
    'logging.datefmt-empty': ('logging.datefmt', 'HYPERSHELL_LOGGING_DATEFMT', ''),
    'relay.template-null': ('relay.template', 'HYPERSHELL_RELAY_TEMPLATE', 'null'),
    'server.auth-leading-zeros': ('server.auth', 'HYPERSHELL_SERVER_AUTH', DIGIT_KEY),
    'server.auth-exponent': ('server.auth', 'HYPERSHELL_SERVER_AUTH', '1234567890123456e5'),
}
"""Keys that take text, given text that looks like another type: the key, the variable and the text."""


@mark.integration
@mark.parametrize('case', list(TEXT_KEPT), ids=list(TEXT_KEPT))
def test_text_kept(temp_site: Path, case: str) -> None:
    """A key that takes text holds the variable's text unchanged."""
    path, name, value = TEXT_KEPT[case]
    rc, seen, stderr = config_repr(temp_site, f'key({path!r})', **{name: value})
    assert rc == exit_status.success, stderr
    assert seen == repr(value)


def start_server(site: Path, env: Dict[str, str], *args: str) -> Popen:
    """Start `hs server` in its own process group, writing to `server.log` in `site`; wait until it listens."""
    log_path = site / 'server.log'
    with open(log_path, mode='w') as stream:
        server = Popen(['hs', 'server', *args], cwd=site, env=env, stdin=DEVNULL, stdout=stream, stderr=stream,
                       start_new_session=True)
    deadline = time.monotonic() + DEADLINE
    while 'Server listening' not in log_path.read_text() and server.poll() is None:
        if time.monotonic() > deadline:
            stop_group(server)
            pytest.fail(f'Server not listening within {DEADLINE} seconds: {log_path.read_text()[-2000:]}')
        time.sleep(0.1)
    assert server.poll() is None, log_path.read_text()
    return server


def stop_server(server: Popen) -> int:
    """Wait for `server` to exit on its own and return its status; stop its group if it does not."""
    try:
        return server.wait(timeout=DEADLINE)
    except TimeoutExpired:
        stop_group(server)
        return server.wait()


@mark.integration
def test_all_digit_key_server_and_client(temp_site: Path) -> None:
    """An all-digit `HYPERSHELL_SERVER_AUTH` is the same key for `hs server` and `hs client`."""
    site = Path(str(temp_site))
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY)
    taskfile = create_taskfile(site, ['echo one', 'echo two'])
    server = start_server(site, env, str(taskfile))
    try:
        status, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1'], env)
    finally:
        server_status = stop_server(server)
    assert status == exit_status.success, stderr
    assert server_status == exit_status.success, (site / 'server.log').read_text()
    assert stdout.split() == ['one', 'two']


@mark.integration
def test_all_digit_key_submit_queue(temp_site: Path) -> None:
    """An all-digit `HYPERSHELL_SERVER_AUTH` is the same key for `hs submit --queue` and the server."""
    site = Path(str(temp_site))
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY, HYPERSHELL_SERVER_QUEUESIZE='100')
    server = start_server(site, env, '--no-db')
    try:
        submit = run(['hs', 'submit', '--queue', '-w', '1'], cwd=site, env=env, input='echo one\necho two\n',
                     capture_output=True, text=True, timeout=DEADLINE)
        status, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1', '-T', '20'], env)
    finally:
        server_status = stop_server(server)
    assert submit.returncode == exit_status.success, submit.stderr
    assert status == exit_status.success, stderr
    assert server_status == exit_status.success, (site / 'server.log').read_text()
    assert sorted(stdout.split()) == ['one', 'two']


# --- Typed keys -----------------------------------------------------------------------------------------------

BOOLEAN_WORDS: Final[Dict[str, bool]] = {'no': False, 'OFF': False, 'Yes': True, '1': True}
"""Boolean words in assorted letter case, and the boolean each gives."""


@mark.integration
@mark.parametrize('word', list(BOOLEAN_WORDS), ids=list(BOOLEAN_WORDS))
def test_boolean_words(temp_site: Path, word: str) -> None:
    """A key that takes a boolean holds the boolean its word names."""
    rc, seen, stderr = config_repr(temp_site, "key('server.eager')", HYPERSHELL_SERVER_EAGER=word)
    assert rc == exit_status.success, stderr
    assert seen == repr(BOOLEAN_WORDS[word])


TYPED_REFUSED: Final[Dict[str, Tuple[str, str, str]]] = {
    'server.port-abc': ('HYPERSHELL_SERVER_PORT', 'abc', 'whole number'),
    'server.eager-maybe': ('HYPERSHELL_SERVER_EAGER', 'maybe', 'boolean'),
    'relay.max-fraction': ('HYPERSHELL_RELAY_MAX', '1.0', 'whole number'),
    'autoscale.factor-inf': ('HYPERSHELL_AUTOSCALE_FACTOR', 'inf', 'number'),
    'server.tls.enabled-empty': ('HYPERSHELL_SERVER_TLS_ENABLED', '', 'boolean'),
}
"""Typed keys given text that does not convert: the variable, its text, and a word of the form expected."""


@mark.integration
@mark.parametrize('case', list(TYPED_REFUSED), ids=list(TYPED_REFUSED))
def test_typed_refused(temp_site: Path, case: str) -> None:
    """Text that does not convert to the key's type stops every command, naming the variable and the form."""
    name, value, form = TYPED_REFUSED[case]
    rc, _, stderr = hs(temp_site, ['hs', 'config', 'get', '-r', 'logging.level'], **{name: value})
    assert_refused(rc, stderr, name, case.split('-')[0], form)


@mark.integration
def test_logging_file_enabled_any_case(temp_site: Path) -> None:
    """`logging.file` takes `enabled` in any letter case as enabling the file at its default path."""
    rc, seen, stderr = config_repr(temp_site, "key('logging.file')", HYPERSHELL_LOGGING_FILE='Enabled')
    assert rc == exit_status.success, stderr
    assert seen == repr(True)


@mark.integration
def test_logging_file_false_refused(temp_site: Path) -> None:
    """`logging.file` has no false form: `false` is a configuration error naming the variable."""
    rc, _, stderr = hs(temp_site, ['hs', 'config', 'get', '-r', 'logging.level'], HYPERSHELL_LOGGING_FILE='false')
    assert_refused(rc, stderr, 'HYPERSHELL_LOGGING_FILE')


@mark.integration
def test_open_entry_toml_rule(temp_site: Path) -> None:
    """An open-table entry holds a TOML integer, float or boolean when its text is one, else the text."""
    rc, seen, stderr = config_repr(temp_site, "(key('database.connect_args.timeout'), key('database.pragmas.x'), "
                                              "key('database.pragmas.y'))",
                                   HYPERSHELL_DATABASE_CONNECT_ARGS_TIMEOUT='5', HYPERSHELL_DATABASE_PRAGMAS_X='007',
                                   HYPERSHELL_DATABASE_PRAGMAS_Y='true')
    assert rc == exit_status.success, stderr
    assert seen == repr((5, '007', True))


# --- Variables that set keys before keep setting them ----------------------------------------------------------

KEPT: Final[Dict[str, Tuple[Dict[str, str], str, Any]]] = {
    'server.port': ({'HYPERSHELL_SERVER_PORT': '50123'}, 'server.port', 50123),
    'database.file': ({'HYPERSHELL_DATABASE_FILE': 'x.db'}, 'database.file', 'x.db'),
    'logging.level': ({'HYPERSHELL_LOGGING_LEVEL': 'TRACE'}, 'logging.level', 'TRACE'),
    'server.tls.enabled': ({'HYPERSHELL_SERVER_TLS_ENABLED': 'false'}, 'server.tls.enabled', False),
    'server.auth': ({'HYPERSHELL_SERVER_AUTH': 'a1b2c3d4e5f60718293a'}, 'server.auth', 'a1b2c3d4e5f60718293a'),
    'server.evict': ({'HYPERSHELL_SERVER_EVICT': '10'}, 'server.evict', 10),
    'submit.bundlewait': ({'HYPERSHELL_SUBMIT_BUNDLEWAIT': '7'}, 'submit.bundlewait', 7),
    'task.signalwait': ({'HYPERSHELL_TASK_SIGNALWAIT': '0.5'}, 'task.signalwait', 0.5),
    'server.poll': ({'HYPERSHELL_SERVER_POLL': '1m'}, 'server.poll', '1m'),
    'task.memory': ({'HYPERSHELL_TASK_MEMORY': '16G'}, 'task.memory', '16G'),
    'relay.upstream.host': ({'HYPERSHELL_RELAY_UPSTREAM_HOST': '127.0.0.1'}, 'relay.upstream.host', '127.0.0.1'),
    'autoscale.sites': ({'HYPERSHELL_AUTOSCALE_SITES': 'east,west'}, 'autoscale.sites', 'east,west'),
    'ssh.args': ({'HYPERSHELL_SSH_ARGS': '-n -o BatchMode=yes'}, 'ssh.args', '-n -o BatchMode=yes'),
    'relay.zone': ({'HYPERSHELL_RELAY_ZONE': 'gpu'}, 'relay.zone', 'gpu'),
    'relay.zone.gpu.size': ({'HYPERSHELL_RELAY_ZONE_GPU_SIZE': '2'}, 'relay.zone.gpu.size', 2),
    'logging.file.level': ({'HYPERSHELL_LOGGING_FILE': 'enabled', 'HYPERSHELL_LOGGING_FILE_LEVEL': 'devel'},
                           'logging.file.level', 'devel'),
}
"""Variables that set a key on the base build, and the value that key holds there and after."""


@mark.integration
@mark.parametrize('case', list(KEPT), ids=list(KEPT))
def test_kept(temp_site: Path, case: str) -> None:
    """A variable that set a key before still sets it, to the same value."""
    variables, path, expected = KEPT[case]
    rc, seen, stderr = config_repr(temp_site, f'key({path!r})', **variables)
    assert rc == exit_status.success, stderr
    assert seen == repr(expected)
