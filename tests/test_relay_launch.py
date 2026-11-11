# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay's credential fence: the upstream key never reaches a site client or a task.

Site clients inherit the relay's environment when they are launched, and tasks inherit their
client's. Before the first launch the relay refuses a client invocation or an exported task variable
that would carry the upstream key, and scrubs its own environment of relay settings and of anything
holding the key. Here the key is put in the relay's environment on purpose, under several names, and
must reach neither the scrubbed environment nor the command line a site client is launched with.
The site key the relay mints reaches each site client in its environment, never on its command line.
A configuration file holding relay settings or the key is replaced, for the site, by a private copy
without them. The key itself may be given in any of its documented forms.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Tuple

# Standard libs
import os
import sys
import json
import time
import stat
import functools
import threading
import tomllib
from pathlib import Path
from datetime import timedelta

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace

# Internal libs
from hypershell.core.config import config, DEFAULT_AUTH, ConfigurationError
from hypershell.relay import RelayThread, RelayApp, site_bundlesize, zone_options
from hypershell.core.queue import QueueConfig
from hypershell.cluster.launch import ClientLaunch, AUTH_ENV, OMPI_ENV_LIST
from hypershell.relay.site import (site_cluster, site_launch, fence_site, scrub_environment, check_invocation,
                                   check_exports, CredentialFenceError, site_config_copy, zone_launch, zone_fleet,
                                   end_site_clients)
from hypershell.relay.upstream import upstream_key, upstream_identity
from cmdkit.cli import ArgumentError
from tests import main
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
    """With nowhere to write a copy, the file variable goes when its file holds relay settings or the key."""
    path = tmp_path / 'config.toml'
    path.write_text(text)
    environ = relay_environment(config_file=str(path))
    scrub_environment(KEY, environ)
    assert ('HYPERSHELL_CONFIG_FILE' not in environ) is removed


SITE_SETTINGS = f'''\
[server.tls]
cert = "/certs/site.pem"
cafile = "/certs/site.pem"
[client]
bundlesize = 2
[task]
timeout = 60
[export]
TOOL_HOME = "/opt/tool"
TOKEN = "Bearer {KEY}"
'''


