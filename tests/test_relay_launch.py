# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's credential fence: the upstream key never reaches a site client or a task.

Site clients inherit the relay's environment when they are launched, and tasks inherit their
client's. Before the first launch the relay refuses a client invocation or an exported task variable
that would carry the upstream key, and scrubs its own environment of relay settings and of anything
holding the key. Here the key is put in the relay's environment on purpose, under several names, and
must reach neither the scrubbed environment nor the command line a site client is launched with.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List

# Standard libs
import os
import sys
import json
import time
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from hypershell.relay.site import (site_cluster, fence_site, scrub_environment, check_invocation, check_exports,
                                   CredentialFenceError)
from tests.test_relay_db import relays, upstream, free_port, UPSTREAM_AUTH, DEADLINE, POLL  # noqa: fixtures


KEY = 'fence-upstream-key-5f0c9a7e31d24b68'


def relay_environment(config_file: str = '') -> Dict[str, str]:
    """A relay's environment with the upstream key placed in it on purpose, beside ordinary settings."""
    return {
        'PATH': '/usr/bin:/bin',
        'HOME': '/home/someone',
        'HYPERSHELL_SITE': '/site',
        'HYPERSHELL_DATABASE_FILE': '/site/primary.db',
        'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
        'HYPERSHELL_CONFIG_FILE': config_file,
        'SITE_MARK': 'a',
        'HYPERSHELL_RELAY_UPSTREAM_AUTH': KEY,
        'HYPERSHELL_RELAY_UPSTREAM_HOST': 'upstream.example',
        'HYPERSHELL_RELAY_SIZE': '2',
        'MY_UPSTREAM_KEY': KEY,
        'WRAPPED': f'prefix:{KEY}:suffix',
        f'NAMED_{KEY}': 'x',
    }


def holds_key(items: Dict[str, str]) -> bool:
    """True if any name or value holds the key."""
    return any(KEY in name or KEY in value for name, value in items.items())


@mark.unit
def test_scrub_removes_relay_settings_and_anything_holding_the_key() -> None:
    """Every relay setting and every variable holding the key goes; everything else stays as it was."""
    environ = relay_environment()
    removed = scrub_environment(KEY, environ)

    assert not holds_key(environ)
    assert not any(name.startswith('HYPERSHELL_RELAY_') for name in environ)
    assert removed == sorted(['HYPERSHELL_RELAY_UPSTREAM_AUTH', 'HYPERSHELL_RELAY_UPSTREAM_HOST',
                              'HYPERSHELL_RELAY_SIZE', 'MY_UPSTREAM_KEY', 'WRAPPED', f'NAMED_{KEY}'])
    assert environ == {'PATH': '/usr/bin:/bin', 'HOME': '/home/someone', 'HYPERSHELL_SITE': '/site',
                       'HYPERSHELL_DATABASE_FILE': '/site/primary.db', 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
                       'HYPERSHELL_CONFIG_FILE': '', 'SITE_MARK': 'a'}


@mark.unit
def test_scrub_with_no_key_removes_only_relay_settings() -> None:
    """An empty key matches nothing (it would match every value); relay settings still go."""
    environ = relay_environment()
    scrub_environment('', environ)
    assert 'MY_UPSTREAM_KEY' in environ and 'PATH' in environ
    assert not any(name.startswith('HYPERSHELL_RELAY_') for name in environ)


@mark.unit
@mark.parametrize('text, removed', [
    (f'[relay.upstream]\nauth = "{KEY}"\n', True),
    ('[relay]\nsize = 4\n', True),
    ('[ relay . upstream ]\nauth_env = "MY_UPSTREAM_KEY"\n', True),
    ('relay.upstream.auth_eval = "cat keyfile"\n', True),
    (f'[export]\nTOKEN = "{KEY}"\n', True),
    ('[logging]\nlevel = "INFO"\n[client]\nbundlesize = 2\n', False),
    ('[server]\nauth = "something-else"\n# a relay is configured elsewhere\n', False),
])
def test_scrub_drops_a_config_file_holding_relay_settings(tmp_path: Path, text: str, removed: bool) -> None:
    """The configuration file variable goes when its file holds relay settings or the key, and only then."""
    path = tmp_path / 'config.toml'
    path.write_text(text)
    environ = relay_environment(config_file=str(path))
    scrub_environment(KEY, environ)
    assert ('HYPERSHELL_CONFIG_FILE' not in environ) is removed


@mark.unit
def test_scrub_drops_an_unreadable_config_file(tmp_path: Path) -> None:
    """A configuration file that cannot be read is not handed on."""
    environ = relay_environment(config_file=str(tmp_path / 'missing.toml'))
    scrub_environment(KEY, environ)
    assert 'HYPERSHELL_CONFIG_FILE' not in environ


@mark.unit
def test_client_invocation_excludes_the_key(monkeypatch) -> None:
    """With the key in the relay's environment, the site client command line never carries it."""
    for name, value in relay_environment().items():
        monkeypatch.setenv(name, value)
    cluster = site_cluster(address=('127.0.0.1', free_port()), size=2, launcher='srun --exact',
                           launcher_args=['--mpi=none'], zone='gpu')
    argv = cluster.autoscaler.machine.launcher
    assert argv[:3] == ['srun', '--exact', '--mpi=none'] and 'client' in argv
    assert not any(KEY in arg for arg in argv)
    site_key = argv[argv.index('-k') + 1]
    assert site_key != KEY and len(site_key) == 128

    environ = dict(os.environ)
    assert fence_site(cluster, KEY, environ=environ, exports={}) != []
    assert not holds_key(environ)


