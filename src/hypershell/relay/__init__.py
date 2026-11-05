# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Run a relay: one site's link to an upstream server.

A relay is one long-running process at a site. Upstream it is an ordinary client of the upstream
server, once for the zone it serves (see `hypershell.relay.upstream`); at the site it runs a complete
server, scheduling from its own database, for clients it launches there (see `hypershell.relay.site`).
Its database is always a private SQLite file, whatever database is configured (see
`hypershell.core.config.relay_database_section`): the configuration swaps the database section when
the process starts, before the database engine is built, so a process runs at most one relay.

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
import time
import errno
import shlex
import platform
import threading
import functools
from signal import signal as register, SIG_DFL

# External libs
from multiprocessing import AuthenticationError
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import Namespace

# Internal libs
from hypershell.core.config import (config, RELAY_PROCESS, RELAY_MARKER, DEFAULT_RELAY_PORT, DEFAULT_AUTH,
                                    PARAM_UNSET, relay_database_section)
from hypershell.core.signal import SIGUSR1, SIGUSR2, SIGTERM
from hypershell.core.exceptions import get_shared_exception_mapping, handle_exception, handle_disconnect
from hypershell.core.logging import Logger, INSTANCE
from hypershell.core.queue import QueueClient, QueueConfig
from hypershell.core.thread import Thread
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.data import ensuredb
from hypershell.data.core import engine
from hypershell.data.model import zone_name, DEFAULT_ZONE_NAME
from hypershell.relay import store
from hypershell.relay.store import ensure_tables, store_utc
from hypershell.relay.site import site_cluster, fence_site, end_site_clients, DEFAULT_SITE_PERIOD
from hypershell.relay.upstream import (UpstreamPullerThread, UpstreamReturnerThread, UpstreamHeartbeatThread,
                                       upstream_identity, upstream_config, upstream_key, relay_site,
                                       drain_signalled, DEFAULT_UPSTREAM_BUNDLESIZE, DEFAULT_UPSTREAM_HEARTRATE,
                                       DEFAULT_RETENTION)
from hypershell.cluster.remote import AutoScalingCluster
from hypershell.server import ServerThread

try:
    import fcntl
except ImportError:  # pragma: no cover (not available on Windows)
    fcntl = None

# Public interface
__all__ = ['RelayThread', 'RelayApp', 'DEFAULT_RELAY_BIND', 'DEFAULT_RELAY_PORT', 'DEFAULT_RELAY_POLL',
           'DEFAULT_RELAY_SIZE', 'lock_database', 'RelayDatabaseLocked', 'DRAIN', 'install_drain_handlers',
           'STOP_TIMEOUT', 'CLIENT_GRACE', 'RelayPartEnded', 'RelayTerminated', 'install_terminate_handler']

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

DEFAULT_RELAY_SIZE: Final[int] = config.relay.size
"""Default number of site clients the relay keeps launched."""

DEFAULT_RELAY_LAUNCHER: Final[str] = config.relay.launcher
"""Default command prefix that launches each site client (empty runs them directly)."""

DEFAULT_RELAY_LAUNCHER_ARGS: Final[str] = config.relay.launcher_args
"""Default extra arguments for the site client launcher."""

STOP_CHECK_INTERVAL: Final[float] = 1.0
"""Seconds between the relay's checks on its parts: whether one has failed or died, and whether the site should stop."""

STOP_TIMEOUT: float = 10.0
"""Seconds each part of a failing relay is given to stop before the relay goes on without it."""

CLIENT_GRACE: float = 5.0
"""Seconds a site client is given to exit after SIGTERM before it is killed."""

DRAIN: Final[threading.Event] = threading.Event()
"""Set when this relay process is told to drain (SIGUSR1 or SIGUSR2, see `install_drain_handlers`)."""


def drain_handler(signum: int, frame: object) -> None:  # noqa: unused frame
    """Mark the relay as draining; nothing else in the process sees the signal."""
    DRAIN.set()