@mark.unit
@mark.parametrize('text, copied', [
    (f'[relay.upstream]\nauth = "{KEY}"\nheartrate = 2\n[relay]\nsize = 4\n' + SITE_SETTINGS, True),
    ('relay.upstream.auth_eval = "cat keyfile"\n[logging]\nlevel = "INFO"\n', True),
    (f'[server]\nauth = "{KEY}"\nport = 50001\n', True),
    ('[logging]\nlevel = "INFO"\n[client]\nbundlesize = 2\n', False),
])
def test_scrub_gives_the_site_a_copy_without_relay_settings(tmp_path: Path, text: str, copied: bool) -> None:
    """A file holding relay settings or the key is replaced by a private copy without them, keeping the rest."""
    path, target = tmp_path / 'config.toml', tmp_path / 'relay.db.site.toml'
    path.write_text(text)
    environ = relay_environment(config_file=str(path))
    removed = scrub_environment(KEY, environ, site_config=str(target))
    assert 'HYPERSHELL_CONFIG_FILE' not in removed
    if not copied:
        assert environ['HYPERSHELL_CONFIG_FILE'] == str(path) and not target.exists()
        return
    assert environ['HYPERSHELL_CONFIG_FILE'] == str(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    copy, original = target.read_text(), tomllib.loads(text)
    assert KEY not in copy
    settings = tomllib.loads(copy)
    assert 'relay' not in settings
    expected = {name: value for name, value in original.items() if name != 'relay'}
    if 'export' in expected:
        assert settings.pop('export') == {'TOOL_HOME': '/opt/tool'}
        expected.pop('export')
    if 'server' in expected:
        expected['server'] = {name: value for name, value in expected['server'].items() if KEY not in str(value)}
    assert settings == expected


@mark.unit
def test_scrub_drops_an_unreadable_config_file(tmp_path: Path, caplog) -> None:
    """A configuration file that cannot be read is not handed on, with a warning."""
    environ = relay_environment(config_file=str(tmp_path / 'missing.toml'))
    with caplog.at_level('WARNING', logger='hypershell.relay.site'):
        scrub_environment(KEY, environ, site_config=str(tmp_path / 'copy.toml'))
    assert 'HYPERSHELL_CONFIG_FILE' not in environ
    assert 'site clients will run without it' in caplog.text
    assert not (tmp_path / 'copy.toml').exists()


@mark.unit
def test_site_config_copy_replaces_an_existing_copy_privately(tmp_path: Path) -> None:
    """A copy left by an earlier run is overwritten and made private again."""
    path, target = tmp_path / 'config.toml', tmp_path / 'copy.toml'
    path.write_text('[relay]\nsize = 2\n[client]\nbundlesize = 3\n')
    target.write_text('stale = true\n')
    target.chmod(0o644)
    assert site_config_copy(str(path), KEY, str(target)) == str(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert tomllib.loads(target.read_text()) == {'client': {'bundlesize': 3}}


# --- The upstream key ---------------------------------------------------------------------------


def layered(**layers: dict) -> Configuration:
    """A configuration over the built-in relay defaults, with the given layers on top."""
    default = Namespace({'relay': {'upstream': {'auth': DEFAULT_AUTH, 'port': 50001}}})
    return Configuration(default=default, **{name: Namespace(layer) for name, layer in layers.items()})


@mark.unit
def test_upstream_key_forms(monkeypatch) -> None:
    """The key may be literal, from a variable or from a command; the highest layer setting one decides."""
    monkeypatch.setenv('MY_RELAY_KEY', KEY)
    assert upstream_key(layered()) == DEFAULT_AUTH
    assert upstream_key(layered(user={'relay': {'upstream': {'auth': 'literal-key'}}})) == 'literal-key'
    assert upstream_key(layered(user={'relay': {'upstream': {'auth_env': 'MY_RELAY_KEY'}}})) == KEY
    assert upstream_key(layered(user={'relay': {'upstream': {'auth_eval': 'echo evaluated-key'}}})) == 'evaluated-key'
    assert upstream_key(layered(user={'relay': {'upstream': {'auth': 'from-user-file'}}},
                                env={'relay': {'upstream': {'auth_env': 'MY_RELAY_KEY'}}})) == KEY
    assert upstream_key(layered(user={'relay': {'upstream': {'auth_env': 'MY_RELAY_KEY'}}},
                                env={'relay': {'upstream': {'auth': 'from-environment'}}})) == 'from-environment'


@mark.unit
def test_upstream_key_refuses_two_forms_in_one_place(monkeypatch) -> None:
    """Two forms of the key in one layer are refused, naming the forms and never the key."""
    monkeypatch.setenv('MY_RELAY_KEY', KEY)
    with pytest.raises(ConfigurationError) as caught:
        upstream_key(layered(user={'relay': {'upstream': {'auth': 'literal', 'auth_env': 'MY_RELAY_KEY'}}}))
    assert 'auth, auth_env' in str(caught.value) and KEY not in str(caught.value)
    with pytest.raises(ConfigurationError):
        upstream_key(layered(user={'relay': {'upstream': {'auth_env': 'NOT_SET_ANYWHERE_RELAY'}}}))


@mark.unit
def test_upstream_key_unset_variable_names_it(monkeypatch) -> None:
    """An `auth_env` naming an unset variable is refused, naming the key and the variable, never the default."""
    monkeypatch.delenv('NOT_SET_ANYWHERE_RELAY', raising=False)
    with pytest.raises(ConfigurationError) as caught:
        upstream_key(layered(user={'relay': {'upstream': {'auth_env': 'NOT_SET_ANYWHERE_RELAY'}}}))
    message = str(caught.value)
    assert 'relay.upstream.auth_env' in message and 'NOT_SET_ANYWHERE_RELAY' in message
    assert DEFAULT_AUTH not in message


@mark.unit
def test_upstream_key_failed_command_names_it() -> None:
    """An `auth_eval` command that exits non-zero is refused, naming the key and the status, not its output."""
    with pytest.raises(ConfigurationError) as caught:
        upstream_key(layered(user={'relay': {'upstream': {'auth_eval': f'echo {KEY}; exit 4'}}}))
    message = str(caught.value)
    assert 'relay.upstream.auth_eval' in message and 'status 4' in message
    assert KEY not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@mark.unit
def test_upstream_key_command_output_not_utf8() -> None:
    """An `auth_eval` command whose output is not UTF-8 is refused, naming the key, not a traceback."""
    with pytest.raises(ConfigurationError) as caught:
        upstream_key(layered(user={'relay': {'upstream': {'auth_eval': f"printf '{KEY}\\377'"}}}))
    message = str(caught.value)
    assert 'relay.upstream.auth_eval' in message and 'UnicodeDecodeError' in message
    assert KEY not in message and '0xff' not in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@mark.unit
def test_upstream_key_refuses_two_forms_in_a_lower_layer(monkeypatch) -> None:
    """Two forms of the key in a lower layer are refused even when a higher layer overrides them."""
    monkeypatch.setenv('MY_RELAY_KEY', KEY)
    with pytest.raises(ConfigurationError) as caught:
        upstream_key(layered(user={'relay': {'upstream': {'auth': 'literal', 'auth_env': 'MY_RELAY_KEY'}}},
                             env={'relay': {'upstream': {'auth': 'from-environment'}}}))
    message = str(caught.value)
    assert 'relay.upstream.auth (auth, auth_env)' in message
    assert KEY not in message and 'from-environment' not in message


@mark.integration
@mark.parametrize('form', ['auth_env', 'auth_eval'])
def test_config_get_relay_upstream_auth(temp_site: Path, monkeypatch, form: str) -> None:
    """`hs config get relay.upstream.auth` prints the key an `auth_env` or `auth_eval` resolves to."""
    value = 'MY_RELAY_KEY' if form == 'auth_env' else 'printf %s "$MY_RELAY_KEY"'
    config_file = Path(str(temp_site)) / 'config.toml'
    config_file.write_text(f"[relay.upstream]\n{form} = '{value}'\n", encoding='utf-8')
    monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))
    monkeypatch.setenv('MY_RELAY_KEY', KEY)
    assert main(['hs', 'config', 'get', 'relay.upstream.auth', '-r']) == (exit_status.success, KEY, '')


