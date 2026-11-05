# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Run a relay: one site's link to an upstream server.

A relay is one long-running process at a site. Upstream it is an ordinary client of the upstream
server; at the site it runs a complete server, scheduling from its own database, for clients it
launches there. Its database is always a private SQLite file, whatever database is configured
(see `hypershell.core.config.relay_database_section`): the configuration swaps the database section
when the process starts, before the database engine is built, so a process runs at most one relay.

Example:
    >>> from hypershell.relay import RelayThread
    >>> relay = RelayThread.new(address=('0.0.0.0', 50002))
    >>> relay.join()
"""


# Type annotations
from __future__ import annotations
from typing import List, Tuple, IO, Optional, Dict, Final
from types import TracebackType

# Standard libs
import os
import sys
import errno
import secrets

# External libs
from cmdkit.app import Application
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import Namespace

# Internal libs
from hypershell.core.config import config, RELAY_PROCESS, RELAY_MARKER, DEFAULT_RELAY_PORT, relay_database_section
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.core.logging import Logger
from hypershell.core.thread import Thread
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.data import ensuredb
from hypershell.data.core import engine
from hypershell.relay.store import ensure_tables
from hypershell.server import ServerThread, DEFAULT_EVICT

try:
    import fcntl
except ImportError:  # pragma: no cover (not available on Windows)
    fcntl = None

# Public interface
__all__ = ['RelayThread', 'RelayApp', 'DEFAULT_RELAY_BIND', 'DEFAULT_RELAY_PORT', 'DEFAULT_RELAY_POLL',
           'lock_database', 'RelayDatabaseLocked']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_RELAY_BIND: Final[str] = config.relay.bind
"""Default bind address of the relay-local server."""

DEFAULT_RELAY_POLL: Final[int] = config.relay.poll
"""Default polling interval of the relay-local scheduler in seconds."""

DEFAULT_RELAY_BUNDLESIZE: Final[int] = config.relay.bundlesize
"""Default bundle size of the relay-local server."""

DEFAULT_RELAY_BUNDLEWAIT: Final[int] = config.relay.bundlewait
"""Default seconds the relay waits before returning results regardless of size."""

STOP_CHECK_INTERVAL: Final[float] = 1.0
"""Seconds between the relay's checks on whether the relay-local server should stop."""

REEXEC_MARKER: Final[str] ='_HYPERSHELL_RELAY_REEXEC'
"""Private environment variable set on a relay re-executed to bind its database."""

REEXEC_CODE: Final[str] = 'import sys; from hypershell import main; sys.exit(main())'
"""Program a relay re-executes with; it reads the relay arguments from the command line."""


class RelayDatabaseLocked(RuntimeError):
    """Another relay holds the relay database."""


def lock_database(path: str) -> Optional[IO]:
    """Hold an exclusive lock on the relay database's sidecar lock file; return the open lock file.

    The lock is held for as long as the returned file stays open (the life of the process). Raises
    `RelayDatabaseLocked` if another relay holds it. On a filesystem with no advisory locking the
    relay goes on without the lock, with a warning.
    """
    if fcntl is None:
        log.warning(f'Advisory locking unavailable - not guarding relay database ({path})')
        return None
    stream = open(f'{path}.lock', mode='a')
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        stream.close()
        raise RelayDatabaseLocked(f'Relay database in use by another relay ({path})') from error
    except OSError as error:
        if error.errno in (errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOTSUP):
            log.warning(f'Advisory locking unsupported here - not guarding relay database ({path})')
            return stream
        stream.close()
        raise
    return stream


class RelayThread(Thread):
    """
    Run a relay within a dedicated thread.

    The relay-local server is a stock server in forever mode on the relay database, with local
    retries off: the upstream server alone retries tasks, so a failure at the site is a completed
    row like any other. Its clients authenticate with a key minted for this run only.

    Args:
        address (tuple, optional):
            Bind address and port of the relay-local server.
            See :const:`DEFAULT_RELAY_BIND` and :const:`DEFAULT_RELAY_PORT`.

        bundlesize (int, optional):
            Bundle size of the relay-local server. See :const:`DEFAULT_RELAY_BUNDLESIZE`.

        bundlewait (int, optional):
            Seconds to wait before flushing tasks. See :const:`DEFAULT_RELAY_BUNDLEWAIT`.

        poll (int, optional):
            Maximum polling interval of the relay-local scheduler in seconds.
            See :const:`DEFAULT_RELAY_POLL`.

        evict_after (int, optional):
            Period in seconds before the relay-local server evicts a silent site client.

        tls (TLSConfig, optional):
            TLS configuration of the relay-local server (site clients connect with it).
    """

    server: ServerThread
    site_key: str

    def __init__(self: RelayThread,
                 address: Tuple[str, int] = (DEFAULT_RELAY_BIND, DEFAULT_RELAY_PORT),
                 bundlesize: int = DEFAULT_RELAY_BUNDLESIZE,
                 bundlewait: int = DEFAULT_RELAY_BUNDLEWAIT,
                 poll: int = DEFAULT_RELAY_POLL,
                 evict_after: int = DEFAULT_EVICT,
                 tls: Optional[TLSConfig] = None) -> None:
        """Initialize the relay-local server."""
        self.site_key = secrets.token_hex(64)
        self.server = ServerThread(source=None, bundlesize=bundlesize, bundlewait=bundlewait,
                                   in_memory=False, no_confirm=False, poll=poll, forever_mode=True,
                                   address=address, auth=self.site_key, max_retries=0,
                                   evict_after=evict_after, tls=tls)
        super().__init__(name='hypershell-relay')

    def run_with_exceptions(self: RelayThread) -> None:
        """Start the relay-local server and wait for it to finish.

        A drain signal (USR1/USR2) ends the relay-local scheduler. The server then waits on its
        clients, but a server that never had a client waits for its first one, so once scheduling
        is over the relay stops the server itself.
        """
        log.debug('Started')
        self.server.start()
        log.info(f'Relay-local server ready on {self.server.queue.config.host}:{self.server.queue.config.port}')
        while self.server.is_alive():
            self.server.join(timeout=STOP_CHECK_INTERVAL)
            if self.server.is_alive() and self.scheduling_over:
                log.debug('Scheduling over - stopping relay-local server')
                self.server.stop(wait=True)
        self.server.join()
        log.debug('Done')

    @property
    def scheduling_over(self: RelayThread) -> bool:
        """The relay-local scheduler started and has finished."""
        scheduler = self.server.scheduler
        return scheduler is not None and scheduler.ident is not None and not scheduler.is_alive()

    def stop(self: RelayThread, wait: bool = False, timeout: int = None) -> None:
        """Stop the relay-local server, then this thread."""
        log.warning('Stopping')
        self.server.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