def install_drain_handlers() -> None:
    """Take SIGUSR1 and SIGUSR2 for the relay alone (call from the main thread).

    The stock handler records the signal for the whole process, so the relay-local scheduler would
    stop at once and abandon the site's backlog. Here the signal only ends pulling: the site finishes
    every task the relay holds, the results go back, and the upstream registration ends cleanly.
    """
    if platform.system() != 'Windows':
        register(SIGUSR1, drain_handler)
        register(SIGUSR2, drain_handler)


def draining() -> bool:
    """True once the relay has been told to drain, by its own handler or the stock one."""
    return DRAIN.is_set() or drain_signalled()


class RelayTerminated(BaseException):
    """SIGTERM, raised in the main thread so the relay ends through its exit path (see `install_terminate_handler`)."""

    def __init__(self: RelayTerminated, signum: int) -> None:
        super().__init__(f'Received signal {signum}')
        self.signum = signum


def terminate_handler(signum: int, frame: object) -> None:  # noqa: unused frame
    """End the relay through its exit path; a second SIGTERM ends the process at once."""
    register(signum, SIG_DFL)
    raise RelayTerminated(signum)


def install_terminate_handler() -> None:
    """Take SIGTERM for the relay (call from the main thread): it ends the relay as SIGINT does, then the process.

    The relay stops every part once and ends the site clients it launched, the site's copy of the
    configuration file is removed, and then the signal is delivered again with its default action,
    so the process still ends by SIGTERM (see `end_by_signal`).
    """
    if platform.system() != 'Windows':
        register(SIGTERM, terminate_handler)


def end_by_signal(signum: int) -> None:
    """End this process by `signum`, with the signal's default action."""
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    register(signum, SIG_DFL)
    os.kill(os.getpid(), signum)
    time.sleep(1)  # The signal is delivered before this ends; exit regardless.
    os._exit(128 + signum)


