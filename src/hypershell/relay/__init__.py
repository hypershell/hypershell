# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Run a relay: one site's link to an upstream server.

A relay is one long-running process at a site. Upstream it is an ordinary client of the upstream
server, once for each zone it serves (see `hypershell.relay.upstream`); at the site it runs a complete
server, scheduling from its own database, for clients it launches there, one fleet per zone (see
`hypershell.relay.site`).
Its database is always a private SQLite file, whatever database is configured (see
`hypershell.core.config.relay_database_section`): the configuration swaps the database section when
the process starts, before the database engine is built, so a process runs at most one relay.

A relay that a newer relay at its site supersedes (the upstream server says so in the topology it
publishes) retires: it asks for no more work, launches no more site clients, hands back upstream the
tasks no site client has received, lets its site clients finish what they hold, and then ends its
registration and exits.

Example:
    >>> from hypershell.relay import RelayThread
    >>> from hypershell.relay.upstream import upstream_config
    >>> relay = RelayThread.new(address=('0.0.0.0', 50002), upstream=upstream_config('upstream.example', 50001))
    >>> relay.join()
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Tuple, IO, Optional, Dict, Final, Mapping, Sequence
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
from sqlalchemy import inspect

# Internal libs
from hypershell.core.config import (config, RELAY_PROCESS, RELAY_MARKER, DEFAULT_RELAY_PORT, DEFAULT_AUTH,
                                    PARAM_UNSET, relay_database_section)
from hypershell.core.signal import SIGUSR1, SIGUSR2, SIGTERM
from hypershell.core.exceptions import get_shared_exception_mapping, handle_exception, handle_disconnect
from hypershell.core.logging import Logger, INSTANCE
from hypershell.core.queue import QueueClient, QueueConfig
from hypershell.core.reconnect import UpstreamLink, LinkDown
from hypershell.core.thread import Thread, join_waking
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.data import ensuredb
from hypershell.data.core import engine
from hypershell.data.model import Task, zone_name, zone_id, DEFAULT_ZONE_NAME
from hypershell.relay import store
from hypershell.relay.store import ensure_tables, store_utc
from hypershell.relay.site import (site_cluster, zone_launch, zone_fleet, fence_site, end_site_clients,
                                   launched_now, TopologyView, DEFAULT_SITE_PERIOD)
from hypershell.core.topology import Bounds, MAX_HANDBACK_TASKS, handback_text, read_handback_reply
from hypershell.cluster.demand import ZoneRef
from hypershell.relay.upstream import (UpstreamPullerThread, UpstreamReturnerThread, UpstreamHeartbeatThread,
                                       served_zones, upstream_config, upstream_key, relay_site,
                                       drain_signalled, log_drain, DEFAULT_UPSTREAM_BUNDLESIZE,
                                       DEFAULT_UPSTREAM_HEARTRATE, DEFAULT_RETENTION, LINK_STAGES)
from hypershell.cluster.remote import AutoScalingCluster, AutoScalerThread
from hypershell.server import ServerThread

try:
    import fcntl
except ImportError:  # pragma: no cover (not available on Windows)
    fcntl = None

# Public interface
__all__ = ['RelayThread', 'RelayApp', 'DEFAULT_RELAY_BIND', 'DEFAULT_RELAY_PORT', 'DEFAULT_RELAY_POLL',
           'DEFAULT_RELAY_SIZE', 'lock_database', 'RelayDatabaseLocked', 'DRAIN', 'install_drain_handlers',
           'STOP_TIMEOUT', 'CLIENT_GRACE', 'RelayPartEnded', 'RelayTerminated', 'install_terminate_handler',
           'site_bundlesize', 'DEFAULT_UPSTREAM_DEADLINE', 'DEFAULT_UPSTREAM_BACKOFF', 'DEFAULT_EVICT_HINT',
           'DEFAULT_SITE_EVICT', 'ZONE_OPTIONS', 'zone_options', 'BOUND_OPTIONS', 'zone_bounds']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_RELAY_BIND: Final[str] = config.relay.bind
"""Default bind address of the relay-local server."""

DEFAULT_RELAY_POLL: Final[float] = config.relay.poll
"""Default polling interval of the relay-local scheduler in seconds."""

DEFAULT_UPSTREAM_DEADLINE: Final[float] = config.relay.upstream.deadline
"""Default seconds an upstream call may take before the upstream link counts as down."""

DEFAULT_UPSTREAM_BACKOFF: Final[float] = config.relay.upstream.backoff
"""Default longest wait in seconds between attempts to reach the upstream server again."""

DEFAULT_EVICT_HINT: Final[float] = config.relay.upstream.evict_hint
"""Default seconds of upstream outage after which the upstream server has likely evicted the relay."""