@mark.integration
def test_config_get_relay_upstream_auth_unset(temp_site: Path, monkeypatch) -> None:
    """With the variable unset, `hs config get relay.upstream.auth` fails naming the key, with no traceback."""
    config_file = Path(str(temp_site)) / 'config.toml'
    config_file.write_text("[relay.upstream]\nauth_env = 'MY_RELAY_KEY'\n", encoding='utf-8')
    monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))
    monkeypatch.delenv('MY_RELAY_KEY', raising=False)
    rc, stdout, stderr = main(['hs', 'config', 'get', 'relay.upstream.auth', '-r'])
    assert rc == exit_status.bad_config
    assert stdout == ''
    assert 'relay.upstream.auth_env' in stderr and 'MY_RELAY_KEY' in stderr and str(config_file) in stderr
    assert 'Traceback' not in stderr and 'traceback' not in stderr.lower()
    assert DEFAULT_AUTH not in stderr


@mark.integration
@mark.parametrize('argv', [['hs', 'config', 'get', 'relay.upstream.auth', '-r'], ['hs', 'relay', '-H', 'localhost', '-p', '1']])
def test_relay_upstream_auth_output_not_utf8(temp_site: Path, monkeypatch, argv: list) -> None:
    """An `auth_eval` printing a byte that is not UTF-8 stops the command naming the key, with no traceback file."""
    config_file = Path(str(temp_site)) / 'config.toml'
    config_file.write_text(f"[relay.upstream]\nauth_eval = 'printf \"{KEY}\\377\"'\n", encoding='utf-8')
    monkeypatch.setenv('HYPERSHELL_CONFIG_FILE', str(config_file))
    rc, stdout, stderr = main(argv)
    assert rc == exit_status.bad_config
    assert stdout == ''
    assert 'relay.upstream.auth_eval' in stderr and str(config_file) in stderr
    assert 'Traceback' not in stderr and 'traceback' not in stderr.lower()
    assert KEY not in stderr and DEFAULT_AUTH not in stderr
    assert not list(Path(str(temp_site)).rglob('exception-*.log'))