class RelayPartEnded(RuntimeError):
    """A part of the relay, or of its site, ended while the relay still needed it."""


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

    The site is a stock autoscaling cluster (see `hypershell.relay.site`): a server in forever mode on
    the relay database, with local retries off, and a fixed fleet of site clients launched with a key
    minted for this run only. The upstream server alone retries tasks, so a failure at the site is a
    completed row like any other.

    Upstream the relay is one client for its zone, under an identity derived from this relay and the
    zone name. It connects before anything else starts, so a server it cannot use (a version
    mismatch, a wrong key) ends it before the site is touched. Before that, the upstream key is fenced
    off from the site: a client invocation that would carry it is refused, and the relay's environment,
    which site clients and their tasks inherit, is cleared of relay settings and of anything holding
    the key (see `hypershell.relay.site.fence_site`). A puller requests work while the site
    has room for it and confirms it once it is stored; a returner hands completed results back upstream
    through the relay's outbox; a heartbeat keeps the identity registered, naming the relay's site
    and listing the site clients connected now, so upstream they appear attributed to the site and
    disconnect with the relay.

    The relay ends when pulling is over (an upstream shutdown or a drain) and the site has finished
    what it holds. It then stops launching site clients, lets the site server tell them to finish,
    returns every result still held, and ends its upstream registration with a final heartbeat if
    nothing it received is left without a result.

    If any part fails (the upstream connection is lost, say), the relay stops every part once, each
    within a bounded wait, ends the site clients it launched (SIGTERM, then SIGKILL after a grace
    period) and raises the failure, leaving its identity to upstream eviction. A part that dies
    without raising is a failure too: the relay watches its own threads and every thread of the
    relay-local server (see `check_site`), so a dead receiver, say, never leaves it holding tasks
    while it keeps heartbeating upstream.

    Args:
        address (tuple, optional):
            Bind address and port of the relay-local server.
            See :const:`DEFAULT_RELAY_BIND` and :const:`DEFAULT_RELAY_PORT`.

        bundlesize (int, optional):
            Bundle size of the relay-local server. See :const:`DEFAULT_RELAY_BUNDLESIZE`.

        bundlewait (int, optional):
            Seconds to wait before flushing tasks, at the site and upstream.
            See :const:`DEFAULT_RELAY_BUNDLEWAIT`.

        poll (int, optional):
            Maximum polling interval of the relay-local scheduler, and of the upstream puller while
            the site is full, in seconds. See :const:`DEFAULT_RELAY_POLL`.

        tls (TLSConfig, optional):
            TLS configuration of the relay-local server (site clients connect with it).

        upstream (QueueConfig, optional):
            Connection to the upstream server. By default, from `[relay.upstream]` when the relay starts.

        zone (str, optional):
            Name of the zone the relay serves (default zone if None).

        site (str, optional):
            Name of the relay's site, as upstream lists it and its site clients (default: this host's name).

        upstream_bundlesize (int, optional):
            Most tasks the relay asks for, and returns, at once. See :const:`DEFAULT_UPSTREAM_BUNDLESIZE`.

        heartrate (int, optional):
            Seconds between upstream heartbeats. See :const:`DEFAULT_UPSTREAM_HEARTRATE`.

        size (int, optional):
            Number of site clients kept launched. See :const:`DEFAULT_RELAY_SIZE`.

        launcher (str, optional):
            Command prefix that launches each site client (empty runs them directly on this host).

        launcher_args (list, optional):
            Extra arguments for the launcher.

        num_threads (int, optional):
            Executor threads in each site client.

        template (str, optional):
            Command template of the site clients.

        client_timeout (int, optional):
            Seconds a site client waits for tasks before it exits (never if None).

        retention (int, optional):
            Seconds a returned result is kept before it is pruned. See :const:`DEFAULT_RETENTION`.

        period (int, optional):
            Seconds between checks on the site clients (an exited one is replaced at the next).

        site_config (str, optional):
            Path the site's copy of the configuration file is written to, if one is needed
            (see `hypershell.relay.site.fence_site`). Default: beside the relay database.
    """

    cluster: AutoScalingCluster
    server: ServerThread
    zone: Optional[str]
    site: str
    identity: str
    upstream_config: Optional[QueueConfig]
    upstream: Optional[QueueClient] = None
    puller: Optional[UpstreamPullerThread] = None
    returner: Optional[UpstreamReturnerThread] = None
    heartbeat: Optional[UpstreamHeartbeatThread] = None
    site_config: Optional[str]
    aborted: bool = False

    def __init__(self: RelayThread,
                 address: Tuple[str, int] = (DEFAULT_RELAY_BIND, DEFAULT_RELAY_PORT),
                 bundlesize: int = DEFAULT_RELAY_BUNDLESIZE,
                 bundlewait: int = DEFAULT_RELAY_BUNDLEWAIT,
                 poll: int = DEFAULT_RELAY_POLL,
                 tls: Optional[TLSConfig] = None,
                 upstream: Optional[QueueConfig] = None,
                 zone: Optional[str] = None,
                 site: Optional[str] = None,
                 upstream_bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE,
                 heartrate: int = DEFAULT_UPSTREAM_HEARTRATE,
                 size: int = DEFAULT_RELAY_SIZE,
                 launcher: Optional[str] = DEFAULT_RELAY_LAUNCHER,
                 launcher_args: Optional[List[str]] = None,
                 num_threads: int = 1,
                 template: str = '{}',
                 client_timeout: Optional[int] = None,
                 retention: int = DEFAULT_RETENTION,
                 period: int = DEFAULT_SITE_PERIOD,
                 site_config: Optional[str] = None) -> None:
        """Initialize the site and the upstream side."""
        self.cluster = site_cluster(address=address, bundlesize=bundlesize, bundlewait=bundlewait, poll=poll,
                                    tls=tls, zone=zone, size=size, launcher=launcher, launcher_args=launcher_args,
                                    num_threads=num_threads, template=template, client_timeout=client_timeout,
                                    period=period)
        self.server = self.cluster.server
        self.bundlesize = bundlesize
        self.bundlewait = bundlewait
        self.poll = poll
        self.zone = zone
        self.site = relay_site(site)
        self.identity = upstream_identity(zone)
        self.upstream_config = upstream
        self.upstream_bundlesize = upstream_bundlesize
        self.heartrate = heartrate
        self.retention = retention
        database_file = config.database.get('file', None)
        self.site_config = site_config or (f'{database_file}.site.toml' if database_file else None)
        self.ending = threading.Lock()
        super().__init__(name='hypershell-relay')

    @property
    def site_key(self: RelayThread) -> str:
        """The key site clients authenticate with, minted for this run only."""
        return self.server.queue.config.auth

    def run_with_exceptions(self: RelayThread) -> None:
        """Connect upstream, start the site, pull and return work, and wait for the relay to finish."""
        log.debug('Started')
        log.info(f'Upstream identity {self.identity} for zone {self.zone or DEFAULT_ZONE_NAME} at site {self.site}')
        upstream = self.upstream_config or upstream_config()
        try:
            fence_site(self.cluster, key=upstream.auth, site_config=self.site_config)
            self.upstream = QueueClient(config=upstream)
            self.upstream.connect()
            log.info(f'Connected to upstream server {upstream.host}:{upstream.port}')
            self.heartbeat = UpstreamHeartbeatThread(queue=self.upstream, identity=self.identity,
                                                     heartrate=self.heartrate, site=self.site,
                                                     roster=functools.partial(store.roster, engine, INSTANCE))
            self.puller = UpstreamPullerThread(queue=self.upstream, engine=engine, identity=self.identity,
                                               zone=self.zone, bundlesize=self.upstream_bundlesize,
                                               local_bundlesize=self.bundlesize, poll=self.poll, drain=draining)
            self.returner = UpstreamReturnerThread(queue=self.upstream, engine=engine,
                                                   bundlesize=self.upstream_bundlesize, bundlewait=self.bundlewait,
                                                   poll=self.poll, retention=self.retention)
            self.cluster.start()
            self.wait_ready()
            log.info(f'Relay-local server ready on {self.server.queue.config.host}:{self.server.queue.config.port}')
            self.heartbeat.start()
            self.returner.start()
            self.puller.start()
            self.wait_site()
            self.stop_site()
            self.returner.flush()
            self.wait_returner()
            self.finish_upstream()
            self.reap_site_clients()
        except Exception:
            self.abort()
            raise
        finally:
            self.release()
        log.debug('Done')

    def wait_ready(self: RelayThread) -> None:
        """Wait for the relay-local server to listen; raise its failure if the site ends first."""
        while not self.server.queue.ready:
            if not self.cluster.is_alive():
                self.cluster.join()  # Raises the failure that ended the site, if any.
                raise RuntimeError('Relay-local server stopped before it was ready')
            time.sleep(0.1)

    def wait_site(self: RelayThread) -> None:
        """Wait until the relay should finish: pulling is over and the site holds nothing, or scheduling is over.

        A failure in the site, the puller, the returner or the heartbeat is raised here, as is a
        thread of the site, the returner or the heartbeat ending before its time (see `check_site`).
        """
        while True:
            self.cluster.join(timeout=STOP_CHECK_INTERVAL)
            if not self.cluster.is_alive():
                log.warning('Relay-local server stopped')
                return
            for thread in (self.returner, self.heartbeat):
                if not thread.is_alive():
                    thread.join()  # Raises its failure, if any.
                    raise RelayPartEnded(f'Relay {thread.name} ended while the relay runs')
            self.check_site()
            if not self.puller.is_alive():
                self.puller.join()  # Raises the puller's failure, if any.
                if self.scheduling_over or store.count_backlog(engine) == 0:
                    log.debug('Pulling over and site drained - stopping site')
                    return
            elif self.scheduling_over:
                log.debug('Scheduling over - stopping site')
                return

    def stop_site(self: RelayThread) -> None:
        """Stop launching site clients, end scheduling, and wait for the site server to finish.

        The site server tells its clients to finish once scheduling is over and waits for them, so
        every result they hold reaches the relay database first. A site server that never had a client
        would wait for its first one, so it is stopped outright. A failure upstream meanwhile is raised,
        as is a thread of the site ending out of turn (see `check_site`).
        """
        self.cluster.autoscaler.stop()
        scheduler = self.server.scheduler
        if scheduler is not None and scheduler.is_alive():
            scheduler.stop()
        if self.server.heartmonitor.machine.startup_phase:
            log.debug('No site client ever connected - stopping relay-local server')
            self.server.stop(wait=True)
        while self.cluster.is_alive():
            self.cluster.join(timeout=STOP_CHECK_INTERVAL)
            self.check_upstream()
            self.check_site(stopping=True)
        self.cluster.join()  # Raises the failure that ended the site, if any.

    def wait_returner(self: RelayThread) -> None:
        """Wait for the returner to hand back everything held; a failure upstream meanwhile is raised."""
        while self.returner.is_alive():
            self.returner.join(timeout=STOP_CHECK_INTERVAL)
            self.check_upstream()
        self.returner.join()  # Raises the returner's failure, if any.

    def check_site(self: RelayThread, stopping: bool = False) -> None:
        """Raise if a thread of the site has died, so the relay takes its failure path.

        A thread that has ended with a failure raises it. On a clean stop the relay-local server's
        threads end in turn: the scheduler, then the heart monitor, the receiver, confirm and the
        server itself, each only once the one before it has ended. One that has ended while an earlier
        one still runs has died, and the server would wait on the earlier one forever: a dead receiver,
        say, leaves the site's results unwritten, so the relay would hold its tasks indefinitely. Until
        the site is told to stop (`stopping`), the autoscaler that keeps the site clients launched must
        be running too.
        """
        server = self.server
        chain = [thread for thread in (server.scheduler, server.heartmonitor, server.receiver, server.confirm, server)
                 if thread is not None]
        autoscaler = self.cluster.autoscaler
        for thread in (*chain, autoscaler):
            if thread.ident is not None and not thread.is_alive():
                thread.join()  # Raises its failure, if any.
        for earlier, later in zip(chain, chain[1:]):
            # The later one is read first: once it has ended on a clean stop, the earlier one has too.
            if later.ident is not None and not later.is_alive() and earlier.is_alive():
                raise RelayPartEnded(f'Relay-local {later.name} ended while {earlier.name} still runs')
        if not stopping and autoscaler.ident is not None and not autoscaler.is_alive():
            raise RelayPartEnded(f'Relay-local {autoscaler.name} ended while the relay runs')

    def check_upstream(self: RelayThread) -> None:
        """Raise the failure of the heartbeat or the returner, if either has ended with one."""
        for thread in (self.heartbeat, self.returner):
            if thread is not None and thread.ident is not None and not thread.is_alive():
                thread.join()  # Raises its failure, if any.

    def finish_upstream(self: RelayThread) -> None:
        """Stop pulling, then end the upstream registration.

        A request the puller has already sent is inserted and confirmed before it stops, and only
        once it has stopped is it known what the relay holds. The final heartbeat goes upstream only
        when every received task's result has been handed over. Otherwise the identity is left to the
        upstream server's eviction, which puts the tasks it still holds back in the queue for other
        clients.
        """
        self.puller.stop(wait=True, timeout=STOP_TIMEOUT)
        if self.puller.is_alive():
            log.warning(f'Upstream puller did not stop within {STOP_TIMEOUT} seconds - not finishing upstream '
                        f'(the upstream server will evict {self.identity} and revert what it holds)')
            self.heartbeat.stop(wait=True, timeout=STOP_TIMEOUT)
            return
        held = store.count_held(engine)
        if held:
            log.warning(f'Holding {held} tasks with no result returned upstream - not finishing upstream '
                        f'(the upstream server will evict {self.identity} and revert them)')
            self.heartbeat.stop(wait=True, timeout=STOP_TIMEOUT)
        else:
            self.heartbeat.signal_finished()
            self.heartbeat.join(timeout=STOP_TIMEOUT)

    def reap_site_clients(self: RelayThread) -> None:
        """After a clean drain, end any site client still running after the grace period (one that never connected)."""
        if ended := end_site_clients(self.cluster, grace=CLIENT_GRACE, patience=CLIENT_GRACE):
            log.warning(f'Ended {ended} site clients still running after the site finished')

    def abort(self: RelayThread) -> None:
        """Stop every part of the relay once, each within a bounded wait, and end the site clients it launched.

        Safe to call more than once, and from any thread: only the first call does anything. A part
        that does not stop in time is left behind (every thread here is a daemon).
        """
        with self.ending:
            if self.aborted:
                return
            self.aborted = True
        log.warning('Stopping')
        self.stop_quietly(self.puller)
        self.stop_quietly(self.cluster.autoscaler)
        if ended := end_site_clients(self.cluster, grace=CLIENT_GRACE):
            log.warning(f'Ended {ended} site clients')
        if self.cluster.is_alive():
            stopper = threading.Thread(target=self.stop_quietly, args=(self.cluster, ),
                                       name='hypershell-relay-stop', daemon=True)
            stopper.start()
            stopper.join(timeout=STOP_TIMEOUT)
            if stopper.is_alive():
                log.warning(f'Relay-local server did not stop within {STOP_TIMEOUT} seconds - leaving it')
        self.stop_quietly(self.returner)
        self.stop_quietly(self.heartbeat)

    @staticmethod
    def stop_quietly(thread: Optional[Thread]) -> None:
        """Stop `thread` if it was started, waiting at most `STOP_TIMEOUT`; its failure is not raised here."""
        if thread is None or thread.ident is None:
            return
        try:
            thread.stop(wait=True, timeout=STOP_TIMEOUT)
        except Exception as error:
            log.debug(f'Stopped {thread.name} ({error.__class__.__name__}: {error})')

    def release(self: RelayThread) -> None:
        """Remove the site's copy of the configuration file, if one was written.

        Called as the relay ends, however it ends: a clean drain or a failure (by this thread), and
        SIGINT or SIGTERM (by `RelayApp.run`). Only a relay killed outright leaves the copy behind.
        """
        if self.site_config and os.path.exists(self.site_config):
            try:
                os.remove(self.site_config)
            except OSError as error:
                log.warning(f'Could not remove site configuration file ({error})')

    @property
    def scheduling_over(self: RelayThread) -> bool:
        """The relay-local scheduler started and has finished."""
        scheduler = self.server.scheduler
        return scheduler is not None and scheduler.ident is not None and not scheduler.is_alive()

    def stop(self: RelayThread, wait: bool = False, timeout: Optional[float] = None) -> None:
        """Stop every part of the relay once (see `abort`), then this thread."""
        self.abort()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_UPSTREAM_HOST: Final[str] = config.relay.upstream.host
"""Default hostname of the upstream server."""

DEFAULT_UPSTREAM_PORT: Final[int] = config.relay.upstream.port
"""Default port of the upstream server."""


APP_NAME = 'hs relay'
APP_USAGE = f"""\
Usage:
  hs relay [-h] [-H ADDR] [-p NUM] [--no-tls] [--zone NAME] [-N NUM] [--launcher CMD]
           [--launcher-args ARGS] [--bind ADDR] [--port NUM] [--database PATH]

  Run relay for one site.\
