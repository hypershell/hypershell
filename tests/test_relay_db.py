# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay database: always a private SQLite file, and the configured primary never touched.

The configuration swaps the database section in a relay process before the database engine is
built. The integration tests run each relay as its own process under a wrapper that records every
database connection the process opens (a SQLAlchemy ``do_connect`` listener installed before
HyperShell is imported) and reports, at exit, what was opened and whether the PostgreSQL driver was
ever imported. Each relay is started, seen ready, drained with SIGUSR1, and must exit cleanly. A
queue server in the test process stands in for the upstream server the relays connect to.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional

# Standard libs
import os
import sys
import json
import time
import signal
import socket
import sqlite3
import subprocess
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from cmdkit.config import Namespace

# Internal libs
from hypershell.core.config import (relay_database_section, default_relay_database, _relay_requested,
                                    RELAY_MARKER, RELAY_PRAGMAS)
from hypershell.core.queue import QueueServer, QueueConfig
from hypershell.data.model import Entity
from hypershell.relay import RelayThread
from hypershell.relay.store import RelayBase
from tests import main


DEADLINE = 60.0  # Seconds for a relay to come up, and again for it to exit once drained.
POLL = 0.05      # Seconds between looks at a relay's log.

READY = 'Relay-local server ready'
UPSTREAM_AUTH = 'relay-db-upstream-key-0123456789'
RELAY_TABLES = {'relay_task', 'relay_result'}

# Runs `hs` with every database connection recorded; the report is written at exit. Everything sits
# under the main guard, since a spawned helper process (the queue manager) imports this module again.
RECORDER = '''\
if __name__ == '__main__':
    import os, sys, json, atexit
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    opened = []

    @event.listens_for(Engine, 'do_connect')
    def record(dialect, connection_record, cargs, cparams):
        opened.append({'dialect': dialect.name, 'database': str(cparams.get('database', cargs[0] if cargs else ''))})

    def report():
        from hypershell.data.core import engine
        from hypershell.data.model import Task
        with open(os.environ['RELAY_TEST_REPORT'], mode='w') as stream:
            json.dump({'opened': opened,
                       'psycopg': any(name == 'psycopg' or name.startswith('psycopg.') for name in sys.modules),
                       'schema': Task.__table__.schema,
                       'url': str(engine.url)}, stream)

    atexit.register(report)

    from hypershell import main
    sys.exit(main(sys.argv[1:]))
'''


def closed_port() -> int:
    """A port number nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


free_port = closed_port  # Free now; the relay binds it next.


class Relay:
    """A relay process in its own process group, with its log and report files."""

    def __init__(self, argv: List[str], env: Dict[str, str], log: Path, report: Path) -> None:
        self.log = log
        self.report_path = report
        with open(log, mode='w') as stream:
            self.proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=stream,
                                         start_new_session=True)

    def text(self) -> str:
        return self.log.read_text()

    def wait_ready(self) -> None:
        """Wait until the relay-local server is up; fail if the relay exits or the deadline passes."""
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline:
            if READY in self.text():
                return
            if self.proc.poll() is not None:
                pytest.fail(f'Relay exited ({self.proc.returncode}) before it was ready:\n{self.text()}')
            time.sleep(POLL)
        pytest.fail(f'Relay not ready within {DEADLINE} seconds:\n{self.text()}')

    def wait_exit(self) -> int:
        """Wait for the relay to exit; return its status."""
        try:
            return self.proc.wait(DEADLINE)
        except subprocess.TimeoutExpired:
            pytest.fail(f'Relay did not exit within {DEADLINE} seconds:\n{self.text()}')

    def drain(self) -> int:
        """Send the drain signal and wait for the relay to exit; return its status."""
        self.proc.send_signal(signal.SIGUSR1)
        return self.wait_exit()

    def report(self) -> Dict[str, Any]:
        return json.loads(self.report_path.read_text())

    def kill(self) -> None:
        """Kill whatever is left of the relay's process group."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(DEADLINE)
        except subprocess.TimeoutExpired:
            pass


@fixture
def upstream() -> Iterator[QueueServer]:
    """A queue server on loopback (cleartext) standing in for the upstream server."""
    with QueueServer(QueueConfig(host='127.0.0.1', port=0, auth=UPSTREAM_AUTH, size=2)) as server:
        yield server


