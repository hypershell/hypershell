# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
`hs server` start-up: `--initdb` initializes the database, `--no-db` leaves it alone, and
without either flag the server checks the database as before.

Unit tests run `ServerApp` in-process with the database steps and the serving entry points
replaced by recorders, so they need no database server and start no listener. Integration
tests launch the real `hs server` and `hs client` with the key in the environment. Those that
need a PostgreSQL server are skipped when HS_TEST_POSTGRES is unset:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE=community        # or: apache, or empty for a plain PostgreSQL server

As in tests/test_timescale.py, HS_TEST_TIMESCALE names the edition the server must report; the
`timescale`-provider cases run only with it set, and the `postgres`-provider cases need only a
PostgreSQL server.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, List, Optional, Tuple

# Standard libs
import os
import socket
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from signal import SIGINT
from subprocess import run, Popen, DEVNULL, TimeoutExpired, CompletedProcess

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from cmdkit.config import Namespace

# Internal libs
import hypershell.data
import hypershell.server
import hypershell.cluster.topology
from hypershell.core.config import AUTH_ENV
from hypershell.data import timescale
from hypershell.data.timescale import TimescaleError
from hypershell.server import ServerApp
from tests import create_taskfile
from tests.conftest import free_port


KEY: Final[str] = secrets.token_hex(32)
"""The queue key every server and client here reads from the environment."""

DEADLINE: Final[float] = 60
"""Seconds any one launched process may take to reach its next step."""


# --------------------------------------------------------------------------------------------------
# Unit: which database step each launch reaches


class Recorder:
    """Records, in order, the database steps and the serving entry point a launch reaches."""

    calls: List[str]
    entry: Optional[str]

    def __init__(self) -> None:
        self.calls = []
        self.entry = None

    def step(self, name: str):
        """A stand-in for one database step."""
        def record(*args, **kwargs) -> None:
            self.calls.append(name)
        return record

    def serve(self, name: str):
        """A stand-in for one serving entry point."""
        def record(*args, **kwargs) -> None:
            self.calls.append('serve')
            self.entry = name
        return record


PROVIDERS: Final[Dict[str, Tuple[str, bool]]] = {
    'postgres': ('postgres', False),
    'timescale': ('postgres', True),
    'sqlite': ('sqlite', False),
}
"""Each provider's dialect and TimescaleDB setting."""


@fixture
def recorder(monkeypatch, tmp_path: Path) -> Recorder:
    """Replace the database steps, the serving entry points, and the key lookup with recorders."""
    rec = Recorder()
    for name in ('checkdb', 'upgradedb'):
        monkeypatch.setattr(hypershell.data, name, rec.step(name))
    for name in ('prepare', 'converge'):
        monkeypatch.setattr(timescale, name, rec.step(name))
    for name in ('serve_from', 'serve_forever'):
        monkeypatch.setattr(hypershell.server, name, rec.serve(name))
    monkeypatch.setattr(hypershell.cluster.topology, 'serve_with_sites', rec.serve('serve_with_sites'))
    monkeypatch.setattr(hypershell.server, 'resolve_auth', lambda *section, **options: KEY)
    use_database_file(monkeypatch, str(tmp_path / 'unit.db'))
    return rec


def use_provider(monkeypatch, provider: str) -> None:
    """Set the provider the database module acts on."""
    dialect, with_timescale = PROVIDERS[provider]
    monkeypatch.setattr(hypershell.data, 'DATABASE_DIALECT', dialect)
    monkeypatch.setattr(hypershell.data, 'TIMESCALE', with_timescale)


def use_database_file(monkeypatch, path: str) -> None:
    """Set the SQLite file the database module's configuration check reads (empty for none)."""
    monkeypatch.setattr(hypershell.data, 'config', Namespace({'database': {'provider': 'sqlite', 'file': path}}))


MODES: Final[Dict[str, Tuple[List[str], str]]] = {
    'file': (['{taskfile}'], 'serve_from'),
    'stdin': (['-'], 'serve_from'),
    'forever': (['--forever'], 'serve_forever'),
    'restart': (['--restart'], 'serve_from'),
    'autoscaling': (['--autoscaling', 'fixed'], 'serve_with_sites'),
}
"""Each scheduling mode's arguments and the entry point it serves through."""

FLAGS: Final[Dict[str, List[str]]] = {
    'none': [],
    'initdb': ['--initdb'],
    'no-db': ['--no-db'],
}
"""The database flags."""

EXPECTED: Final[Dict[Tuple[str, str], List[str]]] = {
    ('postgres', 'none'): ['checkdb', 'serve'],
    ('postgres', 'initdb'): ['upgradedb', 'serve'],
    ('postgres', 'no-db'): ['serve'],
    ('timescale', 'none'): ['checkdb', 'serve'],
    ('timescale', 'initdb'): ['prepare', 'upgradedb', 'converge', 'serve'],
    ('timescale', 'no-db'): ['serve'],
    ('sqlite', 'none'): ['upgradedb', 'serve'],
    ('sqlite', 'initdb'): ['upgradedb', 'serve'],
    ('sqlite', 'no-db'): ['serve'],
}
"""The steps a launch reaches, by provider and flag, in every mode but `--autoscaling --no-db`."""


def server_argv(tmp_path: Path, mode: str, flag: str) -> List[str]:
    """Command line for one mode and flag (TLS off, so nothing is written to the site)."""
    args, _ = MODES[mode]
    taskfile = create_taskfile(tmp_path, ['echo one'])
    return [arg.format(taskfile=taskfile) for arg in args] + FLAGS[flag] + ['--no-tls']