@mark.unit
def test_client_invocation_excludes_the_key(monkeypatch) -> None:
    """With the key in the relay's environment, the site client invocation never carries it.

    The site key is handed over in the client's environment only: no key option and no key on the
    command line, and the autoscaler launches with exactly this invocation.
    """
    for name, value in relay_environment().items():
        monkeypatch.setenv(name, value)
    cluster = site_cluster(address=('127.0.0.1', free_port()), size=2, launcher='srun --exact',
                           launcher_args=['--mpi=none'], zone='gpu')
    launch = site_launch(cluster)
    assert cluster.autoscaler.machine.launch is launch
    argv = launch.argv
    assert argv[:3] == ['srun', '--exact', '--mpi=none'] and 'client' in argv
    assert not any(KEY in arg for arg in argv)
    assert '-k' not in argv and '--auth' not in argv
    site_key = cluster.server.queue.config.auth
    assert site_key != KEY and len(site_key) == 128
    assert not any(site_key in arg for arg in argv)
    assert launch.env == {AUTH_ENV: site_key} and launch.stdin is None and not launch.inherit_env
    assert site_key not in launch.describe()

    environ = dict(os.environ)
    assert fence_site(cluster, KEY, environ=environ, exports={}) != []
    assert not holds_key(environ)


@mark.unit
def test_open_mpi_launcher_forwards_the_site_key_by_name_only() -> None:
    """An Open MPI launcher is told to forward the site key variable, by name; the key stays off the command line."""
    cluster = site_cluster(address=('127.0.0.1', free_port()), size=2, launcher='mpirun -np 1')
    launch = site_launch(cluster)
    site_key = cluster.server.queue.config.auth
    assert launch.argv[:3] == ['mpirun', '-np', '1'] and '-k' not in launch.argv
    assert not any(site_key in arg for arg in launch.argv)
    assert launch.env[AUTH_ENV] == site_key
    assert AUTH_ENV in launch.env[OMPI_ENV_LIST].split(';') and site_key not in launch.env[OMPI_ENV_LIST]
    fence_site(cluster, KEY, environ={}, exports={})


@mark.unit
def test_site_server_evicts_after_the_configured_period() -> None:
    """The site server takes its eviction period from the relay (`[server] evict`), not a fixed 600 seconds."""
    cluster = site_cluster(address=('127.0.0.1', free_port()), evict_after=45)
    assert cluster.server.heartmonitor.machine.evict_after == timedelta(seconds=45)
    relay = RelayThread(address=('127.0.0.1', free_port()), evict=30)
    assert relay.server.heartmonitor.machine.evict_after == timedelta(seconds=30)
    assert RelayThread(address=('127.0.0.1', free_port())).server.heartmonitor.machine.evict_after == \
        timedelta(seconds=config.server.evict)


@mark.unit
@mark.parametrize('bundlesize, num_threads, expected', [(1, 8, 8), (32, 8, 32), (1, 1, 1), (4, 0, 4)])
def test_site_bundle_size_is_never_below_the_executor_threads(bundlesize: int, num_threads: int,
                                                               expected: int) -> None:
    """Site clients ask for, and the puller's bound counts, at least one task per executor thread at once."""
    assert site_bundlesize(bundlesize, num_threads) == expected
    relay = RelayThread(address=('127.0.0.1', free_port()), bundlesize=bundlesize, num_threads=num_threads)
    argv = relay.cluster.launch.argv
    assert argv[argv.index('-b') + 1] == str(expected)
    assert relay.bundlesize == expected and relay.server.scheduler.machine.bundlesize == expected


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
    check_invocation(ClientLaunch(argv=['env', 'hs', 'client'], env={AUTH_ENV: 'site'}), '')
    check_exports({'A': 'b'}, '')