@fixture
def relays(tmp_path: Path, upstream: QueueServer) -> Iterator[Any]:
    """Start relays as separate processes under the recorder; kill any left at the end of the test."""
    started: List[Relay] = []
    site = tmp_path / 'site'
    site.mkdir()
    recorder = tmp_path / 'hs'  # Named `hs`, so `hs relay` on its command line is recognised.
    recorder.write_text(RECORDER)

    def start(*args: str, config: Optional[Path] = None, marker: bool = False,
              program: Optional[List[str]] = None, **env: str) -> Relay:
        name = f'relay-{len(started)}'
        environ = {**os.environ, 'HYPERSHELL_SITE': str(site), 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
                   'HYPERSHELL_CONFIG_FILE': str(config) if config else '',
                   'RELAY_TEST_REPORT': str(tmp_path / f'{name}.json'),
                   'HYPERSHELL_RELAY_UPSTREAM_HOST': '127.0.0.1',
                   'HYPERSHELL_RELAY_UPSTREAM_PORT': str(upstream.address[1]),
                   'HYPERSHELL_RELAY_UPSTREAM_AUTH': UPSTREAM_AUTH,
                   'HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED': 'false', **env}
        environ.pop('HYPERSHELL_DATABASE_FILE', None)
        if marker:
            environ[RELAY_MARKER] = '1'
        argv = program or [sys.executable, str(recorder), 'relay', '--bind', '127.0.0.1', '--port', str(free_port())]
        relay = Relay([*argv, *args], environ, tmp_path / f'{name}.log', tmp_path / f'{name}.json')
        started.append(relay)
        return relay

    start.site = site
    try:
        yield start
    finally:
        for relay in started:
            relay.kill()


def tables(path: Path) -> set:
    """Table names in a SQLite file."""
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def site_relay_database(site: Path) -> Path:
    """The one relay database under a site's lib directory."""
    found = list(site.glob('*/relay/*.db'))
    if len(found) != 1:
        pytest.fail(f'Expected one relay database under {site}, found {found}')
    return found[0]


def write_config(path: Path, database: Dict[str, Any]) -> Path:
    """Write a configuration file with the given database section."""
    lines = ['[database]'] + [f'{key} = {json.dumps(value)}' for key, value in database.items()]
    path.write_text('\n'.join(lines) + '\n')
    return path


# --- The database section builder ---------------------------------------------------------------


PRIMARIES = {
    'sqlite': {'provider': 'sqlite', 'file': '/somewhere/primary.db'},
    'sqlite-string': '/somewhere/primary.db',
    'postgres': {'provider': 'postgres', 'host': 'db.example', 'port': 5432, 'database': 'tasks',
                 'user': 'me', 'password': 'secret', 'schema': 'mine', 'sslmode': 'require'},
    'timescale': {'provider': 'timescale', 'database': 'tasks', 'schema': 'mine',
                  'timescale': {'chunk_interval': 1000}},
    'url': {'url': 'postgresql+psycopg://me:secret@db.example/tasks', 'echo': True},
}


@mark.unit
@mark.parametrize('primary', list(PRIMARIES))
def test_section_is_relay_sqlite_whatever_the_primary(primary: str) -> None:
    """The relay section names only the relay file, carrying nothing from the configured section."""
    section = relay_database_section(Namespace({'database': PRIMARIES[primary], 'relay': {'database': '<none>'}}))
    assert dict(section) == {'provider': 'sqlite', 'file': default_relay_database(), 'pragmas': RELAY_PRAGMAS}


@mark.unit
def test_section_takes_the_configured_relay_file() -> None:
    """A configured relay database is used, as an absolute path."""
    section = relay_database_section(Namespace({'database': PRIMARIES['postgres'],
                                                'relay': {'database': '~/relay/site.db'}}))
    assert section.file == os.path.expanduser('~/relay/site.db')
    assert section.provider == 'sqlite'


@mark.unit
def test_default_relay_database_is_host_scoped() -> None:
    """The default file is named for the short host name, under the site lib directory."""
    path = Path(default_relay_database())
    assert path.parent.name == 'relay'
    assert path.name == socket.gethostname().split('.', 1)[0] + '.db'


@mark.unit
@mark.parametrize('argv, expected', [
    (['/usr/bin/hs', 'relay'], True),
    (['hs', 'relay', '--port', '1'], True),
    (['hyper-shell', 'relay'], True),
    (['hs.exe', 'relay'], True),
    (['hs', 'server'], False),
    (['hs', 'list', 'relay'], False),
    (['hsx', 'relay'], False),
    (['hs'], False),
    ([], False),
])
def test_relay_requested_by_command_line(argv: List[str], expected: bool) -> None:
    """Only `hs relay` (or `hyper-shell relay`) on the command line marks a relay process."""
    assert _relay_requested(argv) is expected


@mark.unit
def test_relay_marker_is_read_once(monkeypatch) -> None:
    """The private marker marks a relay process and is removed, so no child inherits it."""
    monkeypatch.setenv(RELAY_MARKER, '1')
    assert _relay_requested(['python', '-c']) is True
    assert RELAY_MARKER not in os.environ
    assert _relay_requested(['python', '-c']) is False


@mark.unit
def test_relay_tables_are_not_entity_tables() -> None:
    """The relay tables are on their own metadata, so the schema-upgrade step never adds them to a primary."""
    assert set(RelayBase.metadata.tables) == RELAY_TABLES
    assert RELAY_TABLES.isdisjoint(Entity.metadata.tables)


