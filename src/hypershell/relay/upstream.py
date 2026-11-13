# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The relay's upstream side: an ordinary client of the upstream server, once per zone it serves.

Each zone a relay serves has its own upstream identity, derived from the relay instance and the zone
name, so the upstream server holds the relay's demand for each zone as it would any client's. The
puller asks for work under that identity with the stock work-request text, never for more than its
site can take: while the tasks it holds and has not finished reach the bound (its connected site
clients times the relay-local bundle size) it does not ask. A received bundle is inserted into the
relay database in one transaction and confirmed upstream only after that transaction commits. The
returner moves completed site rows into the relay's outbox and hands them back upstream, as a client
returns its results, each matched upstream against the assignment it answers; a result is marked
returned only once its put has returned. The heartbeat keeps each identity registered upstream, and
carries the relay's site and its roster (the site clients connected to the relay-local server), so the
upstream server lists the site clients, attributed to the site, as they come and go. Each zone
identity's beat also advertises its zone, that zone's client bounds, the clients launched in it and
the relay's instance key; after each beat the heartbeat reads the site topology the upstream server
publishes, if any, for the relay's fleets to follow.

Example:
    >>> from hypershell.relay.upstream import upstream_identity
    >>> identity = upstream_identity('gpu')
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Final

# Standard libs
import json
import time
import functools
import threading
from enum import Enum
from datetime import datetime, timedelta
from uuid import UUID, uuid5
from queue import Full as QueueFull

# External libs
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

# Internal libs
from hypershell.core.config import config, PARAM_UNSET, resolve_auth
from hypershell.core.fsm import State, StateMachine
from hypershell.core.heartbeat import Heartbeat, ClientState, RosterEntry
from hypershell.core.topology import Bounds, request_text as topology_request, read_topology
from hypershell.core.logging import HOSTNAME, INSTANCE, Logger
from hypershell.core.queue import QueueConfig, QueueVersionError, PROTOCOL, DEFAULT_REMOTE_TIMEOUT
from hypershell.core.signal import check_signal, SIGUSR1, SIGUSR2
from hypershell.core.thread import Thread
from hypershell.core.tls import from_namespace as tls_from_namespace
from hypershell.core.dispatch import build_version
from hypershell.core.reconnect import LinkDown, as_link
from hypershell.client import ClientInfo
from hypershell.data.model import DEFAULT_ZONE_NAME, zone_id
from hypershell.relay import store

# Public interface
__all__ = ['upstream_identity', 'served_zones', 'upstream_config', 'upstream_key', 'relay_site', 'request_text', 'read_reply',
           'RelayInsertError', 'drain_signalled',
           'PullerState', 'UpstreamPuller', 'UpstreamPullerThread',
           'ReturnerState', 'UpstreamReturner', 'UpstreamReturnerThread',
           'HeartbeatState', 'UpstreamHeartbeat', 'UpstreamHeartbeatThread',
           'DEFAULT_UPSTREAM_BUNDLESIZE', 'DEFAULT_UPSTREAM_HEARTRATE', 'DEFAULT_REQUEST_WAIT', 'ZONE_REQUEST_WAIT',
           'INSERT_ATTEMPTS',
           'DEFAULT_RETENTION', 'DEFAULT_RETURN_BUNDLEWAIT']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_UPSTREAM_BUNDLESIZE: Final[int] = config.relay.upstream.bundlesize
"""Default bundle size the relay advertises upstream."""

DEFAULT_UPSTREAM_HEARTRATE: Final[int] = config.relay.upstream.heartrate
"""Default seconds between upstream heartbeats."""

DEFAULT_REQUEST_WAIT: Final[float] = DEFAULT_REMOTE_TIMEOUT
"""Seconds a work request may wait upstream for tasks (the upstream server bounds it)."""

ZONE_REQUEST_WAIT: Final[float] = 1.0
"""Seconds a work request may wait upstream when the relay serves several zones, so it turns to the next soon."""

INSERT_ATTEMPTS: Final[int] = 3
"""Times the puller tries to insert a received bundle before it gives up (never confirming it)."""

HEARTBEAT_WAIT_STEP: float = 1.0
"""Longest single wait between heartbeats, in seconds, so the machine re-enters often."""

DEFAULT_RETENTION: Final[int] = config.relay.retention
"""Default seconds a returned result is kept in the relay database before it is pruned."""

DEFAULT_RETURN_BUNDLEWAIT: Final[int] = config.relay.bundlewait
"""Default seconds the relay waits before returning results regardless of size."""

PRUNE_INTERVAL: float = 60.0
"""Longest time in seconds between prunes of returned results (shorter with a shorter retention)."""

BACKLOG_FACTOR: Final[int] = 2
"""The backlog bound is this many bundles per connected site client: one running, one ready behind it."""

GATE_WAIT: float = 0.1
"""Longest single wait at the puller's gate in seconds, so a site that runs short is refilled promptly."""

LINK_WAIT: float = 1.0
"""Longest single wait for the upstream link in seconds, so a waiting machine re-enters often."""