DEFAULT_SITE_EVICT: Final[int] = config.server.evict
"""Default seconds the relay-local server waits on a silent site client before evicting it."""

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


ZONE_OPTIONS: Final[Tuple[str, ...]] = ('launcher', 'launcher_args', 'size', 'min', 'max', 'init')
"""Settings a served zone may give for its own fleet (`[relay.zone.<name>]`), in place of the relay's."""

BOUND_OPTIONS: Final[Tuple[str, ...]] = ('min', 'max', 'init')
"""Client bounds a served zone may give (`[relay.zone.<name>]`), or the relay for every zone (`[relay]`)."""


def _given(value: Any) -> bool:
    """True for a setting that was given (not None and not unset)."""
    return value is not None and value is not PARAM_UNSET and value != PARAM_UNSET


def zone_bounds(settings: Mapping[str, Any], relay_wide: Mapping[str, Any]) -> Tuple[Bounds, Bounds]:
    """The client bounds of one served zone: those it advertises upstream, and those of its fallback fleet.

    Each of `size`, `min`, `max` and `init` is the zone's own (`settings`) if it gives one, else the
    relay's (`relay_wide`). The advertised bounds, which a server autoscaling with relay sites plans
    with: `min` (default 0), `init` (default `min`) and `max` (default `size`). The fallback fleet's,
    used when no topology is published: `min` (default `size`, at least 1, so the zone always has a
    client to ask for work), `init` (default `size`) and `max` (default `size`), with `min` and `init`
    held within `max`. With only `size` given the fallback is a fixed fleet of `size`, as without bounds.
    ValueError when a value is not a whole number or the advertised bounds are not
    0 <= min <= init <= max with max >= 1.
    """
    merged = {name: settings[name] if _given(settings.get(name)) else relay_wide.get(name)
              for name in ('size', *BOUND_OPTIONS)}
    for name, value in merged.items():
        if _given(value) and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f'{name} must be a whole number, at least 0 (given {value!r})')
    size = merged['size'] if _given(merged['size']) else 1
    lo, hi, init = (merged[name] if _given(merged[name]) else None for name in BOUND_OPTIONS)
    upper = size if hi is None else hi
    advertised = Bounds(min=lo or 0, max=upper, init=(lo or 0) if init is None else init)
    floor = min(upper, max(1, size if lo is None else lo))
    start = min(upper, max(floor, size if init is None else init))
    return advertised, Bounds(min=floor, max=upper, init=start)


def zone_options(value: Any) -> Tuple[List[Optional[str]], Dict[Optional[str], Dict[str, Any]]]:
    """The zones `[relay] zone` names, in order, and each one's own fleet settings.

    The setting is one zone name, a list of names, or a table with one table per zone (`[relay.zone.gpu]`)
    holding that zone's `launcher`, `launcher_args`, `size`, `min`, `max` or `init`. The default zone is None. Raises
    ValueError for an invalid or repeated name, or a setting a zone may not give.
    """
    if value in (None, '', PARAM_UNSET):
        return [None], {}
    if isinstance(value, Mapping):
        names, tables = list(value), dict(value)
    elif isinstance(value, (list, tuple)):
        names, tables = list(value), {}
    else:
        names, tables = [value], {}
    zones: List[Optional[str]] = []
    options: Dict[Optional[str], Dict[str, Any]] = {}
    for given in names:
        name = zone_name(str(given))
        zone = None if name == DEFAULT_ZONE_NAME else name
        if zone in zones:
            raise ValueError(f'zone {name} named more than once')
        zones.append(zone)
        table = tables.get(given) or {}
        if not isinstance(table, Mapping):
            raise ValueError(f'settings of zone {name} must be a table')
        if unknown := sorted(set(table) - set(ZONE_OPTIONS)):
            raise ValueError(f'zone {name} cannot set {", ".join(unknown)} '
                             f'(a zone sets only {", ".join(ZONE_OPTIONS)})')
        options[zone] = dict(table)
    if not zones:
        raise ValueError('no zone named')
    return zones, options


def site_bundlesize(bundlesize: int, num_threads: int) -> int:
    """The relay-local bundle size: `bundlesize`, but never below a site client's executor threads.

    A site client asks for one bundle at a time and holds at most a bundle in waiting, so a bundle
    smaller than its executor threads leaves executors idle between round trips (`num_threads` of 0,
    one per core, is unknown here and adds no floor).
    """
    return max(1, int(bundlesize), int(num_threads or 0))