@mark.unit
def test_invocation_check_covers_what_the_launch_adds_to_the_environment() -> None:
    """A variable the launch would add to a client's environment holding the key is refused too."""
    check_invocation(ClientLaunch(argv=['hs', 'client'], env={AUTH_ENV: 'site'}), KEY)
    for env in ({AUTH_ENV: KEY}, {'EXTRA': f'x{KEY}x'}, {f'N_{KEY}': '1'}):
        with pytest.raises(CredentialFenceError) as caught:
            check_invocation(ClientLaunch(argv=['hs', 'client'], env=env), KEY)
        assert KEY not in str(caught.value)


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
    site_keys = set()
    for launch in launches(tmp_path):
        assert not any(UPSTREAM_AUTH in arg for arg in launch['argv'])
        assert not any(UPSTREAM_AUTH in name or UPSTREAM_AUTH in value for name, value in launch['environ'].items())
        assert not any(name.startswith('HYPERSHELL_RELAY_') for name in launch['environ'])
        assert {'MY_UPSTREAM_KEY', 'WRAPPED'}.isdisjoint(launch['environ'])
        assert launch['environ']['SITE_MARK'] == 'a'
        assert 'client' in launch['argv']
        # The site key arrives in the environment only, never on the command line.
        assert '-k' not in launch['argv'] and '--auth' not in launch['argv']
        site_key = launch['environ'].get(AUTH_ENV, '')
        assert len(site_key) == 128 and site_key != UPSTREAM_AUTH
        assert not any(site_key in arg for arg in launch['argv'])
        site_keys.add(site_key)
    assert len(site_keys) == 1, 'site clients of one relay run got different keys'
    assert UPSTREAM_AUTH not in relay.text() and site_keys.pop() not in relay.text()


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


@mark.integration
@mark.parametrize('form', ['auth_env', 'auth_eval'])
def test_relay_takes_the_upstream_key_from_a_variable_or_a_command(relays, tmp_path: Path, form: str) -> None:
    """The documented ways to keep the key out of the file work: the relay connects upstream with it."""
    config = tmp_path / 'relay.toml'
    value = 'MY_RELAY_KEY' if form == 'auth_env' else 'printf %s "$MY_RELAY_KEY"'
    config.write_text(f'[relay.upstream]\n{form} = {json.dumps(value)}\n')
    relay = relays(config=config, HYPERSHELL_RELAY_UPSTREAM_AUTH=None, MY_RELAY_KEY=UPSTREAM_AUTH)
    relay.wait_ready()
    assert 'Connected to upstream server' in relay.text()
    assert relay.drain() == exit_status.success
    assert UPSTREAM_AUTH not in relay.text()


@mark.integration
@mark.parametrize('source', ['environment', 'file'])
def test_relay_refuses_an_exported_task_variable_carrying_the_key(relays, tmp_path: Path, source: str) -> None:
    """An [export] variable holding the key, the real path into every site launch, ends the relay before any launch."""
    launcher = recording_launcher(tmp_path)
    if source == 'environment':
        relay = relays('-N', '2', '--launcher', str(launcher), HYPERSHELL_EXPORT_FENCE_PROBE=f'probe:{UPSTREAM_AUTH}')
    else:
        config = tmp_path / 'relay.toml'
        config.write_text(f'[export]\nFENCE_PROBE = "probe:{UPSTREAM_AUTH}"\n')
        relay = relays('-N', '2', '--launcher', str(launcher), config=config)

    deadline = time.monotonic() + DEADLINE
    while relay.proc.poll() is None:
        assert not launches(tmp_path), f'a site client was launched with the key exported:\n{relay.text()}'
        assert time.monotonic() < deadline, f'relay neither refused nor launched:\n{relay.text()}'
        time.sleep(POLL)
    assert relay.proc.returncode == exit_status.runtime_error
    assert 'Upstream key found in an exported task variable' in relay.text()
    assert UPSTREAM_AUTH not in relay.text()
    assert launches(tmp_path) == []
    assert 'Relay-local server ready' not in relay.text()