STAGE_BEAT: Final[str] = 'beat'
"""Link stage passed by the first heartbeat on a new connection."""

STAGE_DRAIN: Final[str] = 'drain'
"""Link stage passed once the outbox holds nothing pending on a new connection."""

LINK_STAGES: Final[Tuple[str, ...]] = (STAGE_BEAT, STAGE_DRAIN)
"""The stages of the relay's upstream link, in the order they are passed after a reconnection."""

CONFIRM_STAGES: Final[Tuple[str, ...]] = (STAGE_BEAT, )
"""Stages passed before a lost confirmation is sent again (the identity is registered again first)."""

RETURN_STAGES: Final[Tuple[str, ...]] = (STAGE_BEAT, )
"""Stages passed before results are handed back."""

PULL_STAGES: Final[Tuple[str, ...]] = (STAGE_BEAT, STAGE_DRAIN)
"""Stages passed before work is requested (held results go back first)."""


def upstream_identity(zone: Optional[str], instance: str = INSTANCE) -> str:
    """The upstream client id of a relay `instance` for the zone named `zone` (None is the default zone)."""
    return str(uuid5(UUID(instance), zone or DEFAULT_ZONE_NAME))


def served_zones(zones: Sequence[Optional[str]], instance: str = INSTANCE) -> List[Tuple[str, Optional[str]]]:
    """The (identity, zone) of each zone in `zones`, in order: the upstream clients a relay `instance` is."""
    return [(upstream_identity(zone, instance), zone) for zone in zones]


def upstream_config(host: Optional[str] = None, port: Optional[int] = None, no_tls: bool = False) -> QueueConfig:
    """Connection details for the upstream server, from `[relay.upstream]` (never from `[server]`).

    `host` and `port` override the configured values; `no_tls` turns TLS off on this connection only.
    """
    section = config.relay.upstream
    tls = None if no_tls else tls_from_namespace(section.get('tls'))
    return QueueConfig(host=host or section.host, port=int(port or section.port), auth=upstream_key(), tls=tls)


def upstream_key(base: Any = None) -> str:
    """The upstream key: `[relay.upstream] auth`, `auth_env` or `auth_eval` from the highest layer setting one.

    Each configuration layer (built-in defaults, then system, user and local files, then the
    environment) is looked at from the top, and the first that sets any form of the key decides it,
    so a file's `auth_env` takes precedence over the built-in `auth` rather than colliding with it.
    Two forms in any one layer, an unset variable, or a failed command is a configuration error
    naming the key. With none set anywhere, the built-in default key is used.
    """
    return resolve_auth('relay', 'upstream', base=base)


def relay_site(site: Optional[str] = None) -> str:
    """The site a relay names upstream: `site` (from `[relay] site`) when set, else the relay's host name."""
    return HOSTNAME if site in (None, '', PARAM_UNSET) else str(site)


def drain_signalled() -> bool:
    """True once the process has received a drain signal (SIGUSR1 or SIGUSR2) through the stock handler."""
    return check_signal() in (SIGUSR1, SIGUSR2)


def request_text(identity: str, zone: Optional[str], size: int, wait: float) -> str:
    """The work request of `identity` for `zone`: the stock client request text."""
    return json.dumps({'protocol': PROTOCOL, 'client': identity, 'host': HOSTNAME, 'zone': zone,
                       'size': size, 'wait': wait})


def read_reply(text: str) -> Dict[str, Any]:
    """Decode a work-request reply; an error reply or another protocol raises the client's version error."""
    reply = json.loads(text)
    if 'error' in reply or reply.get('protocol') != PROTOCOL:
        raise QueueVersionError(f'Server refused the work request (server protocol {reply.get("protocol")}, '
                                f'client protocol {PROTOCOL}): {reply.get("message")} '
                                f'(server {reply.get("server_version")}, client {build_version()})')
    return reply


class RelayInsertError(RuntimeError):
    """A received bundle could not be inserted into the relay database; it was not confirmed."""


class PullerState(State, Enum):
    """Finite states of the upstream puller."""
    START = 0
    GATE = 1
    REQUEST = 2
    INSERT = 3
    CONFIRM = 4
    WAIT_LINK = 5
    FINAL = 6
    HALT = 7