class RelayThread(Thread):
    """
    Run a relay within a dedicated thread.

    The site is a stock autoscaling cluster (see `hypershell.relay.site`): a server in forever mode on
    the relay database, with local retries off, and a fixed fleet of site clients launched with a key
    minted for this run only, handed to each in its environment, never on its command line. The
    upstream server alone retries tasks, so a failure at the site is a completed row like any other.

    Upstream the relay is one client for each zone it serves, under an identity derived from this
    relay and the zone name. A relay serving several zones keeps one fleet of site clients per zone,
    the first zone's in the cluster and the others alongside it, all on the one relay-local server;
    each zone is pulled for under its own identity, bounded by its own fleet, and every identity
    beats upstream (the first, the roster holder, listing the site clients of every zone), so the
    upstream server evicts each on its own and puts back what each held if the relay fails. It
    connects before anything else starts, so a server it cannot use (a version
    mismatch, a wrong key) ends it before the site is touched; a server it cannot reach yet is tried
    again with backoff until it answers. Before that, the upstream key is fenced
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

    A relay the upstream server marks retired (a newer relay registered its site while this one runs)
    ends sooner, without waiting for its backlog (see `retire`): it stops pulling, launching and
    scheduling, hands back upstream every task no site client has received, and then finishes as
    above, its site clients running what they hold. The upstream server puts the tasks handed back in
    the queue again at once, for the newer relay, rather than at this relay's eviction.

    Each zone's fleet has client bounds (see `zone_bounds`): every zone identity's beat advertises
    them upstream, with the clients launched in the zone and the relay's instance key (its first
    identity, new each time a relay starts). Against an upstream server autoscaling with relay sites,
    which publishes a topology, each fleet follows its zone's entry there, read after every beat and
    once before the site starts: it launches what the server asks for and never ends a client, so a
    zone the server lowers or zeroes shrinks only as its clients exit when idle. Against one that
    publishes none, each fleet is fixed at its fallback size, as a relay always was.

    The upstream connection is a link that rides through outages (see `hypershell.core.reconnect`):
    every call has a deadline, and a lost connection is made again with backoff while the site keeps
    running and completed results stay in the outbox. Once the link is back the relay beats first
    (registering again if it was evicted), sends again a confirmation the outage cut off (unless the
    outage outlasted `evict_hint`), hands back what the outbox holds, and only then asks for more.

    If any part fails (a dead receiver, or an upstream server it can no longer use), the relay stops
    every part once, each within a bounded wait, ends the site clients it launched (SIGTERM, then SIGKILL after a grace
    period) and raises the failure, leaving its identity to upstream eviction. A part that dies
    without raising is a failure too: the relay watches its own threads and every thread of the
    relay-local server (see `check_site`), so a dead receiver, say, never leaves it holding tasks
    while it keeps heartbeating upstream.

    Args:
        address (tuple, optional):
            Bind address and port of the relay-local server.
            See :const:`DEFAULT_RELAY_BIND` and :const:`DEFAULT_RELAY_PORT`.

        bundlesize (int, optional):
            Bundle size of the relay-local server and its site clients, never below `num_threads`.
            See :const:`DEFAULT_RELAY_BUNDLESIZE`.

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

        zones (list, optional):
            Names of every zone the relay serves, in place of `zone` (None is the default zone).

        zone_settings (dict, optional):
            A zone's own fleet settings by zone name (`launcher`, `launcher_args`, `size`, `min`,
            `max`, `init`), in place of the relay's.

        site (str, optional):
            Name of the relay's site, as upstream lists it and its site clients (default: this host's name).

        upstream_bundlesize (int, optional):
            Most tasks the relay asks for, and returns, at once. See :const:`DEFAULT_UPSTREAM_BUNDLESIZE`.

        heartrate (int, optional):
            Seconds between upstream heartbeats. See :const:`DEFAULT_UPSTREAM_HEARTRATE`.

        size (int, optional):
            Number of site clients kept launched. See :const:`DEFAULT_RELAY_SIZE`.

        min_size, max_size, init_size (int, optional):
            Client bounds of every zone that gives none of its own (see `zone_bounds`).

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

        deadline (float, optional):
            Seconds an upstream call may take before the link counts as down.
            See :const:`DEFAULT_UPSTREAM_DEADLINE`.

        backoff (float, optional):
            Longest wait in seconds between attempts to reach the upstream server again.
            See :const:`DEFAULT_UPSTREAM_BACKOFF`.

        evict_hint (float, optional):
            Seconds of upstream outage after which the upstream server has likely evicted the relay.
            See :const:`DEFAULT_EVICT_HINT`.

        evict (int, optional):
            Seconds the relay-local server waits on a silent site client before evicting it.
            See :const:`DEFAULT_SITE_EVICT`.
    """

    cluster: AutoScalingCluster
    fleets: Sequence[AutoScalerThread] = ()  # The fleets of zones after the first (the cluster has the first's).
    server: ServerThread
    zone: Optional[str]
    zones: List[Optional[str]]
    site: str
    identity: str
    identities: List[str]
    upstream_config: Optional[QueueConfig]
    link: Optional[UpstreamLink] = None
    puller: Optional[UpstreamPullerThread] = None
    returner: Optional[UpstreamReturnerThread] = None
    heartbeat: Optional[UpstreamHeartbeatThread] = None
    site_config: Optional[str]
    view: TopologyView
    advertised: Dict[Optional[str], Bounds]
    aborted: bool = False
    retiring: bool = False
    withdrawn: bool = False
    handback_due: bool = False

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
                 site_config: Optional[str] = None,
                 deadline: float = DEFAULT_UPSTREAM_DEADLINE,
                 backoff: float = DEFAULT_UPSTREAM_BACKOFF,
                 evict_hint: float = DEFAULT_EVICT_HINT,
                 evict: int = DEFAULT_SITE_EVICT,
                 zones: Optional[Sequence[Optional[str]]] = None,
                 zone_settings: Optional[Mapping[Optional[str], Mapping[str, Any]]] = None,
                 min_size: Optional[int] = None,
                 max_size: Optional[int] = None,
                 init_size: Optional[int] = None) -> None:
        """Initialize the site and the upstream side."""
        bundlesize = site_bundlesize(bundlesize, num_threads)
        self.zones = list(zones) if zones else [zone]
        self.zone = self.zones[0]
        settings = {name: {'size': size, 'launcher': launcher, 'launcher_args': launcher_args,
                           **dict((zone_settings or {}).get(name) or {})}
                    for name in self.zones}
        relay_wide = {'size': size, 'min': min_size, 'max': max_size, 'init': init_size}
        bounds = {name: zone_bounds(settings[name], relay_wide) for name in self.zones}
        self.advertised = {name: advertised for name, (advertised, _) in bounds.items()}
        self.view = TopologyView()
        first, (_, fallback) = settings[self.zone], bounds[self.zone]
        self.cluster = site_cluster(address=address, bundlesize=bundlesize, bundlewait=bundlewait, poll=poll,
                                    tls=tls, zone=self.zone, size=first['size'], launcher=first['launcher'],
                                    launcher_args=first['launcher_args'], num_threads=num_threads,
                                    template=template, client_timeout=client_timeout,
                                    period=period, evict_after=evict, init_size=fallback.init,
                                    min_size=fallback.min, max_size=fallback.max, view=self.view)
        self.fleets = [zone_fleet(zone_launch(self.cluster, name, launcher=settings[name]['launcher'],
                                              launcher_args=settings[name]['launcher_args'],
                                              num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                                              template=template, client_timeout=client_timeout, tls=tls),
                                  name, size=settings[name]['size'], period=period,
                                  init_size=bounds[name][1].init, min_size=bounds[name][1].min,
                                  max_size=bounds[name][1].max, view=self.view)
                       for name in self.zones[1:]]
        self.server = self.cluster.server
        self.bundlesize = bundlesize
        self.bundlewait = bundlewait
        self.poll = poll
        self.site = relay_site(site)
        self.served = served_zones(self.zones)
        self.identities = [identity for identity, _ in self.served]
        self.identity = self.identities[0]
        self.upstream_config = upstream
        self.upstream_bundlesize = upstream_bundlesize
        self.heartrate = heartrate
        self.retention = retention
        self.deadline = deadline
        self.backoff = backoff
        self.evict_hint = evict_hint
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
        for identity, zone in self.served:
            log.info(f'Upstream identity {identity} for zone {zone or DEFAULT_ZONE_NAME} at site {self.site}')
        upstream = self.upstream_config or upstream_config()
        try:
            fence_site(self.cluster, key=upstream.auth, site_config=self.site_config,
                       launches=[fleet.machine.launch for fleet in self.fleets])
            # The link holds the upstream configuration (and so the key) read before the fence:
            # every reconnection builds its client from this object, never from configuration again.
            self.link = UpstreamLink(functools.partial(QueueClient, config=upstream),
                                     name=f'upstream server {upstream.host}:{upstream.port}',
                                     deadline=self.deadline, evict_hint=self.evict_hint,
                                     backoff_cap=self.backoff, stages=LINK_STAGES)
            self.link.start()
            if not self.wait_link():
                return
            self.heartbeat = UpstreamHeartbeatThread(queue=self.link, identity=self.identity,
                                                     heartrate=self.heartrate, site=self.site,
                                                     roster=functools.partial(store.roster, engine, INSTANCE),
                                                     identities=self.identities,
                                                     zones={identity: ZoneRef.from_name(zone).name
                                                            for identity, zone in self.served},
                                                     bounds={ZoneRef.from_name(zone).name: bounds
                                                             for zone, bounds in self.advertised.items()},
                                                     launched=self.zone_launched, instance=self.identity,
                                                     view=self.view)
            self.puller = UpstreamPullerThread(queue=self.link, engine=engine, identity=self.identity,
                                               zone=self.zone, bundlesize=self.upstream_bundlesize,
                                               local_bundlesize=self.bundlesize, poll=self.poll, drain=draining,
                                               zones=self.served,
                                               connected=self.zone_clients if len(self.zones) > 1 else None,
                                               retired=self.superseded)
            self.returner = UpstreamReturnerThread(queue=self.link, engine=engine,
                                                   bundlesize=self.upstream_bundlesize, bundlewait=self.bundlewait,
                                                   poll=self.poll, retention=self.retention)
            # Read the topology first, so a relay a topology sizes never launches its local fleet first.
            self.heartbeat.machine.fetch()
            self.cluster.start()
            self.wait_ready()
            log.info(f'Relay-local server ready on {self.server.queue.config.host}:{self.server.queue.config.port}')
            for fleet in self.fleets:
                fleet.start()
            self.heartbeat.start()
            self.returner.start()
            self.puller.start()
            self.wait_site()
            self.stop_site()
            self.returner.flush()
            self.wait_returner()
            if self.handback_due:
                self.hand_back()
            self.finish_upstream()
            self.link.stop(timeout=STOP_TIMEOUT)
            self.reap_site_clients()
        except Exception:
            self.abort()
            raise
        finally:
            self.release()
        log.debug('Done')

    def superseded(self: RelayThread) -> bool:
        """Whether the upstream server has said a newer relay at this site superseded this one."""
        return self.view.retired

    def zone_clients(self: RelayThread, zone: Optional[str]) -> int:
        """Site clients now serving `zone` (asking the relay-local server for its tasks, not yet gone)."""
        return self.server.queue.dispatcher.clients(zone_id(zone))

    def zone_launched(self: RelayThread, name: str) -> int:
        """Site clients launched for the zone called `name` and still running, as advertised upstream."""
        fleets = [self.cluster.autoscaler, *self.fleets]
        for zone, fleet in zip(self.zones, fleets):
            if ZoneRef.from_name(zone).name == name:
                return launched_now(fleet.machine)
        return 0

    def wait_link(self: RelayThread) -> bool:
        """Wait for the first upstream connection; false if the relay was stopped first.

        An upstream server that cannot be reached yet is tried again with backoff (the link logs it);
        one that refuses the relay for good (another protocol, a wrong key) ends it here, with the
        client's error, before the site is touched.
        """
        while not self.link.wait_up(timeout=STOP_CHECK_INTERVAL):
            if self.aborted:
                return False
        return not self.aborted

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
        The first check that finds the relay superseded retires it (see `retire`), which ends scheduling.
        """
        while True:
            self.cluster.join(timeout=STOP_CHECK_INTERVAL)
            if draining():
                log_drain()
            if not self.cluster.is_alive():
                log.warning('Relay-local server stopped')
                return
            for thread in (self.returner, self.heartbeat):
                if not thread.is_alive():
                    thread.join()  # Raises its failure, if any.
                    raise RelayPartEnded(f'Relay {thread.name} ended while the relay runs')
            self.check_site(stopping=self.retiring)
            if self.superseded() and not self.retiring:
                self.retire()
            elif self.handback_due:
                self.hand_back()
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
        as is a thread of the site ending out of turn (see `check_site`). A retired relay's hand-back
        the link lost is tried again at each check.
        """
        if not self.retiring:  # A retired relay stopped them already.
            self.cluster.autoscaler.stop()
            for fleet in self.fleets:
                fleet.stop()
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
            if self.handback_due:
                self.hand_back()
        self.cluster.join()  # Raises the failure that ended the site, if any.

    def retire(self: RelayThread) -> None:
        """End pulling, launching and scheduling at once, and hand back upstream what no site client has received.

        Called once, the first time the relay finds itself superseded at its site. In order: every
        zone's fleet stops launching; the puller stops (a request already sent is inserted and
        confirmed first); the relay-local scheduler stops, so nothing more is staged; and, once it has
        stopped, every task held that no site client has received is taken back here (see `withdraw`)
        and handed back upstream (see `hand_back`). A scheduler that has not stopped within
        `STOP_TIMEOUT` leaves the hand-back due, tried again at each check once it has. A task a site
        client has received is its to run: it runs once there, and its result goes back as any other.
        The site clients are ended by nothing here: once scheduling is over the relay finishes as it
        does at the end of any run.
        """
        self.retiring = True
        log.debug('Retiring - stopping site client launches, the upstream puller and the relay-local scheduler')
        for autoscaler in (self.cluster.autoscaler, *self.fleets):
            if autoscaler.ident is not None:
                autoscaler.stop()
        self.puller.stop(wait=True, timeout=STOP_TIMEOUT)
        if self.puller.is_alive():
            log.warning(f'Upstream puller did not stop within {STOP_TIMEOUT} seconds - handing back what is held now')
        scheduler = self.server.scheduler
        if scheduler is not None and scheduler.is_alive():
            scheduler.stop(wait=True, timeout=STOP_TIMEOUT)
            if scheduler.is_alive():
                log.warning(f'Relay-local scheduler did not stop within {STOP_TIMEOUT} seconds - '
                            f'handing back once it has')
        self.handback_due = True
        self.hand_back()

    def withdraw(self: RelayThread) -> bool:
        """Take back every task the relay-local scheduler holds for its site clients; false while it still runs.

        Once the scheduler has stopped, nothing more is staged and nothing it holds can reach a site
        client: the tasks staged and not yet taken are taken back, as the site server does at
        shutdown, and so is the chunk it had loaded and not yet staged when it stopped (marked
        scheduled here, but in no pool). Each is then unscheduled here, as a task no site client has
        received. Done once; a scheduler still running is left alone, and nothing is taken back yet.
        """
        if self.withdrawn:
            return True
        scheduler = self.server.scheduler
        if scheduler is not None and scheduler.is_alive():
            return False
        self.server.revert_staged()
        if scheduler is not None and (ids := [inspect(task).identity[0]
                                              for _, tasks in scheduler.machine.loaded for task in tasks]):
            reverted = Task.revert_staged(ids)
            log.debug(f'Reverted {reverted} tasks loaded for site clients but not staged when scheduling stopped')
        self.withdrawn = True
        return True

    def hand_back(self: RelayThread) -> None:
        """Hand back upstream every task held that no site client has received, and delete it here once acknowledged.

        Nothing is handed back until the relay-local scheduler has stopped and what it held for the
        site is taken back (see `withdraw`): until then the hand-back stays due. Each zone identity
        hands back its own tasks, by id with the schedule time it was sent under, at most
        `MAX_HANDBACK_TASKS` at once; once the upstream server acknowledges them they are deleted from
        the relay database, so the relay holds them no more. A hand-back the link loses stays due, and
        is tried again (at each check while the site finishes, and once more before the registration
        ends). An upstream server that refuses it (one without the call, say) leaves the tasks held:
        they are then left to its eviction of the relay, as before.
        """
        if not self.withdraw():
            log.debug('Relay-local scheduler still running - hand-back waits for it to stop')
            return
        try:
            held = store.unstarted(engine)
        except Exception as error:
            log.warning(f'Could not read the tasks to hand back ({error.__class__.__name__}: {error})')
            return
        by_identity: Dict[str, List[Tuple[str, str]]] = {}
        for task_id, identity, schedule_time in held:
            by_identity.setdefault(identity, []).append((task_id, schedule_time))
        for identity, tasks in by_identity.items():
            for start in range(0, len(tasks), MAX_HANDBACK_TASKS):
                chunk = tasks[start:start + MAX_HANDBACK_TASKS]
                text = handback_text(identity, chunk)

                def call(client: Any, text: str = text) -> Optional[str]:
                    method = getattr(getattr(client, 'dispatcher', None), 'handback', None)
                    return None if method is None else method(text)

                try:
                    reply = self.link.call('handback', call)
                except LinkDown:
                    log.debug(f'Hand-back of {len(chunk)} tasks lost with the link ({identity}) - trying again')
                    return
                except Exception as error:
                    self.refuse_hand_back(len(held), f'{error.__class__.__name__}: {error}')
                    return
                if reply is None:
                    self.refuse_hand_back(len(held), 'the upstream server takes none')
                    return
                try:
                    read_handback_reply(reply)
                except ValueError as error:
                    self.refuse_hand_back(len(held), str(error))
                    return
                dropped = store.drop_handed_back(engine, [task_id for task_id, _ in chunk])
                log.info(f'Handed back {dropped} tasks no site client started ({identity})')
        self.handback_due = False

    def refuse_hand_back(self: RelayThread, count: int, reason: str) -> None:
        """Give up handing back: the tasks stay held, for the upstream server's eviction of the relay to put back."""
        self.handback_due = False
        log.warning(f'Could not hand back {count} tasks no site client started ({reason}) - '
                    f'the upstream server will put them back when it evicts this relay')

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
        the site is told to stop (`stopping`), the autoscalers that keep the site clients launched (one
        per zone) must be running too.
        """
        server = self.server
        chain = [thread for thread in (server.scheduler, server.heartmonitor, server.receiver, server.confirm, server)
                 if thread is not None]
        autoscalers = (self.cluster.autoscaler, *self.fleets)
        for thread in (*chain, *autoscalers):
            if thread.ident is not None and not thread.is_alive():
                thread.join()  # Raises its failure, if any.
        for earlier, later in zip(chain, chain[1:]):
            # The later one is read first: once it has ended on a clean stop, the earlier one has too.
            if later.ident is not None and not later.is_alive() and earlier.is_alive():
                raise RelayPartEnded(f'Relay-local {later.name} ended while {earlier.name} still runs')
        for autoscaler in autoscalers:
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
        once it has stopped is it known what the relay holds. The final heartbeat goes upstream, under
        every identity, only when every received task's result has been handed over. Otherwise the
        identities are left to the upstream server's eviction, which puts the tasks each still holds
        back in the queue for other clients.
        """
        identities = ', '.join(self.identities)
        self.puller.stop(wait=True, timeout=STOP_TIMEOUT)
        if self.puller.is_alive():
            log.warning(f'Upstream puller did not stop within {STOP_TIMEOUT} seconds - not finishing upstream '
                        f'(the upstream server will evict {identities} and revert what it holds)')
            self.heartbeat.stop(wait=True, timeout=STOP_TIMEOUT)
            return
        held = store.count_held(engine)
        if held:
            log.warning(f'Holding {held} tasks with no result returned upstream - not finishing upstream '
                        f'(the upstream server will evict {identities} and revert them)')
            self.heartbeat.stop(wait=True, timeout=STOP_TIMEOUT)
        else:
            self.heartbeat.signal_finished()
            self.heartbeat.join(timeout=STOP_TIMEOUT)

    def reap_site_clients(self: RelayThread) -> None:
        """After a clean drain, end any site client still running after the grace period (one that never connected)."""
        if ended := end_site_clients(self.cluster, grace=CLIENT_GRACE, patience=CLIENT_GRACE, fleets=self.fleets):
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
        for fleet in self.fleets:
            self.stop_quietly(fleet)
        if ended := end_site_clients(self.cluster, grace=CLIENT_GRACE, fleets=self.fleets):
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
        if self.link is not None:
            self.link.stop(timeout=STOP_TIMEOUT)

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
        SIGINT or SIGTERM (by `RelayApp.run`). Only a relay killed outright leaves the copy behind:
        by SIGKILL, or by a second SIGTERM while it ends (which ends the process at once).
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
  hs relay [-h] [-H ADDR] [-p NUM] [--no-tls] [--zone NAME ...] [-N NUM] [--launcher CMD]
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
  Site clients receive the relay's own site key in their environment, never on their
  command line, so the launcher must pass the environment through.

  A relay may serve several zones (--zone given more than once, or a list in [relay] zone):
  upstream it is one client per zone, and at the site it keeps one fleet of clients per zone,
  each asking upstream only for what its own fleet has room for. A zone's own launcher,
  launcher arguments, size or client bounds can be set in [relay.zone.NAME]. Every launch
  names its zone to the launcher: {{zone}} in the launcher or its arguments, and the
  HYPERSHELL_ZONE variable.

  Each zone advertises its client bounds upstream ([relay] or [relay.zone.NAME] min, max
  and init; min defaults to 0, init to min, max to the size). An upstream server
  autoscaling with relay sites (hs server --autoscaling) plans each zone's clients over
  the relays that serve it within those bounds, and the relay launches what it is asked
  for. It never ends a client to shrink: clients exit when idle ([relay] timeout), and
  the relay stays connected with none, ready to grow. Against any other upstream server
  each zone keeps its own fleet, as before: init clients at start, and one launched again
  while fewer than min run (here min is at least 1, and each defaults to the size). A relay
  that a newer relay at its site supersedes asks for no more work, launches no client,
  hands back the tasks no client has started, lets its clients finish, and exits.

  If the upstream server cannot be reached, the site keeps running and results are kept in
  the relay database; the relay connects again with backoff, then returns them before it
  asks for more work.

  SIGUSR1 or SIGUSR2 drain the relay: it asks for no more work, finishes what it holds and
  returns the results. SIGINT or SIGTERM end it at once, with the site's clients; a second
  SIGTERM ends the process at once, and, like SIGKILL, leaves the site's copy of the
  configuration file behind. If the relay fails, it ends the site's clients and exits non-zero.

  The relay database is always a private SQLite file, whatever database is configured,
  and the configured database is never opened. One relay runs per process, and a second
  relay refuses to start on the same relay database.