"""

APP_HELP = f"""\
{APP_USAGE}

  A relay connects a site to an upstream server. Upstream it is an ordinary client; at
  the site it runs its own server, scheduling the tasks it receives to clients it launches
  there, and returns their results upstream. It asks upstream only for as many tasks as
  its site has room for, and confirms them once they are stored in its database.

  The site runs no retries: a task that fails there goes back upstream as it ended, and
  the upstream server decides whether to run it again. Results go back attributed to the
  client at the site that ran them, and are kept in the relay database until handed over.
  Upstream, the site's clients are listed under the relay and its site ([relay] site,
  default: this host's name) while they are connected, and disconnect with the relay.

  The upstream key comes from the configuration ([relay.upstream] auth, auth_env or
  auth_eval), never from the command line, and never reaches the site: relay settings, and
  any variable holding the key, are removed from the environment the site's clients and
  tasks inherit, and they are given the configuration file without the relay settings.

  SIGUSR1 or SIGUSR2 drain the relay: it asks for no more work, finishes what it holds and
  returns the results. SIGINT or SIGTERM end it at once, with the site's clients. If the
  relay fails, it ends the site's clients and exits non-zero.

  The relay database is always a private SQLite file, whatever database is configured,
  and the configured database is never opened. One relay runs per process, and a second
  relay refuses to start on the same relay database.