# --- Several zones ------------------------------------------------------------------------------


SITE_OPTIONS = dict(num_threads=4, bundlesize=8, bundlewait=2, template='run {}', client_timeout=30)


def zone_word(argv: List[str]) -> List[str]:
    """The zones a client command line serves (each `--zone` value)."""
    return [argv[i + 1] for i, word in enumerate(argv) if word == '--zone']


@mark.unit
def test_zone_launch_is_the_cluster_launch_but_for_its_zone() -> None:
    """A further zone's clients are launched exactly as the cluster's, with the same site key, serving their own zone."""
    cluster = site_cluster(address=('127.0.0.1', free_port()), size=2, launcher='srun --exact',
                           launcher_args=['--mpi=none'], zone='cpu', **SITE_OPTIONS)
    same = zone_launch(cluster, 'cpu', launcher='srun --exact', launcher_args=['--mpi=none'], **SITE_OPTIONS)
    assert same.argv == site_launch(cluster).argv and same.env == site_launch(cluster).env
    other = zone_launch(cluster, 'gpu', launcher='srun --exact', launcher_args=['--mpi=none'], **SITE_OPTIONS)
    assert zone_word(other.argv) == ['gpu'] and zone_word(site_launch(cluster).argv) == ['cpu']
    assert [word for word in other.argv if word != 'gpu'] == [word for word in same.argv if word != 'cpu']
    assert other.env == {AUTH_ENV: cluster.server.queue.config.auth}
    assert '--no-tls' in other.argv  # No site TLS given, as for the cluster's own clients.


@mark.unit
def test_zone_fleet_keeps_a_fixed_size() -> None:
    """A further zone's fleet is a fixed-policy autoscaler of its own size, named for its zone."""
    cluster = site_cluster(address=('127.0.0.1', free_port()))
    fleet = zone_fleet(zone_launch(cluster, 'gpu'), 'gpu', size=3, period=7)
    machine = fleet.machine
    assert machine.policy.name.lower() == 'fixed' and machine.period == 7
    assert machine.init_size == machine.min_size == machine.max_size == 3
    assert fleet.name == 'hypershell-autoscaler-gpu'


@mark.unit
def test_fence_checks_every_zone_launch() -> None:
    """A further zone's launch carrying the key is refused, as the cluster's own would be."""
    cluster = site_cluster(address=('127.0.0.1', free_port()))
    leaking = zone_launch(cluster, 'gpu', launcher=f'srun --comment={KEY}')
    with pytest.raises(CredentialFenceError):
        fence_site(cluster, KEY, environ={}, exports={}, launches=[zone_launch(cluster, 'cpu'), leaking])
    fence_site(cluster, KEY, environ={}, exports={}, launches=[zone_launch(cluster, 'cpu')])


class Proc:
    """A launched client that ends when told to."""

    def __init__(self, running: bool = True) -> None:
        self.status = None if running else 0
        self.pid = id(self)

    def poll(self):
        return self.status

    def wait(self, timeout=None):
        return self.status

    def terminate(self) -> None:
        self.status = -15

    def kill(self) -> None:
        self.status = -9


@mark.unit
def test_site_clients_of_every_fleet_are_ended() -> None:
    """Ending the site ends the clients of every zone's fleet, not only the cluster's."""
    cluster = site_cluster(address=('127.0.0.1', free_port()))
    fleet = zone_fleet(zone_launch(cluster, 'gpu'), 'gpu', size=2)
    ours, theirs, done = [Proc(), Proc()], [Proc(), Proc()], Proc(running=False)
    cluster.autoscaler.machine.clients = [*ours, done]
    fleet.machine.clients = theirs
    assert end_site_clients(cluster, grace=0.1, fleets=[fleet]) == 4
    assert all(proc.status == -15 for proc in ours + theirs) and done.status == 0


