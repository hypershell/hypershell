# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A queue server near its open-file limit keeps its own files working and stays up."""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Optional

# Standard libs
import sys
import time
import errno
import socket
import sqlite3
import secrets
import threading
from pathlib import Path
from subprocess import Popen, STDOUT, TimeoutExpired
from multiprocessing.connection import Listener

# External libs
from cmdkit.app import exit_status
from pytest import fixture, mark, raises

# Internal libs
import hypershell.core.queue
from hypershell.core.queue import QueueServer, QueueClient, QueueConfig
from hypershell.core.tls import TLSConfig, ensure_default_materials, clear_process_context
from tests import create_taskfile


AUTH = 'queue-test-key-0123456789abcdef'
WAIT = 2.0
DEADLINE = 60

# Open-file limit (soft and hard) given to the server process in the subprocess test
LIMIT = 64


@fixture(scope='module')
def tls_materials(tmp_path_factory) -> tuple[str, str]:
    """Generate a self-signed cert+key once per test module."""
    directory = str(tmp_path_factory.mktemp('tls'))
    return ensure_default_materials(directory, hostnames=['localhost', '127.0.0.1'])


@fixture(params=['no-tls', 'tls'])
def tls(request, tls_materials) -> Iterator[Optional[TLSConfig]]:
    """Run once with TLS disabled and once with TLS enabled; the process slot is cleared after."""
    if request.param == 'no-tls':
        yield None
    else:
        cert, key = tls_materials
        yield TLSConfig(enabled=True, cert=cert, key=key, cafile=cert)
    clear_process_context()


def make_config(port: int = 0, tls: Optional[TLSConfig] = None) -> QueueConfig:
    """Queue configuration on loopback (port 0 binds an ephemeral port)."""
    return QueueConfig(host='127.0.0.1', port=port, auth=AUTH, size=2, tls=tls)


def connect(port: int, tls: Optional[TLSConfig] = None) -> QueueClient:
    """Connect a client to a running server on loopback."""
    client = QueueClient(make_config(port, tls))
    client.connect()
    return client


@mark.unit
def test_connections_past_the_reserve_are_refused(tls: Optional[TLSConfig], monkeypatch) -> None:
    """Once the open-file limit leaves no room past the reserve, new connections are closed at once and
    admitted clients keep working."""
    limit = {'value': None}
    monkeypatch.setattr(hypershell.core.queue, '_open_file_limit', lambda: limit['value'])
    with QueueServer(make_config(tls=tls)) as server:
        port = server.address[1]
        admitted = connect(port, tls)
        server.scheduled.put(b'before', timeout=WAIT)
        assert admitted.scheduled.get(timeout=WAIT) == b'before'
        limit['value'] = 0
        for _ in range(3):
            with raises((EOFError, OSError)):
                connect(port, tls)
        server.scheduled.put(b'after', timeout=WAIT)
        assert admitted.scheduled.get(timeout=WAIT) == b'after'
        admitted.completed.put(b'result', timeout=WAIT)
        assert server.completed.get(timeout=WAIT) == b'result'


@mark.unit
@mark.parametrize('code', [errno.EMFILE, errno.ENFILE], ids=['EMFILE', 'ENFILE'])
def test_accept_backs_off_when_out_of_descriptors(code: int, monkeypatch) -> None:
    """An accept that fails for want of descriptors is retried after a pause, not in a tight loop."""
    calls = []

    def accept(self) -> None:
        calls.append(time.monotonic())
        raise OSError(code, 'no descriptors')

    monkeypatch.setattr(Listener, 'accept', accept)
    with QueueServer(make_config()) as server:
        with socket.create_connection(server.address, timeout=WAIT):
            # The pending connection keeps the listening socket readable for the whole window
            time.sleep(1.0)
            window = list(calls)
    assert 1 <= len(window) <= 10, f'{len(window)} accept attempts in 1 s'


def server_command(taskfile: Path, port: int, key: str) -> List[str]:
    """Command line for ``hs server`` on loopback without TLS."""
    return ['hs', 'server', str(taskfile), '-H', '127.0.0.1', '-p', str(port), '-k', key, '--no-tls']


def client_command(port: int, key: str) -> List[str]:
    """Command line for ``hs client`` on loopback without TLS."""
    return ['hs', 'client', '-H', '127.0.0.1', '-p', str(port), '-k', key, '--no-tls', '-N', '2']


def limit_open_files() -> None:
    """Lower both open-file limits in a child before it runs, so nothing can raise them again."""
    import resource
    resource.setrlimit(resource.RLIMIT_NOFILE, (LIMIT, LIMIT))


