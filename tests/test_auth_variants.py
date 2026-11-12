# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test the authkey given as `auth`, `auth_env` or `auth_eval`, in any configuration layer."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# Standard libs
import os
import time
from pathlib import Path
from signal import SIGINT
from subprocess import run, Popen, STDOUT, TimeoutExpired

# External libs
from pytest import mark, raises
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace, ConfigurationError

# Internal libs
from tests import main, create_taskfile
from hypershell.core.config import resolve_auth, check_auth_forms, DEFAULT_AUTH


KEY: Final[str] = 'Xk3v9QpL2mN7bR4tW8yZ'
"""A well-formed key that no message may contain."""

OTHER_KEY: Final[str] = 'Hc6dF1gJ5kM0nP2qS4uV'
"""A second well-formed key, for precedence."""

VAR: Final[str] = 'AUTHVAR_TEST_KEY'
"""The variable an `auth_env` names."""

DEADLINE: Final[int] = 60
"""Seconds a server or client subprocess may take."""


def layered(**layers: dict) -> Configuration:
    """A configuration over the built-in `server.auth` default, with the given layers on top."""
    default = Namespace({'server': {'auth': DEFAULT_AUTH, 'port': 50001}})
    return Configuration(default=default, **{name: Namespace(layer) for name, layer in layers.items()})


def server(**forms: str) -> Dict[str, Dict[str, str]]:
    """A layer with a `[server]` table holding the given authkey forms."""
    return {'server': dict(forms)}


@mark.unit
def test_resolve_builtin_default() -> None:
    """With no layer giving the key, the built-in default is what the configuration holds."""
    assert resolve_auth('server', base=layered()) == DEFAULT_AUTH


@mark.unit
def test_resolve_literal() -> None:
    """A literal `auth` in a file is the key."""
    assert resolve_auth('server', base=layered(user=server(auth=KEY))) == KEY


@mark.unit
def test_resolve_auth_env(monkeypatch) -> None:
    """An `auth_env` naming a set variable takes the variable's value over the built-in default."""
    monkeypatch.setenv(VAR, KEY)
    assert resolve_auth('server', base=layered(local=server(auth_env=VAR))) == KEY


@mark.unit
def test_resolve_auth_eval() -> None:
    """An `auth_eval` naming a command that succeeds takes its output, stripped."""
    assert resolve_auth('server', base=layered(user=server(auth_eval=f'echo {KEY}'))) == KEY


@mark.unit
def test_resolve_higher_auth_env_over_lower_auth(monkeypatch) -> None:
    """A higher layer's `auth_env` decides over a lower layer's literal `auth`."""
    monkeypatch.setenv(VAR, KEY)
    base = layered(user=server(auth=OTHER_KEY), env=server(auth_env=VAR))
    assert resolve_auth('server', base=base) == KEY


@mark.unit
def test_resolve_higher_auth_over_lower_auth_env(monkeypatch) -> None:
    """A higher layer's literal `auth` decides over a lower layer's `auth_env` (or `auth_eval`)."""
    monkeypatch.setenv(VAR, OTHER_KEY)
    assert resolve_auth('server', base=layered(user=server(auth_env=VAR), env=server(auth=KEY))) == KEY
    assert resolve_auth('server', base=layered(user=server(auth_eval='exit 4'), local=server(auth=KEY))) == KEY


@mark.unit
def test_resolve_unset_variable(monkeypatch) -> None:
    """An `auth_env` naming an unset variable is an error naming the key and the variable, not `None`."""
    monkeypatch.delenv(VAR, raising=False)
    with raises(ConfigurationError) as caught:
        resolve_auth('server', base=layered(user=server(auth_env=VAR)))
    message = str(caught.value)
    assert 'server.auth_env' in message and VAR in message
    assert DEFAULT_AUTH not in message


