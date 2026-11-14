# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Launched clients receive the authentication key off every command line.

Each end-to-end test runs `hs cluster` with stand-in launch programs written to the test's
own directory. The stand-ins record the command line they ran and the key the client
received, then run the real client from this tree. Every run happens in its own session
with a hard timeout, and its whole process group is killed when the run ends, so a client
that cannot authenticate can never hang the suite.
"""


# Type annotations
from __future__ import annotations
from typing import IO, Any, Callable, Dict, Final, Iterable, List, Optional, Set

# Standard libs
import os
import sys
import json
import time
import re
import shlex
import shutil
import signal
import socket
import tarfile
import subprocess
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass, replace
from subprocess import Popen, DEVNULL, PIPE

# External libs
import pytest
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status

# Internal libs
from tests import create_taskfile_echo
from hypershell import client as client_module
from hypershell.client import DEFAULT_AUTH, task_env
from hypershell.cluster.launch import AUTH_ENV, OMPI_ENV_LIST, ZONE_ENV, ClientLaunch, client_launch
from hypershell.core.logging import HOSTNAME
from hypershell.cluster.remote import AutoScaler, AutoScalingCluster
from hypershell.cluster.ssh import SSHCluster, DEFAULT_REMOTE_EXE
from hypershell.data.model import Task


pytestmark = mark.integration


RUN_TIMEOUT: Final[float] = 90
"""Seconds before a run's process group is killed and the test fails."""

TASK_COUNT: Final[int] = 4
"""Tasks in each end-to-end run."""

KEY_OPTIONS: Final[Set[str]] = {'-k', '--auth'}
"""Client options that take a key value."""

LOOPBACK: Final[str] = '127.0.0.1'
"""Bind address for every run, so clients never depend on resolving the host's name."""


# Records its own command line and what its environment holds, then runs the rest of its
# command line. `role` is 'launcher' for launch programs and 'client' for the remote executable.
STAND_IN_HEAD: Final[str] = """\
#!{python}
import os, sys, json, uuid
RECORDS = {records!r}
def record(role, **fields):
    # Written under a .part name and renamed into place, so a test polling for *.json never
    # reads a record that is still being written.
    path = os.path.join(RECORDS, f'{{uuid.uuid4().hex}}.json')
    with os.fdopen(os.open(path + '.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump({{'role': role, 'name': os.path.basename(sys.argv[0]), 'argv': sys.argv,
                    'key': os.environ.get({auth_env!r}), 'ompi': os.environ.get({ompi_env!r}),
                    'level': os.environ.get('HYPERSHELL_LOGGING_LEVEL'), **fields}}, stream)
    os.rename(path + '.part', path)
"""

# A launcher that passes its environment through, as `srun` and `mpirun` do by default.
LAUNCHER_BODY: Final[str] = """\
record('launcher')
os.execv(sys.argv[1], sys.argv[1:])
"""

# A launcher that drops the environment, as `srun --export=NONE` does. It keeps only what the
# test needs to stay isolated from the developer's own site and configuration.
LAUNCHER_DROP_ENV_BODY: Final[str] = """\
record('launcher')
keep = ('PATH', 'HOME', 'HYPERSHELL_CONFIG_FILE', 'HYPERSHELL_SITE', 'HYPERSHELL_LOGGING_LEVEL')
os.execve(sys.argv[1], sys.argv[1:], {name: os.environ[name] for name in keep if name in os.environ})
"""

# A launcher for autoscaling runs. The autoscaler advertises the host's own name, which some hosts
# (sandboxes, laptops) cannot resolve, so after recording what it was given the stand-in points
# the client at the loopback address the run binds.
AUTOSCALE_LAUNCHER_BODY: Final[str] = f"""\
record('launcher')
argv = sys.argv[1:]
argv[argv.index('-H') + 1] = {LOOPBACK!r}
os.execv(argv[0], argv)
"""

# A stand-in for `ssh`. It parses its options as ssh does, records its command line and the remote
# command, and saves its standard input to a private file. That file is empty under -n, since ssh
# then reads /dev/null, and when STAND_IN_SSH_NO_INPUT=1, as with a site wrapper that closes ssh's
# input. It runs the remote command with `sh -c`, as sshd runs it under a login shell, with that
# input and without the logging level, which a remote login would not inherit unless --env sends
# it. The cluster advertises the host's own name, so the stand-in points the client at the loopback
# address the run binds.
SSH_BODY: Final[str] = f"""\
import subprocess
NO_INPUT = os.environ.get('STAND_IN_SSH_NO_INPUT') == '1'
WITH_VALUE = set('BbcDEeFIiJLlmOoPpQRSWw')
args, index, flags = sys.argv[1:], 0, set()
while index < len(args) and args[index].startswith('-'):
    word, index = args[index], index + 1
    for position, letter in enumerate(word[1:]):
        if letter in WITH_VALUE:
            index += int(position == len(word) - 2)
            break
        flags.add(letter)
host, remote = args[index], ' '.join(args[index + 1:])
data = b'' if 'n' in flags or NO_INPUT else sys.stdin.buffer.read()
stdin_path = os.path.join(RECORDS, uuid.uuid4().hex + '.stdin')
with os.fdopen(os.open(stdin_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
    stream.write(data)
record('launcher', host=host, flags=sorted(flags), remote=remote, stdin=stdin_path)
advertised = {' -H ' + shlex.quote(HOSTNAME) + ' '!r}
if remote.count(advertised) != 1:
    sys.exit('stand-in ssh: expected the advertised host once in the remote command')
env = {{name: value for name, value in os.environ.items() if name != 'HYPERSHELL_LOGGING_LEVEL'}}
with open(stdin_path, 'rb') as stream:
    status = subprocess.call(['/bin/sh', '-c', remote.replace(advertised, {' -H ' + LOOPBACK + ' '!r})],
                             stdin=stream, env=env)
record('ssh-exit', status=status)
sys.exit(status)
"""

# The remote executable: runs this tree's `hypershell.main` under the test's interpreter.
CLIENT_BODY: Final[str] = """\
record('client')
status = None
try:
    from hypershell import main
    status = main(sys.argv[1:])
finally:
    record('exit', status=status)
sys.exit(status)
"""

# Reads every task's result through the data model in the run's site.
TASK_RESULTS: Final[str] = """\
import json
from hypershell.data.model import Task
print(json.dumps([[args, status] for args, status in Task.query(Task.args, Task.exit_status).all()]))
"""

# Reports whether every task in the run's site has completed.
TASKS_DONE: Final[str] = f"""\
from hypershell.data.model import Task
print(Task.query().count() == {TASK_COUNT} and Task.count_remaining() == 0)
"""

# Builds and runs the launcher-mode cluster in-process, capturing every log record at every level.
CAPTURE_LOGS: Final[str] = """\
import sys, json, logging
from hypershell.cluster.remote import RemoteCluster
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append({'level': record.levelname, 'name': record.name, 'message': record.getMessage()})
logger = logging.getLogger('hypershell')
logger.addHandler(Capture(level=1))
logger.setLevel(1)
launcher, remote_exe, port, output = sys.argv[1:]
cluster = RemoteCluster(source=[f'echo {n}' for n in range(4)], launcher=launcher, remote_exe=remote_exe,
                        bind=('127.0.0.1', int(port)), in_memory=True, tls=None)
cluster.start()
cluster.join()
with open(output, mode='w') as stream:
    json.dump(records, stream)
"""

# Builds and runs an SSH cluster in-process, capturing every log record at every level.
CAPTURE_SSH_LOGS: Final[str] = """\
import sys, json, logging
from hypershell.cluster.ssh import SSHCluster
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append({'level': record.levelname, 'name': record.name, 'message': record.getMessage()})
logger = logging.getLogger('hypershell')
logger.addHandler(Capture(level=1))
logger.setLevel(1)
remote_exe, port, output = sys.argv[1:]
cluster = SSHCluster(source=[f'echo {n}' for n in range(4)], nodelist=['node-a'], remote_exe=remote_exe,
                     bind=('127.0.0.1', int(port)), in_memory=True, tls=None)
cluster.start()
cluster.join()
with open(output, mode='w') as stream:
    json.dump(records, stream)
"""

# Builds and runs an autoscaling cluster in-process, capturing every log record at every level.
# Autoscaling clusters do not exit on their own: once every task has completed the records are
# written, and the test kills the run's process group.
CAPTURE_AUTOSCALING_LOGS: Final[str] = f"""\
import os, sys, json, time, logging
from hypershell.cluster.remote import AutoScalingCluster
from hypershell.data import initdb
from hypershell.data.model import Task
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append({{'level': record.levelname, 'name': record.name, 'message': record.getMessage()}})
logger = logging.getLogger('hypershell')
logger.addHandler(Capture(level=1))
logger.setLevel(1)
launcher, remote_exe, port, policy, output = sys.argv[1:]
initdb()
cluster = AutoScalingCluster(source=[f'echo {{n}}' for n in range({TASK_COUNT})], launcher=launcher,
                             remote_exe=remote_exe, bind=({LOOPBACK!r}, int(port)), policy=policy, tls=None)
cluster.start()
while cluster.server.ident is None:
    time.sleep(0.1)
cluster.server.wait_scheduling()
while not (Task.query().count() == {TASK_COUNT} and Task.count_remaining() == 0):
    time.sleep(0.2)
with open(output + '.part', mode='w') as stream:
    json.dump(records, stream)
os.rename(output + '.part', output)
cluster.join()
"""


@dataclass
class StandIns:
    """Stand-in programs for one test and the directory their records land in."""

    bin: Path
    records: Path
    launcher: Path
    launcher_drop_env: Path
    autoscale_launcher: Path
    ssh: Path
    remote_exe: Path

    def read(self: StandIns) -> List[Dict[str, Any]]:
        """Every record written so far."""
        return [json.loads(path.read_text()) for path in sorted(self.records.glob('*.json'))]

    def exits(self: StandIns) -> List[Dict[str, Any]]:
        """Records of clients that have exited."""
        return [record for record in self.read() if record['role'] == 'exit']