def count(db_path: Path, query: str) -> int:
    """Run a counting query against the task database."""
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=WAIT) as db:
        return db.execute(query).fetchone()[0]


def wait_for(predicate, timeout: float = DEADLINE) -> bool:
    """Poll `predicate` until it holds or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except sqlite3.Error:
            pass
        time.sleep(0.1)
    return False


def hold(port: int) -> Optional[socket.socket]:
    """A raw connection the server admitted (it sends its challenge first), else None."""
    sock = socket.create_connection(('127.0.0.1', port), timeout=WAIT)
    try:
        data = sock.recv(64)
    except OSError:  # Timed out (never accepted) or reset
        data = b''
    if data:
        return sock
    sock.close()
    return None


def finish(procs: List[Popen], timeout: float) -> List[Optional[int]]:
    """Wait for every process in `procs` within one shared `timeout`; kill any still running (status None)."""
    deadline = time.monotonic() + timeout
    status = []
    for proc in procs:
        try:
            status.append(proc.wait(timeout=max(0.0, deadline - time.monotonic())))
        except TimeoutExpired:
            proc.kill()
            proc.wait()
            status.append(None)
    return status


@mark.integration
@mark.skipif(sys.platform == 'win32', reason='open-file limits are POSIX')
def test_server_keeps_its_files_at_the_open_file_limit(temp_site: Path) -> None:
    """At the open-file limit the server refuses new clients, keeps its database and log, and finishes."""
    site = Path(str(temp_site))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    key = secrets.token_hex(16)
    go = site / 'GO'
    # The gated tasks occupy every admitted executor (two clients, two each) until the test opens the gate
    quick, gated = 20, 4
    gate = f'for i in $(seq 1200); do test -f {go} && exit 0; sleep 0.05; done; exit 1'
    taskfile = create_taskfile(site, ['true'] * quick + [gate] * gated)
    db_path = site / 'local.db'

    def start(command: List[str], name: str, **options) -> Popen:
        with open(site / f'{name}.log', mode='w') as stream:
            return Popen(command, cwd=site, stdout=stream, stderr=STDOUT, **options)

    server = start(server_command(taskfile, port, key), 'server', preexec_fn=limit_open_files)
    admitted, refused, held = [], [], []
    try:
        assert wait_for(lambda: server.poll() is not None or count(db_path, 'SELECT COUNT(*) FROM task') > 0)
        admitted = [start(client_command(port, key), f'admitted-{n}') for n in range(2)]
        # Every admitted client has opened all of its connections once each has completed a task
        assert wait_for(lambda: count(db_path, 'SELECT COUNT(DISTINCT client_id) FROM task '
                                               'WHERE completion_time IS NOT NULL') == 2)
        # Then the server goes quiet: every task is confirmed and only the gated ones are unfinished, so it
        # neither opens nor closes files of its own while the connections are filled
        assert wait_for(lambda: count(db_path, 'SELECT COUNT(*) FROM task WHERE client_id IS NOT NULL')
                        == quick + gated and count(db_path, 'SELECT COUNT(*) FROM task '
                                                            'WHERE completion_time IS NOT NULL') == quick)
        time.sleep(0.5)
        # Fill the connection share of the descriptors with idle peers until the server refuses one
        for _ in range(LIMIT):
            sock = hold(port)
            if sock is None:
                break
            held.append(sock)
        refused = [start(client_command(port, key), f'refused-{n}') for n in range(6)]
        refused_status = finish(refused, DEADLINE)
        go.touch()
        server_status, = finish([server], DEADLINE)
        admitted_status = finish(admitted, DEADLINE)
    finally:
        for sock in held:
            sock.close()
        finish([server, *admitted, *refused], 0)
    output = (site / 'server.log').read_text()
    assert server_status == exit_status.success, output[-4000:]
    for text in ('Too many open files', 'unable to open database file', 'Uncaught exception', 'CRITICAL'):
        assert text not in output, output[-4000:]
    warnings = [line for line in output.splitlines()
                if 'WARNING' in line and 'Refusing new queue connections' in line]
    assert len(warnings) == 1, output[-4000:]
    assert str(LIMIT) in warnings[0]
    assert admitted_status == [exit_status.success] * 2
    assert all(status not in (None, exit_status.success) for status in refused_status), refused_status
    assert count(db_path, 'SELECT COUNT(*) FROM task WHERE completion_time IS NOT NULL '
                          'AND exit_status = 0') == quick + gated