@mark.unit
def test_resolve_failed_command() -> None:
    """An `auth_eval` command that exits non-zero is an error naming the key and the status, not its output."""
    with raises(ConfigurationError) as caught:
        resolve_auth('server', base=layered(user=server(auth_eval=f'echo {KEY}; exit 4')))
    message = str(caught.value)
    assert 'server.auth_eval' in message and 'status 4' in message
    assert KEY not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@mark.unit
def test_resolve_command_output_not_utf8() -> None:
    """An `auth_eval` command whose output is not UTF-8 is an error naming the key, not a traceback."""
    with raises(ConfigurationError) as caught:
        resolve_auth('server', base=layered(user=server(auth_eval=f"printf '{KEY}\\377'")))
    message = str(caught.value)
    assert 'server.auth_eval' in message and 'UnicodeDecodeError' in message
    assert KEY not in message and '0xff' not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@mark.unit
@mark.parametrize('given', [('auth', 'auth_env'), ('auth', 'auth_eval'), ('auth_env', 'auth_eval')])
def test_resolve_two_forms_in_deciding_layer(monkeypatch, given: tuple) -> None:
    """Two forms of the key in the layer that decides it are refused, naming the key and both forms."""
    monkeypatch.setenv(VAR, KEY)
    values = {'auth': KEY, 'auth_env': VAR, 'auth_eval': f'echo {KEY}'}
    with raises(ConfigurationError) as caught:
        resolve_auth('server', base=layered(user=server(**{form: values[form] for form in given})))
    message = str(caught.value)
    assert 'server.auth' in message and ', '.join(given) in message
    assert KEY not in message


@mark.unit
def test_resolve_two_forms_in_lower_layer(monkeypatch) -> None:
    """Two forms of the key in a lower layer are refused even when a higher layer overrides them."""
    monkeypatch.setenv(VAR, KEY)
    base = layered(user=server(auth=KEY, auth_env=VAR), local=server(auth=OTHER_KEY))
    with raises(ConfigurationError) as caught:
        resolve_auth('server', base=base)
    message = str(caught.value)
    assert 'server.auth (auth, auth_env)' in message
    assert KEY not in message and OTHER_KEY not in message
    with raises(ConfigurationError):
        check_auth_forms(base)


@mark.unit
def test_check_auth_forms_accepts_one_form_per_layer(monkeypatch) -> None:
    """One form in each of several layers is not a duplicate."""
    monkeypatch.setenv(VAR, KEY)
    check_auth_forms(layered(user=server(auth=OTHER_KEY), local=server(auth_env=VAR),
                             env=server(auth_eval='exit 4')))


@mark.unit
def test_resolve_absent_section() -> None:
    """A section no layer has is reported as not found."""
    base = Configuration(default=Namespace({'logging': {'level': 'info'}}))
    with raises(ConfigurationError, match=r'"server.auth" not found in configuration'):
        resolve_auth('server', base=base)
    with raises(ConfigurationError, match=r'"relay.upstream.auth" not found in configuration'):
        resolve_auth('relay', 'upstream', base=base)


def write_config(directory: Path, text: str) -> Path:
    """Write a configuration file and point `HYPERSHELL_CONFIG_FILE` at it."""
    filepath = Path(directory) / 'config.toml'
    filepath.write_text(text, encoding='utf-8')
    os.environ['HYPERSHELL_CONFIG_FILE'] = str(filepath)
    return filepath


def assert_clean_failure(rc: int, stdout: str, stderr: str, *names: str) -> None:
    """The command exited with a configuration error naming each of `names`, with no traceback or key."""
    assert rc == exit_status.bad_config
    assert 'ConfigurationError' in stderr
    for name in names:
        assert name in stderr
    assert 'Traceback' not in stderr and 'traceback' not in stderr.lower()
    assert KEY not in stdout and KEY not in stderr