def write_program(path: Path, body: str, records: Path) -> Path:
    """Write an executable stand-in at `path`."""
    head = STAND_IN_HEAD.format(python=sys.executable, records=str(records),
                                auth_env=AUTH_ENV, ompi_env=OMPI_ENV_LIST)
    path.write_text(head + body)
    path.chmod(0o700)
    return path


@pytest.fixture
def stand_ins(tmp_path: Path) -> StandIns:
    """Stand-in launcher (also installed as `mpirun`), `ssh`, and remote executable."""
    bin_dir = tmp_path / 'bin'
    records = tmp_path / 'records'
    bin_dir.mkdir()
    records.mkdir(mode=0o700)
    launcher = write_program(bin_dir / 'launcher', LAUNCHER_BODY, records)
    write_program(bin_dir / 'mpirun', LAUNCHER_BODY, records)
    launcher_drop_env = write_program(bin_dir / 'launcher-drop-env', LAUNCHER_DROP_ENV_BODY, records)
    autoscale_launcher = write_program(bin_dir / 'autoscale-launcher', AUTOSCALE_LAUNCHER_BODY, records)
    ssh = write_program(bin_dir / 'ssh', SSH_BODY, records)
    remote_exe = write_program(bin_dir / 'hs-client-under-test', CLIENT_BODY, records)
    return StandIns(bin=bin_dir, records=records, launcher=launcher, launcher_drop_env=launcher_drop_env,
                    autoscale_launcher=autoscale_launcher, ssh=ssh, remote_exe=remote_exe)


@dataclass
class Run:
    """Outcome of one process run in its own session."""

    status: Optional[int]
    stdout: str
    stderr: str


