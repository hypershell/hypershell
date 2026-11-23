# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Every Python API entry point that takes `max_retries` defaults to the commands' rule,
`server.attempts - 1` (none with the built-in configuration).

Each entry point is built, never started: the server's scheduler (and the autoscaler's) records
the attempts it allows. The database is reported enabled so a scheduler is built. The `run_*` and
`serve_*` functions build their thread through `.new` and wait in `join_waking`; both are replaced
so the function returns the thread it built without starting it.

Prototype (plan time): copied to `tests/test_retry_defaults.py` by update-client-retry P1.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List

# Standard libs
import sys
import logging
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
import hypershell.server
import hypershell.cluster.local
import hypershell.cluster.remote
import hypershell.cluster.ssh
from hypershell.core.config import config
from hypershell.server import ServerThread, serve_from, serve_file, serve_forever
from hypershell.cluster import (LocalCluster, RemoteCluster, SSHCluster, AutoScalingCluster,
                                run_local, run_cluster, run_ssh)
from tests import main
from tests.conftest import free_port


def server_attempts(thread: Any) -> int:
    """The attempts allowed by the scheduler of a server thread, or of a cluster's server."""
    server = thread if isinstance(thread, ServerThread) else thread.server
    return server.scheduler.machine.attempts


def loopback() -> tuple:
    """A loopback address on a free port."""
    return '127.0.0.1', free_port()


def capture(monkeypatch: pytest.MonkeyPatch, module: Any, cls: type, call: Callable[[], None]) -> Any:
    """Run `call` with `cls.new` building without starting and `module.join_waking` returning at once;
    return the one thread it built."""
    threads: List[Any] = []

    def new(*args, **kwargs) -> Any:
        threads.append(cls(*args, **kwargs))
        return threads[-1]

    monkeypatch.setattr(cls, 'new', staticmethod(new))
    monkeypatch.setattr(module, 'join_waking', lambda thread: None)
    call()
    assert len(threads) == 1
    return threads[0]


def server_thread(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    return [server_attempts(ServerThread(address=loopback(), auth='test'))]


def via_serve_from(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.server, ServerThread,
                     lambda: serve_from(['echo 1'], address=loopback(), auth='test'))
    return [server_attempts(thread)]


def via_serve_file(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    path = tmp / 't.in'
    path.write_text('echo 1\n', encoding='utf-8')
    thread = capture(mp, hypershell.server, ServerThread,
                     lambda: serve_file(str(path), address=loopback(), auth='test'))
    return [server_attempts(thread)]


def via_serve_forever(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.server, ServerThread, lambda: serve_forever(address=loopback(), auth='test'))
    return [server_attempts(thread)]


def local_cluster(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    return [server_attempts(LocalCluster(source=['echo 1'], port=free_port()))]


def remote_cluster(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    return [server_attempts(RemoteCluster(source=['echo 1'], launcher='srun -n1', bind=loopback()))]


def ssh_cluster(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    return [server_attempts(SSHCluster(source=['echo 1'], nodelist=['node-1'], bind=loopback()))]


def autoscaling_cluster(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    cluster = AutoScalingCluster(launcher='srun -n1', bind=loopback())
    return [server_attempts(cluster), cluster.autoscaler.machine.attempts]


def via_run_local(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.cluster.local, LocalCluster, lambda: run_local(['echo 1'], port=free_port()))
    return [server_attempts(thread)]


def via_run_cluster(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.cluster.remote, RemoteCluster,
                     lambda: run_cluster(['echo 1'], launcher='srun -n1', bind=loopback()))
    return [server_attempts(thread)]


def via_run_cluster_autoscaling(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.cluster.remote, AutoScalingCluster,
                     lambda: run_cluster(launcher='srun -n1', autoscaling=True, bind=loopback()))
    return [server_attempts(thread), thread.autoscaler.machine.attempts]


def via_run_ssh(mp: pytest.MonkeyPatch, tmp: Path) -> List[int]:
    thread = capture(mp, hypershell.cluster.ssh, SSHCluster,
                     lambda: run_ssh(['echo 1'], nodelist=['node-1'], bind=loopback()))
    return [server_attempts(thread)]


ENTRY_POINTS: Dict[str, Callable[[pytest.MonkeyPatch, Path], List[int]]] = {
    'ServerThread': server_thread,
    'serve_from': via_serve_from,
    'serve_file': via_serve_file,
    'serve_forever': via_serve_forever,
    'LocalCluster': local_cluster,
    'RemoteCluster': remote_cluster,
    'SSHCluster': ssh_cluster,
    'AutoScalingCluster': autoscaling_cluster,
    'run_local': via_run_local,
    'run_cluster': via_run_cluster,
    'run_cluster-autoscaling': via_run_cluster_autoscaling,
    'run_ssh': via_run_ssh,
}


@mark.unit
@mark.parametrize('attempts', ['default', 3])
@mark.parametrize('entry', sorted(ENTRY_POINTS))
def test_default_retries_follow_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                              entry: str, attempts: Any) -> None:
    """Without `max_retries`, each entry point allows `server.attempts` attempts (one by default)."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    expected = 1
    if attempts != 'default':
        monkeypatch.setitem(config.server, 'attempts', attempts)
        expected = attempts
    found = ENTRY_POINTS[entry](monkeypatch, tmp_path)
    assert found and found == [expected] * len(found)


@mark.unit
@mark.parametrize('entry', ['LocalCluster', 'ServerThread'])
def test_explicit_max_retries_wins(monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """An explicit `max_retries` is used as given, whatever the configuration (guard)."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    monkeypatch.setitem(config.server, 'attempts', 3)
    if entry == 'LocalCluster':
        thread = LocalCluster(source=['echo 1'], port=free_port(), max_retries=1)
    else:
        thread = ServerThread(address=loopback(), auth='test', max_retries=1)
    assert server_attempts(thread) == 2


@mark.unit
def test_in_memory_default_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """An in-memory local cluster built with the defaults asks for no retry, so it does not warn of one."""
    caplog.set_level(logging.WARNING, logger='hypershell')
    LocalCluster(source=['echo 1'], port=free_port(), in_memory=True)
    assert 'Retries disabled when database disabled' not in caplog.text


READ_ATTEMPTS: str = """\
import json
import hypershell.server
hypershell.server.DATABASE_ENABLED = True
from hypershell.server import ServerThread
from hypershell.cluster import LocalCluster
import socket
def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
print(json.dumps([ServerThread(address=('127.0.0.1', free_port()), auth='test').scheduler.machine.attempts,
                  LocalCluster(source=['echo 1'], port=free_port()).server.scheduler.machine.attempts]))
"""


@mark.integration
def test_default_retries_from_environment(temp_site: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`HYPERSHELL_SERVER_ATTEMPTS` reaches the library defaults, as it reaches `hs server` and `hs cluster`."""
    monkeypatch.setenv('HYPERSHELL_SERVER_ATTEMPTS', '3')
    rc, stdout, stderr = main([sys.executable, '-c', READ_ATTEMPTS])
    assert rc == exit_status.success, stderr
    assert stdout.splitlines()[-1] == '[3, 3]'