Options:
  -H, --upstream-host ADDR  Hostname of upstream server (default: {DEFAULT_UPSTREAM_HOST}).
  -p, --upstream-port NUM   Port of upstream server (default: {DEFAULT_UPSTREAM_PORT}).
      --no-tls              Disable TLS to upstream server (not recommended).
      --zone          NAME  Zone this relay serves; repeat for more (default: the default zone).
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

    zone_option: Optional[List[str]] = None
    interface.add_argument('--zone', action='append', default=zone_option, dest='zone_option')

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
                             zones=self.zones,
                             zone_settings=self.zone_settings,
                             site=config.relay.get('site', None),
                             upstream_bundlesize=config.relay.upstream.bundlesize,
                             heartrate=config.relay.upstream.heartrate,
                             size=self.size,
                             min_size=self.relay_bounds['min'],
                             max_size=self.relay_bounds['max'],
                             init_size=self.relay_bounds['init'],
                             launcher=self.launcher,
                             launcher_args=[self.launcher_args] if self.launcher_args else None,
                             num_threads=config.relay.num_threads,
                             template=config.relay.template,
                             client_timeout=config.relay.timeout or None,
                             retention=config.relay.retention,
                             deadline=config.relay.upstream.deadline,
                             backoff=config.relay.upstream.backoff,
                             evict_hint=config.relay.upstream.evict_hint,
                             evict=config.server.evict)
        try:
            thread.start()
            join_waking(thread)
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
        self.zone_settings  # noqa: checked when first read
        self.check_bounds()

    @functools.cached_property
    def relay_bounds(self: RelayApp) -> Dict[str, Optional[int]]:
        """The client bounds `[relay]` gives every zone (`min`, `max`, `init`; None where not given)."""
        given: Dict[str, Optional[int]] = {}
        for key in BOUND_OPTIONS:
            value = config.relay.get(key, None)
            if value in (None, PARAM_UNSET) or value == PARAM_UNSET:
                given[key] = None
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ArgumentError(f'relay.{key} must be a whole number, at least 0 (given {value!r})')
            else:
                given[key] = value
        return given

    def check_bounds(self: RelayApp) -> None:
        """Fail client bounds of a served zone that are not 0 <= min <= init <= max with max >= 1."""
        relay_wide = {'size': self.size, **self.relay_bounds}
        for zone in self.zones:
            settings = {'size': self.size, **self.zone_settings.get(zone, {})}
            try:
                zone_bounds(settings, relay_wide)
            except ValueError as error:
                raise ArgumentError(f'Invalid client bounds of zone {zone or DEFAULT_ZONE_NAME} ({error}; '
                                    f'max defaults to the size)') from error

    @functools.cached_property
    def zone_plan(self: RelayApp) -> Tuple[List[Optional[str]], Dict[Optional[str], Dict[str, Any]]]:
        """The zones this relay serves, from `--zone` or `[relay] zone`, and the settings each gives itself.

        `--zone` names the zones in place of the configuration; a zone named there still takes its
        settings from `[relay.zone.<name>]`. None is the default zone.
        """
        try:
            zones, settings = zone_options(config.relay.get('zone', None))
            if self.zone_option:
                zones, _ = zone_options(list(self.zone_option))
        except ValueError as error:
            raise ArgumentError(f'Invalid relay zone ({error})') from error
        return zones, {zone: settings.get(zone, {}) for zone in zones}

    @property
    def zones(self: RelayApp) -> List[Optional[str]]:
        """Names of the zones this relay serves, in order (None is the default zone)."""
        return self.zone_plan[0]

    @property
    def zone(self: RelayApp) -> Optional[str]:
        """Name of the first zone this relay serves, the one whose identity holds the roster."""
        return self.zones[0]

    @functools.cached_property
    def zone_settings(self: RelayApp) -> Dict[Optional[str], Dict[str, Any]]:
        """The fleet settings each served zone gives itself, checked and in the relay's own form."""
        checked: Dict[Optional[str], Dict[str, Any]] = {}
        for zone, given in self.zone_plan[1].items():
            name, table = zone or DEFAULT_ZONE_NAME, {}
            if 'size' in given:
                size = given['size']
                if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                    raise ArgumentError(f'Site size of zone {name} must be a whole number, at least 1 '
                                        f'(given {size!r})')
                table['size'] = size
            for key in BOUND_OPTIONS:
                if key in given:
                    value = given[key]
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise ArgumentError(f'relay.zone.{name}.{key} must be a whole number, at least 0 '
                                            f'(given {value!r})')
                    table[key] = value
            for key in ('launcher', 'launcher_args'):
                if key in given:
                    if not isinstance(given[key], str):
                        raise ArgumentError(f'relay.zone.{name}.{key} must be text (given {given[key]!r})')
                    try:
                        shlex.split(given[key])
                    except ValueError as error:
                        raise ArgumentError(f'Invalid launcher of zone {name} ({error})') from error
            if 'launcher' in given:
                table['launcher'] = given['launcher']
            if 'launcher_args' in given:
                table['launcher_args'] = [given['launcher_args']] if given['launcher_args'] else None
            checked[zone] = table
        return checked

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
