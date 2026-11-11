# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test the authkey given as `auth`, `auth_env` or `auth_eval`, in any configuration layer."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# Standard libs
import os
from pathlib import Path

# External libs
from pytest import mark, raises
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace, ConfigurationError

# Internal libs
from tests import main
from hypershell.core.config import resolve_auth, check_auth_forms, DEFAULT_AUTH


KEY: Final[str] = 'Xk3v9QpL2mN7bR4tW8yZ'
"""A well-formed key that no message may contain."""

OTHER_KEY: Final[str] = 'Hc6dF1gJ5kM0nP2qS4uV'
"""A second well-formed key, for precedence."""

VAR: Final[str] = 'AUTHVAR_TEST_KEY'
"""The variable an `auth_env` names."""


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
