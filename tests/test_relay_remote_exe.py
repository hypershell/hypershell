# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The program a relay's site clients are launched as, and the Python API default.

`hs relay --remote-exe PATH`, else `relay.remote_exe` (`HYPERSHELL_RELAY_REMOTE_EXE`), else the
relay's own `hs`: the program the user ran, by its absolute path, kept across the re-execution that
binds the relay database; else the `hs` in the interpreter's scripts directory; else `hs` from
`PATH`. Every zone's clients are launched as that one program. The built-in default of the cluster
classes (`DEFAULT_REMOTE_EXE`) is `hs`.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional

# Standard libs
import os
import re
import sys
import shlex
import shutil
import signal
import inspect
import sysconfig
import subprocess
import time
from pathlib import Path

# External libs
import pytest
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status
from cmdkit.config import Namespace

# Internal libs
import hypershell.cluster
import hypershell.cluster.ssh
import hypershell.cluster.remote
import hypershell.relay as relay_module
from hypershell.relay import RelayApp, RelayThread, APP_USAGE, APP_HELP
from hypershell.relay.site import site_cluster, site_launch, zone_launch, scrub_environment
from hypershell.cluster.ssh import SSHCluster
from hypershell.cluster.remote import RemoteCluster, AutoScalingCluster
from hypershell.core.config import config, default, PARAM_UNSET, RELAY_MARKER, is_relay_variable
from hypershell.core.config_schema import KEYS, EMPTY_UNSET, Kind, match_name
from tests.conftest import free_port
from tests.test_config_schema import parse_reference, documented_default
from tests.test_relay_db import relays, upstream, DEADLINE, POLL  # noqa: fixtures
from tests.test_relay_launch import recording_launcher, launches

REPO = Path(__file__).resolve().parent.parent

PROGRAM_MARKER = '_HYPERSHELL_RELAY_PROGRAM'
REEXEC_MARKER = '_HYPERSHELL_RELAY_REEXEC'
SCRIPTS_HS = os.path.join(sysconfig.get_path('scripts'), 'hs')

relay_program = getattr(relay_module, 'relay_program', None)

FIXED = pytest.mark.xfail(strict=True, reason='relay-remote-exe P2')
API = pytest.mark.xfail(strict=True, reason='relay-remote-exe P3')


def executable(path: Path) -> Path:
    """Write an executable file at `path`."""
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return path


# --- R5 end to end: a relay from a symlinked `bin/hs` ---------------------------------------------------------


UPSTREAM_KEY = 'relay-remote-exe-upstream-key-0123456789'
TASKS = 3