@mark.unit
def test_relay_serving_several_zones_has_one_fleet_and_one_identity_per_zone() -> None:
    """The first zone's fleet is the cluster's; each further zone gets its own, with any settings it gives itself."""
    relay = RelayThread(address=('127.0.0.1', free_port()), size=2, launcher='srun', num_threads=4,
                        zones=['cpu', 'gpu', None],
                        zone_settings={'gpu': {'size': 3, 'launcher': 'mpirun', 'launcher_args': ['-np 1']}})
    assert relay.zone == 'cpu' and relay.identity == upstream_identity('cpu')
    assert relay.identities == [upstream_identity(zone) for zone in ('cpu', 'gpu', None)]
    assert zone_word(site_launch(relay.cluster).argv) == ['cpu'] and relay.cluster.autoscaler.machine.min_size == 2
    gpu, default = relay.fleets
    assert zone_word(gpu.machine.launch.argv) == ['gpu'] and gpu.machine.launch.argv[:3] == ['mpirun', '-np', '1']
    assert gpu.machine.min_size == 3
    assert zone_word(default.machine.launch.argv) == [] and default.machine.launch.argv[0] == 'srun'
    assert default.machine.min_size == 2
    site_key = relay.site_key
    assert all(fleet.machine.launch.env[AUTH_ENV] == site_key for fleet in relay.fleets)
    single = RelayThread(address=('127.0.0.1', free_port()), zone='cpu')
    assert single.zones == ['cpu'] and single.fleets == [] and single.identities == [upstream_identity('cpu')]


@mark.unit
@mark.parametrize('value, zones, settings', [
    (None, [None], {}),
    ('', [None], {}),
    ('gpu', ['gpu'], {'gpu': {}}),
    (['cpu', 'gpu'], ['cpu', 'gpu'], {'cpu': {}, 'gpu': {}}),
    (['default', 'gpu'], [None, 'gpu'], {None: {}, 'gpu': {}}),
    ({'cpu': {}, 'gpu': {'size': 2, 'launcher': 'srun'}}, ['cpu', 'gpu'],
     {'cpu': {}, 'gpu': {'size': 2, 'launcher': 'srun'}}),
])
def test_zone_setting_forms(value, zones, settings) -> None:
    """`[relay] zone` is one name, a list of names, or one table per zone with that zone's fleet settings."""
    assert zone_options(value) == (zones, settings)


@mark.unit
@mark.parametrize('value', [['gpu', 'gpu'], ['gpu', ' gpu '], {'gpu': {'num_threads': 4}}, {'gpu': 'srun'}, []])
def test_zone_setting_refuses_repeats_and_unknown_settings(value) -> None:
    """A zone named twice, a setting a zone may not give, or no zone at all is refused."""
    with pytest.raises(ValueError):
        zone_options(value)


@mark.unit
def test_zone_option_repeats() -> None:
    """`--zone` given more than once names every zone the relay serves, in order."""
    assert RelayApp.from_cmdline(['--zone', 'cpu', '--zone', 'gpu']).zones == ['cpu', 'gpu']
    assert RelayApp.from_cmdline(['--zone', 'gpu']).zone == 'gpu'
    with pytest.raises(ArgumentError):
        RelayApp.from_cmdline(['--zone', 'gpu', '--zone', 'gpu']).zones


