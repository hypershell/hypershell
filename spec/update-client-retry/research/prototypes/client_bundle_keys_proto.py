# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs client` takes its bundle size and wait from the `[client]` section; `[submit]` no longer reaches it.

The layered `client.bundlesize` cases are `tests/test_client_request.py::test_advertise_only_an_explicit_size`.

Prototype (plan time): copied to `tests/test_client_bundle_keys.py` by update-client-retry P1.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List

# Standard libs
import sys
import json
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace, ConfigurationError

# Internal libs
from hypershell.core.config import default
from hypershell.client import advertised_size
from tests import main


def layered(**layers: dict) -> Configuration:
    """A configuration with the built-in defaults and the given extra layers."""
    return Configuration(default=default, **{name: Namespace(data) for name, data in layers.items()})


@mark.unit
def test_submit_bundlesize_not_advertised() -> None:
    """`submit.bundlesize` has no effect on what a client advertises."""
    assert advertised_size(None, layered(env={'submit': {'bundlesize': 7}})) is None
    assert advertised_size(None, layered(local={'submit': {'bundlesize': 3}})) is None


@mark.unit
def test_submit_bundlesize_form_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `submit.bundlesize_env` form is not read by the client either (it raised KeyError before)."""
    monkeypatch.setenv('UCR_BUNDLESIZE', '6')
    assert advertised_size(None, layered(local={'submit': {'bundlesize_env': 'UCR_BUNDLESIZE'}})) is None


@mark.unit
def test_advertise_client_bundlesize_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `_env` and `_eval` forms of `client.bundlesize` are advertised as whole numbers."""
    monkeypatch.setenv('UCR_BUNDLESIZE', '6')
    size = advertised_size(None, layered(local={'client': {'bundlesize_env': 'UCR_BUNDLESIZE'}}))
    assert size == 6 and type(size) is int
    size = advertised_size(None, layered(local={'client': {'bundlesize_eval': 'echo 4'}}))
    assert size == 4 and type(size) is int


@mark.unit
def test_client_bundlesize_form_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `client.bundlesize` form that is not a whole number is refused, naming the key."""
    monkeypatch.setenv('UCR_BUNDLESIZE', 'abc')
    with pytest.raises(ConfigurationError, match='client.bundlesize'):
        advertised_size(None, layered(local={'client': {'bundlesize_env': 'UCR_BUNDLESIZE'}}))


# Defaults are read when the app modules are imported, so each read runs in a fresh interpreter.
READ_CLIENT: str = """\
import json
from hypershell.client import ClientApp, advertised_size
print(json.dumps([advertised_size(None), ClientApp.interface.parse_args([]).bundlewait]))
"""


def read_client() -> tuple[int, Any, str]:
    """What a client started now would advertise and wait, under the current site and environment."""
    rc, stdout, stderr = main([sys.executable, '-c', READ_CLIENT])
    return rc, (json.loads(stdout) if rc == exit_status.success else None), stderr


def write_config(temp_site: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Write the site configuration file and read it (the suite reads none by default)."""
    config_file = Path(temp_site) / 'config.toml'
    config_file.write_text(text, encoding='utf-8')
    monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))


@mark.integration
@mark.parametrize('wait, expected', [('11', 11), ('30s', 30), ('1m30s', 90), ('0.5', 0.5)])
def test_client_keys_from_environment(temp_site: Path, monkeypatch: pytest.MonkeyPatch,
                                      wait: str, expected: Any) -> None:
    """`HYPERSHELL_CLIENT_BUNDLESIZE` is advertised and `HYPERSHELL_CLIENT_BUNDLEWAIT` is the wait."""
    monkeypatch.setenv('HYPERSHELL_CLIENT_BUNDLESIZE', '7')
    monkeypatch.setenv('HYPERSHELL_CLIENT_BUNDLEWAIT', wait)
    rc, values, stderr = read_client()
    assert rc == exit_status.success, stderr
    assert values == [7, expected] and type(values[1]) is type(expected)


@mark.integration
def test_client_keys_from_config_file(temp_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`[client]` bundlesize and bundlewait in a configuration file reach the client."""
    write_config(temp_site, monkeypatch, '[client]\nbundlesize = 7\nbundlewait = "11s"\n')
    rc, values, stderr = read_client()
    assert rc == exit_status.success, stderr
    assert values == [7, 11]


@mark.integration
def test_submit_keys_do_not_reach_client(temp_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`HYPERSHELL_SUBMIT_BUNDLESIZE`/`_BUNDLEWAIT` and a `[submit]` file section leave the client at its defaults."""
    monkeypatch.setenv('HYPERSHELL_SUBMIT_BUNDLESIZE', '9')
    monkeypatch.setenv('HYPERSHELL_SUBMIT_BUNDLEWAIT', '13')
    rc, values, stderr = read_client()
    assert rc == exit_status.success, stderr
    assert values == [None, 5]
    monkeypatch.delenv('HYPERSHELL_SUBMIT_BUNDLESIZE')
    monkeypatch.delenv('HYPERSHELL_SUBMIT_BUNDLEWAIT')
    write_config(temp_site, monkeypatch, '[submit]\nbundlesize = 9\nbundlewait = 13\n')
    rc, values, stderr = read_client()
    assert rc == exit_status.success, stderr
    assert values == [None, 5]


@mark.integration
def test_client_bundlewait_refused(temp_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `client.bundlewait` that is not a duration stops the client's parse, naming the value and the forms."""
    monkeypatch.setenv('HYPERSHELL_CLIENT_BUNDLEWAIT', '5MB')
    rc, _, stderr = main([sys.executable, '-c', READ_CLIENT])
    assert rc != exit_status.success
    assert "'5MB'" in stderr and '1h30m' in stderr


@mark.integration
def test_explicit_options_win(temp_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`-b` and `-w` still override the `[client]` keys."""
    monkeypatch.setenv('HYPERSHELL_CLIENT_BUNDLESIZE', '7')
    monkeypatch.setenv('HYPERSHELL_CLIENT_BUNDLEWAIT', '11')
    code = ('import json; from hypershell.client import ClientApp, advertised_size; '
            'a = ClientApp.interface.parse_args(["-b", "3", "-w", "2"]); '
            'print(json.dumps([advertised_size(a.bundlesize), a.bundlewait]))')
    rc, stdout, stderr = main([sys.executable, '-c', code])
    assert rc == exit_status.success, stderr
    assert json.loads(stdout) == [3, 2]
