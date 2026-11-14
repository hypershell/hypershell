# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
A cluster whose launched clients are all gone with work left says so and exits.

The cluster-level tests run a launcher-mode or SSH cluster in a child process in the test's own
site, with launchers that exit at once (`sh -c 'exit N'`) or run the client and then exit, and a
stand-in `ssh` that fails every host. Each child runs in its own session, under a hard timeout, and
its whole process group is killed when it ends, so a cluster that never stops fails the test
instead of hanging the suite.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional, Final

# Standard libs
import os
import sys
import json
import time
import signal
import logging
import threading
from pathlib import Path
from subprocess import Popen, PIPE

# External libs
import pytest
from pytest import mark, MonkeyPatch

# Internal libs
from tests.conftest import free_port
from hypershell.cluster import watch
from hypershell.cluster.watch import ClusterLaunchError, exit_text, watch_launched


CHILD_TIMEOUT: Final[float] = 60
"""Seconds before a child run's process group is killed and the test fails."""

STOP_BOUND: Final[float] = 10
"""Seconds within which a cluster whose clients are all gone with work left must have exited."""

WAIT_BOUND: Final[float] = 8
"""Seconds a cluster that should keep waiting is watched before it is stopped."""

ADDRESS: Final[str] = '127.0.0.1'
"""Bind address of every child cluster, so nothing depends on resolving this host's name."""

HS: Final[str] = os.path.join(os.path.dirname(sys.executable), 'hs')
"""This tree's `hs`, run as the remote executable when a launcher starts a real client."""


# Builds a cluster from the JSON options in argv[1] and runs it. With `wait`, the cluster is
# expected to keep running: it is stopped after that many seconds. With `submit_after`, one task
# is added to the database that many seconds after the start. Prints what it saw as JSON.
CLUSTER_SCRIPT: Final[str] = """\
import sys, json, time, logging, threading
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append({'time': time.monotonic(), 'message': record.getMessage()})
logging.getLogger('hypershell').addHandler(Capture(level=logging.WARNING))
from hypershell.data import initdb
from hypershell.data.model import Task
from hypershell.cluster.remote import RemoteCluster
from hypershell.cluster.ssh import SSHCluster
options = json.loads(sys.argv[1])
kind, wait, submit_after = options.pop('kind'), options.pop('wait', None), options.pop('submit_after', None)
if not options.get('in_memory'):
    initdb()
tasks = options.pop('tasks')
source = [f'echo {n}' for n in range(tasks)] if tasks else None
cluster = (SSHCluster if kind == 'ssh' else RemoteCluster)(source=source, tls=None, **options)
started = time.monotonic()
cluster.start()
if submit_after is not None:
    time.sleep(submit_after)
    submitted = time.monotonic()
    Task.add(Task.new(args='echo late'))
error, waiting = None, False
try:
    if wait is not None:
        cluster.join(timeout=wait)
        waiting = cluster.is_alive()
        if waiting:
            cluster.server.stop(wait=True)
    cluster.join()
except Exception as exc:
    error = {'type': type(exc).__name__, 'message': str(exc)}
ended = time.monotonic()
print(json.dumps({
    'error': error, 'waiting': waiting, 'elapsed': ended - started,
    'since_submit': None if submit_after is None else ended - submitted,
    'server_alive': cluster.server.is_alive(),
    'remaining': None if options.get('in_memory') else Task.count_remaining(),
    'warnings': [record['message'] for record in records],
}))
"""

# Starts a server without a database holding more tasks than its queues take, with no client,
# then stops it. Prints the seconds the stop took.
SUBMIT_STOP_SCRIPT: Final[str] = """\
import sys, time
from hypershell.server import ServerThread
tasks, bundlesize, port = map(int, sys.argv[1:])
server = ServerThread(source=[f'echo {n}' for n in range(tasks)], in_memory=True, bundlesize=bundlesize,
                      address=('127.0.0.1', port), auth='stop-test', tls=None)
server.start()
time.sleep(2)  # Every queue is full and the submitter is waiting on room
started = time.monotonic()
server.stop(wait=True)
print(time.monotonic() - started)
"""

# A stand-in for `ssh` that fails every host, as ssh does for one it cannot reach.
SSH_FAIL_BODY: Final[str] = """\
#!/bin/sh
exit 255
"""


