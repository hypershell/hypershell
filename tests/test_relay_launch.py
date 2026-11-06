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
from typing import Dict, List

# Standard libs
import os
import sys
import json
import time
import stat
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
from hypershell.relay import RelayThread, site_bundlesize
from hypershell.cluster.launch import ClientLaunch, AUTH_ENV, OMPI_ENV_LIST
from hypershell.relay.site import (site_cluster, site_launch, fence_site, scrub_environment, check_invocation,
                                   check_exports, CredentialFenceError, site_config_copy)
from hypershell.relay.upstream import upstream_key
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