class Installation:
    """A `bin/` holding only `hs` and `hsx`, symlinks to this interpreter's own, with a site of its own."""

    def __init__(self, root: Path) -> None:
        scripts = Path(sysconfig.get_path('scripts'))
        self.bin = root / 'inst' / 'bin'
        self.bin.mkdir(parents=True)
        for name in ('hs', 'hsx'):
            (self.bin / name).symlink_to(scripts / name)
        self.hs = str(self.bin / 'hs')
        self.root = root
        site = root / 'site'
        site.mkdir()
        self.env = {name: value for name, value in os.environ.items()
                    if not name.startswith('HYPERSHELL_') and name != 'PATH'}
        self.env.update({'PATH': f'{self.bin}:/usr/bin:/bin', 'HYPERSHELL_SITE': str(site),
                         'HYPERSHELL_CONFIG_FILE': ''})
        self.procs: List[subprocess.Popen] = []

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([self.hs, *args], env=self.env, capture_output=True, text=True, timeout=DEADLINE)

    def start(self, name: str, args: List[str], **env: str) -> subprocess.Popen:
        log = open(self.root / f'{name}.log', mode='w')
        proc = subprocess.Popen([self.hs, *args], env={**self.env, **env}, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(proc)
        return proc

    def text(self, name: str) -> str:
        return (self.root / f'{name}.log').read_text()

    def end(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait(DEADLINE)


@pytest.fixture
def installation(tmp_path: Path) -> Iterator[Installation]:
    inst = Installation(tmp_path)
    try:
        yield inst
    finally:
        inst.end()


@mark.integration
@FIXED
@mark.parametrize('database', [pytest.param(False, id='plain'), pytest.param(True, id='database')])
def test_relay_from_a_symlinked_hs_runs_its_clients(installation: Installation, database: bool) -> None:
    """A relay from a `bin/` with only `hs` and `hsx` launches its clients as that `hs`, and they run the tasks."""
    inst = installation
    assert shutil.which('hyper-shell', path=inst.env['PATH']) is None
    (inst.root / 'tasks.in').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    assert inst.run('submit', str(inst.root / 'tasks.in')).returncode == exit_status.success
    up = free_port()
    server = inst.start('server', ['server', '--forever', '--no-tls', '-H', '127.0.0.1', '-p', str(up)],
                        HYPERSHELL_SERVER_AUTH=UPSTREAM_KEY)
    extra = ['--database', str(inst.root / 'relay.db')] if database else []
    relay = inst.start('relay', ['relay', '-H', '127.0.0.1', '-p', str(up), '--no-tls', '--bind', '127.0.0.1',
                                 '--port', str(free_port()), '-N', '1', *extra],
                       HYPERSHELL_RELAY_UPSTREAM_AUTH=UPSTREAM_KEY, HYPERSHELL_LOGGING_LEVEL='DEBUG')
    deadline = time.monotonic() + DEADLINE
    while inst.run('list', '--remaining', '--count').stdout.strip() != '0':
        assert relay.poll() is None, inst.text('relay')
        assert server.poll() is None, inst.text('server')
        assert time.monotonic() < deadline, f'tasks remain:\n{inst.text("relay")}'
        time.sleep(0.5)
    text = inst.text('relay')
    assert f'Autoscale launcher (zone default): {shlex.quote(inst.hs)} client ' in text
    assert 'hyper-shell' not in text
    assert ('Re-executing to bind relay database' in text) is database
    relay.send_signal(signal.SIGUSR1)
    assert relay.wait(DEADLINE) == exit_status.success, inst.text('relay')


# --- R3: option > key > the relay's own program, in a relay process ---------------------------------------


def wait_launch(relay: Any, tmp_path: Path) -> List[dict]:
    """Every launch recorded once there is one."""
    deadline = time.monotonic() + DEADLINE
    while not (found := launches(tmp_path)):
        assert relay.proc.poll() is None, relay.text()
        assert time.monotonic() < deadline, f'no site client launched:\n{relay.text()}'
        time.sleep(POLL)
    return found


@mark.integration
@FIXED
@mark.parametrize('args, env, expected, reexec', [
    pytest.param([], {'HYPERSHELL_RELAY_REMOTE_EXE': '/opt/key/bin/hs'}, '/opt/key/bin/hs', False, id='key'),
    pytest.param(['--remote-exe', '/opt/opt/bin/hs'], {'HYPERSHELL_RELAY_REMOTE_EXE': '/opt/key/bin/hs'},
                 '/opt/opt/bin/hs', False, id='option-over-key'),
    pytest.param([], {'HYPERSHELL_RELAY_REMOTE_EXE': ''}, SCRIPTS_HS, False, id='empty-key'),
    pytest.param(['--database', 'relay.db'], {'HYPERSHELL_RELAY_REMOTE_EXE': '/opt/key/bin/hs'},
                 '/opt/key/bin/hs', True, id='database-key'),
    pytest.param(['--database', 'relay.db'], {}, SCRIPTS_HS, True, id='database-own'),
])
def test_relay_launches_its_clients_as(relays, tmp_path: Path, args: List[str], env: Dict[str, str],
                                       expected: str, reexec: bool) -> None:
    """The word after the launcher is the option, else the key, else the relay's own program.

    The harness's relay runs as a non-executable `tmp/hs`, so its own program is the interpreter's
    scripts `hs`. Re-executed to bind its database, it launches the same program, and the variable
    that carried it there reaches no site client.
    """
    args = [str(tmp_path / arg) if arg == 'relay.db' else arg for arg in args]
    relay = relays('--launcher', str(recording_launcher(tmp_path)), *args, **env)
    relay.wait_ready()
    found = wait_launch(relay, tmp_path)
    assert relay.drain() == exit_status.success
    assert {launch['argv'][1] for launch in found} == {expected}
    assert all(launch['argv'][2] == 'client' for launch in found)
    assert ('Re-executing to bind relay database' in relay.text()) is reexec
    for launch in found:
        assert PROGRAM_MARKER not in launch['environ']
        assert not any(name.startswith('HYPERSHELL_RELAY_') for name in launch['environ'])


# --- relay_program: the relay's own program ----------------------------------------------------------------


@mark.unit
@FIXED
def test_relay_program_takes_an_executable_by_absolute_path(tmp_path: Path) -> None:
    """An executable file named by absolute path is itself."""
    exe = executable(tmp_path / 'hs')
    assert relay_program(str(exe)) == str(exe)


@mark.unit
@FIXED
def test_relay_program_makes_a_relative_path_absolute(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """An executable file named by relative path is its absolute path."""
    (tmp_path / 'bin').mkdir()
    exe = executable(tmp_path / 'bin' / 'hs')
    monkeypatch.chdir(tmp_path)
    assert relay_program(os.path.join('bin', 'hs')) == str(exe)


@mark.unit
@FIXED
def test_relay_program_keeps_a_symlink(tmp_path: Path) -> None:
    """A symlink is named as the link the user ran, not its target."""
    (tmp_path / 'libexec').mkdir()
    (tmp_path / 'bin').mkdir()
    target = executable(tmp_path / 'libexec' / 'hs')
    link = tmp_path / 'bin' / 'hs'
    link.symlink_to(target)
    assert relay_program(str(link)) == str(link)


@mark.unit
@FIXED
@mark.parametrize('argv0', [
    pytest.param('not-executable', id='not-executable'),
    pytest.param('-c', id='dash-c'),
    pytest.param(None, id='none'),
    pytest.param('missing', id='missing'),
])
def test_relay_program_falls_back_to_the_scripts_hs(tmp_path: Path, argv0: Optional[str]) -> None:
    """No usable `argv[0]`: the `hs` in this interpreter's scripts directory."""
    assert os.access(SCRIPTS_HS, os.X_OK)
    if argv0 == 'not-executable':
        argv0 = str(tmp_path / 'hs')
        Path(argv0).write_text('')
    elif argv0 == 'missing':
        argv0 = str(tmp_path / 'missing' / 'hs')
    assert relay_program(argv0) == SCRIPTS_HS


@mark.unit
@FIXED
def test_relay_program_falls_back_to_hs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """No usable `argv[0]` and no `hs` in the scripts directory: `hs`, from `PATH`."""
    empty = tmp_path / 'scripts'
    empty.mkdir()
    monkeypatch.setattr(sysconfig, 'get_path', lambda *args, **kwargs: str(empty))
    assert relay_program(None) == 'hs'


# --- RelayApp.program: which argv[0], and the carried value ---------------------------------------------------


@pytest.fixture
def no_markers(monkeypatch: MonkeyPatch) -> MonkeyPatch:
    """Neither private relay variable in the environment."""
    monkeypatch.delenv(PROGRAM_MARKER, raising=False)
    monkeypatch.delenv(REEXEC_MARKER, raising=False)
    return monkeypatch


@mark.unit
@FIXED
def test_relay_app_program_is_argv0_on_its_own_command_line(tmp_path: Path, no_markers: MonkeyPatch) -> None:
    """A process whose command line is this relay's names the program it was started as."""
    exe = executable(tmp_path / 'hs')
    no_markers.setattr(sys, 'argv', [str(exe), 'relay', '--port', '1'])
    no_markers.setattr(RelayApp, 'argv', ['--port', '1'])
    assert RelayApp.from_cmdline(['--port', '1']).program == str(exe)


@mark.unit
@FIXED
def test_relay_app_program_ignores_an_embedding_program(tmp_path: Path, no_markers: MonkeyPatch) -> None:
    """`main(['relay', ...])` inside another program does not name that program."""
    exe = executable(tmp_path / 'x.py')
    no_markers.setattr(sys, 'argv', [str(exe), 'tests/x.py'])
    no_markers.setattr(RelayApp, 'argv', ['--port', '1'])
    program = RelayApp.from_cmdline(['--port', '1']).program
    assert program != str(exe) and program == SCRIPTS_HS


@mark.unit
@FIXED
def test_relay_app_program_takes_the_carried_value(no_markers: MonkeyPatch) -> None:
    """A re-executed relay takes the program it was handed, and keeps the variable out of its environment."""
    no_markers.setenv(PROGRAM_MARKER, '/carried/hs')
    no_markers.setenv(REEXEC_MARKER, '1')
    no_markers.setattr(sys, 'argv', ['-c', 'relay'])
    no_markers.setattr(RelayApp, 'argv', [])
    assert RelayApp.from_cmdline([]).program == '/carried/hs'
    assert PROGRAM_MARKER not in os.environ


@mark.unit
@FIXED
def test_relay_app_program_ignores_a_stray_carried_value(no_markers: MonkeyPatch) -> None:
    """Without the re-execution marker the variable is dropped and ignored."""
    no_markers.setenv(PROGRAM_MARKER, '/carried/hs')
    no_markers.setattr(sys, 'argv', ['-c', 'relay'])
    no_markers.setattr(RelayApp, 'argv', [])
    assert RelayApp.from_cmdline([]).program == SCRIPTS_HS
    assert PROGRAM_MARKER not in os.environ


# --- RelayApp.client_exe: option > key > program -----------------------------------------------------------


def with_remote_exe(value: Any) -> Namespace:
    """The configuration with `relay.remote_exe` set."""
    data = config.copy()
    data['relay'] = {**data['relay'], 'remote_exe': value}
    return Namespace(data)


@mark.unit
@FIXED
@mark.parametrize('argv, key, expected', [
    pytest.param(['--remote-exe', '/opt/opt/bin/hs'], PARAM_UNSET, '/opt/opt/bin/hs', id='option'),
    pytest.param([], '/opt/key/bin/hs', '/opt/key/bin/hs', id='key'),
    pytest.param(['--remote-exe', '/opt/opt/bin/hs'], '/opt/key/bin/hs', '/opt/opt/bin/hs', id='option-over-key'),
    pytest.param([], '', '/sentinel/hs', id='empty-key'),
    pytest.param([], None, '/sentinel/hs', id='none-key'),
    pytest.param([], PARAM_UNSET, '/sentinel/hs', id='unset'),
])
def test_relay_app_client_exe(monkeypatch: MonkeyPatch, argv: List[str], key: Any, expected: str) -> None:
    """`--remote-exe`, else `relay.remote_exe`, else the relay's own program."""
    monkeypatch.setattr(relay_module, 'config', with_remote_exe(key))
    monkeypatch.setattr(RelayApp, 'program', '/sentinel/hs')
    assert RelayApp.from_cmdline(argv).client_exe == expected


# --- The re-execution carries the program -------------------------------------------------------------------


class Executed(Exception):
    """Raised by the stand-in `os.execve`."""


@mark.unit
@FIXED
def test_reexecution_hands_on_the_program(tmp_path: Path, no_markers: MonkeyPatch) -> None:
    """A relay re-executing to bind its database hands its program to the new process."""
    recorded: Dict[str, Any] = {}

    def execve(path: str, argv: List[str], env: Dict[str, str]) -> None:
        recorded.update(path=path, argv=argv, env=env)
        raise Executed()

    no_markers.setattr(os, 'execve', execve)
    no_markers.setattr(RelayApp, 'program', '/opt/x/bin/hs')
    no_markers.setattr(RelayApp, 'argv', [])
    app = RelayApp.from_cmdline(['--database', str(tmp_path / 'other.db')])
    with pytest.raises(Executed):
        app.bind_database()
    assert recorded['env'][PROGRAM_MARKER] == '/opt/x/bin/hs'
    assert recorded['env'][REEXEC_MARKER] == '1'
    assert recorded['env'][RELAY_MARKER] == '1'


# --- Every fleet launches the one program --------------------------------------------------------------------


def programs(relay: RelayThread) -> List[List[str]]:
    """The launch argv of the cluster's own fleet and of each further fleet."""
    machines = [relay.cluster.autoscaler.machine, *(fleet.machine for fleet in relay.fleets)]
    return [machine.launch.argv for machine in machines]


@mark.unit
@FIXED
def test_relay_thread_launches_every_fleet_as_its_program() -> None:
    """The cluster's zone and each further zone, fixed or following a topology, launch `remote_exe`."""
    relay = RelayThread(address=('127.0.0.1', free_port()), zones=['cpu', 'gpu', None], launcher=None,
                        remote_exe='/opt/x/bin/hs')
    found = programs(relay)
    assert len(found) == 3
    assert [argv[:2] for argv in found] == [['/opt/x/bin/hs', 'client']] * 3
    assert site_launch(relay.cluster).argv[0] == '/opt/x/bin/hs'


@mark.unit
@FIXED
def test_relay_thread_launches_its_program_after_the_launcher() -> None:
    """With a launcher, the word after it is `remote_exe`, in every fleet."""
    relay = RelayThread(address=('127.0.0.1', free_port()), zones=['cpu', 'gpu', None], launcher='srun --exact',
                        remote_exe='/opt/x/bin/hs')
    assert [argv[:4] for argv in programs(relay)] == [['srun', '--exact', '/opt/x/bin/hs', 'client']] * 3


@mark.unit
@FIXED
def test_site_cluster_and_zone_launch_take_the_program() -> None:
    """`site_cluster` and `zone_launch` launch the program they are given."""
    cluster = site_cluster(address=('127.0.0.1', free_port()), zone='cpu', remote_exe='/opt/x/bin/hs')
    assert site_launch(cluster).argv[:2] == ['/opt/x/bin/hs', 'client']
    assert cluster.autoscaler.machine.launch.argv[0] == '/opt/x/bin/hs'
    assert zone_launch(cluster, 'gpu', remote_exe='/opt/x/bin/hs').argv[:2] == ['/opt/x/bin/hs', 'client']


# --- The key: relay.remote_exe -----------------------------------------------------------------------------


@mark.unit
@FIXED
def test_relay_remote_exe_is_a_declared_text_key() -> None:
    """`relay.remote_exe` is declared text, unset by default, and an empty variable sets nothing."""
    assert KEYS.get('relay.remote_exe') is Kind.TEXT
    assert 'relay.remote_exe' in EMPTY_UNSET
    assert match_name('HYPERSHELL_RELAY_REMOTE_EXE') == (('relay', 'remote_exe'), Kind.TEXT)
    assert default.relay.get('remote_exe') is PARAM_UNSET


@mark.unit
def test_relay_remote_exe_is_fenced() -> None:
    """Like every relay setting, the variable never reaches the site environment."""
    assert is_relay_variable('HYPERSHELL_RELAY_REMOTE_EXE')
    environ = {'HYPERSHELL_RELAY_REMOTE_EXE': '/opt/x/bin/hs', 'PATH': '/usr/bin:/bin'}
    assert scrub_environment('', environ) == ['HYPERSHELL_RELAY_REMOTE_EXE']
    assert environ == {'PATH': '/usr/bin:/bin'}


# --- Help, docs and completions --------------------------------------------------------------------------------


def text(path: str) -> str:
    return (REPO / path).read_text(encoding='utf-8')


@mark.unit
@FIXED
def test_relay_help_lists_remote_exe_after_launcher_args() -> None:
    """`hs relay --help` lists `--remote-exe` after `--launcher-args`, in the usage line and the options."""
    assert '[--remote-exe PATH]' in APP_USAGE
    assert APP_USAGE.index('[--launcher-args ARGS]') < APP_USAGE.index('[--remote-exe PATH]')
    options = APP_HELP[APP_HELP.index('Options:'):]
    assert options.index('--launcher-args') < options.index('--remote-exe') < options.index('--bind')
    assert '--remote-exe' in APP_HELP[:APP_HELP.index('Options:')]


@mark.unit
@FIXED
def test_relay_remote_exe_documented_in_the_help_pages() -> None:
    """The usage and help includes list `--remote-exe` after `--launcher-args`, and no longer name `hyper-shell`."""
    usage = text('docs/_include/relay_usage.rst')
    assert '``[--remote-exe PATH]``' in usage
    assert usage.index('``[--launcher-args ARGS]``') < usage.index('``[--remote-exe PATH]``')
    help_text = text('docs/_include/relay_help.rst')
    assert 'hyper-shell client' not in help_text
    assert '``--remote-exe`` *PATH*' in help_text
    assert (help_text.index('``--launcher-args`` *ARGS*') < help_text.index('``--remote-exe`` *PATH*')
            < help_text.index('``--bind`` *ADDR*'))
    entry = help_text[help_text.index('``--remote-exe`` *PATH*'):help_text.index('``--bind`` *ADDR*')]
    assert 'relay.remote_exe' in entry and '--launcher' in entry
    assert re.search(r'\.\. versionadded:: 2\.9\.0\n\s+``--remote-exe``', text('docs/cli/relay.rst'))


@mark.unit
@FIXED
def test_relay_remote_exe_key_reference() -> None:
    """The reference gives `relay.remote_exe` after `.launcher_args`, its variable, and its default in words."""
    reference = parse_reference()
    assert 'relay.remote_exe' in reference
    relay_keys = [path for path in reference if path.startswith('relay.') and path.count('.') == 1]
    assert relay_keys.index('relay.remote_exe') == relay_keys.index('relay.launcher_args') + 1
    entry = reference['relay.remote_exe'][1]
    assert 'HYPERSHELL_RELAY_REMOTE_EXE' in entry and '--remote-exe' in entry
    assert documented_default('relay.remote_exe', reference) is None


@mark.unit
@FIXED
def test_relay_remote_exe_documented_in_the_completions() -> None:
    """The bash and zsh completions of `hs relay` offer `--remote-exe`, completing files."""
    bash = text('share/bash_completion.d/hs')
    relay_bash = bash[bash.index('function _hs_relay()'):bash.index('function _hs_server()')]
    assert re.search(r'--launcher-args\s+--remote-exe', relay_bash)
    assert re.search(r'--remote-exe\)\s+COMPREPLY=\(\$\(compgen -f', relay_bash)
    zsh = text('share/zsh/site-functions/_hs')
    relay_zsh = zsh[zsh.index('function _hs_relay()'):]
    relay_zsh = relay_zsh[:relay_zsh.index('\n}\n')]
    assert "'--remote-exe[" in relay_zsh and ':_files' in relay_zsh[relay_zsh.index("'--remote-exe["):]
    assert relay_zsh.index("'--launcher-args[") < relay_zsh.index("'--remote-exe[")


@mark.unit
@FIXED
def test_relay_launcher_default_comment_names_the_relays_program() -> None:
    """The relay's `launcher` default says an empty launcher runs the relay's own `hs`."""
    source = text('src/hypershell/core/config.py')
    assert "(empty means '<relay's hs> client ...')" in source


# --- R4: the Python API default ------------------------------------------------------------------------------


@mark.unit
@API
@mark.parametrize('value', [
    pytest.param(lambda: hypershell.cluster.ssh.DEFAULT_REMOTE_EXE, id='ssh'),
    pytest.param(lambda: hypershell.cluster.remote.DEFAULT_REMOTE_EXE, id='remote'),
    pytest.param(lambda: hypershell.cluster.DEFAULT_REMOTE_EXE, id='cluster'),
    pytest.param(lambda: inspect.signature(SSHCluster).parameters['remote_exe'].default, id='SSHCluster'),
    pytest.param(lambda: inspect.signature(RemoteCluster).parameters['remote_exe'].default, id='RemoteCluster'),
    pytest.param(lambda: inspect.signature(AutoScalingCluster).parameters['remote_exe'].default,
                 id='AutoScalingCluster'),
    pytest.param(lambda: inspect.signature(site_cluster).parameters['remote_exe'].default, id='site_cluster'),
    pytest.param(lambda: inspect.signature(zone_launch).parameters['remote_exe'].default, id='zone_launch'),
    pytest.param(lambda: programs(RelayThread(address=('127.0.0.1', free_port()), launcher=None))[0][0],
                 id='RelayThread'),
])
def test_python_api_default_is_hs(value: Any) -> None:
    """No built-in default names the legacy `hyper-shell`: remote clients are launched as `hs`."""
    assert value() == 'hs'