@mark.integration
@mark.parametrize('expand', [[], ['-x']])
def test_config_get_auth_env(temp_site: Path, monkeypatch, expand: list) -> None:
    """`hs config get server.auth` prints the value of the variable an `auth_env` names."""
    write_config(temp_site, f'[server]\nauth_env = "{VAR}"\n')
    monkeypatch.setenv(VAR, KEY)
    assert main(['hs', 'config', 'get', 'server.auth', '-r', *expand]) == (exit_status.success, KEY, '')


@mark.integration
def test_config_get_auth_eval(temp_site: Path) -> None:
    """`hs config get server.auth` prints the output of the command an `auth_eval` names."""
    write_config(temp_site, f'[server]\nauth_eval = "echo {KEY}"\n')
    assert main(['hs', 'config', 'get', 'server.auth', '-r']) == (exit_status.success, KEY, '')


@mark.integration
def test_config_get_auth_env_unset(temp_site: Path, monkeypatch) -> None:
    """With the variable unset, `hs config get server.auth` fails naming the key and the variable."""
    filepath = write_config(temp_site, f'[server]\nauth_env = "{VAR}"\n')
    monkeypatch.delenv(VAR, raising=False)
    rc, stdout, stderr = main(['hs', 'config', 'get', 'server.auth', '-r'])
    assert_clean_failure(rc, stdout, stderr, 'server.auth_env', VAR, str(filepath))
    assert stdout == '' and DEFAULT_AUTH not in stderr


@mark.integration
def test_config_get_auth_eval_failed(temp_site: Path) -> None:
    """With the command failing, `hs config get server.auth` fails naming the key and the status."""
    filepath = write_config(temp_site, f'[server]\nauth_eval = "echo {KEY}; exit 4"\n')
    rc, stdout, stderr = main(['hs', 'config', 'get', 'server.auth', '-r'])
    assert_clean_failure(rc, stdout, stderr, 'server.auth_eval', 'status 4', str(filepath))
    assert stdout == ''


@mark.integration
def test_config_get_literal_auth(temp_site: Path) -> None:
    """A literal `auth` in a file is printed as before."""
    write_config(temp_site, f'[server]\nauth = "{KEY}"\n')
    assert main(['hs', 'config', 'get', 'server.auth', '-r']) == (exit_status.success, KEY, '')


@mark.integration
def test_config_get_environment_auth(temp_site: Path, monkeypatch) -> None:
    """`HYPERSHELL_SERVER_AUTH` is printed as before, over a file's `auth_env`."""
    write_config(temp_site, f'[server]\nauth_env = "{VAR}"\n')
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setenv('HYPERSHELL_SERVER_AUTH', KEY)
    assert main(['hs', 'config', 'get', 'server.auth', '-r']) == (exit_status.success, KEY, '')


@mark.integration
def test_config_get_builtin_auth(temp_site: Path) -> None:
    """With no key configured, the built-in default is printed as before."""
    assert main(['hs', 'config', 'get', 'server.auth', '-r']) == (exit_status.success, DEFAULT_AUTH, '')
    assert main(['hs', 'config', 'get', 'server.auth', '-r', '--default']) == (
        exit_status.success, DEFAULT_AUTH, '')


@mark.integration
@mark.parametrize('argv', [['hs', 'config', 'get', 'logging.level'], ['hs', 'list']])
def test_two_forms_in_one_file(temp_site: Path, monkeypatch, argv: list) -> None:
    """A file giving `auth` and `auth_env` stops every command with one line naming the key and the file."""
    filepath = write_config(temp_site, f'[server]\nauth = "{KEY}"\nauth_env = "{VAR}"\n')
    monkeypatch.setenv(VAR, KEY)
    rc, stdout, stderr = main(argv)
    assert_clean_failure(rc, stdout, stderr, 'server.auth', 'auth, auth_env', str(filepath))
    assert len(stderr.splitlines()) == 1