@mark.unit
def test_relay_local_server_never_retries() -> None:
    """The relay-local server runs forever with one attempt per task: the upstream server owns retries."""
    relay = RelayThread(address=('127.0.0.1', free_port()))
    scheduler = relay.server.scheduler.machine
    assert scheduler.attempts == 1
    assert scheduler.forever_mode is True
    assert relay.server.no_confirm is False and relay.server.in_memory is False
    assert len(relay.site_key) == 128


# --- A relay process ---------------------------------------------------------------------------


@mark.integration
@mark.parametrize('provider', ['postgres', 'timescale'])
def test_relay_never_opens_postgres_primary(relays, tmp_path: Path, provider: str) -> None:
    """Over a PostgreSQL configuration, a relay opens only its own file and never imports the driver."""
    config = write_config(tmp_path / 'config.toml', {
        'provider': provider, 'host': '127.0.0.1', 'port': closed_port(), 'database': 'nowhere',
        'user': 'nobody', 'password': 'nothing', 'schema': 'relayfence', 'sslmode': 'require'})
    relay = relays(config=config, marker=True)
    relay.wait_ready()

    assert relay.drain() == exit_status.success

    path = site_relay_database(relays.site)
    report = relay.report()
    assert report['opened'], 'no connection recorded'
    assert {(item['dialect'], item['database']) for item in report['opened']} == {('sqlite', str(path))}
    assert report['psycopg'] is False
    assert report['schema'] is None
    assert report['url'] == f'sqlite:///{path}'
    assert RELAY_TABLES | {'task', 'client'} <= tables(path)


@mark.integration
def test_relay_never_creates_sqlite_primary(relays, tmp_path: Path) -> None:
    """Over a SQLite configuration, `hs relay` opens only its own file and the primary is never created."""
    primary = tmp_path / 'primary.db'
    config = write_config(tmp_path / 'config.toml', {'provider': 'sqlite', 'file': str(primary)})
    relay = relays(config=config, HYPERSHELL_DATABASE_FILE=str(primary))
    relay.wait_ready()

    assert relay.drain() == exit_status.success

    path = site_relay_database(relays.site)
    assert {(item['dialect'], item['database']) for item in relay.report()['opened']} == {('sqlite', str(path))}
    assert not primary.exists()
    assert not list(relays.site.glob('*/*.db')), 'a primary database was created in the site'


@mark.integration
def test_initdb_never_adds_relay_tables(temp_site: Path) -> None:
    """A primary initialised by `hs initdb` holds the HyperShell tables and no relay table."""
    rc, _, stderr = main(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success, stderr

    found = tables(Path(os.environ['HYPERSHELL_DATABASE_FILE']))
    assert {'task', 'client'} <= found
    assert RELAY_TABLES.isdisjoint(found)


@mark.integration
def test_second_relay_on_same_database_refused(relays) -> None:
    """A second relay on a relay database another relay holds exits with an error; the first runs on."""
    first = relays()
    first.wait_ready()

    second = relays()
    assert second.wait_exit() == exit_status.runtime_error
    assert 'in use by another relay' in second.text()
    assert READY not in second.text()

    assert first.proc.poll() is None
    assert first.drain() == exit_status.success


@mark.integration
def test_relay_started_after_import_reexecutes(relays) -> None:
    """`main(['relay', ...])` in a process that imported HyperShell as something else re-executes as a relay."""
    port = free_port()
    code = (f'import sys; from hypershell import main; '
            f'sys.exit(main(["relay", "--bind", "127.0.0.1", "--port", "{port}"]))')
    relay = relays(program=[sys.executable, '-c', code])
    relay.wait_ready()

    assert relay.drain() == exit_status.success
    assert 'Re-executing to bind relay database' in relay.text()
    path = site_relay_database(relays.site)
    assert f'Relay database {path}' in relay.text()
    assert RELAY_TABLES <= tables(path)


@mark.integration
def test_database_option_binds_that_file(relays, tmp_path: Path) -> None:
    """`--database PATH` makes the relay use that file, by re-executing once."""
    path = tmp_path / 'elsewhere' / 'site-a.db'
    relay = relays('--database', str(path))
    relay.wait_ready()

    assert relay.drain() == exit_status.success
    assert relay.text().count('Re-executing to bind relay database') == 1
    assert f'Relay database {path}' in relay.text()
    assert RELAY_TABLES <= tables(path)
    assert not list(relays.site.glob('*/relay/*.db'))


@mark.integration
def test_relay_refuses_no_confirm(relays) -> None:
    """`--no-confirm` is refused before anything starts."""
    relay = relays('--no-confirm')

    assert relay.wait_exit() == exit_status.bad_argument
    assert 'cannot run with --no-confirm' in relay.text()
    assert not list(relays.site.glob('*/relay/*.db'))