class UpstreamPuller(StateMachine):
    """
    Request work upstream for each zone the relay serves, insert it durably, then confirm it.

    The gate holds while the tasks in the relay database not yet complete exceed the low-water mark,
    half the bound: the relay-local server's connected clients times `local_bundlesize`, times
    `BACKLOG_FACTOR`. At or below it the puller asks for the whole gap, at most `bundlesize`, so it
    asks seldom and in large bundles, and the site has a second round of work ready while the first
    runs. Every wait is finite and re-enters.

    A relay serving several zones (`zones`, each an upstream identity and its zone) is one upstream
    client per zone over the same link: the gate takes the zones in turn, each with its own bound
    and backlog (the site clients serving that zone, by `connected`, and the unfinished tasks of that
    zone), and asks for the first that has room, under that zone's identity; the insert and the
    confirmation that follow are that identity's too. Its requests wait upstream at most
    `ZONE_REQUEST_WAIT`, so one zone with nothing staged never holds up the others. A zone the
    upstream server answers with a shutdown is asked no more; pulling ends once every zone has been.
    The upstream server finishes for every zone at once, so once one zone has had a shutdown reply, a
    zone with no site client and nothing held ends too: it never asks, so it would never hear one. A
    relay with no site client in any zone never asks at all, and pulling ends only on a drain or halt.

    Every upstream call goes through the link (see `hypershell.core.reconnect`). While the link is down
    the puller waits for it (WAIT_LINK); pulling resumes once the link is back, has carried a heartbeat
    and has handed over what the outbox held (the link's `beat` and `drain` stages). A confirmation the
    link lost is sent again first, unless the outage outlasted the link's eviction hint: the upstream
    server has then likely evicted this identity and put those tasks back, perhaps for another client,
    and a late confirmation would claim them from it. The tasks still run here, and their results
    still land if the upstream server kept the assignment.

    Pulling ends at the gate: on a drain (`drain` returns true) or once told to halt. A request
    already sent is always seen through first: its reply is inserted and confirmed before the
    machine stops, so a bundle cut for the relay upstream is never dropped while the link holds.
    """

    queue: Any
    link: Any
    engine: Engine
    identity: str
    zone: Optional[str]
    zones: List[Tuple[str, Optional[str]]]
    connected: Optional[Callable[[Optional[str]], int]]
    finished: set
    turn: int
    bundlesize: int
    local_bundlesize: int
    poll: float
    wait_request: float
    server_id: str
    drain: Callable[[], bool]
    stopping: bool

    size: int
    bundle: List[Dict[str, Any]]
    task_ids: List[str]
    insert_failures: int
    unconfirmed: bool

    state = PullerState.START
    states = PullerState

    def __init__(self: UpstreamPuller, queue: Any, engine: Engine, identity: str, zone: Optional[str] = None,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, local_bundlesize: int = 1, poll: float = 1,
                 wait: float = DEFAULT_REQUEST_WAIT, server_id: str = INSTANCE,
                 drain: Callable[[], bool] = drain_signalled,
                 zones: Optional[Sequence[Tuple[str, Optional[str]]]] = None,
                 connected: Optional[Callable[[Optional[str]], int]] = None) -> None:
        """Initialize with the upstream link (or a connected client), the relay database and the zone identity.

        `zones`, if given, are every (identity, zone) served, in place of `identity` and `zone`; with
        more than one, `connected` must count the site clients serving a zone.
        """
        self.queue = queue
        self.link = as_link(queue)
        self.engine = engine
        self.zones = list(zones) if zones else [(identity, zone)]
        if len(self.zones) > 1 and connected is None:
            raise ValueError('A puller for several zones needs the site clients of each zone counted')
        self.identity, self.zone = self.zones[0]
        self.connected = connected
        self.finished = set()
        self.turn = 0
        self.bundlesize = max(1, int(bundlesize))
        self.local_bundlesize = max(1, int(local_bundlesize))
        self.poll = poll
        self.wait_request = wait if len(self.zones) == 1 else min(wait, ZONE_REQUEST_WAIT)
        self.server_id = server_id
        self.drain = drain
        self.stopping = False
        self.size = 0
        self.bundle = []
        self.task_ids = []
        self.insert_failures = 0
        self.unconfirmed = False
        self.wake = threading.Event()
        self.retry = threading.Event()  # Never set: a retry waits its full interval, even when stopping.

    @functools.cached_property
    def actions(self: UpstreamPuller) -> Dict[PullerState, Callable[[], PullerState]]:
        return {
            PullerState.START: self.start,
            PullerState.GATE: self.gate,
            PullerState.REQUEST: self.request,
            PullerState.INSERT: self.insert,
            PullerState.CONFIRM: self.confirm,
            PullerState.WAIT_LINK: self.wait_link,
            PullerState.FINAL: self.finalize,
        }

    def start(self: UpstreamPuller) -> PullerState:
        """Jump to GATE state."""
        log.debug(f'Started (puller: {self.identity})')
        return PullerState.GATE

    def bound(self: UpstreamPuller, zone: Optional[str] = None) -> int:
        """Most unfinished tasks the relay holds for `zone`: its site clients times the local bundle size, twice over.

        A relay serving one zone counts every site client connected to the relay-local server.
        """
        if self.connected is None:
            connected = store.count_connected(self.engine, self.server_id)
        else:
            connected = self.connected(zone)
        return connected * self.local_bundlesize * BACKLOG_FACTOR

    def backlog(self: UpstreamPuller, zone: Optional[str] = None) -> int:
        """Tasks in the relay database not yet complete (of `zone` only, when the relay serves several)."""
        if len(self.zones) == 1:
            return store.count_backlog(self.engine)
        return store.count_backlog(self.engine, zone=zone_id(zone))

    def gate(self: UpstreamPuller) -> PullerState:
        """Ask for work only at or below the low-water mark, and only on a ready link; end on a drain or halt.

        Zones are taken in turn, from the one after the zone last asked for: the first with room is asked.
        """
        if self.stopping:  # atomic
            return PullerState.FINAL
        if self.drain():
            log.warning('Drain requested - no more upstream requests')
            return PullerState.FINAL
        if self.finished and self.finish_idle():
            log.info('Upstream server is shutting down - no more upstream requests')
            return PullerState.FINAL
        if not self.link.ready(PULL_STAGES):
            return PullerState.WAIT_LINK
        count = len(self.zones)
        for step in range(count):
            index = (self.turn + step) % count
            identity, zone = self.zones[index]
            if identity in self.finished:
                continue
            bound = self.bound(zone)
            backlog = self.backlog(zone)
            if bound == 0 or backlog > bound // 2:
                continue
            self.turn = (index + 1) % count
            self.identity, self.zone = identity, zone
            self.size = min(bound - backlog, self.bundlesize)
            return PullerState.REQUEST
        self.wake.wait(min(self.poll, GATE_WAIT))
        return PullerState.GATE

    def finish_idle(self: UpstreamPuller) -> bool:
        """End every zone with no site client and nothing held, the upstream server being finished; true once all are.

        Only called once some zone has had a shutdown reply. A zone with site clients, or with tasks
        still held, asks for itself and ends on its own reply.
        """
        for identity, zone in self.zones:
            if identity not in self.finished and self.bound(zone) == 0 and self.backlog(zone) == 0:
                self.finished.add(identity)
                log.info(f'Upstream server is shutting down - no site client for zone '
                         f'{zone or DEFAULT_ZONE_NAME}, no more requests for it ({identity})')
        return len(self.finished) == len(self.zones)

    def request(self: UpstreamPuller) -> PullerState:
        """Request up to `size` tasks under the zone identity (the upstream server bounds the wait)."""
        if self.stopping:  # atomic; once past here the request is sent and its reply seen through
            return PullerState.FINAL
        text = request_text(self.identity, self.zone, self.size, self.wait_request)
        try:
            answer = self.link.call('puller', lambda client: client.dispatcher.request(text))
        except LinkDown:
            log.debug(f'Upstream request lost with the link ({self.identity})')
            return PullerState.WAIT_LINK
        reply = read_reply(answer)
        if reply['tasks']:
            self.bundle = reply['tasks']
            log.debug(f'Received {len(self.bundle)} tasks ({self.identity})')
            return PullerState.INSERT
        if reply.get('shutdown'):
            self.finished.add(self.identity)
            if len(self.finished) < len(self.zones):
                log.debug(f'Upstream server is shutting down - no more requests for zone '
                          f'{self.zone or DEFAULT_ZONE_NAME} ({self.identity})')
                return PullerState.GATE
            log.info('Upstream server is shutting down - no more upstream requests')
            return PullerState.FINAL
        return PullerState.GATE

    def insert(self: UpstreamPuller) -> PullerState:
        """Insert the received bundle and its upstream stamps in one transaction; never confirm a failure."""
        try:
            self.task_ids = store.insert_received(self.engine, self.bundle, identity=self.identity)
        except SQLAlchemyError as error:
            self.insert_failures += 1
            if self.insert_failures >= INSERT_ATTEMPTS:
                raise RelayInsertError(f'Could not insert {len(self.bundle)} received tasks into relay database '
                                       f'after {self.insert_failures} attempts ({error})') from error
            log.error(f'Failed to insert {len(self.bundle)} received tasks - trying again ({error})')
            self.retry.wait(self.poll)
            return PullerState.INSERT
        self.insert_failures = 0
        self.bundle = []
        return PullerState.CONFIRM

    def confirm(self: UpstreamPuller) -> PullerState:
        """Confirm the inserted tasks upstream under the zone identity."""
        info = ClientInfo(client_id=self.identity, client_host=HOSTNAME, task_ids=self.task_ids).pack()
        try:
            self.link.call('puller', lambda client: client.confirmed.put(info, timeout=DEFAULT_REMOTE_TIMEOUT))
        except QueueFull:
            return PullerState.CONFIRM
        except LinkDown:
            self.unconfirmed = True
            return PullerState.WAIT_LINK
        log.debug(f'Confirmed {len(self.task_ids)} tasks ({self.identity})')
        self.unconfirmed = False
        self.task_ids = []
        return PullerState.GATE

    def wait_link(self: UpstreamPuller) -> PullerState:
        """Wait for the link; then confirm what the link lost (unless the outage was too long), or go to the gate."""
        if self.stopping:  # atomic
            if self.unconfirmed:
                log.warning(f'Not confirming {len(self.task_ids)} tasks upstream (link down while stopping); '
                            f'they still run here')
            return PullerState.FINAL
        if not self.link.wait_ready(CONFIRM_STAGES if self.unconfirmed else PULL_STAGES, timeout=LINK_WAIT):
            return PullerState.WAIT_LINK
        if not self.unconfirmed:
            return PullerState.GATE
        if self.link.outage < self.link.evict_hint:
            log.debug(f'Confirming {len(self.task_ids)} tasks again after the link came back ({self.identity})')
            return PullerState.CONFIRM
        log.warning(f'Not confirming {len(self.task_ids)} tasks upstream after an outage of '
                    f'{self.link.outage:.0f} seconds (past {self.link.evict_hint:g}): the upstream server has '
                    f'likely put them back; they still run here')
        self.unconfirmed = False
        self.task_ids = []
        return PullerState.GATE

    def finalize(self: UpstreamPuller) -> PullerState:
        """Stop pulling."""
        log.debug(f'Done (puller: {self.identity})')
        return PullerState.HALT

    def halt(self: UpstreamPuller) -> None:
        """Stop at the gate, ending any wait there at once; a request already sent is inserted and confirmed first."""
        self.stopping = True
        self.wake.set()