def start_server(site: Path, *args: str) -> Popen:
    """Start `hs server` writing to `server.log` in `site`, and wait until it listens."""
    log_path = site / 'server.log'
    with open(log_path, mode='w') as stream:
        server = Popen(['hs', 'server', *args], cwd=site, env=os.environ, stdout=stream, stderr=STDOUT)
    deadline = time.monotonic() + DEADLINE
    while 'Server listening' not in log_path.read_text() and server.poll() is None:
        assert time.monotonic() < deadline, log_path.read_text()
        time.sleep(0.1)
    assert server.poll() is None, log_path.read_text()
    return server


def stop_server(server: Popen) -> int:
    """Wait for `server` to exit on its own and return its exit status.

    The server may still be shutting down when its client returns, so it gets `DEADLINE` seconds
    to finish before any signal. Only a server that does not exit by then is interrupted (and
    killed if that fails too), and its status then fails the caller's assertion.
    """
    try:
        return server.wait(timeout=DEADLINE)
    except TimeoutExpired:
        server.send_signal(SIGINT)
    try:
        return server.wait(timeout=DEADLINE)
    except TimeoutExpired:
        server.kill()
        return server.wait()


RESOLVED_FORMS: Final[Dict[str, str]] = {
    'auth': f'[server]\nauth = "{KEY}"\n',
    'auth_env': f'[server]\nauth_env = "{VAR}"\n',
    'auth_eval': f'[server]\nauth_eval = "echo {KEY}"\n',
    'environment': f'[server]\nauth_env = "UNSET_{VAR}"\n',
}
"""Configuration files whose key resolves to `KEY` (`environment` sets `HYPERSHELL_SERVER_AUTH` over it)."""


@mark.integration
@mark.parametrize('form', list(RESOLVED_FORMS))
def test_command_server_and_client(temp_site: Path, monkeypatch, form: str) -> None:
    """`hs server` and `hs client` both take the configured key, in any form, and run every task."""
    site = Path(str(temp_site))
    write_config(site, RESOLVED_FORMS[form])
    monkeypatch.setenv(VAR, KEY)
    monkeypatch.delenv(f'UNSET_{VAR}', raising=False)
    if form == 'environment':
        monkeypatch.setenv('HYPERSHELL_SERVER_AUTH', KEY)
    server = start_server(site, str(create_taskfile(site, ['echo one', 'echo two', 'echo three'])))
    try:
        client = run(['hs', 'client', '-N', '1'], cwd=site, env=os.environ, capture_output=True, text=True, timeout=DEADLINE)
    finally:
        status = stop_server(server)
    log = (site / 'server.log').read_text()
    assert client.returncode == exit_status.success, client.stderr
    assert status == exit_status.success, log
    assert client.stdout.split() == ['one', 'two', 'three']
    assert KEY not in client.stderr and KEY not in log
    assert main(['hs', 'list', '--succeeded', '--count']) == (exit_status.success, '3', '')


@mark.integration
def test_command_submit_queue(temp_site: Path, monkeypatch) -> None:
    """`hs submit -q` takes the key from `auth_env` and reaches a server that does too.

    An in-memory server with no source of its own stops its clients once its queue drains, so the
    queue is deep enough to take every submitted bundle before the client starts.
    """
    site = Path(str(temp_site))
    write_config(site, RESOLVED_FORMS['auth_env'])
    monkeypatch.setenv(VAR, KEY)
    monkeypatch.setenv('HYPERSHELL_SERVER_QUEUESIZE', '100')
    server = start_server(site, '--no-db')
    try:
        submit = run(['hs', 'submit', '-q', '-w', '1'], cwd=site, env=os.environ, input='echo one\necho two\n',
                     capture_output=True, text=True, timeout=DEADLINE)
        client = run(['hs', 'client', '-N', '1', '-T', '20'], cwd=site, env=os.environ, capture_output=True,
                     text=True, timeout=DEADLINE)
    finally:
        status = stop_server(server)
    assert submit.returncode == exit_status.success, submit.stderr
    assert client.returncode == exit_status.success, client.stderr
    assert status == exit_status.success, (site / 'server.log').read_text()
    assert sorted(client.stdout.split()) == ['one', 'two']
    assert KEY not in submit.stderr and KEY not in client.stderr