Options:
  -H, --upstream-host ADDR  Hostname of upstream server (default: {DEFAULT_UPSTREAM_HOST}).
  -p, --upstream-port NUM   Port of upstream server (default: {DEFAULT_UPSTREAM_PORT}).
      --no-tls              Disable TLS to upstream server (not recommended).
      --zone          NAME  Zone this relay serves (default: the default zone).
  -N, --size          NUM   Clients kept launched at the site, at least 1 (default: {DEFAULT_RELAY_SIZE}).
      --launcher      CMD   Command prefix that launches each client (default: none).
      --launcher-args ARGS  Extra arguments for the launcher.
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

    zone_option: Optional[str] = None
    interface.add_argument('--zone', default=zone_option, dest='zone_option')

    size: int = DEFAULT_RELAY_SIZE
    interface.add_argument('-N', '--size', type=int, default=size)

    launcher: str = DEFAULT_RELAY_LAUNCHER
    interface.add_argument('--launcher', default=launcher)

    launcher_args: str = DEFAULT_RELAY_LAUNCHER_ARGS
    interface.add_argument('--launcher-args', default=launcher_args)

    bind: str = DEFAULT_RELAY_BIND
    interface.add_argument('--bind', default=bind)

    port: int = config.relay.port
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
        """Keep the relay arguments, which a re-executed relay is given again; end by SIGTERM if it came.

        A relay ended by SIGTERM has stopped its parts, ended its site clients and removed the site's
        configuration copy by now (see `run`); the signal is delivered again so the process ends by it.
        """
        cls.argv = list(cmdline or [])
        try:
            return super().main(cmdline, shared)
        except RelayTerminated as signalled:
            end_by_signal(signalled.signum)
            raise

    def run(self: RelayApp) -> None:
        """Run relay."""
        self.check_args()
        self.bind_database()
        path = config.database.file
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = lock_database(path)
        log.info(f'Relay database {path}')
        store_utc(engine)
        ensuredb()
        ensure_tables(engine)
        install_drain_handlers()
        install_terminate_handler()
        thread = RelayThread(address=(self.bind, self.port),
                             bundlesize=config.relay.bundlesize,
                             bundlewait=config.relay.bundlewait,
                             poll=config.relay.poll,
                             tls=tls_from_namespace(config.server.tls),
                             upstream=upstream_config(self.upstream_host, self.upstream_port, self.no_tls),
                             zone=self.zone,
                             site=config.relay.get('site', None),
                             upstream_bundlesize=config.relay.upstream.bundlesize,
                             heartrate=config.relay.upstream.heartrate,
                             size=self.size,
                             launcher=self.launcher,
                             launcher_args=[self.launcher_args] if self.launcher_args else None,
                             num_threads=config.relay.num_threads,
                             template=config.relay.template,
                             client_timeout=config.relay.timeout or None,
                             retention=config.relay.retention)
        try:
            thread.start()
            thread.join()
        except BaseException:  # A failure, SIGINT, or SIGTERM (`RelayTerminated`).
            thread.stop()
            raise
        finally:
            thread.release()

    def check_args(self: RelayApp) -> None:
        """Fail invalid arguments; warn of an insecure upstream connection."""
        if self.no_confirm:
            raise ArgumentError('A relay cannot run with --no-confirm (upstream failover rests on confirmation)')
        if upstream_key() == DEFAULT_AUTH:
            log.warning('Using default upstream key - do not use this in production!')
        if self.no_tls or not config.relay.upstream.tls.get('enabled', True):
            log.warning('TLS to upstream server is disabled - this is not recommended for production!')
        if self.size < 1:
            raise ArgumentError(f'Site size must be at least 1 (given {self.size}): '
                                f'with no client at the site the relay never asks for work')
        try:
            shlex.split(self.launcher_args or '')
            shlex.split(self.launcher or '')
        except ValueError as error:
            raise ArgumentError(f'Invalid launcher ({error})') from error

    @functools.cached_property
    def zone(self: RelayApp) -> Optional[str]:
        """Name of the zone this relay serves, from `--zone` or `[relay] zone` (None is the default zone)."""
        value = self.zone_option if self.zone_option is not None else config.relay.get('zone', None)
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