def run_child(argv: List[str], workdir: Path, env: Dict[str, str] = None,
              timeout: float = CHILD_TIMEOUT) -> str:
    """Run `argv` in its own session and return its standard output; fail if it outlives `timeout`."""
    stderr_path = workdir / f'child-{time.monotonic_ns()}.stderr'
    with open(stderr_path, mode='w') as stderr:
        proc = Popen(argv, cwd=workdir, env={**os.environ, **(env or {})}, stdout=PIPE, stderr=stderr,
                     start_new_session=True, text=True)
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except Exception:
            kill_group(proc)
            pytest.fail(f'Child did not exit within {timeout}s:\n' + stderr_path.read_text()[-4000:])
        finally:
            kill_group(proc)
    assert proc.returncode == 0, stderr_path.read_text()[-4000:]
    return stdout


def kill_group(proc: Popen) -> None:
    """Kill every process left in the session `proc` leads, then reap `proc`."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # Group already gone
    proc.wait()


def run_cluster(workdir: Path, env: Dict[str, str] = None, **options: Any) -> Dict[str, Any]:
    """Run a cluster in a child process with `options` and return what it saw."""
    options = {'bind': [ADDRESS, free_port()], **options}
    stdout = run_child([sys.executable, '-c', CLUSTER_SCRIPT, json.dumps(options)], workdir, env=env)
    return json.loads(stdout.strip().splitlines()[-1])


def ssh_stand_in(workdir: Path) -> Dict[str, str]:
    """Environment putting a stand-in `ssh` that fails every host first on PATH."""
    bin_dir = workdir / 'bin'
    bin_dir.mkdir(exist_ok=True)
    ssh = bin_dir / 'ssh'
    ssh.write_text(SSH_FAIL_BODY)
    ssh.chmod(0o755)
    return {'PATH': os.pathsep.join([str(bin_dir), os.environ.get('PATH', '')])}


# --- How an exit is named ---------------------------------------------------------------------------------


@mark.unit
@mark.parametrize('code, text', [
    (0, 'status 0'), (1, 'status 1'), (255, 'status 255'), (137, 'status 137'),
    (-9, 'signal 9 (SIGKILL)'), (-15, 'signal 15 (SIGTERM)'), (-200, 'signal 200'),
])
def test_exit_text(code: int, text: str) -> None:
    """A return code reads as an exit status, or as the signal that ended the process."""
    assert exit_text(code) == text


# --- The watch, against a stand-in server -----------------------------------------------------------------


class StandInServer:
    """Runs until stopped, as a server with no client does; records the stop."""

    def __init__(self: StandInServer) -> None:
        self.ended = threading.Event()
        self.stopped = False

    def is_alive(self: StandInServer) -> bool:
        return not self.ended.is_set()

    def join(self: StandInServer, timeout: Optional[float] = None) -> None:
        self.ended.wait(timeout)

    def stop(self: StandInServer, wait: bool = False, timeout: Optional[float] = None) -> None:
        self.stopped = True
        self.ended.set()


def sh(script: str) -> Popen:
    """A launched process running `script`."""
    return Popen(['sh', '-c', script])


def watch_in_thread(server: StandInServer, launched: list) -> Dict[str, Any]:
    """Run the watch in a thread; the returned dict gets its error once it ends."""
    outcome: Dict[str, Any] = {'error': None}

    def target() -> None:
        try:
            watch_launched(server, launched, (ADDRESS, 50123), interval=0.1)
        except ClusterLaunchError as error:
            outcome['error'] = error

    outcome['thread'] = thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return outcome


def client_warnings(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Captured warnings naming a launched client's exit."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and record.getMessage().startswith('Client ')]


@mark.unit
def test_watch_stops_the_server_when_every_client_fails(monkeypatch: MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    """Every process gone, one non-zero, work left: the server is stopped and one error names them all."""
    monkeypatch.setattr(watch, 'work_remains', lambda server: True)
    server = StandInServer()
    launched = [('node-a', sh('exit 3')), ('node-a', sh('exit 0')), ('node-b', sh('kill -9 $$'))]
    with pytest.raises(ClusterLaunchError) as caught:
        watch_launched(server, launched, (ADDRESS, 50123), interval=0.1)
    assert server.stopped
    assert str(caught.value) == ('every launched client has exited with tasks remaining: '
                                 'node-a exited with status 3; node-a exited with status 0; '
                                 'node-b exited with signal 9 (SIGKILL); clients were told to dial 127.0.0.1:50123')
    assert sorted(client_warnings(caplog)) == ['Client node-a exited with status 3',
                                               'Client node-b exited with signal 9 (SIGKILL)']


@mark.unit
def test_watch_waits_when_every_client_exits_zero(monkeypatch: MonkeyPatch) -> None:
    """A launcher that exits 0 with work left leaves the cluster waiting, and work is never even read."""
    reads = []
    monkeypatch.setattr(watch, 'work_remains', lambda server: reads.append(server) or True)
    server = StandInServer()
    outcome = watch_in_thread(server, [('srun', sh('exit 0'))])
    outcome['thread'].join(timeout=2)
    assert outcome['thread'].is_alive() and not server.stopped and not reads
    server.ended.set()
    outcome['thread'].join(timeout=5)
    assert outcome['error'] is None


@mark.unit
def test_watch_waits_while_no_work_remains(monkeypatch: MonkeyPatch) -> None:
    """With every process failed but nothing left to run, the cluster waits; work arriving later ends it."""
    work = threading.Event()
    monkeypatch.setattr(watch, 'work_remains', lambda server: work.is_set())
    server = StandInServer()
    outcome = watch_in_thread(server, [('srun', sh('exit 1'))])
    outcome['thread'].join(timeout=2)
    assert outcome['thread'].is_alive() and not server.stopped
    work.set()
    outcome['thread'].join(timeout=5)
    assert server.stopped and isinstance(outcome['error'], ClusterLaunchError)


@mark.unit
def test_watch_warns_at_each_exit_while_others_run(monkeypatch: MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    """One host's failure is reported while another host's client still runs."""
    monkeypatch.setattr(watch, 'work_remains', lambda server: True)
    server = StandInServer()
    running = sh('sleep 30')
    outcome = watch_in_thread(server, [('node-a', sh('exit 255')), ('node-b', running)])
    deadline = time.monotonic() + 5
    while not client_warnings(caplog) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client_warnings(caplog) == ['Client node-a exited with status 255']
    assert running.poll() is None and outcome['thread'].is_alive()
    running.kill()
    outcome['thread'].join(timeout=5)
    assert isinstance(outcome['error'], ClusterLaunchError)
    assert 'node-b exited with signal 9 (SIGKILL)' in str(outcome['error'])


@mark.unit
def test_watch_only_warns_when_the_server_ends_on_its_own(monkeypatch: MonkeyPatch,
                                                          caplog: pytest.LogCaptureFixture) -> None:
    """A server that finishes its work ends the watch without an error; a later failure is only warned about."""
    monkeypatch.setattr(watch, 'work_remains', lambda server: False)
    server = StandInServer()
    late = sh('sleep 1; exit 4')
    outcome = watch_in_thread(server, [('node-a', sh('exit 2')), ('node-b', late)])
    time.sleep(0.5)
    server.ended.set()  # Finished on its own; node-b is still running
    outcome['thread'].join(timeout=5)
    assert not outcome['thread'].is_alive() and outcome['error'] is None and not server.stopped
    assert late.returncode == 4
    assert client_warnings(caplog) == ['Client node-a exited with status 2', 'Client node-b exited with status 4']


# --- Clusters whose clients all fail ----------------------------------------------------------------------


@mark.integration
@mark.parametrize('in_memory', [pytest.param(True, id='no-db'), pytest.param(False, id='database')])
@mark.parametrize('launcher, exited', [
    pytest.param("sh -c 'exit 3'", 'status 3', id='status'),
    pytest.param("sh -c 'kill -9 $$'", 'signal 9 (SIGKILL)', id='signal'),
])
def test_launcher_cluster_exits_when_every_client_fails(temp_site: Path, in_memory: bool,
                                                        launcher: str, exited: str) -> None:
    """A launcher that fails with work left stops the server and raises within the bound, naming it."""
    seen = run_cluster(Path(temp_site), kind='launch', launcher=launcher, in_memory=in_memory, tasks=4)
    assert seen['error'] is not None, seen
    assert seen['error']['type'] == 'ClusterLaunchError'
    message = seen['error']['message']
    assert message.startswith('every launched client has exited with tasks remaining: ')
    assert f'{launcher} hyper-shell client ' in message and f'exited with {exited}' in message
    assert f'clients were told to dial {ADDRESS}:' in message
    assert seen['elapsed'] <= STOP_BOUND, seen
    assert not seen['server_alive']
    assert any(warning.startswith('Client ') and warning.endswith(f'exited with {exited}')
               for warning in seen['warnings'])
    if not in_memory:
        assert seen['remaining'] == 4


@mark.integration
@mark.parametrize('in_memory', [pytest.param(True, id='no-db'), pytest.param(False, id='database')])
def test_ssh_cluster_exits_when_every_host_fails(temp_site: Path, in_memory: bool) -> None:
    """Every host's ssh failing with work left stops the server and raises, naming each host."""
    site = Path(temp_site)
    seen = run_cluster(site, env=ssh_stand_in(site), kind='ssh', nodelist=['node-a', 'node-b'],
                       in_memory=in_memory, tasks=4)
    assert seen['error'] is not None, seen
    assert seen['error']['type'] == 'ClusterLaunchError'
    assert ('node-a exited with status 255; node-b exited with status 255; '
            f'clients were told to dial {ADDRESS}:') in seen['error']['message']
    assert seen['elapsed'] <= STOP_BOUND, seen
    assert sorted(warning for warning in seen['warnings'] if warning.startswith('Client ')) == [
        'Client node-a exited with status 255', 'Client node-b exited with status 255']


