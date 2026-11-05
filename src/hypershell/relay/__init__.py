# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Run a relay: one site's link to an upstream server.

A relay is one long-running process at a site. Upstream it is an ordinary client of the upstream
server, once for the zone it serves (see `hypershell.relay.upstream`); at the site it runs a complete
server, scheduling from its own database, for clients it launches there. Its database is always a private SQLite file, whatever database is configured
(see `hypershell.core.config.relay_database_section`): the configuration swaps the database section
when the process starts, before the database engine is built, so a process runs at most one relay.

Example:
    >>> from hypershell.relay import RelayThread
    >>> from hypershell.relay.upstream import upstream_config
    >>> relay = RelayThread.new(address=('0.0.0.0', 50002), upstream=upstream_config('upstream.example', 50001))
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
import functools

# External libs
from multiprocessing import AuthenticationError
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import Namespace

# Internal libs
from hypershell.core.config import (config, RELAY_PROCESS, RELAY_MARKER, DEFAULT_RELAY_PORT, DEFAULT_AUTH,
                                    PARAM_UNSET, relay_database_section)
from hypershell.core.exceptions import get_shared_exception_mapping, handle_exception, handle_disconnect
from hypershell.core.logging import Logger
from hypershell.core.queue import QueueClient, QueueConfig
from hypershell.core.thread import Thread
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.data import ensuredb
from hypershell.data.core import engine
from hypershell.data.model import zone_name, DEFAULT_ZONE_NAME
from hypershell.relay import store
from hypershell.relay.store import ensure_tables
from hypershell.relay.upstream import (UpstreamPullerThread, UpstreamHeartbeatThread, upstream_identity,
                                       upstream_config, DEFAULT_UPSTREAM_BUNDLESIZE, DEFAULT_UPSTREAM_HEARTRATE)
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

    Upstream the relay is one client for its zone, under an identity derived from this relay and the
    zone name. It connects before anything else starts, so a server it cannot use (a version
    mismatch, a wrong key) ends it before the site is touched. A puller requests work while the site
    has room for it and confirms it once it is stored, and a heartbeat keeps the identity registered.

    Args:
        address (tuple, optional):
            Bind address and port of the relay-local server.
            See :const:`DEFAULT_RELAY_BIND` and :const:`DEFAULT_RELAY_PORT`.

        bundlesize (int, optional):
            Bundle size of the relay-local server. See :const:`DEFAULT_RELAY_BUNDLESIZE`.

        bundlewait (int, optional):
            Seconds to wait before flushing tasks. See :const:`DEFAULT_RELAY_BUNDLEWAIT`.

        poll (int, optional):
            Maximum polling interval of the relay-local scheduler, and of the upstream puller while
            the site is full, in seconds. See :const:`DEFAULT_RELAY_POLL`.

        evict_after (int, optional):
            Period in seconds before the relay-local server evicts a silent site client.

        tls (TLSConfig, optional):
            TLS configuration of the relay-local server (site clients connect with it).

        upstream (QueueConfig, optional):
            Connection to the upstream server. By default, from `[relay.upstream]` when the relay starts.

        zone (str, optional):
            Name of the zone the relay serves (default zone if None).

        upstream_bundlesize (int, optional):
            Most tasks the relay asks for at once. See :const:`DEFAULT_UPSTREAM_BUNDLESIZE`.

        heartrate (int, optional):
            Seconds between upstream heartbeats. See :const:`DEFAULT_UPSTREAM_HEARTRATE`.
    """

    server: ServerThread
    site_key: str
    zone: Optional[str]
    identity: str
    upstream_config: Optional[QueueConfig]
    upstream: Optional[QueueClient] = None
    puller: Optional[UpstreamPullerThread] = None
    heartbeat: Optional[UpstreamHeartbeatThread] = None

    def __init__(self: RelayThread,
                 address: Tuple[str, int] = (DEFAULT_RELAY_BIND, DEFAULT_RELAY_PORT),
                 bundlesize: int = DEFAULT_RELAY_BUNDLESIZE,
                 bundlewait: int = DEFAULT_RELAY_BUNDLEWAIT,
                 poll: int = DEFAULT_RELAY_POLL,
                 evict_after: int = DEFAULT_EVICT,
                 tls: Optional[TLSConfig] = None,
                 upstream: Optional[QueueConfig] = None,
                 zone: Optional[str] = None,
                 upstream_bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE,
                 heartrate: int = DEFAULT_UPSTREAM_HEARTRATE) -> None:
        """Initialize the relay-local server and the upstream side."""
        self.site_key = secrets.token_hex(64)
        self.server = ServerThread(source=None, bundlesize=bundlesize, bundlewait=bundlewait,
                                   in_memory=False, no_confirm=False, poll=poll, forever_mode=True,
                                   address=address, auth=self.site_key, max_retries=0,
                                   evict_after=evict_after, tls=tls)
        self.bundlesize = bundlesize
        self.poll = poll
        self.zone = zone
        self.identity = upstream_identity(zone)
        self.upstream_config = upstream
        self.upstream_bundlesize = upstream_bundlesize
        self.heartrate = heartrate
        super().__init__(name='hypershell-relay')

    def run_with_exceptions(self: RelayThread) -> None:
        """Connect upstream, start the relay-local server, pull work and wait for the relay to finish.

        A drain signal (USR1/USR2) ends the relay-local scheduler and the puller. An upstream
        shutdown ends the puller, and the relay then waits for the tasks it holds to finish. The
        server waits on its clients, but a server that never had a client waits for its first one,
        so once scheduling is over the relay stops the server itself.
        """
        log.debug('Started')
        log.info(f'Upstream identity {self.identity} for zone {self.zone or DEFAULT_ZONE_NAME}')
        upstream = self.upstream_config or upstream_config()
        self.upstream = QueueClient(config=upstream)
        self.upstream.connect()
        log.info(f'Connected to upstream server {upstream.host}:{upstream.port}')
        self.heartbeat = UpstreamHeartbeatThread(queue=self.upstream, identity=self.identity,
                                                 heartrate=self.heartrate)
        self.puller = UpstreamPullerThread(queue=self.upstream, engine=engine, identity=self.identity,
                                           zone=self.zone, bundlesize=self.upstream_bundlesize,
                                           local_bundlesize=self.bundlesize, poll=self.poll)
        self.server.start()
        log.info(f'Relay-local server ready on {self.server.queue.config.host}:{self.server.queue.config.port}')
        self.heartbeat.start()
        self.puller.start()
        try:
            self.wait_server()
        except Exception:
            self.server.stop(wait=True)
            self.puller.stop(wait=True)
            self.heartbeat.stop(wait=True)
            raise
        self.finish_upstream()
        log.debug('Done')

    def wait_server(self: RelayThread) -> None:
        """Wait for the relay-local server, stopping it once scheduling is over or the pulled work is done."""
        while self.server.is_alive():
            self.server.join(timeout=STOP_CHECK_INTERVAL)
            if not self.server.is_alive():
                break
            if not self.puller.is_alive():
                self.puller.join()  # Raises the puller's failure, if any.
                if self.scheduling_over or store.count_backlog(engine) == 0:
                    log.debug('Pulling and scheduling over - stopping relay-local server')
                    self.server.stop(wait=True)
            elif self.scheduling_over:
                log.debug('Scheduling over - stopping relay-local server')
                self.server.stop(wait=True)
        self.server.join()

    def finish_upstream(self: RelayThread) -> None:
        """Stop pulling, then end the upstream registration.

        The final heartbeat goes upstream only when every received task's result has been handed
        over. Otherwise the identity is left to the upstream server's eviction, which puts the tasks
        it still holds back in the queue for other clients.
        """
        self.puller.stop(wait=True)
        self.puller.join()
        held = store.count_held(engine)
        if held:
            log.warning(f'Holding {held} tasks with no result returned upstream - not finishing upstream '
                        f'(the upstream server will evict {self.identity} and revert them)')
            self.heartbeat.stop(wait=True)
        else:
            self.heartbeat.signal_finished()
        self.heartbeat.join()

    @property
    def scheduling_over(self: RelayThread) -> bool:
        """The relay-local scheduler started and has finished."""
        scheduler = self.server.scheduler
        return scheduler is not None and scheduler.ident is not None and not scheduler.is_alive()

    def stop(self: RelayThread, wait: bool = False, timeout: int = None) -> None:
        """Stop the puller, the relay-local server (if it started) and the heartbeat, then this thread."""
        log.warning('Stopping')
        if self.puller is not None:
            self.puller.stop(wait=wait, timeout=timeout)
        if self.server.ident is not None:
            self.server.stop(wait=wait, timeout=timeout)
        if self.heartbeat is not None:
            self.heartbeat.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


DEFAULT_UPSTREAM_HOST: Final[str] = config.relay.upstream.host
"""Default hostname of the upstream server."""

DEFAULT_UPSTREAM_PORT: Final[int] = config.relay.upstream.port
"""Default port of the upstream server."""


APP_NAME = 'hs relay'
APP_USAGE = f"""\
Usage:
  hs relay [-h] [-H ADDR] [-p NUM] [--no-tls] [--bind ADDR] [--port NUM] [--database PATH]

  Run relay for one site.\