APP_NAME = 'hs relay'
APP_USAGE = f"""\
Usage:
  hs relay [-h] [--bind ADDR] [--port NUM] [--database PATH]

  Run relay for one site.\
"""

APP_HELP = f"""\
{APP_USAGE}

  A relay connects a site to an upstream server. Upstream it is an ordinary client; at
  the site it runs its own server, scheduling the tasks it receives to clients it launches
  there, and returns their results upstream.

  The relay database is always a private SQLite file, whatever database is configured,
  and the configured database is never opened. One relay runs per process, and a second
  relay refuses to start on the same relay database.

Options:
      --bind          ADDR  Bind address of relay-local server (default: {DEFAULT_RELAY_BIND}).
      --port          NUM   Port of relay-local server (default: {DEFAULT_RELAY_PORT}).
      --database      PATH  Path to relay database (default: <lib>/relay/<host>.db).
  -h, --help                Show this message and exit.\
"""


class RelayApp(Application):
    """Run relay for one site."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    # A relay runs from configuration alone.
    ALLOW_NOARGS = True

    bind: str = DEFAULT_RELAY_BIND
    interface.add_argument('--bind', default=bind)

    port: int = DEFAULT_RELAY_PORT
    interface.add_argument('--port', type=int, default=port)

    database: Optional[str] = None
    interface.add_argument('--database', default=database)

    # Refused: orphan recovery upstream rests on confirmation.
    no_confirm: bool = False
    interface.add_argument('--no-confirm', action='store_true')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    argv: List[str] = []
    lock: Optional[IO] = None

    @classmethod
    def main(cls: type[RelayApp], cmdline: List[str] = None, shared: Namespace = None) -> int:
        """Keep the relay arguments, which a re-executed relay is given again."""
        cls.argv = list(cmdline or [])
        return super().main(cmdline, shared)

    def run(self: RelayApp) -> None:
        """Run relay."""
        self.check_args()
        self.bind_database()
        path = config.database.file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = lock_database(path)
        log.info(f'Relay database {path}')
        ensuredb()
        ensure_tables(engine)
        thread = RelayThread.new(address=(self.bind, self.port),
                                 bundlesize=config.relay.bundlesize,
                                 bundlewait=config.relay.bundlewait,
                                 poll=config.relay.poll,
                                 tls=tls_from_namespace(config.server.tls))
        try:
            thread.join()
        except Exception:
            thread.stop()
            raise

    def check_args(self: RelayApp) -> None:
        """Fail invalid arguments."""
        if self.no_confirm:
            raise ArgumentError('A relay cannot run with --no-confirm (upstream failover rests on confirmation)')

    def bind_database(self: RelayApp) -> None:
        """Re-execute the process if its database section is not the relay database this run names.

        The database engine is built when the package is imported, so a relay started after import
        (`main(['relay', ...])`) or naming another relay database (`--database`) starts again as a
        new program in the same process, marked as a relay.
        """
        reexecuted = os.environ.pop(REEXEC_MARKER, None)
        env: Dict[str, str] = {}
        if self.database:
            wanted = relay_database_section(Namespace({'relay': {'database': self.database}})).file
            if wanted != config.database.get('file'):
                env['HYPERSHELL_RELAY_DATABASE'] = wanted
        if RELAY_PROCESS and not env:
            return
        if reexecuted:
            raise RuntimeError('Relay database could not be bound after re-executing')
        log.debug('Re-executing to bind relay database')
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        os.execve(sys.executable, [sys.executable, '-c', REEXEC_CODE, 'relay', *self.argv],
                  {**os.environ, **env, RELAY_MARKER: '1', REEXEC_MARKER: '1'})

    def __enter__(self: RelayApp) -> RelayApp:
        """Resource management."""
        return self

    def __exit__(self: RelayApp,
                 exc_type: Optional[type],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Release the relay database lock."""
        if self.lock is not None:
            self.lock.close()