@mark.integration
def test_launcher_cluster_exiting_zero_keeps_waiting(temp_site: Path) -> None:
    """A launcher that exits 0 with work left (one that hands its clients on) leaves the cluster waiting."""
    seen = run_cluster(Path(temp_site), kind='launch', launcher="sh -c 'exit 0'", in_memory=True, tasks=4,
                       wait=WAIT_BOUND)
    assert seen['waiting'] and seen['error'] is None, seen
    assert not [warning for warning in seen['warnings'] if warning.startswith('Client ')]


@mark.integration
@mark.parametrize('in_memory', [pytest.param(True, id='no-db'), pytest.param(False, id='database')])
def test_cluster_whose_clients_finish_ends_as_before(temp_site: Path, in_memory: bool) -> None:
    """A launcher that runs the client, which does the work, then exits non-zero: no error, one warning."""
    launcher = "sh -c '\"$@\"; exit 3' sh"
    seen = run_cluster(Path(temp_site), kind='launch', launcher=launcher, remote_exe=HS, in_memory=in_memory,
                       tasks=4)
    assert seen['error'] is None, seen
    assert not seen['server_alive']
    warned, = [warning for warning in seen['warnings'] if warning.startswith('Client ')]
    assert warned.startswith(f'Client {launcher} {HS} client -H {ADDRESS} ')
    assert warned.endswith(' exited with status 3')
    if not in_memory:
        assert seen['remaining'] == 0