"""

APP_HELP = f"""\
{APP_USAGE}

  A relay connects a site to an upstream server. Upstream it is an ordinary client; at
  the site it runs its own server, scheduling the tasks it receives to clients it launches
  there, and returns their results upstream. It asks upstream only for as many tasks as
  its site has room for, and confirms them once they are stored in its database.

  The upstream key comes from the configuration ([relay.upstream] auth), never from the
  command line.

  The relay database is always a private SQLite file, whatever database is configured,
  and the configured database is never opened. One relay runs per process, and a second
  relay refuses to start on the same relay database.

Options:
  -H, --upstream-host ADDR  Hostname of upstream server (default: {DEFAULT_UPSTREAM_HOST}).
  -p, --upstream-port NUM   Port of upstream server (default: {DEFAULT_UPSTREAM_PORT}).
      --no-tls              Disable TLS to upstream server (not recommended).
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

    upstream_host: str = DEFAULT_UPSTREAM_HOST
    interface.add_argument('-H', '--upstream-host', default=upstream_host)

    upstream_port: int = DEFAULT_UPSTREAM_PORT
    interface.add_argument('-p', '--upstream-port', type=int, default=upstream_port)

    no_tls: bool = False
    interface.add_argument('--no-tls', action='store_true')

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
        EOFError: functools.partial(handle_disconnect, logger=log),
        ConnectionResetError: functools.partial(handle_disconnect, logger=log),
        ConnectionRefusedError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        AuthenticationError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
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
                                 tls=tls_from_namespace(config.server.tls),
                                 upstream=upstream_config(self.upstream_host, self.upstream_port, self.no_tls),
                                 zone=self.zone,
                                 upstream_bundlesize=config.relay.upstream.bundlesize,
                                 heartrate=config.relay.upstream.heartrate)
        try:
            thread.join()
        except Exception:
            thread.stop()
            raise

    def check_args(self: RelayApp) -> None:
        """Fail invalid arguments; warn of an insecure upstream connection."""
        if self.no_confirm:
            raise ArgumentError('A relay cannot run with --no-confirm (upstream failover rests on confirmation)')
        if config.relay.upstream.auth == DEFAULT_AUTH:
            log.warning('Using default upstream key - do not use this in production!')
        if self.no_tls or not config.relay.upstream.tls.get('enabled', True):
            log.warning('TLS to upstream server is disabled - this is not recommended for production!')

    @functools.cached_property
    def zone(self: RelayApp) -> Optional[str]:
        """Name of the zone this relay serves, from `[relay] zone` (None is the default zone)."""
        value = config.relay.get('zone', None)
        if value in (None, '', PARAM_UNSET):
            return None
        try:
            name = zone_name(value)
        except ValueError as error:
            raise ArgumentError(f'Invalid relay zone ({error})') from error
        return None if name == DEFAULT_ZONE_NAME else name

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