@mark.integration
def test_command_builtin_default_key(temp_site: Path) -> None:
    """With no key configured, `hs server` still refuses the built-in default, as before."""
    rc, stdout, stderr = main(['hs', 'server', '--forever'])
    assert rc == exit_status.bad_argument
    assert 'Refusing to run server without authentication key' in stderr


UNRESOLVED_FORMS: Final[Dict[str, str]] = {
    'server.auth_env': f'[server]\nauth_env = "{VAR}"\n',
    'server.auth_eval': f'[server]\nauth_eval = "echo {KEY}; exit 4"\n',
}
"""Configuration files whose key cannot resolve (with `VAR` unset), by the key form each names."""


@mark.integration
@mark.parametrize('name', list(UNRESOLVED_FORMS))
@mark.parametrize('argv', [['hs', 'server', '--forever'], ['hs', 'client', '-N', '1'], ['hs', 'submit', '-q', 'echo', 'one']])
def test_command_unresolved_key(temp_site: Path, monkeypatch, name: str, argv: list) -> None:
    """A key that cannot resolve stops each command that uses it, naming the key, with nothing in its place."""
    filepath = write_config(temp_site, UNRESOLVED_FORMS[name])
    monkeypatch.delenv(VAR, raising=False)
    proc = run(argv, cwd=str(temp_site), env=os.environ, capture_output=True, text=True, timeout=DEADLINE)
    assert_clean_failure(proc.returncode, proc.stdout, proc.stderr, name, str(filepath))
    assert DEFAULT_AUTH not in proc.stderr


NOT_UTF8_FORM: Final[str] = f"[server]\nauth_eval = 'printf \"{KEY}\\377\"'\n"
"""A configuration file whose `auth_eval` command succeeds but prints a byte that is not UTF-8."""


@mark.integration
@mark.parametrize('argv', [['hs', 'config', 'get', 'server.auth', '-r'], ['hs', 'server', '--forever'],
                           ['hs', 'client', '-N', '1'], ['hs', 'submit', '-q', 'echo', 'one']])
def test_command_key_output_not_utf8(temp_site: Path, argv: list) -> None:
    """Output that is not UTF-8 stops each command that uses the key, naming the key, with no traceback file."""
    filepath = write_config(temp_site, NOT_UTF8_FORM)
    proc = run(argv, cwd=str(temp_site), env=os.environ, capture_output=True, text=True, timeout=DEADLINE)
    assert_clean_failure(proc.returncode, proc.stdout, proc.stderr, 'server.auth_eval', str(filepath))
    assert DEFAULT_AUTH not in proc.stderr
    assert not list(Path(str(temp_site)).rglob('exception-*.log'))


@mark.integration
def test_command_unresolved_key_unused(temp_site: Path, monkeypatch) -> None:
    """A command that does not use the key runs although the key cannot resolve."""
    write_config(temp_site, UNRESOLVED_FORMS['server.auth_env'])
    monkeypatch.delenv(VAR, raising=False)
    assert main(['hs', 'list', '--count']) == (exit_status.success, '0', '')


@mark.integration
def test_command_environment_over_unset_variable(temp_site: Path, monkeypatch) -> None:
    """`HYPERSHELL_SERVER_AUTH` decides over a file's `auth_env`, so a short key fails as a key, not as configuration."""
    write_config(temp_site, UNRESOLVED_FORMS['server.auth_env'])
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setenv('HYPERSHELL_SERVER_AUTH', 'tooshort')
    rc, stdout, stderr = main(['hs', 'server', '--forever'])
    assert rc == exit_status.bad_argument
    assert 'Authentication key must be at least' in stderr
    assert 'ConfigurationError' not in stderr
