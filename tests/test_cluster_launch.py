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
from hypershell.cluster.launch import AUTH_ENV, OMPI_ENV_LIST, ClientLaunch, client_launch
from hypershell.core.logging import HOSTNAME
from hypershell.cluster.remote import AutoScaler, AutoScalingCluster
from hypershell.cluster.ssh import SSHCluster, DEFAULT_REMOTE_EXE


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
    path = os.path.join(RECORDS, f'{{uuid.uuid4().hex}}.json')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump({{'role': role, 'name': os.path.basename(sys.argv[0]), 'argv': sys.argv,
                    'key': os.environ.get({auth_env!r}), 'ompi': os.environ.get({ompi_env!r}),
                    'level': os.environ.get('HYPERSHELL_LOGGING_LEVEL'), **fields}}, stream)
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
    exports = ['HYPERSHELL_SITE=/site']
    launch = client_launch(remote_exe, address, key, launcher=launcher, launcher_args=launcher_args,
                           ssh_host='node-7', ssh_exports=exports, **params)
    before, after, preamble, command = split_ssh_launch(launch.argv, 'node-7')
    assert before == [*launcher, *launcher_args, '-T']
    assert after[:-1] == [*exports, 'sh', '-c']
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


@mark.parametrize('ssh_host', [pytest.param(None, id='launcher'), pytest.param('node-7', id='ssh')])
@mark.parametrize('options', LAUNCH_OPTIONS)
def test_launch_contract_uses_only_known_client_options(options: Dict[str, Any], ssh_host: Optional[str]) -> None:
    """Every option a launch passes to the client, in every mode, is one a 2.9.0a4 client accepts."""
    params = {**LAUNCH_BASE, **options}
    remote_exe, address = params.pop('remote_exe'), params.pop('address')
    launch = client_launch(remote_exe, address, 'k' * 32, ssh_host=ssh_host, **params)
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