@mark.unit
@mark.parametrize('size', [0, -1, True, 1.5, '2'])
def test_zone_table_size_must_be_a_whole_number_of_at_least_one(size) -> None:
    """A zone table's `size` is refused unless it is a whole number of at least one; a good one is kept."""
    app = RelayApp.from_cmdline(['--zone', 'gpu'])
    app.__dict__['zone_plan'] = (['gpu'], {'gpu': {'size': size}})  # As `[relay.zone.gpu]` would give it.
    with pytest.raises(ArgumentError, match='Site size of zone gpu'):
        app.zone_settings  # noqa: checked when first read
    good = RelayApp.from_cmdline(['--zone', 'gpu'])
    good.__dict__['zone_plan'] = (['gpu'], {'gpu': {'size': 3}})
    assert good.zone_settings == {'gpu': {'size': 3}}


# --- The relay's start-up order (no server is started: the site stops the run when it is started) ---


class SiteStarted(Exception):
    """Raised in place of starting the relay-local server, so the run ends there."""


class HeldLink:
    """An upstream link that does not come up until told (`up`); it is never called."""

    def __init__(self, *args, up: bool = False, **kwargs) -> None:
        self.up = threading.Event()
        if up:
            self.up.set()
        self.started = False

    def start(self) -> None:
        self.started = True

    def wait_up(self, timeout: float = 1.0) -> bool:
        return self.up.wait(timeout)

    def call(self, role, fn, deadline=None):
        raise AssertionError('No upstream call is made before the site starts')

    def stop(self, timeout=None) -> None:
        pass


def startup_relay(monkeypatch, up: bool, **options) -> Tuple[RelayThread, List[HeldLink], threading.Event]:
    """A relay whose upstream link is a `HeldLink` and whose site raises `SiteStarted` when started.

    The fence runs as written but on empty stand-ins for the environment and the exported task
    variables, so the test process's own environment is never scrubbed.
    """
    import hypershell.relay as relay_module
    links: List[HeldLink] = []

    def link(*args, **kwargs) -> HeldLink:
        links.append(HeldLink(*args, up=up, **kwargs))
        return links[-1]

    monkeypatch.setattr(relay_module, 'UpstreamLink', link)
    monkeypatch.setattr(relay_module, 'fence_site', functools.partial(fence_site, environ={}, exports={}))
    relay = RelayThread(address=('127.0.0.1', free_port()), site_config='',
                        upstream=QueueConfig(host='127.0.0.1', port=free_port(), auth=KEY), **options)
    started = threading.Event()

    def start_site() -> None:
        started.set()
        raise SiteStarted()

    monkeypatch.setattr(relay.cluster, 'start', start_site)
    for fleet in relay.fleets:
        monkeypatch.setattr(fleet, 'start', start_site)
    return relay, links, started


@mark.unit
def test_relay_starts_its_site_only_once_the_upstream_server_answers(monkeypatch) -> None:
    """Until the first upstream connection, no site server is started and no site client launched; then they are."""
    relay, links, started = startup_relay(monkeypatch, up=False, zones=['cpu', 'gpu'])
    failure: List[BaseException] = []

    def run() -> None:
        try:
            relay.run_with_exceptions()
        except BaseException as error:  # noqa: recorded for the test
            failure.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        time.sleep(2.5)  # Two and a half of the relay's waits for the link.
        assert len(links) == 1 and links[0].started
        assert not started.is_set() and thread.is_alive()
    finally:
        for link in links:
            link.up.set()
    thread.join(timeout=30)
    assert not thread.is_alive() and started.is_set()
    assert len(failure) == 1 and isinstance(failure[0], SiteStarted)


@mark.unit
def test_relay_fences_the_launch_of_every_zone_before_connecting(monkeypatch) -> None:
    """A further zone's launcher carrying the upstream key is refused before the relay connects or starts its site."""
    relay, links, started = startup_relay(monkeypatch, up=True, zones=['cpu', 'gpu'],
                                          zone_settings={'gpu': {'launcher': f'srun --comment={KEY}'}})
    with pytest.raises(CredentialFenceError):
        relay.run_with_exceptions()
    assert links == [] and not started.is_set()
    clean, links, started = startup_relay(monkeypatch, up=True, zones=['cpu', 'gpu'])
    with pytest.raises(SiteStarted):
        clean.run_with_exceptions()
    assert len(links) == 1 and started.is_set()