def run_in_session(argv: List[str], env: Dict[str, str], workdir: Path,
                   until: Callable[[], bool] = None, timeout: float = RUN_TIMEOUT, name: str = 'run',
                   stdin: IO = None) -> Run:
    """
    Run `argv` in its own session; kill its process group when it ends.

    The run ends when the process exits, when `until()` turns true (status is then None),
    or at `timeout`, which fails the test. Its output is kept in `workdir` under `name`.
    Its input is `stdin`, or nothing.
    """
    stdout_path, stderr_path = workdir / f'{name}.stdout', workdir / f'{name}.stderr'
    with open(stdout_path, mode='w') as stdout, open(stderr_path, mode='w') as stderr:
        proc = Popen(argv, env=env, stdin=stdin or DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
        deadline = time.monotonic() + timeout
        status = None
        try:
            while (status := proc.poll()) is None:
                if until is not None and until():
                    break
                if time.monotonic() > deadline:
                    kill_group(proc)
                    pytest.fail(f'Run exceeded {timeout}s: {shlex.join(argv)}\n'
                                + stderr_path.read_text()[-4000:])
                time.sleep(0.2)
        finally:
            kill_group(proc)
    return Run(status=status, stdout=stdout_path.read_text(), stderr=stderr_path.read_text())


def kill_group(proc: Popen) -> None:
    """Kill every process left in the session `proc` leads, then reap `proc`."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # Group already gone
    proc.wait()


def run_env(stand_ins: StandIns) -> Dict[str, str]:
    """Environment for a run: the temp site, every log level, and the stand-ins first on PATH."""
    return {**os.environ,
            'PATH': os.pathsep.join([str(stand_ins.bin), os.environ.get('PATH', '')]),
            'HYPERSHELL_LOGGING_LEVEL': 'DEVEL'}


def run_cluster(stand_ins: StandIns, mode: List[str], temp_site: Path, no_tls: bool, zone: Optional[str],
                until: Callable[[], bool] = None, env: Dict[str, str] = None) -> Run:
    """Run `hs cluster` over a small task file in launcher `mode`, with `env` added to the run's environment."""
    taskfile = create_taskfile_echo(temp_site, count=TASK_COUNT)
    argv = ['hs', 'cluster', str(taskfile), '-H', LOOPBACK, *mode, '--remote-exe', str(stand_ins.remote_exe)]
    if no_tls:
        argv.append('--no-tls')
    if zone is not None:
        argv.extend(['--zone', zone])
    return run_in_session(argv, {**run_env(stand_ins), **(env or {})}, Path(temp_site), until=until)


def task_results(stand_ins: StandIns, temp_site: Path) -> List[List[Any]]:
    """Every task's arguments and exit status, read through the data model in the temp site."""
    run = run_in_session([sys.executable, '-c', TASK_RESULTS], run_env(stand_ins), Path(temp_site), name='results')
    assert run.status == exit_status.success, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def all_tasks_done(stand_ins: StandIns, temp_site: Path, interval: float = 1) -> Callable[[], bool]:
    """A check, made at most once per `interval` seconds, that every task in the site has completed."""
    last_check = 0.0

    def check() -> bool:
        nonlocal last_check
        if time.monotonic() - last_check < interval:
            return False
        last_check = time.monotonic()
        run = run_in_session([sys.executable, '-c', TASKS_DONE], run_env(stand_ins), Path(temp_site),
                             name='poll', timeout=30)
        return run.status == exit_status.success and run.stdout.strip().endswith('True')

    return check


def keys_seen(records: Iterable[Dict[str, Any]]) -> Set[str]:
    """Every key a launched process held, in its environment or as a key option value."""
    keys = set()
    for record in records:
        if record['key']:
            keys.add(record['key'])
        argv = record['argv']
        keys.update(value for option, value in zip(argv, argv[1:]) if option in KEY_OPTIONS)
    return keys


def assert_key_off_command_lines(records: List[Dict[str, Any]]) -> str:
    """The client got a key, that key is on no recorded command line, and it came in the environment."""
    clients = [record for record in records if record['role'] == 'client']
    assert clients, 'No client was launched'
    keys = keys_seen(records)
    assert keys, 'No key reached the client'
    for key in keys:
        for record in records:
            on_command_line = key in ' '.join(record['argv'])
            assert not on_command_line, f'Key on the command line of {record["name"]}'
    for record in clients:
        assert record['key'], 'Client did not receive the key in its environment'
    key, = keys
    return key


def assert_key_not_in(key: str, *outputs: str) -> None:
    """No output holds the key."""
    for output in outputs:
        in_output = key in output
        assert not in_output, 'Key found in output'


def assert_run_complete(run: Run, stand_ins: StandIns, temp_site: Path) -> None:
    """The run exited cleanly and every task has a recorded exit status of 0."""
    assert run.status == exit_status.success, run.stderr[-4000:]
    results = task_results(stand_ins, temp_site)
    assert sorted(args for args, _ in results) == sorted(f'echo {n}' for n in range(TASK_COUNT))
    assert [status for _, status in results] == [0] * TASK_COUNT


RUN_CASES: Final[List] = [
    pytest.param(False, None, id='tls'),
    pytest.param(True, None, id='no-tls'),
    pytest.param(False, 'blue', id='tls-zone'),
]


@mark.parametrize('no_tls, zone', RUN_CASES)
def test_launcher_mode_keeps_key_off_command_lines(stand_ins: StandIns, temp_site: Path,
                                                   no_tls: bool, zone: Optional[str]) -> None:
    """With --launcher, the client gets the key in its environment and on no command line."""
    run = run_cluster(stand_ins, [f'--launcher={stand_ins.launcher}'], temp_site, no_tls, zone)
    records = stand_ins.read()
    key = assert_key_off_command_lines(records)
    assert_key_not_in(key, run.stdout, run.stderr)
    assert_run_complete(run, stand_ins, temp_site)


@mark.parametrize('no_tls, zone', RUN_CASES)
def test_mpi_mode_keeps_key_off_command_lines(stand_ins: StandIns, temp_site: Path,
                                              no_tls: bool, zone: Optional[str]) -> None:
    """With --mpi, the client gets the key in its environment, and Open MPI is asked to forward it."""
    run = run_cluster(stand_ins, ['--mpi'], temp_site, no_tls, zone)
    records = stand_ins.read()
    key = assert_key_off_command_lines(records)
    assert_key_not_in(key, run.stdout, run.stderr)
    launchers = [record for record in records if record['role'] == 'launcher']
    assert [record['name'] for record in launchers] == ['mpirun']
    assert AUTH_ENV in launchers[0]['ompi'].split(';')
    assert_run_complete(run, stand_ins, temp_site)


def test_launcher_mode_logs_never_hold_key(stand_ins: StandIns, temp_site: Path) -> None:
    """Building and running a launcher-mode cluster logs no key, at any level, in any process."""
    script = Path(temp_site) / 'capture_logs.py'
    script.write_text(CAPTURE_LOGS)
    output = Path(temp_site) / 'records.json'
    run = run_in_session([sys.executable, str(script), str(stand_ins.launcher), str(stand_ins.remote_exe),
                          os.environ['HYPERSHELL_SERVER_PORT'], str(output)], run_env(stand_ins), Path(temp_site))
    assert run.status == exit_status.success, run.stderr[-4000:]
    logged = json.loads(output.read_text())
    assert any(record['level'] == 'DEBUG' and 'Launching clients' in record['message'] for record in logged)
    key, = keys_seen(stand_ins.read())
    assert_key_not_in(key, *(record['message'] for record in logged), run.stdout, run.stderr)


def test_launcher_mode_without_environment_names_the_cause(stand_ins: StandIns, temp_site: Path) -> None:
    """A launched client that receives no key exits with an error naming the launcher's environment."""
    run = run_cluster(stand_ins, [f'--launcher={stand_ins.launcher_drop_env}'], temp_site, True, None,
                      until=lambda: bool(stand_ins.exits()))
    exits = stand_ins.exits()
    assert [record['status'] for record in exits] == [exit_status.runtime_error], run.stderr[-4000:]
    assert 'CRITICAL [hypershell.client] AuthenticationError' in run.stderr
    assert 'the launcher most likely did not pass the environment to the client' in run.stderr


def previous_argv(remote_exe: str, address: tuple, launcher: List[str], launcher_args: List[str],
                  num_threads: int, bundlesize: int, bundlewait: int, template: str, delay_start: float,
                  task_signalwait: int, no_confirm: bool = False, capture: bool = False, monitor: bool = False,
                  client_cores: int = None, client_memory: int = None, client_timeout: int = None,
                  task_timeout: int = None, ratelimit: int = None, tls_enabled: bool = True,
                  zone: str = None) -> List[str]:
    """The launcher-mode client command line as the launchers built it inline, with no key option."""
    client_args = []
    if no_confirm:
        client_args.append('--no-confirm')
    if capture:
        client_args.append('--capture')
    if monitor:
        client_args.append('--monitor')
    if client_cores is not None:
        client_args.extend(['-C', str(client_cores)])
    if client_memory is not None:
        client_args.extend(['-M', str(client_memory)])
    if client_timeout is not None:
        client_args.extend(['-T', str(client_timeout)])
    if task_timeout is not None:
        client_args.extend(['-W', str(task_timeout)])
    if ratelimit is not None:
        client_args.extend(['-R', str(ratelimit)])
    if not tls_enabled:
        client_args.append('--no-tls')
    if zone is not None:
        client_args.extend(['--zone', zone])
    return [*launcher, *launcher_args, remote_exe, 'client',
            '-H', address[0], '-p', str(address[1]), '-N', str(num_threads), '-b', str(bundlesize),
            '-w', str(bundlewait), '-t', template, '-d', str(delay_start), '-S', str(task_signalwait),
            *client_args]


LAUNCH_BASE: Final[Dict[str, Any]] = {
    'remote_exe': 'hs', 'address': ('node-1', 50001), 'launcher': ['srun'], 'launcher_args': ['--mpi=pmi2'],
    'num_threads': 4, 'bundlesize': 2, 'bundlewait': 5, 'template': 'echo {} > "$OUT"',
    'delay_start': -1.5, 'task_signalwait': 10,
}

LAUNCH_OPTIONS: Final[List] = [
    pytest.param({}, id='defaults'),
    pytest.param({'no_confirm': True, 'tls_enabled': False}, id='no-confirm-no-tls'),
    pytest.param({'capture': True, 'monitor': True, 'client_cores': 8, 'client_memory': 1024,
                  'client_timeout': 60, 'task_timeout': 30, 'ratelimit': 5, 'zone': 'blue'}, id='every-option'),
]


@mark.parametrize('options', LAUNCH_OPTIONS)
def test_client_launch_keeps_every_client_option(options: Dict[str, Any]) -> None:
    """The built command line carries every client option the launchers built inline, and no key."""
    params = {**LAUNCH_BASE, **options}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    key = 'k' * 32
    launch = client_launch(remote_exe, address, key, **params)
    assert launch.argv == previous_argv(remote_exe, address, **params)
    assert key not in launch.argv
    assert launch.env[AUTH_ENV] == key


def test_client_launch_leaves_caller_environment_unchanged() -> None:
    """Building and starting a launch hands the key to the child and never to the caller."""
    before = dict(os.environ)
    key = 'k' * 32
    probe = [sys.executable, '-c', f'import os, sys; sys.stdout.write(os.environ.get({AUTH_ENV!r}, ""))']
    launch = client_launch('hs', ('localhost', 50001), key, launcher=probe, num_threads=1, bundlesize=1,
                           bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    proc = launch.start(stdout=PIPE, stderr=PIPE)
    stdout, _ = proc.communicate(timeout=30)
    assert stdout.decode() == key
    assert dict(os.environ) == before


def test_client_launch_never_shows_key() -> None:
    """Neither the representation nor the log description of a launch holds the key."""
    key = 'k' * 32
    launch = client_launch('hs', ('localhost', 50001), key, launcher=['launcher'], num_threads=1, bundlesize=1,
                           bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    assert isinstance(launch, ClientLaunch)
    assert key not in repr(launch)
    assert key not in launch.describe()


@mark.parametrize('current, expected', [
    pytest.param(None, AUTH_ENV, id='unset'),
    pytest.param('FOO;BAR', f'FOO;BAR;{AUTH_ENV}', id='appended'),
    pytest.param(f'FOO;{AUTH_ENV}', f'FOO;{AUTH_ENV}', id='already-named'),
])
def test_client_launch_asks_open_mpi_to_forward_key_by_name(monkeypatch: MonkeyPatch,
                                                            current: Optional[str], expected: str) -> None:
    """The launch names the key variable for Open MPI to forward, keeping names already set."""
    if current is None:
        monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    else:
        monkeypatch.setenv(OMPI_ENV_LIST, current)
    launch = client_launch('hs', ('localhost', 50001), 'k' * 32, launcher=['mpirun'], num_threads=1,
                           bundlesize=1, bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    assert launch.env[OMPI_ENV_LIST] == expected


def test_redact_secrets_still_available_from_remote() -> None:
    """The log redaction helper keeps its import path in the remote launcher module."""
    from hypershell.cluster import launch, remote
    assert remote.redact_secrets is launch.redact_secrets
    assert 'redact_secrets' in remote.__all__


AUTOSCALING_CASES: Final[List] = [
    pytest.param('fixed', False, None, id='fixed-tls'),
    pytest.param('fixed', True, None, id='fixed-no-tls'),
    pytest.param('dynamic', False, None, id='dynamic-tls'),
    pytest.param('dynamic', True, None, id='dynamic-no-tls'),
    pytest.param('fixed', False, 'blue', id='fixed-tls-zone'),
]


@mark.parametrize('policy, no_tls, zone', AUTOSCALING_CASES)
def test_autoscaling_mode_keeps_key_off_command_lines(stand_ins: StandIns, temp_site: Path,
                                                      policy: str, no_tls: bool, zone: Optional[str]) -> None:
    """With --autoscaling, every client gets the key in its environment and on no command line."""
    mode = [f'--launcher={stand_ins.autoscale_launcher}', f'--autoscaling={policy}']
    run = run_cluster(stand_ins, mode, temp_site, no_tls, zone, until=all_tasks_done(stand_ins, temp_site))
    assert run.status is None, f'Autoscaling run exited ({run.status}) before its tasks completed\n' + run.stderr[-4000:]
    records = stand_ins.read()
    assert [record['role'] for record in records].count('launcher') >= 1
    key = assert_key_off_command_lines(records)
    assert_key_not_in(key, run.stdout, run.stderr)
    results = task_results(stand_ins, temp_site)
    assert sorted(args for args, _ in results) == sorted(f'echo {n}' for n in range(TASK_COUNT))
    assert [status for _, status in results] == [0] * TASK_COUNT


@mark.parametrize('policy', ['fixed', 'dynamic'])
def test_autoscaling_mode_logs_never_hold_key(stand_ins: StandIns, temp_site: Path, policy: str) -> None:
    """Building and running an autoscaling cluster logs no key, at any level, in any process."""
    script = Path(temp_site) / 'capture_autoscaling_logs.py'
    script.write_text(CAPTURE_AUTOSCALING_LOGS)
    output = Path(temp_site) / 'records.json'
    run = run_in_session([sys.executable, str(script), str(stand_ins.autoscale_launcher), str(stand_ins.remote_exe),
                          os.environ['HYPERSHELL_SERVER_PORT'], policy, str(output)],
                         run_env(stand_ins), Path(temp_site), until=output.exists)
    assert run.status is None, f'Autoscaling run exited ({run.status}) before its tasks completed\n' + run.stderr[-4000:]
    logged = json.loads(output.read_text())
    assert any(record['level'] == 'DEBUG' and 'Autoscale launcher' in record['message'] for record in logged)
    key, = keys_seen(stand_ins.read())
    assert_key_not_in(key, *(record['message'] for record in logged), run.stdout, run.stderr)


@mark.parametrize('options', [
    pytest.param({}, id='defaults'),
    pytest.param({'no_confirm': True, 'capture': True, 'monitor': True, 'client_cores': 8, 'client_memory': 1024,
                  'client_timeout': 60, 'task_timeout': 30, 'ratelimit': 5, 'zone': 'blue'}, id='every-option'),
])
def test_autoscaling_mode_keeps_every_client_option(options: Dict[str, Any]) -> None:
    """The autoscaler's one launch carries the client options it built inline, and no key."""
    port = int(os.environ['HYPERSHELL_SERVER_PORT'])
    cluster = AutoScalingCluster(launcher='srun -n1', launcher_args=['--mpi=pmi2'], remote_exe='hs', num_threads=4,
                                 bundlesize=2, bundlewait=5, template='echo {} > "$OUT"', delay_start=-1.5,
                                 task_signalwait=10, bind=(LOOPBACK, port), tls=None, **options)
    expected = {name: value for name, value in options.items() if name != 'no_confirm'}  # Never passed on
    assert cluster.launch.argv == previous_argv('hs', (HOSTNAME, port), ['srun', '-n1'], ['--mpi=pmi2'],
                                                num_threads=4, bundlesize=2, bundlewait=5,
                                                template='echo {} > "$OUT"', delay_start=-1.5,
                                                task_signalwait=10, tls_enabled=False, **expected)
    assert cluster.autoscaler.machine.launch is cluster.launch
    assert not set(cluster.launch.argv) & KEY_OPTIONS


@mark.parametrize('zone, name', [pytest.param(None, 'default', id='default'), pytest.param('blue', 'blue', id='zone')])
def test_autoscaling_mode_names_its_zone_to_the_launcher(zone: Optional[str], name: str) -> None:
    """The autoscaler's own launch names its zone in the environment and in the launcher's words only."""
    port = int(os.environ['HYPERSHELL_SERVER_PORT'])
    options = dict(launcher='zone-shim {zone}', launcher_args=['--partition={zone}'], remote_exe='hs',
                   template='echo {zone} {}', bind=(LOOPBACK, port), tls=None, zone=zone)
    cluster = AutoScalingCluster(**options)
    assert cluster.launch.env[ZONE_ENV] == name
    assert cluster.launch.argv[:3] == ['zone-shim', name, f'--partition={name}']
    assert cluster.launch.argv[cluster.launch.argv.index('-t') + 1] == 'echo {zone} {}'
    assert cluster.autoscaler.machine.launch is cluster.launch
    unexposed = AutoScalingCluster(discover_zones=False, **options)
    assert ZONE_ENV not in unexposed.launch.env
    assert unexposed.launch.argv[:3] == ['zone-shim', '{zone}', '--partition={zone}']
    assert unexposed.launch.argv[3:] == cluster.launch.argv[3:]


def test_autoscaling_mode_starts_every_client_through_one_launch(tmp_path: Path) -> None:
    """Each scale-up starts a client from the autoscaler's one launch: key in the environment only."""
    key = 'k' * 32
    probe = [sys.executable, '-c',
             'import os, sys, uuid; '
             f'open(os.path.join({str(tmp_path)!r}, uuid.uuid4().hex), "w").write(os.environ.get({AUTH_ENV!r}, ""))']
    launch = client_launch('hs', ('localhost', 50001), key, launcher=probe, num_threads=1, bundlesize=1,
                           bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    scaler = AutoScaler(launch, policy='fixed', max_size=2)
    scaler.clients = []
    before = dict(os.environ)
    try:
        scaler.scale()
        scaler.scale()
        assert scaler.launch is launch
        assert len(scaler.clients) == 2
        for client in scaler.clients:
            assert client.wait(timeout=30) == 0
            assert key not in ' '.join(client.args)
    finally:
        for client in scaler.clients:
            if client.poll() is None:
                client.kill()
                client.wait()
    assert [path.read_text() for path in tmp_path.iterdir()] == [key, key]
    assert dict(os.environ) == before


SSH_HOSTS: Final[List[str]] = ['node-a', 'node-b']
"""Hosts each SSH run launches a client on (each one reaches the stand-in `ssh`)."""

QUOTED_TEMPLATE: Final[str] = '{} "it\'s a b"'
"""A template with a space and a single quote, which must reach the client intact."""


def ssh_failed(stand_ins: StandIns) -> Callable[[], bool]:
    """A check that some remote command run by the stand-in `ssh` has failed (the run cannot complete)."""
    return lambda: any(record['status'] != 0 for record in stand_ins.read() if record['role'] == 'ssh-exit')


def assert_ssh_key_off_command_lines(records: List[Dict[str, Any]]) -> str:
    """The key is on no command line and no remote command; it came on ssh's input into the client's environment."""
    launchers = [record for record in records if record['role'] == 'launcher']
    clients = [record for record in records if record['role'] == 'client']
    assert launchers, 'No ssh was launched'
    stdin_keys = {Path(record['stdin']).read_text().strip() for record in launchers}
    keys = keys_seen(records) | {key for key in stdin_keys if key}
    assert keys, 'No key reached any launched process'
    for key in keys:
        for record in records:
            on_command_line = key in ' '.join(record['argv'])
            assert not on_command_line, f'Key on the command line of {record["name"]}'
            in_remote_command = key in record.get('remote', '')
            assert not in_remote_command, 'Key in the remote command'
    key, = keys
    for record in launchers:
        assert Path(record['stdin']).read_text() == key + '\n', 'ssh did not receive the key on its input'
        assert record['key'] is None, 'ssh itself held the key in its environment'
    assert clients, 'No client was launched'
    for record in clients:
        assert record['key'] == key, 'Client did not receive the key in its environment'
    return key


SSH_CASES: Final[List] = [
    pytest.param(False, None, False, '{}', id='tls'),
    pytest.param(True, None, False, '{}', id='no-tls'),
    pytest.param(False, 'blue', False, '{}', id='tls-zone'),
    pytest.param(True, None, True, '{}', id='no-tls-env'),
    pytest.param(True, None, False, QUOTED_TEMPLATE, id='no-tls-quoted-template'),
]


@mark.parametrize('no_tls, zone, export_env, template', SSH_CASES)
def test_ssh_mode_keeps_key_off_command_lines(stand_ins: StandIns, temp_site: Path, no_tls: bool,
                                              zone: Optional[str], export_env: bool, template: str) -> None:
    """With --ssh, the key travels on ssh's input into each client's environment, and on no command line."""
    mode = ['--ssh', ','.join(SSH_HOSTS), '-t', template, *(['--env'] if export_env else [])]
    run = run_cluster(stand_ins, mode, temp_site, no_tls, zone, until=ssh_failed(stand_ins))
    records = stand_ins.read()
    key = assert_ssh_key_off_command_lines(records)
    assert_key_not_in(key, run.stdout, run.stderr)
    launchers = [record for record in records if record['role'] == 'launcher']
    assert sorted(record['host'] for record in launchers) == SSH_HOSTS
    assert all('T' in record['flags'] for record in launchers), 'A remote terminal may be allocated'
    clients = [record for record in records if record['role'] == 'client']
    assert len(clients) == len(SSH_HOSTS)
    for record in clients:
        argv = record['argv']
        assert argv[argv.index('-t') + 1] == template
        assert record['level'] == ('DEVEL' if export_env else None)
        if zone is not None:
            assert argv[argv.index('--zone') + 1] == zone
    assert_run_complete(run, stand_ins, temp_site)


def test_ssh_mode_logs_never_hold_key(stand_ins: StandIns, temp_site: Path) -> None:
    """Building and running an SSH cluster logs no key, at any level, in any process."""
    script = Path(temp_site) / 'capture_ssh_logs.py'
    script.write_text(CAPTURE_SSH_LOGS)
    output = Path(temp_site) / 'records.json'
    run = run_in_session([sys.executable, str(script), str(stand_ins.remote_exe),
                          os.environ['HYPERSHELL_SERVER_PORT'], str(output)], run_env(stand_ins), Path(temp_site))
    assert run.status == exit_status.success, run.stderr[-4000:]
    logged = json.loads(output.read_text())
    assert any(record['level'] == 'DEBUG' and 'Launching client' in record['message'] for record in logged)
    records = stand_ins.read()
    keys = keys_seen(records) | {Path(record['stdin']).read_text().strip()
                                 for record in records if record['role'] == 'launcher'}
    key, = keys - {''}
    assert_key_not_in(key, *(record['message'] for record in logged), run.stdout, run.stderr)


def test_ssh_mode_without_key_names_the_cause(stand_ins: StandIns, temp_site: Path) -> None:
    """A client launch whose ssh passes no input exits before the client starts, naming the cause."""
    run = run_cluster(stand_ins, ['--ssh', SSH_HOSTS[0]], temp_site, True, None,
                      until=ssh_failed(stand_ins), env={'STAND_IN_SSH_NO_INPUT': '1'})
    records = stand_ins.read()
    assert [record['status'] for record in records if record['role'] == 'ssh-exit'] == [1], run.stderr[-4000:]
    assert not [record for record in records if record['role'] == 'client']
    assert f'no authentication key arrived on standard input for {AUTH_ENV}' in run.stderr


def no_input_warnings(stderr: str) -> List[str]:
    """Log lines warning that `-n` was dropped from the ssh arguments."""
    return [line for line in stderr.splitlines() if 'WARNING' in line and "'-n'" in line]


def test_ssh_mode_ignores_no_input_option(stand_ins: StandIns, temp_site: Path) -> None:
    """With -n in --ssh-args, the launch still sends the key on ssh's input, and warns once."""
    mode = ['--ssh', ','.join(SSH_HOSTS), '--ssh-args=-n -o BatchMode=yes']
    run = run_cluster(stand_ins, mode, temp_site, True, None, until=ssh_failed(stand_ins))
    records = stand_ins.read()
    key = assert_ssh_key_off_command_lines(records)
    assert_key_not_in(key, run.stdout, run.stderr)
    launchers = [record for record in records if record['role'] == 'launcher']
    assert sorted(record['host'] for record in launchers) == SSH_HOSTS
    for record in launchers:
        assert 'n' not in record['flags'], 'ssh was given -n'
        assert record['argv'][1:4] == ['-o', 'BatchMode=yes', '-T']
    assert len(no_input_warnings(run.stderr)) == 1, run.stderr[-4000:]
    assert_run_complete(run, stand_ins, temp_site)


# Builds an SSH cluster in-process, its ssh arguments from `--ssh-args` or from `ssh.args` in the
# configuration, and reports each host's command line and every WARNING record.
BUILD_SSH_CLUSTER: Final[str] = """\
import sys, json, logging
from hypershell.cluster.ssh import SSHCluster
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())
logging.getLogger('hypershell').addHandler(Capture(level=logging.WARNING))
source, port = sys.argv[1:]
launcher_args = ['-n', '-o', 'BatchMode=yes'] if source == 'ssh-args' else None
cluster = SSHCluster(nodelist=['node-a', 'node-b'], launcher_args=launcher_args, bind=('127.0.0.1', int(port)),
                     tls=None)
print(json.dumps({'argv': cluster.client_argv, 'warnings': records}))
"""


@mark.parametrize('source', ['ssh-args', 'config'])
def test_ssh_mode_drops_no_input_option_once(temp_site: Path, source: str) -> None:
    """A bare -n, from --ssh-args or the ssh.args setting, is dropped from every host's launch with one warning."""
    script = Path(temp_site) / 'build_ssh_cluster.py'
    script.write_text(BUILD_SSH_CLUSTER)
    env = {**os.environ, 'HYPERSHELL_SSH_ARGS': '-n -o BatchMode=yes'}
    run = run_in_session([sys.executable, str(script), source, os.environ['HYPERSHELL_SERVER_PORT']],
                         env, Path(temp_site), timeout=60)
    assert run.status == exit_status.success, run.stderr[-4000:]
    built = json.loads(run.stdout.strip().splitlines()[-1])
    for host, argv in zip(['node-a', 'node-b'], built['argv']):
        assert argv[:argv.index(host)] == ['ssh', '-o', 'BatchMode=yes', '-T']
    warnings = [message for message in built['warnings'] if "'-n'" in message]
    assert len(warnings) == 1, built['warnings']


@mark.parametrize('launcher, launcher_args, expected', [
    pytest.param(['ssh'], ['-n'], ['ssh'], id='alone'),
    pytest.param(['ssh'], ['-o', 'BatchMode=yes', '-n', '-q'], ['ssh', '-o', 'BatchMode=yes', '-q'], id='among'),
    pytest.param(['ssh', '-n'], ['-q'], ['ssh', '-q'], id='launcher-word'),
    pytest.param(['ssh'], ['-n', '-n'], ['ssh'], id='twice'),
    pytest.param(['ssh'], ['-l', '-n', '-q'], ['ssh', '-l', '-n', '-q'], id='option-value'),
])
def test_ssh_launch_drops_no_input_option(launcher: List[str], launcher_args: List[str],
                                          expected: List[str]) -> None:
    """A bare -n before the host is dropped, since the launch owns ssh's input; an option's value is kept."""
    params = {**SSH_LAUNCH_BASE, 'launcher': launcher, 'launcher_args': launcher_args}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launch = client_launch(remote_exe, address, 'k' * 32, ssh_host='node-7', **params)
    before, *_ = split_ssh_launch(launch.argv, 'node-7')
    assert before == [*expected, '-T']


SSH_LAUNCH_BASE: Final[Dict[str, Any]] = {
    'remote_exe': '~/bin/hs', 'address': ('node-1', 50001), 'launcher': ['ssh'],
    'launcher_args': ['-tt', '-o', 'BatchMode=yes'], 'num_threads': 4, 'bundlesize': 2, 'bundlewait': 5,
    'template': QUOTED_TEMPLATE, 'delay_start': -1.5, 'task_signalwait': 10,
}


def split_ssh_launch(argv: List[str], host: str) -> tuple:
    """The ssh words before the host, the words after it, and the remote client command line."""
    before, after = argv[:argv.index(host)], argv[argv.index(host) + 1:]
    script, = shlex.split(after[-1])
    preamble, command = script.split('; exec ', 1)
    return before, after, preamble, shlex.split(command)


@mark.parametrize('options', LAUNCH_OPTIONS)
def test_ssh_launch_runs_client_under_sh_with_no_terminal(options: Dict[str, Any]) -> None:
    """An ssh launch forces no terminal, runs `sh -c` with no key in it, and keeps every client option."""
    params = {**SSH_LAUNCH_BASE, **options}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launcher, launcher_args = params.pop('launcher'), params.pop('launcher_args')
    key = 'k' * 32
    launch = client_launch(remote_exe, address, key, launcher=launcher, launcher_args=launcher_args,
                           ssh_host='node-7', ssh_exports={'HYPERSHELL_SITE': '/site'}, **params)
    before, after, preamble, command = split_ssh_launch(launch.argv, 'node-7')
    assert before == [*launcher, *launcher_args, '-T']
    assert after[:-1] == ['HYPERSHELL_SITE=/site', 'sh', '-c']
    assert command == previous_argv(remote_exe, address, [], [], **params)
    assert AUTH_ENV in preamble
    assert key not in ' '.join(launch.argv)
    assert launch.stdin == key + '\n'
    assert launch.inherit_env and launch.env == {}
    assert key not in repr(launch) and key not in launch.describe()
    assert launch.describe().endswith(' sh -c ' + after[-1])  # Logged as ssh sends it


LOGIN_SHELLS: Final[List[str]] = ['sh', 'bash', 'zsh', 'dash', 'ksh', 'fish', 'csh', 'tcsh']
"""Login shells a remote account may have; the launch must work under each one."""

# Records the command line it ran with and the key its environment holds.
PROBE: Final[str] = """\
#!{python}
import os, sys, json
with open({output!r}, mode='w') as stream:
    json.dump({{'argv': sys.argv[1:], 'key': os.environ.get({auth_env!r})}}, stream)
"""


@mark.parametrize('shell', LOGIN_SHELLS)
def test_ssh_launch_reads_key_under_any_login_shell(tmp_path: Path, shell: str) -> None:
    """The remote command works under any login shell; without a key it stops before the client starts."""
    shell_path = shutil.which(shell)
    if shell_path is None:
        pytest.skip(f'{shell} is not installed')
    output = tmp_path / 'probe.json'
    probe = tmp_path / 'probe'
    probe.write_text(PROBE.format(python=sys.executable, output=str(output), auth_env=AUTH_ENV))
    probe.chmod(0o700)
    params = {**SSH_LAUNCH_BASE, 'remote_exe': str(probe), 'zone': 'blue'}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launcher, launcher_args = params.pop('launcher'), params.pop('launcher_args')
    key = 'k' * 32
    launch = client_launch(remote_exe, address, key, launcher=launcher, launcher_args=launcher_args,
                           ssh_host='node-7', **params)
    remote = ' '.join(launch.argv[launch.argv.index('node-7') + 1:])  # What ssh sends the login shell
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path)}  # No dotfiles from the developer
    for name, data in [('with-key', launch.stdin), ('no-key', '')]:
        (tmp_path / name).write_text(data)
        with open(tmp_path / name) as stdin:
            run = run_in_session([shell_path, '-c', remote], env, tmp_path, timeout=30, name=name, stdin=stdin)
        if name == 'with-key':
            assert run.status == 0, run.stderr
            probed = json.loads(output.read_text())
            assert probed['key'] == key
            assert probed['argv'] == previous_argv(remote_exe, address, [], [], **params)[1:]
            output.unlink()
        else:
            assert run.status == 1, run.stderr
            assert f'no authentication key arrived on standard input for {AUTH_ENV}' in run.stderr
            assert not output.exists()
        assert key not in run.stdout + run.stderr


@mark.parametrize('options', [
    pytest.param({}, id='defaults'),
    pytest.param({'no_confirm': True, 'capture': True, 'monitor': True, 'client_cores': 8, 'client_memory': 1024,
                  'client_timeout': 60, 'task_timeout': 30, 'ratelimit': 5, 'zone': 'blue'}, id='every-option'),
])
def test_ssh_mode_launches_each_host_with_every_client_option(options: Dict[str, Any]) -> None:
    """The SSH cluster builds one launch per host with the client options it built inline, and no key."""
    port = int(os.environ['HYPERSHELL_SERVER_PORT'])
    cluster = SSHCluster(nodelist=SSH_HOSTS, launcher_args=['-o', 'BatchMode=yes'], num_threads=4, bundlesize=2,
                         bundlewait=5, template=QUOTED_TEMPLATE, delay_start=-1.5, task_signalwait=10,
                         bind=(LOOPBACK, port), tls=None, **options)
    assert [launch.argv for launch in cluster.launches] == cluster.client_argv
    for host, launch in zip(SSH_HOSTS, cluster.launches):
        before, after, _, command = split_ssh_launch(launch.argv, host)
        assert before == ['ssh', '-o', 'BatchMode=yes', '-T']
        assert after[:-1] == ['sh', '-c']
        assert command == previous_argv(DEFAULT_REMOTE_EXE, (HOSTNAME, port), [], [], num_threads=4,
                                        bundlesize=2, bundlewait=5, template=QUOTED_TEMPLATE, delay_start=-1.5,
                                        task_signalwait=10, tls_enabled=False, **options)
        assert not set(launch.argv) & KEY_OPTIONS
    assert len({launch.stdin for launch in cluster.launches}) == 1


CONTRACT_TAG: Final[str] = '2.9.0a4'
"""The oldest client release a 2.9 launch must start (an older client cannot join a 2.9 server)."""

# Every `hs client` option the 2.9.0a4 client accepts, and whether it takes a value. Written from
# `git show 2.9.0a4:src/hypershell/client.py`; the launch may emit nothing outside this set.
CONTRACT_CLIENT_OPTIONS: Final[Dict[str, bool]] = {
    '-N': True, '--num-threads': True, '--num-tasks': True, '-H': True, '--host': True,
    '-p': True, '--port': True, '-k': True, '--auth': True, '-t': True, '--template': True,
    '-b': True, '--bundlesize': True, '-w': True, '--bundlewait': True, '-d': True, '--delay-start': True,
    '--zone': True, '-T': True, '--timeout': True, '-W': True, '--task-timeout': True,
    '-R': True, '--ratelimit': True, '-S': True, '--signalwait': True, '--no-confirm': False,
    '-o': True, '--output': True, '-e': True, '--errors': True, '--capture': False, '--monitor': False,
    '-C': True, '--client-cores': True, '-M': True, '--client-memory': True, '--no-tls': False,
    '--tls-key': True, '--tls-cert': True, '--tls-ca': True,
    '--available-cores': False, '--available-ssh-groups': False,
}

REPO: Final[Path] = Path(__file__).resolve().parent.parent
"""This tree's repository, for reading the contract tag."""


def emitted_client_options(launch: ClientLaunch, remote_exe: str, ssh_host: Optional[str]) -> List[str]:
    """The words a launch passes to `<remote_exe> client`, wherever the launch carries them."""
    if ssh_host is not None:
        *_, command = split_ssh_launch(launch.argv, ssh_host)
    else:
        command = launch.argv[launch.argv.index(remote_exe):]
    assert command[:2] == [remote_exe, 'client']
    return command[2:]


@mark.parametrize('ssh_host, expose_zone', [pytest.param(None, False, id='launcher'),
                                             pytest.param(None, True, id='launcher-zone'),
                                             pytest.param('node-7', False, id='ssh')])
@mark.parametrize('options', LAUNCH_OPTIONS)
def test_launch_contract_uses_only_known_client_options(options: Dict[str, Any], ssh_host: Optional[str],
                                                        expose_zone: bool) -> None:
    """Every option a launch passes to the client, in every mode, is one a 2.9.0a4 client accepts."""
    params = {**LAUNCH_BASE, **options}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launch = client_launch(remote_exe, address, 'k' * 32, ssh_host=ssh_host, expose_zone=expose_zone, **params)
    words = iter(emitted_client_options(launch, remote_exe, ssh_host))
    emitted = []
    for word in words:
        assert word in CONTRACT_CLIENT_OPTIONS, f'{word} is not a {CONTRACT_TAG} client option'
        emitted.append(word)
        if CONTRACT_CLIENT_OPTIONS[word]:
            next(words)
    assert not set(emitted) & KEY_OPTIONS
    assert AUTH_ENV == 'HYPERSHELL_SERVER_AUTH'


def git_output(*args: str) -> Optional[bytes]:
    """Output of a git command in this repository, or None if git or the object is missing."""
    try:
        return subprocess.run(['git', '-C', str(REPO), *args], check=True, capture_output=True, timeout=60).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def require_contract_tag() -> None:
    """Skip unless the contract tag resolves in this repository (shallow clones lack it)."""
    if git_output('rev-parse', '--verify', '--quiet', f'{CONTRACT_TAG}^{{commit}}') is None:
        pytest.skip(f'tag {CONTRACT_TAG} is not available')


def test_launch_contract_matches_tagged_client() -> None:
    """The frozen option set is exactly what the tagged client's command line accepts."""
    require_contract_tag()
    source = git_output('show', f'{CONTRACT_TAG}:src/hypershell/client.py').decode()
    accepted = set()
    for call in re.findall(r"interface\.add_argument\(([^\n]*)\)", source):
        accepted.update(re.findall(r"'(-[^']*)'", call))
    assert accepted == set(CONTRACT_CLIENT_OPTIONS)


# The remote executable for a client from another release: runs that tree's `hypershell.main`.
OLD_CLIENT_BODY: Final[str] = """\
sys.path.insert(0, {src!r})
import hypershell
record('client', module=hypershell.__file__)
status = None
try:
    status = hypershell.main(sys.argv[1:])
finally:
    record('exit', status=status)
sys.exit(status)
"""

# Reports where `hypershell` imports from with the tagged tree first on the path.
OLD_CLIENT_IMPORT: Final[str] = """\
import sys
sys.path.insert(0, sys.argv[1])
import hypershell, hypershell.client
print(hypershell.__file__)
"""


@pytest.fixture
def old_client(stand_ins: StandIns, tmp_path: Path) -> StandIns:
    """The stand-ins, with the remote executable running the contract tag's client."""
    require_contract_tag()
    archive = git_output('archive', '--format=tar', CONTRACT_TAG, 'src')
    assert archive is not None, f'git archive {CONTRACT_TAG} failed'
    tree = tmp_path / CONTRACT_TAG
    with tarfile.open(fileobj=BytesIO(archive)) as stream:
        stream.extractall(tree, filter='data')
    src = tree / 'src'
    probe = run_in_session([sys.executable, '-c', OLD_CLIENT_IMPORT, str(src)], run_env(stand_ins), tmp_path,
                           timeout=60, name='old-import')
    if probe.status != 0 or not probe.stdout.strip().startswith(str(src)):
        pytest.skip(f'{CONTRACT_TAG} does not import under this interpreter: {probe.stderr[-400:]}')
    remote_exe = write_program(stand_ins.bin / f'hs-client-{CONTRACT_TAG}', OLD_CLIENT_BODY.format(src=str(src)),
                               stand_ins.records)
    return replace(stand_ins, remote_exe=remote_exe)


def assert_old_client_ran(records: List[Dict[str, Any]], old: StandIns) -> None:
    """Every client that ran imported the tagged tree."""
    clients = [record for record in records if record['role'] == 'client']
    assert clients
    src = str(old.remote_exe.parent.parent / CONTRACT_TAG / 'src')
    for record in clients:
        assert record['module'].startswith(src), f'client imported {record["module"]}'


@mark.parametrize('mode', ['launcher', 'ssh'])
def test_old_client_runs_under_new_launcher(old_client: StandIns, temp_site: Path, mode: str) -> None:
    """A 2.9.0a4 client, unchanged, takes the key the launch hands it and completes the run."""
    if mode == 'launcher':
        run = run_cluster(old_client, [f'--launcher={old_client.launcher}'], temp_site, False, None)
        key = assert_key_off_command_lines(old_client.read())
    else:
        run = run_cluster(old_client, ['--ssh', SSH_HOSTS[0]], temp_site, False, None, until=ssh_failed(old_client))
        key = assert_ssh_key_off_command_lines(old_client.read())
    assert_key_not_in(key, run.stdout, run.stderr)
    assert_old_client_ran(old_client.read(), old_client)
    assert_run_complete(run, old_client, temp_site)


# A task step that writes its own environment, as JSON, to a private file named for its task.
DUMP_TASK_ENV: Final[str] = """\
import os, sys, json
path = os.path.join(sys.argv[1], os.environ['TASK_ID'] + '.json')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
    json.dump(dict(os.environ), stream)
"""

USER_OMPI_NAMES: Final[str] = 'USER_NAME_A;USER_NAME_B'
"""Names a user already set in the Open MPI forwarding list; a task must still see them."""


@mark.parametrize('mode', ['launcher', 'mpi'])
def test_launched_tasks_never_hold_key(stand_ins: StandIns, temp_site: Path, tmp_path: Path, mode: str) -> None:
    """A launched client's tasks see neither the key nor its name in the Open MPI list; a user's names stay."""
    script = tmp_path / 'dump_task_env.py'
    script.write_text(DUMP_TASK_ENV)
    dumps = tmp_path / 'task-env'
    dumps.mkdir(mode=0o700)
    template = '{} && ' + shlex.join([sys.executable, str(script), str(dumps)])
    launch_mode = [f'--launcher={stand_ins.launcher}'] if mode == 'launcher' else ['--mpi']
    run = run_cluster(stand_ins, [*launch_mode, '-t', template], temp_site, True, None,
                      env={OMPI_ENV_LIST: USER_OMPI_NAMES})
    key = assert_key_off_command_lines(stand_ins.read())
    assert_run_complete(run, stand_ins, temp_site)
    environments = [json.loads(path.read_text()) for path in dumps.glob('*.json')]
    assert len(environments) == TASK_COUNT
    for env in environments:
        holds_variable, holds_key = AUTH_ENV in env, key in json.dumps(env)
        assert not holds_variable, 'A task received the key variable'
        assert not holds_key, 'A task received the key'
        assert env.get(OMPI_ENV_LIST) == USER_OMPI_NAMES


@mark.parametrize('inherited, expected', [
    pytest.param(None, None, id='unset'),
    pytest.param(AUTH_ENV, None, id='launch-only'),
    pytest.param(f'USER_NAME_A;{AUTH_ENV};USER_NAME_B', 'USER_NAME_A;USER_NAME_B', id='user-names-kept'),
    pytest.param('USER_NAME_A', 'USER_NAME_A', id='user-only'),
])
def test_task_environment_never_holds_key(temp_site: Path, monkeypatch: MonkeyPatch,
                                          inherited: Optional[str], expected: Optional[str]) -> None:
    """A task's environment has no key variable, and the Open MPI list keeps only the user's own names."""
    key = 'k' * 32
    monkeypatch.setenv(AUTH_ENV, key)
    if inherited is None:
        monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    else:
        monkeypatch.setenv(OMPI_ENV_LIST, inherited)
    env = task_env(Task.new('echo hello'))
    assert AUTH_ENV not in env
    assert key not in env.values()
    assert env.get(OMPI_ENV_LIST) == expected
    assert os.environ[AUTH_ENV] == key  # The client's own environment is unchanged


def test_task_environment_holds_key_only_when_exported(temp_site: Path, monkeypatch: MonkeyPatch) -> None:
    """A task gets the key variable only when the configured task environment names it explicitly."""
    monkeypatch.setenv(AUTH_ENV, 'k' * 32)
    monkeypatch.setattr(client_module, 'load_task_env', lambda: {AUTH_ENV: 'given-explicitly'})
    env = task_env(Task.new('echo hello'))
    assert env[AUTH_ENV] == 'given-explicitly'


@mark.parametrize('launcher', [
    pytest.param([], id='autoscaling-default'),
    pytest.param(['srun'], id='srun'),
    pytest.param(['flux', 'run'], id='flux'),
    pytest.param(['launcher', 'mpirun'], id='mpirun-not-first'),
    pytest.param(['srun', 'mpirun.openmpi'], id='srun-mpirun'),
    pytest.param(['mpirunner'], id='longer-name'),
])
def test_client_launch_sets_no_open_mpi_parameter_for_other_launchers(monkeypatch: MonkeyPatch,
                                                                      launcher: List[str]) -> None:
    """A launch that does not start `mpirun` or `mpiexec` sets no Open MPI parameter."""
    monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    key = 'k' * 32
    launch = client_launch('hs', ('localhost', 50001), key, launcher=launcher, num_threads=1, bundlesize=1,
                           bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    assert launch.env == {AUTH_ENV: key}


@mark.parametrize('launcher', [
    pytest.param(['mpiexec', '-n', '4'], id='mpiexec'),
    pytest.param(['/opt/openmpi/bin/mpirun'], id='mpirun-path'),
    pytest.param(['mpirun.openmpi', '-np', '2'], id='mpirun-dotted'),
    pytest.param(['/usr/bin/mpiexec.openmpi'], id='mpiexec-dotted'),
    pytest.param(['orterun'], id='orterun'),
    pytest.param(['/opt/openmpi/bin/prterun'], id='prterun'),
])
def test_client_launch_sets_open_mpi_parameter_for_mpi_launchers(monkeypatch: MonkeyPatch,
                                                                 launcher: List[str]) -> None:
    """A launch that starts `mpirun` or `mpiexec` asks Open MPI to forward the key variable by name."""
    monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    launch = client_launch('hs', ('localhost', 50001), 'k' * 32, launcher=launcher, num_threads=1, bundlesize=1,
                           bundlewait=5, template='{}', delay_start=0, task_signalwait=10)
    assert launch.env[OMPI_ENV_LIST] == AUTH_ENV


@mark.parametrize('launcher, launcher_args', [
    pytest.param(['mpirun', '-x', 'LD_LIBRARY_PATH'], [], id='launcher-words'),
    pytest.param(['mpiexec'], ['-np', '2', '-x', 'OMP_NUM_THREADS=2'], id='launcher-args'),
    pytest.param(['mpirun.openmpi', '--x', 'LD_LIBRARY_PATH'], [], id='double-dash'),
    pytest.param(['orterun'], ['-x=OMP_NUM_THREADS'], id='equals'),
])
def test_client_launch_forwards_key_by_name_beside_mpirun_x(monkeypatch: MonkeyPatch, launcher: List[str],
                                                            launcher_args: List[str]) -> None:
    """With `-x` already given to `mpirun`, the launch adds `-x` and the variable's name, and no Open MPI parameter."""
    monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    key = 'k' * 32
    launch = client_launch('hs', ('localhost', 50001), key, launcher=launcher, launcher_args=launcher_args,
                           num_threads=1, bundlesize=1, bundlewait=5, template='{}', delay_start=0,
                           task_signalwait=10)
    assert launch.env == {AUTH_ENV: key}
    start = launch.argv.index('hs')
    assert launch.argv[:start] == [*launcher, *launcher_args, '-x', AUTH_ENV]
    assert key not in ' '.join(launch.argv)
    assert key not in launch.describe()


def zone_launch(launcher: List[str], launcher_args: List[str] = (), zone: Optional[str] = 'gpu',
                template: str = '{}', expose_zone: bool = True, **options: Any) -> ClientLaunch:
    """A launch of `zone` that exposes it, with small fixed client options."""
    return client_launch('hs', ('localhost', 50001), 'k' * 32, launcher=launcher, launcher_args=launcher_args,
                         num_threads=1, bundlesize=1, bundlewait=5, template=template, delay_start=0,
                         task_signalwait=10, zone=zone, expose_zone=expose_zone, **options)


@mark.parametrize('launcher, launcher_args, zone, expected', [
    pytest.param(['shim', '{zone}'], [], 'gpu', ['shim', 'gpu'], id='launcher-word'),
    pytest.param(['srun'], ['--partition={zone}', '-n1'], 'gpu', ['srun', '--partition=gpu', '-n1'], id='option-value'),
    pytest.param(['shim', '{zone}'], ['-J', 'run-{zone}-{zone}'], 'big mem', ['shim', 'big mem', '-J',
                                                                               'run-big mem-big mem'], id='spaced-name'),
    pytest.param(['sh', '-c', 'echo ${HOME} {} {zone}'], [], 'gpu', ['sh', '-c', 'echo ${HOME} {} gpu'],
                 id='other-braces'),
    pytest.param(['shim', '{zone}'], [], None, ['shim', 'default'], id='default-zone'),
    pytest.param(['srun'], [], 'gpu', ['srun'], id='no-zone-word'),
])
def test_client_launch_exposes_zone_to_launcher(monkeypatch: MonkeyPatch, launcher: List[str],
                                                launcher_args: List[str], zone: Optional[str],
                                                expected: List[str]) -> None:
    """With `expose_zone`, `{zone}` in each launcher word becomes the zone's name, and the environment names it."""
    monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    launch = zone_launch(launcher, launcher_args, zone=zone, template='echo {zone} {}')
    start = launch.argv.index('hs')
    assert launch.argv[:start] == expected
    assert launch.argv[launch.argv.index('-t') + 1] == 'echo {zone} {}'
    assert launch.env == {AUTH_ENV: 'k' * 32, ZONE_ENV: 'default' if zone is None else zone}
    client_args = launch.argv[start:]
    assert client_args == zone_launch(launcher, launcher_args, zone=zone, template='echo {zone} {}',
                                      expose_zone=False).argv[start:]
    assert ('--zone' in client_args) is (zone is not None)


def test_client_launch_exposes_no_zone_by_default() -> None:
    """Without `expose_zone`, `{zone}` reaches the launcher as written and the environment does not name a zone."""
    launch = zone_launch(['shim', '{zone}'], expose_zone=False)
    assert launch.argv[:2] == ['shim', '{zone}']
    assert ZONE_ENV not in launch.env


@mark.parametrize('launcher, launcher_args', [
    pytest.param(['mpirun'], ['--host', '{zone}-node'], id='parameter'),
    pytest.param(['mpirun', '-x', 'LD_LIBRARY_PATH'], [], id='forward-option'),
])
def test_client_launch_never_forwards_zone_to_open_mpi(monkeypatch: MonkeyPatch, launcher: List[str],
                                                       launcher_args: List[str]) -> None:
    """Exposing the zone leaves the Open MPI key forwarding exactly as it is without it."""
    monkeypatch.delenv(OMPI_ENV_LIST, raising=False)
    exposed = zone_launch(launcher, launcher_args)
    plain = zone_launch(launcher, launcher_args, expose_zone=False)
    assert {name: value for name, value in exposed.env.items() if name != ZONE_ENV} == plain.env
    assert ZONE_ENV not in exposed.env.get(OMPI_ENV_LIST, '')
    assert ZONE_ENV not in exposed.argv
    assert [word for word in exposed.argv if word != 'gpu-node'] == [word for word in plain.argv
                                                                     if word != '{zone}-node']


def test_ssh_launch_refuses_to_expose_zone() -> None:
    """An SSH launch has no launcher to name a zone to, so asking it to is an error."""
    with pytest.raises(ValueError):
        zone_launch(['ssh'], ssh_host='node-7')


def test_client_zone_never_comes_from_configuration(temp_site: Path) -> None:
    """A client given no --zone serves the default zone, even when the environment names a zone."""
    script = ('from hypershell.core.config import config\n'
              'from hypershell.client import ClientApp\n'
              'app = ClientApp.from_cmdline([\'-H\', \'localhost\'])\n'
              'app.check_args()\n'
              'print(config.zone, app.zone)\n')
    env = {**os.environ, ZONE_ENV: 'gpu'}
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['gpu', 'None']


def test_ssh_env_export_never_carries_key(stand_ins: StandIns, temp_site: Path) -> None:
    """With --ssh --env, a key variable in the launching environment is on no command line and in no log line."""
    held = 'h' * 128
    run = run_cluster(stand_ins, ['--ssh', SSH_HOSTS[0], '--env'], temp_site, True, None,
                      until=ssh_failed(stand_ins), env={AUTH_ENV: held})
    records = stand_ins.read()
    for record in records:
        on_command_line = held in ' '.join(record['argv'])
        assert not on_command_line, f'Key on the command line of {record["name"]}'
        in_remote_command = held in record.get('remote', '')
        assert not in_remote_command, 'Key in the remote command'
    assert_key_not_in(held, run.stdout, run.stderr)
    clients = [record for record in records if record['role'] == 'client']
    assert clients and all(record['key'] and record['key'] != held for record in clients)
    assert record_level_exported(records)
    assert_run_complete(run, stand_ins, temp_site)


def record_level_exported(records: List[Dict[str, Any]]) -> bool:
    """Every client got the launching logging level, so --env still exported the other variables."""
    return all(record['level'] == 'DEVEL' for record in records if record['role'] == 'client')


@mark.parametrize('name', [
    pytest.param('HYPERSHELL_PROBE.NAME', id='dot'),
    pytest.param('HYPERSHELL_PROBE-NAME', id='dash'),
    pytest.param('HYPERSHELL_PROBE NAME', id='space'),
])
def test_ssh_env_skips_names_a_shell_cannot_assign(stand_ins: StandIns, temp_site: Path, name: str) -> None:
    """With --ssh --env, a variable a shell cannot assign is on no command line, and the launch still runs."""
    run = run_cluster(stand_ins, ['--ssh', SSH_HOSTS[0], '--env'], temp_site, True, None,
                      until=ssh_failed(stand_ins), env={name: 'probe'})
    records = stand_ins.read()
    for record in records:
        on_command_line = name in ' '.join(record['argv']) or name in record.get('remote', '')
        assert not on_command_line, f'{name!r} on the command line of {record["name"]}'
    assert f'Not exporting {name!r} to remote clients' in run.stderr, run.stderr[-4000:]
    assert record_level_exported(records)
    assert_run_complete(run, stand_ins, temp_site)


def test_ssh_launch_exports_never_carry_key(monkeypatch: MonkeyPatch) -> None:
    """An SSH launch drops any export of the key variable, from --env or from a caller, and keeps the rest."""
    held = 'h' * 128
    params = {**SSH_LAUNCH_BASE}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launch = client_launch(remote_exe, address, 'k' * 32, ssh_host='node-7',
                           ssh_exports={'HYPERSHELL_SITE': '/site', AUTH_ENV: held}, **params)
    _, after, _, _ = split_ssh_launch(launch.argv, 'node-7')
    assert after[:-1] == ['HYPERSHELL_SITE=/site', 'sh', '-c']
    assert held not in ' '.join(launch.argv) and held not in launch.describe()
    monkeypatch.setenv(AUTH_ENV, held)
    cluster = SSHCluster(nodelist=SSH_HOSTS, export_env=True, bind=(LOOPBACK, int(os.environ['HYPERSHELL_SERVER_PORT'])),
                         tls=None)
    for launch in cluster.launches:
        assert held not in ' '.join(launch.argv) and held not in launch.describe()
        assert any(word.startswith('HYPERSHELL_SERVER_PORT=') for word in launch.argv)


@mark.parametrize('stdin', [pytest.param(None, id='launcher'), pytest.param('key', id='ssh')])
def test_client_launch_description_redacts_key_exports(stdin: Optional[str]) -> None:
    """A launch's log description never shows a value assigned to the key variable on its command line."""
    held = 'h' * 128
    argv = ['ssh', '-T', 'node-7', f'{AUTH_ENV}={held}', 'HYPERSHELL_SITE=/site', 'sh', '-c', "'true'"]
    launch = ClientLaunch(argv=argv, env={}, stdin=stdin, inherit_env=stdin is not None)
    description = launch.describe()
    assert held not in description
    assert f'{AUTH_ENV}=' in description and 'HYPERSHELL_SITE=/site' in description


def test_client_launch_start_survives_closed_input() -> None:
    """Starting a launch whose process closes its input before reading the key raises nothing."""
    child = [sys.executable, '-c', 'import os, time; os.close(0); time.sleep(0.5)']
    launch = ClientLaunch(argv=child, env={}, stdin='k' * (1 << 20) + '\n', inherit_env=True)
    proc = launch.start()
    assert proc.wait(timeout=30) == 0


SERVER_KEY: Final[str] = 's' * 64
"""The key the stand-alone server in the hint tests requires."""


def start_server(temp_site: Path) -> Popen:
    """Start `hs server` with :const:`SERVER_KEY` in its own session, once it accepts connections."""
    taskfile = create_taskfile_echo(temp_site, count=1)
    log = open(Path(temp_site) / 'server.log', mode='w')
    proc = Popen(['hs', 'server', str(taskfile), '--no-db', '--no-tls', '-H', LOOPBACK, '-k', SERVER_KEY],
                 env=dict(os.environ), stdin=DEVNULL, stdout=log, stderr=log, start_new_session=True)
    log.close()
    deadline = time.monotonic() + 60
    port = int(os.environ['HYPERSHELL_SERVER_PORT'])
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail('Server exited early\n' + (Path(temp_site) / 'server.log').read_text()[-4000:])
        try:
            socket.create_connection((LOOPBACK, port), timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.2)
    kill_group(proc)
    pytest.fail('Server did not start')


@mark.parametrize('client_args, env, config_key, hint', [
    pytest.param([], {}, None, True, id='no-key'),
    pytest.param(['-k', DEFAULT_AUTH], {}, None, False, id='explicit-default-key'),
    pytest.param(['-k', 'w' * 64], {}, None, False, id='explicit-wrong-key'),
    pytest.param([], {AUTH_ENV: 'w' * 64}, None, False, id='environment-wrong-key'),
    pytest.param([], {AUTH_ENV: DEFAULT_AUTH}, None, False, id='environment-default-key'),
    pytest.param([], {}, 'w' * 64, False, id='config-wrong-key'),
])
def test_client_names_missing_key_only_when_none_given(temp_site: Path, client_args: List[str],
                                                      env: Dict[str, str], config_key: Optional[str],
                                                      hint: bool) -> None:
    """A client that fails to authenticate names a missing key only when no key came from any source."""
    if config_key is not None:
        config_file = Path(temp_site) / 'client.toml'
        config_file.write_text(f'[server]\nauth = "{config_key}"\n')
        env = {**env, 'HYPERSHELL_CONFIG_FILE': str(config_file)}
    server = start_server(temp_site)
    try:
        run = run_in_session(['hs', 'client', '-H', LOOPBACK, '--no-tls', '-T', '5', *client_args],
                             {**os.environ, **env}, Path(temp_site), timeout=60, name='client')
    finally:
        kill_group(server)
    assert run.status == exit_status.runtime_error, run.stderr[-4000:]
    assert 'AuthenticationError' in run.stderr
    named = 'No key reached this client' in run.stderr
    assert named == hint, run.stderr[-4000:]


# Values a shell treats specially; each must reach the remote shell as exactly itself.
AWKWARD_VALUES: Final[List[str]] = ['a\\', 'q"', "it's", '"', 'two words', '$(echo x) `echo y` $HOME', '']


@mark.parametrize('value', [pytest.param(value, id=f'value-{index}') for index, value in enumerate(AWKWARD_VALUES)])
def test_ssh_env_exports_quote_every_value(monkeypatch: MonkeyPatch, tmp_path: Path, value: str) -> None:
    """With --env, every export reaches the remote shell as exactly its own value, and none carries the key."""
    held = 'h' * 128
    for name in [name for name in os.environ if name.startswith('HYPERSHELL_')]:
        monkeypatch.delenv(name)
    monkeypatch.setenv('HYPERSHELL_PROBE_A', value)
    monkeypatch.setenv(AUTH_ENV, held)
    monkeypatch.setenv('HYPERSHELL_PROBE_Z', value)
    monkeypatch.setenv('HYPERSHELL_SERVER_PORT', str(50001))
    cluster = SSHCluster(nodelist=['node-7'], export_env=True, bind=(LOOPBACK, 50001), tls=None)
    launch, = cluster.launches
    _, after, _, _ = split_ssh_launch(launch.argv, 'node-7')
    exports = after[:-3]
    assert held not in ' '.join(launch.argv) and held not in launch.describe()
    # The remote shell gets the words joined, as ssh sends them; it must read back each value exactly.
    output = tmp_path / 'remote-env.json'
    dump = 'import os, sys, json; json.dump(dict(os.environ), open(sys.argv[1], "w"))'
    probe = shlex.join([sys.executable, '-c', dump, str(output)])
    subprocess.run(['/bin/sh', '-c', ' '.join([*exports, probe])], check=True, timeout=60,
                   env={'PATH': '/usr/bin:/bin'})
    remote = json.loads(output.read_text())
    assert AUTH_ENV not in remote and held not in json.dumps(remote)
    assert remote['HYPERSHELL_PROBE_A'] == value and remote['HYPERSHELL_PROBE_Z'] == value
    assert remote['HYPERSHELL_SERVER_PORT'] == '50001'


@mark.parametrize('word', [
    pytest.param(f'"{AUTH_ENV}=h"', id='double-quoted'),
    pytest.param(f"'{AUTH_ENV}'=h", id='quoted-name'),
    pytest.param(f"{AUTH_ENV}='h'", id='quoted-value'),
    pytest.param(f'HYPERSHELL_A=a {AUTH_ENV}=h', id='two-assignments'),
    pytest.param(f'{AUTH_ENV}="h', id='unbalanced'),
])
def test_client_launch_description_redacts_key_however_quoted(word: str) -> None:
    """A launch's log description redacts a word that assigns the key variable, however the word is quoted."""
    launch = ClientLaunch(argv=['ssh', '-T', 'node-7', word, 'HYPERSHELL_SITE=/site', 'sh', '-c', "'true'"],
                          env={}, stdin='key', inherit_env=True)
    description = launch.describe()
    assert word not in description and shlex.quote(word) not in description
    assert '=h' not in description and "'h'" not in description
    assert f'{AUTH_ENV}=***' in description and 'HYPERSHELL_SITE=/site' in description


# Run as a template shell expansion: writes the environment it runs with, as JSON, to a private
# file in the directory it is given, and prints nothing, so the task's command is just its args.
DUMP_EXPANSION_ENV: Final[str] = """\
import os, sys, json, uuid
path = os.path.join(sys.argv[1], uuid.uuid4().hex + '.json')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
    json.dump(dict(os.environ), stream)
"""


def test_template_shell_expansion_never_sees_key(stand_ins: StandIns, temp_site: Path, tmp_path: Path) -> None:
    """A launched client runs template shell expansions without the key, and with the rest of its environment."""
    script = tmp_path / 'dump_expansion_env.py'
    script.write_text(DUMP_EXPANSION_ENV)
    dumps = tmp_path / 'expansion-env'
    dumps.mkdir(mode=0o700)
    template = '{% ' + shlex.join([sys.executable, str(script), str(dumps)]) + ' %}{}'
    run = run_cluster(stand_ins, [f'--launcher={stand_ins.launcher}', '-t', template], temp_site, True, None,
                      env={'USER_MARKER': 'kept', OMPI_ENV_LIST: USER_OMPI_NAMES})
    key = assert_key_off_command_lines(stand_ins.read())
    assert_run_complete(run, stand_ins, temp_site)
    environments = [json.loads(path.read_text()) for path in dumps.glob('*.json')]
    assert len(environments) == TASK_COUNT
    for env in environments:
        holds_variable, holds_key = AUTH_ENV in env, key in json.dumps(env)
        assert not holds_variable, 'A template expansion received the key variable'
        assert not holds_key, 'A template expansion received the key'
        assert env.get('USER_MARKER') == 'kept'
        assert env.get(OMPI_ENV_LIST) == USER_OMPI_NAMES


# A launcher that drops the whole environment, as `srun --export=NONE` does where the client's site
# and TLS settings came from that environment: the client starts with a home of its own, so it finds
# neither its key nor the server's certificate. Its configuration file stays blank, as in every run,
# unless {key_in} names where the client still gets the run's key: 'env' passes the key variable
# alone through, and 'config' writes the key into a configuration file in that home instead.
LAUNCHER_BARE_ENV_BODY: Final[str] = """\
record('launcher')
home = os.path.join(os.path.dirname(RECORDS), 'bare-home-' + uuid.uuid4().hex)
os.mkdir(home, 0o700)
env = {{'PATH': os.environ['PATH'], 'HOME': home, 'HYPERSHELL_CONFIG_FILE': '', 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG'}}
if {key_in!r} == 'env':
    env[{auth_env!r}] = os.environ[{auth_env!r}]
if {key_in!r} == 'config':
    config_file = os.path.join(home, 'client.toml')
    with os.fdopen(os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write('[server]\\nauth = "' + os.environ[{auth_env!r}] + '"\\n')
    env['HYPERSHELL_CONFIG_FILE'] = config_file
os.chdir(home)
os.execve(sys.argv[1], sys.argv[1:], env)
"""


@mark.parametrize('key_in, hint', [
    pytest.param(None, True, id='no-key'),
    pytest.param('env', False, id='key-kept'),
    pytest.param('config', False, id='key-in-config'),
])
def test_launched_client_without_environment_names_cause_under_tls(stand_ins: StandIns, temp_site: Path,
                                                                   key_in: Optional[str], hint: bool) -> None:
    """Under TLS, a launched client that got no key from any source names the launcher's environment."""
    body = LAUNCHER_BARE_ENV_BODY.format(key_in=key_in, auth_env=AUTH_ENV)
    launcher = write_program(stand_ins.bin / 'launcher-bare-env', body, stand_ins.records)
    run = run_cluster(stand_ins, [f'--launcher={launcher}'], temp_site, False, None,
                      until=lambda: bool(stand_ins.exits()))
    exits = stand_ins.exits()
    assert [record['status'] for record in exits] == [exit_status.runtime_error], run.stderr[-4000:]
    assert 'SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]' in run.stderr, run.stderr[-4000:]
    named = 'the launcher most likely did not pass the environment to the client' in run.stderr
    assert named == hint, run.stderr[-4000:]
    assert ('neither its key nor the TLS settings' in run.stderr) == hint