@mark.unit
@mark.parametrize('flag', list(FLAGS))
@mark.parametrize('mode', list(MODES))
@mark.parametrize('provider', list(PROVIDERS))
def test_database_step(recorder: Recorder, monkeypatch, tmp_path: Path, provider: str, mode: str, flag: str) -> None:
    """A launch initializes the database with --initdb, never opens it with --no-db, and checks it otherwise."""
    use_provider(monkeypatch, provider)
    status = ServerApp.main(server_argv(tmp_path, mode, flag))
    if mode == 'autoscaling' and flag == 'no-db':
        assert status == exit_status.bad_argument
        assert recorder.calls == []
        return
    assert status == exit_status.success
    assert recorder.calls == EXPECTED[provider, flag]
    assert recorder.entry == MODES[mode][1]


@mark.unit
def test_timescale_refusal_stops_launch(recorder: Recorder, monkeypatch, tmp_path: Path, caplog) -> None:
    """A TimescaleDB refusal stops a --initdb launch with the runtime-error status, before the schema step."""
    message = 'Cannot create the TimescaleDB extension in database \'unit\': permission denied'

    def refuse(*args, **kwargs) -> None:
        recorder.calls.append('prepare')
        raise TimescaleError(message)

    use_provider(monkeypatch, 'timescale')
    monkeypatch.setattr(timescale, 'prepare', refuse)
    status = ServerApp.main(server_argv(tmp_path, 'file', 'initdb'))
    assert status == exit_status.runtime_error
    assert recorder.calls == ['prepare']
    assert [(record.levelname, record.getMessage()) for record in caplog.records
            if record.levelname == 'CRITICAL'] == [('CRITICAL', f'TimescaleError: {message}')]


@mark.unit
@mark.parametrize('flag, status, calls', [
    ('none', exit_status.bad_config, []),
    ('no-db', exit_status.success, ['serve']),
], ids=['none', 'no-db'])
def test_no_database_configured(recorder: Recorder, monkeypatch, tmp_path: Path,
                                flag: str, status: int, calls: List[str]) -> None:
    """With no SQLite file configured, --no-db starts and a plain launch is refused as before."""
    use_provider(monkeypatch, 'sqlite')
    use_database_file(monkeypatch, '')
    assert ServerApp.main(server_argv(tmp_path, 'file', flag)) == status
    assert recorder.calls == calls


# --------------------------------------------------------------------------------------------------
# Integration: the real command line


@dataclass
class Served:
    """What one launch of `hs server` left: its status, its log, and its client's result."""

    status: int
    log: str
    client: Optional[CompletedProcess]


def accepts(port: int) -> bool:
    """True if something accepts connections on `port` on the loopback."""
    try:
        socket.create_connection(('localhost', port), timeout=1).close()
        return True
    except OSError:
        return False


def start_server(site: Path, port: int, *args: str) -> Popen:
    """Start `hs server` on `port`, logging to `server.log` in `site`; return once it listens or exits."""
    with open(site / 'server.log', mode='w') as stream:
        server = Popen(['hs', 'server', '-p', str(port), *args], cwd=site, env=os.environ,
                       stdin=DEVNULL, stdout=DEVNULL, stderr=stream)
    deadline = time.monotonic() + DEADLINE
    while server.poll() is None and not accepts(port):
        if time.monotonic() > deadline:
            stop_server(server, wait=0)
            pytest.fail('Server did not listen\n' + (site / 'server.log').read_text()[-4000:])
        time.sleep(0.1)
    return server


def stop_server(server: Popen, wait: float = DEADLINE) -> int:
    """Give `server` `wait` seconds to exit, then interrupt it, then kill it; return its status."""
    try:
        return server.wait(timeout=wait)
    except TimeoutExpired:
        server.send_signal(SIGINT)
    try:
        return server.wait(timeout=DEADLINE)
    except TimeoutExpired:
        server.kill()
        return server.wait()


def run_client(site: Path, port: int) -> CompletedProcess:
    """Run one `hs client` against the server on `port` until it is done or idle."""
    return run(['hs', 'client', '-p', str(port), '-T', '10'], cwd=site, env=os.environ,
               stdin=DEVNULL, capture_output=True, text=True, timeout=DEADLINE)


def serve(site: Path, *args: str, client: bool = True, forever: bool = False, before_client=None) -> Served:
    """Launch `hs server` with `args` and, if it listens, one client; always stop the server.

    A server given a task file exits on its own once its client is done. A `forever` server is
    interrupted after its client returns. `before_client` runs (with the port) once the server
    listens, before the client starts.
    """
    port = free_port()
    server = start_server(site, port, *args)
    result = None
    try:
        if server.poll() is None and client:
            if before_client is not None:
                before_client(port)
            result = run_client(site, port)
    finally:
        status = stop_server(server, wait=0 if forever else DEADLINE)
    return Served(status=status, log=(site / 'server.log').read_text(), client=result)


@mark.integration
def test_no_db_leaves_sqlite_file_alone(temp_site, monkeypatch) -> None:
    """With --no-db the server and its client run every task and never create the configured SQLite file."""
    site = Path(str(temp_site))
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    monkeypatch.setenv(AUTH_ENV, KEY)
    taskfile = create_taskfile(site, ['echo first', 'echo second'])
    served = serve(site, '--no-db', str(taskfile))
    assert served.status == exit_status.success, served.log
    assert served.client is not None, served.log
    assert served.client.returncode == exit_status.success, served.client.stderr
    assert sorted(served.client.stdout.split()) == ['first', 'second']
    assert not database.exists()