@mark.unit
def test_fence_never_logs_the_key(caplog) -> None:
    """Removed names are logged, but a name holding the key is not spelled out."""
    cluster = site_cluster(address=('127.0.0.1', free_port()))
    environ = relay_environment()
    with caplog.at_level('DEBUG', logger='hypershell.relay.site'):
        removed = fence_site(cluster, KEY, environ=environ, exports={})
    assert f'NAMED_{KEY}' in removed
    assert 'Removed from site environment' in caplog.text and 'WRAPPED' in caplog.text
    assert KEY not in caplog.text


@mark.unit
@mark.parametrize('field', ['template', 'launcher', 'launcher_args'])
def test_invocation_carrying_the_key_is_refused(field: str) -> None:
    """A template or launcher that would put the key on a site client's command line is refused, unnamed."""
    options = {'template': '{}', 'launcher': 'env', 'launcher_args': None}
    options[field] = f'--token={KEY}' if field != 'launcher_args' else [f'--token={KEY}']
    if field == 'template':
        options[field] = f'curl -H "X-Key: {KEY}" {{}}'
    cluster = site_cluster(address=('127.0.0.1', free_port()), **options)
    environ = relay_environment()
    with pytest.raises(CredentialFenceError) as caught:
        fence_site(cluster, KEY, environ=environ, exports={})
    assert KEY not in str(caught.value)
    assert environ == relay_environment(), 'the environment was scrubbed before the refusal'


@mark.unit
def test_exported_task_variable_carrying_the_key_is_refused() -> None:
    """A task variable exported from configuration that holds the key is refused."""
    check_exports({'TOOL_HOME': '/opt/tool'}, KEY)
    for exports in ({'TOKEN': f'Bearer {KEY}'}, {f'X_{KEY}': '1'}):
        with pytest.raises(CredentialFenceError) as caught:
            check_exports(exports, KEY)
        assert KEY not in str(caught.value)


@mark.unit
def test_invocation_check_ignores_an_empty_key() -> None:
    """An empty key is not found in every argument."""
    check_invocation(['env', 'hs', 'client', '-k', 'site'], '')
    check_exports({'A': 'b'}, '')


# --- A relay process ---------------------------------------------------------------------------


LAUNCHER = '''\
#!{python}
# Record how a site client would be launched (its command line and environment), then exit.
import os, sys, json
with open(os.path.join({out!r}, f'{{os.getpid()}}.json'), mode='w') as stream:
    json.dump({{'argv': sys.argv, 'environ': dict(os.environ)}}, stream)
'''


def recording_launcher(tmp_path: Path) -> Path:
    """A launcher that records each launch under `tmp_path/launches`."""
    out = tmp_path / 'launches'
    out.mkdir()
    path = tmp_path / 'record-launch'
    path.write_text(LAUNCHER.format(python=sys.executable, out=str(out)))
    path.chmod(0o755)
    return path


def launches(tmp_path: Path) -> List[dict]:
    """Every launch recorded so far."""
    found = []
    for path in sorted((tmp_path / 'launches').glob('*.json')):
        try:
            found.append(json.loads(path.read_text()))
        except ValueError:
            pass  # Still being written.
    return found


@mark.integration
def test_relay_launches_site_clients_without_the_key(relays, tmp_path: Path) -> None:
    """A relay given the key under several names launches its site clients with none of them."""
    launcher = recording_launcher(tmp_path)
    relay = relays('-N', '2', '--launcher', str(launcher),
                   MY_UPSTREAM_KEY=UPSTREAM_AUTH, WRAPPED=f'prefix:{UPSTREAM_AUTH}:suffix', SITE_MARK='a')
    relay.wait_ready()
    deadline = time.monotonic() + DEADLINE
    while len(launches(tmp_path)) < 2:
        assert relay.proc.poll() is None, relay.text()
        assert time.monotonic() < deadline, f'no site client launched:\n{relay.text()}'
        time.sleep(POLL)

    assert relay.drain() == exit_status.success
    for launch in launches(tmp_path):
        assert not any(UPSTREAM_AUTH in arg for arg in launch['argv'])
        assert not any(UPSTREAM_AUTH in name or UPSTREAM_AUTH in value for name, value in launch['environ'].items())
        assert not any(name.startswith('HYPERSHELL_RELAY_') for name in launch['environ'])
        assert {'MY_UPSTREAM_KEY', 'WRAPPED'}.isdisjoint(launch['environ'])
        assert launch['environ']['SITE_MARK'] == 'a'
        assert 'client' in launch['argv']
        assert launch['argv'][launch['argv'].index('-k') + 1] != UPSTREAM_AUTH
    assert UPSTREAM_AUTH not in relay.text()


@mark.integration
def test_relay_refuses_a_template_carrying_the_key(relays, tmp_path: Path) -> None:
    """A template that would put the key on the site's command lines ends the relay before any launch."""
    launcher = recording_launcher(tmp_path)
    relay = relays('-N', '2', '--launcher', str(launcher),
                   HYPERSHELL_RELAY_TEMPLATE=f'echo {UPSTREAM_AUTH} {{}}')

    assert relay.wait_exit() == exit_status.runtime_error
    assert 'Upstream key found in the site client command line' in relay.text()
    assert UPSTREAM_AUTH not in relay.text()
    assert launches(tmp_path) == []
    assert 'Relay-local server ready' not in relay.text()