class UpstreamPullerThread(Thread):
    """Run the upstream puller within a dedicated thread."""

    def __init__(self: UpstreamPullerThread, queue: Any, engine: Engine, identity: str, zone: Optional[str] = None,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, local_bundlesize: int = 1, poll: float = 1,
                 wait: float = DEFAULT_REQUEST_WAIT, server_id: str = INSTANCE,
                 drain: Callable[[], bool] = drain_signalled,
                 zones: Optional[Sequence[Tuple[str, Optional[str]]]] = None,
                 connected: Optional[Callable[[Optional[str]], int]] = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-relay-puller')
        self.machine = UpstreamPuller(queue=queue, engine=engine, identity=identity, zone=zone,
                                      bundlesize=bundlesize, local_bundlesize=local_bundlesize, poll=poll,
                                      wait=wait, server_id=server_id, drain=drain, zones=zones,
                                      connected=connected)

    def run_with_exceptions(self: UpstreamPullerThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: UpstreamPullerThread, wait: bool = False, timeout: Optional[float] = None) -> None:
        """Stop machine at its gate (a request already sent is inserted and confirmed first)."""
        log.debug('Stopping (puller)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class ReturnerState(State, Enum):
    """Finite states of the upstream returner."""
    START = 0
    COLLECT = 1
    SEND = 2
    MARK = 3
    WAIT_LINK = 4
    FINAL = 5
    HALT = 6


class UpstreamReturner(StateMachine):
    """
    Hand completed site results back upstream through the relay's durable outbox.

    Each cycle moves completed rows into the outbox in one transaction, then sends the oldest pending
    results as one bundle once there are `bundlesize` of them or `bundlewait` seconds have passed since
    the last send. A bundle is marked returned only after its put returns; a put that does not return
    leaves it pending, to be sent again. Returned results are pruned after `retention` seconds. Told to
    flush, it sends whatever it holds at once and ends once nothing is left.

    While the upstream link is down, completed rows still move into the outbox, which keeps them
    durable, and nothing is sent (WAIT_LINK). Once the link is back and has carried a heartbeat, the
    outbox goes back oldest first, at once rather than at the bundle wait, and the link's `drain`
    stage is passed when nothing is left pending, which lets the puller ask for work again.
    """

    queue: Any
    link: Any
    engine: Engine
    bundlesize: int
    bundlewait: float
    poll: float
    retention: float

    sent: List[Tuple[str, str]]
    previous_send: float
    previous_prune: float
    flushing: bool

    state = ReturnerState.START
    states = ReturnerState

    def __init__(self: UpstreamReturner, queue: Any, engine: Engine,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, bundlewait: float = DEFAULT_RETURN_BUNDLEWAIT,
                 poll: float = 1, retention: float = DEFAULT_RETENTION) -> None:
        """Initialize with the upstream link (or a connected client) and the relay database."""
        self.queue = queue
        self.link = as_link(queue)
        self.engine = engine
        self.bundlesize = max(1, int(bundlesize))
        self.bundlewait = bundlewait
        self.poll = poll
        self.retention = retention
        self.sent = []
        self.previous_send = time.monotonic()
        self.previous_prune = time.monotonic()
        self.flushing = False
        self.epoch = 0
        self.wake = threading.Event()

    @functools.cached_property
    def actions(self: UpstreamReturner) -> Dict[ReturnerState, Callable[[], ReturnerState]]:
        return {
            ReturnerState.START: self.start,
            ReturnerState.COLLECT: self.collect,
            ReturnerState.SEND: self.send,
            ReturnerState.MARK: self.mark,
            ReturnerState.WAIT_LINK: self.wait_link,
            ReturnerState.FINAL: self.finalize,
        }

    def start(self: UpstreamReturner) -> ReturnerState:
        """Jump to COLLECT state."""
        log.debug('Started (returner)')
        return ReturnerState.COLLECT

    def collect(self: UpstreamReturner) -> ReturnerState:
        """Move completed rows into the outbox, then send a bundle when one is due."""
        flushing = self.flushing  # atomic
        try:
            if moved := store.collect_results(self.engine):
                log.debug(f'Holding {moved} completed tasks for return')
            self.prune()
            pending = store.pending_results(self.engine, limit=self.bundlesize)
        except SQLAlchemyError as error:
            log.error(f'Failed to collect completed tasks - trying again ({error})')
            self.wake.wait(self.poll)
            return ReturnerState.COLLECT
        if not pending:
            self.link.passed(STAGE_DRAIN)
            if flushing:
                return ReturnerState.FINAL
            self.wake.wait(self.poll)
            return ReturnerState.COLLECT
        if not self.link.ready(RETURN_STAGES):
            return ReturnerState.WAIT_LINK
        waited = time.monotonic() - self.previous_send
        catching_up = not self.link.is_passed(STAGE_DRAIN)
        if flushing or catching_up or len(pending) >= self.bundlesize or waited >= self.bundlewait:
            self.sent = pending
            return ReturnerState.SEND
        self.wake.wait(min(self.poll, self.bundlewait - waited))
        return ReturnerState.COLLECT

    def wait_link(self: UpstreamReturner) -> ReturnerState:
        """Wait for the link to carry results again, then collect (completed rows keep moving to the outbox)."""
        self.link.wait_ready(RETURN_STAGES, timeout=min(LINK_WAIT, self.poll))
        return ReturnerState.COLLECT

    def prune(self: UpstreamReturner) -> None:
        """Delete returned results older than the retention, at most once per prune interval."""
        if time.monotonic() - self.previous_prune < min(PRUNE_INTERVAL, self.retention):
            return
        self.previous_prune = time.monotonic()
        before = datetime.now().astimezone() - timedelta(seconds=self.retention)
        if pruned := store.prune_returned(self.engine, before=before):
            log.debug(f'Pruned {pruned} returned results')

    def send(self: UpstreamReturner) -> ReturnerState:
        """Put the bundle upstream, as a client returns its results."""
        bundle = ('[' + ', '.join(record for _, record in self.sent) + ']').encode('utf-8')
        self.epoch = self.link.epoch
        try:
            self.link.call('returner', lambda client: client.completed.put(bundle, timeout=DEFAULT_REMOTE_TIMEOUT))
        except QueueFull:
            return ReturnerState.SEND
        except LinkDown:
            self.sent = []  # Still pending: sent again once the link is back.
            return ReturnerState.WAIT_LINK
        self.previous_send = time.monotonic()
        return ReturnerState.MARK

    def mark(self: UpstreamReturner) -> ReturnerState:
        """Mark the bundle just put returned."""
        try:
            marked = store.mark_returned(self.engine, self.sent)
        except SQLAlchemyError as error:
            log.error(f'Failed to mark {len(self.sent)} results returned - trying again ({error})')
            self.wake.wait(self.poll)
            return ReturnerState.MARK
        log.debug(f'Returned {len(self.sent)} results upstream' +
                  ('' if marked == len(self.sent) else f' ({len(self.sent) - marked} superseded meanwhile)'))
        if len(self.sent) < self.bundlesize:  # What was pending fit in one bundle: the outbox has caught up.
            self.link.passed(STAGE_DRAIN, self.epoch)
        self.sent = []
        return ReturnerState.COLLECT

    def finalize(self: UpstreamReturner) -> ReturnerState:
        """Stop returning."""
        log.debug('Done (returner)')
        return ReturnerState.HALT

    def flush(self: UpstreamReturner) -> None:
        """Send whatever is held at once, then stop once nothing is left."""
        self.flushing = True
        self.wake.set()

    def halt(self: UpstreamReturner) -> None:
        """Halt, ending any wait at once."""
        super().halt()
        self.wake.set()


class UpstreamReturnerThread(Thread):
    """Run the upstream returner within a dedicated thread."""

    def __init__(self: UpstreamReturnerThread, queue: Any, engine: Engine,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, bundlewait: float = DEFAULT_RETURN_BUNDLEWAIT,
                 poll: float = 1, retention: float = DEFAULT_RETENTION) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-relay-returner')
        self.machine = UpstreamReturner(queue=queue, engine=engine, bundlesize=bundlesize, bundlewait=bundlewait,
                                        poll=poll, retention=retention)

    def run_with_exceptions(self: UpstreamReturnerThread) -> None:
        """Run machine."""
        self.machine.run()

    def flush(self: UpstreamReturnerThread) -> None:
        """Return whatever is held, then stop."""
        self.machine.flush()

    def stop(self: UpstreamReturnerThread, wait: bool = False, timeout: Optional[int] = None) -> None:
        """Stop machine, leaving whatever is not yet returned pending."""
        log.debug('Stopping (returner)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class HeartbeatState(State, Enum):
    """Finite states of the upstream heartbeat."""
    START = 0
    SUBMIT = 1
    WAIT = 2
    WAIT_LINK = 3
    FINAL = 4
    HALT = 5


class UpstreamHeartbeat(StateMachine):
    """
    Keep each zone identity registered upstream with a heartbeat every `heartrate` seconds.

    Every identity beats in turn (`identities`, else `identity` alone). Each beat carries the relay's
    `site`; the first identity's, the roster holder's, also carries the roster read by `roster` (the
    site clients connected now, of every zone, a whole snapshot each time). A change in the roster is
    sent at the next wait step rather than when the next beat is due. A roster that cannot be read
    is left out of that beat, so upstream keeps the last one it was sent. The final beat goes out
    under every identity.

    While the upstream link is down the heartbeat waits for it (WAIT_LINK) and beats as soon as it is
    back: that first beat registers the identity again if the upstream server evicted it, and passes
    the link's `beat` stage, after which confirmations, results and requests follow.

    With `zones` (each identity's zone name), every beat of an identity in it also advertises that
    zone's `bounds`, its `launched` count and the relay's `instance` key, on every beat, so the
    advertisement reaches the upstream server on the first beat of every connection. With `view`,
    each running beat is followed by a read of the site topology the upstream server publishes for
    these identities (see `fetch`).
    """

    queue: Any
    link: Any
    identity: str
    identities: List[str]
    heartrate: float
    previous: float
    client_state: ClientState
    site: Optional[str]
    roster: Optional[Callable[[], List[RosterEntry]]]
    sent_roster: Optional[List[RosterEntry]]
    zones: Dict[str, str]
    bounds: Dict[str, Bounds]
    launched: Optional[Callable[[str], int]]
    instance: Optional[str]
    view: Any
    published: Optional[bool]

    state = HeartbeatState.START
    states = HeartbeatState

    def __init__(self: UpstreamHeartbeat, queue: Any, identity: str,
                 heartrate: float = DEFAULT_UPSTREAM_HEARTRATE, site: Optional[str] = None,
                 roster: Optional[Callable[[], List[RosterEntry]]] = None,
                 identities: Optional[Sequence[str]] = None,
                 zones: Optional[Mapping[str, str]] = None,
                 bounds: Optional[Mapping[str, Bounds]] = None,
                 launched: Optional[Callable[[str], int]] = None,
                 instance: Optional[str] = None,
                 view: Any = None) -> None:
        """Initialize with the upstream link (or a connected client) and the zone identity (or every one, holder first)."""
        self.queue = queue
        self.link = as_link(queue)
        self.identities = list(identities) if identities else [identity]
        self.identity = self.identities[0]
        self.heartrate = heartrate
        self.site = site
        self.roster = roster
        self.sent_roster = None
        self.previous = time.monotonic()
        self.client_state = ClientState.RUNNING
        self.wake = threading.Event()
        self.zones = dict(zones or {})
        self.bounds = dict(bounds or {})
        self.launched = launched
        self.instance = instance
        self.view = view
        self.published = None

    @functools.cached_property
    def actions(self: UpstreamHeartbeat) -> Dict[HeartbeatState, Callable[[], HeartbeatState]]:
        return {
            HeartbeatState.START: self.start,
            HeartbeatState.SUBMIT: self.submit,
            HeartbeatState.WAIT: self.wait,
            HeartbeatState.WAIT_LINK: self.wait_link,
            HeartbeatState.FINAL: self.finalize,
        }

    def start(self: UpstreamHeartbeat) -> HeartbeatState:
        """Jump to SUBMIT state."""
        log.debug(f'Started (upstream heartbeat: {self.identity})')
        return HeartbeatState.SUBMIT

    def submit(self: UpstreamHeartbeat) -> HeartbeatState:
        """Send one heartbeat under each zone identity, the roster with the holder's."""
        state = self.client_state  # atomic
        built = time.monotonic()  # Before any count is read into these beats.
        roster = self.read_roster() if state is ClientState.RUNNING else None
        beats = [Heartbeat.new(uuid=identity, state=state, site=self.site,
                               roster=roster if identity == self.identity else None,
                               **self.advertisement(identity)).pack()
                 for identity in self.identities]
        epoch = self.link.epoch
        try:
            for beat in beats:
                self.link.call('heartbeat',
                               lambda client, beat=beat: client.heartbeat.put(beat, timeout=DEFAULT_REMOTE_TIMEOUT))
        except QueueFull:
            return HeartbeatState.SUBMIT
        except LinkDown:
            self.sent_roster = None  # Sent whole again once the link is back.
            return HeartbeatState.WAIT_LINK
        self.link.passed(STAGE_BEAT, epoch)
        self.previous = time.monotonic()
        if roster is not None:
            self.sent_roster = roster
        if state is ClientState.RUNNING:
            log.trace(f'Upstream heartbeat - running ({", ".join(self.identities)})')
            self.fetch(built)
            return HeartbeatState.WAIT
        log.debug(f'Upstream heartbeat - final ({", ".join(self.identities)})')
        return HeartbeatState.FINAL

    def wait(self: UpstreamHeartbeat) -> HeartbeatState:
        """Wait until the next heartbeat is due, or until told to finish."""
        if self.client_state is not ClientState.RUNNING:
            return HeartbeatState.SUBMIT
        remaining = self.heartrate - (time.monotonic() - self.previous)
        if remaining <= 0:
            return HeartbeatState.SUBMIT
        if self.roster is not None and self.read_roster() not in (None, self.sent_roster):
            return HeartbeatState.SUBMIT
        self.wake.wait(min(remaining, HEARTBEAT_WAIT_STEP))
        return HeartbeatState.WAIT

    def wait_link(self: UpstreamHeartbeat) -> HeartbeatState:
        """Wait for the link, then beat at once."""
        if self.link.wait_ready(timeout=LINK_WAIT):
            return HeartbeatState.SUBMIT
        return HeartbeatState.WAIT_LINK

    def read_roster(self: UpstreamHeartbeat) -> Optional[List[RosterEntry]]:
        """The current roster, or None if there is none to send or it cannot be read now."""
        if self.roster is None:
            return None
        try:
            return self.roster()
        except SQLAlchemyError as error:
            log.debug(f'Roster not read for upstream heartbeat ({error.__class__.__name__}: {error})')
            return None

    def advertisement(self: UpstreamHeartbeat, identity: str) -> Dict[str, Any]:
        """The fields `identity` advertises on its beat: its zone, bounds, launched count and the relay's instance key."""
        zone = self.zones.get(identity)
        if zone is None:
            return {}
        try:
            clients = None if self.launched is None else self.launched(zone)
        except Exception as error:  # A count that cannot be read leaves this beat's advertisement out.
            log.debug(f'Launched clients not counted for upstream heartbeat ({error.__class__.__name__}: {error})')
            clients = None
        return {'zone': zone, 'bounds': self.bounds.get(zone), 'clients': clients, 'instance': self.instance}

    def fetch(self: UpstreamHeartbeat, built: Optional[float] = None) -> None:
        """Read the site topology published upstream for these identities into the view.

        An upstream server that publishes none (one of an earlier release, without the method, or
        one not autoscaling with relay sites) clears the view, so the fleets keep their local sizes;
        this is logged once each time it starts. A read that fails, on the link or otherwise, leaves
        the view as it was. A read is stamped with `built`, when the beat just sent was built (now
        when not given).
        """
        if self.view is None:
            return
        text = topology_request(self.site or '', self.identities)

        def call(client: Any) -> Optional[str]:
            method = getattr(getattr(client, 'dispatcher', None), 'topology', None)
            return None if method is None else method(text)

        try:
            reply = self.link.call('heartbeat', call)
        except LinkDown:
            log.debug('Topology not read (upstream link down) - keeping the last one')
            return
        except Exception as error:
            log.warning(f'Topology not read ({error.__class__.__name__}: {error}) - keeping the last one')
            return
        try:
            topology = None if reply is None else read_topology(reply)
        except ValueError as error:
            log.warning(f'Topology not read ({error}) - keeping the last one')
            return
        if topology is None:
            if self.published is not False:
                reason = 'an earlier release' if reply is None else 'not autoscaling with relay sites'
                log.info(f'Upstream server publishes no topology ({reason}) - site clients kept by local size')
            self.published = False
            self.view.set(None, beat_at=built)
            return
        previous = self.view.reply
        if previous is None or previous.version != topology.version:
            zones = ', '.join(f'{zone}: {entry.target}' for zone, entry in sorted(topology.zones.items()))
            log.debug(f'Topology {topology.version} read ({zones or "no entries"})')
        if self.published is not True:
            log.info('Upstream server publishes a topology - site clients follow it')
        self.published = True
        self.view.set(topology, beat_at=built)

    def finalize(self: UpstreamHeartbeat) -> HeartbeatState:
        """Stop heartbeats."""
        log.debug(f'Done (upstream heartbeat: {self.identity})')
        return HeartbeatState.HALT

    def signal_finished(self: UpstreamHeartbeat) -> None:
        """Send the final heartbeat now, then stop."""
        self.client_state = ClientState.FINISHED
        self.wake.set()

    def halt(self: UpstreamHeartbeat) -> None:
        """Halt without a final heartbeat, ending any wait at once."""
        super().halt()
        self.wake.set()


class UpstreamHeartbeatThread(Thread):
    """Run the upstream heartbeat within a dedicated thread."""

    def __init__(self: UpstreamHeartbeatThread, queue: Any, identity: str,
                 heartrate: float = DEFAULT_UPSTREAM_HEARTRATE, site: Optional[str] = None,
                 roster: Optional[Callable[[], List[RosterEntry]]] = None,
                 identities: Optional[Sequence[str]] = None,
                 zones: Optional[Mapping[str, str]] = None,
                 bounds: Optional[Mapping[str, Bounds]] = None,
                 launched: Optional[Callable[[str], int]] = None,
                 instance: Optional[str] = None,
                 view: Any = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-relay-heartbeat')
        self.machine = UpstreamHeartbeat(queue=queue, identity=identity, heartrate=heartrate,
                                         site=site, roster=roster, identities=identities, zones=zones,
                                         bounds=bounds, launched=launched, instance=instance, view=view)

    def run_with_exceptions(self: UpstreamHeartbeatThread) -> None:
        """Run machine."""
        self.machine.run()

    def signal_finished(self: UpstreamHeartbeatThread) -> None:
        """Send the final heartbeat, then stop."""
        self.machine.signal_finished()

    def stop(self: UpstreamHeartbeatThread, wait: bool = False, timeout: Optional[int] = None) -> None:
        """Stop machine without a final heartbeat."""
        log.debug('Stopping (upstream heartbeat)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)