@mark.integration
def test_forever_cluster_waits_until_work_arrives(temp_site: Path) -> None:
    """A forever cluster with nothing pending waits when its clients fail; a task submitted later ends it."""
    seen = run_cluster(Path(temp_site), kind='launch', launcher="sh -c 'exit 1'", forever_mode=True, tasks=0,
                       submit_after=WAIT_BOUND)
    assert seen['error'] is not None and seen['error']['type'] == 'ClusterLaunchError', seen
    assert seen['elapsed'] >= WAIT_BOUND
    assert seen['since_submit'] <= STOP_BOUND, seen
    assert seen['remaining'] == 1


# --- A server without a database stops with no client -----------------------------------------------------


@mark.integration
@mark.parametrize('tasks, bundlesize', [pytest.param(20, 1, id='bundles-of-1'), pytest.param(9, 2, id='bundles-of-2')])
def test_server_without_database_stops_with_tasks_queued(temp_site: Path, tasks: int, bundlesize: int) -> None:
    """A server holding more tasks than its queues take, and no client to take them, stops within a bound."""
    stdout = run_child([sys.executable, '-c', SUBMIT_STOP_SCRIPT, str(tasks), str(bundlesize), str(free_port())],
                       Path(temp_site), timeout=30)
    assert float(stdout.strip().splitlines()[-1]) <= 8
