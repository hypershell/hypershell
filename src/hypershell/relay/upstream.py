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
returned only once its put has returned. The heartbeat keeps each identity registered upstream.

Example:
    >>> from hypershell.relay.upstream import upstream_identity
    >>> identity = upstream_identity('gpu')
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple, Final

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
from hypershell.core.config import config
from hypershell.core.fsm import State, StateMachine
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.logging import HOSTNAME, INSTANCE, Logger
from hypershell.core.queue import QueueConfig, QueueVersionError, PROTOCOL, DEFAULT_REMOTE_TIMEOUT
from hypershell.core.signal import check_signal, SIGNAL_MAP, SIGUSR1, SIGUSR2
from hypershell.core.thread import Thread
from hypershell.core.tls import from_namespace as tls_from_namespace
from hypershell.core.dispatch import build_version
from hypershell.client import ClientInfo
from hypershell.data.model import DEFAULT_ZONE_NAME
from hypershell.relay import store

# Public interface
__all__ = ['upstream_identity', 'upstream_config', 'request_text', 'read_reply', 'RelayInsertError',
           'PullerState', 'UpstreamPuller', 'UpstreamPullerThread',
           'ReturnerState', 'UpstreamReturner', 'UpstreamReturnerThread',
           'HeartbeatState', 'UpstreamHeartbeat', 'UpstreamHeartbeatThread',
           'DEFAULT_UPSTREAM_BUNDLESIZE', 'DEFAULT_UPSTREAM_HEARTRATE', 'DEFAULT_REQUEST_WAIT', 'INSERT_ATTEMPTS',
           'DEFAULT_RETENTION', 'DEFAULT_RETURN_BUNDLEWAIT']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_UPSTREAM_BUNDLESIZE: Final[int] = config.relay.upstream.bundlesize
"""Default bundle size the relay advertises upstream."""

DEFAULT_UPSTREAM_HEARTRATE: Final[int] = config.relay.upstream.heartrate
"""Default seconds between upstream heartbeats."""

DEFAULT_REQUEST_WAIT: Final[float] = DEFAULT_REMOTE_TIMEOUT
"""Seconds a work request may wait upstream for tasks (the upstream server bounds it)."""

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


def upstream_identity(zone: Optional[str], instance: str = INSTANCE) -> str:
    """The upstream client id of a relay `instance` for the zone named `zone` (None is the default zone)."""
    return str(uuid5(UUID(instance), zone or DEFAULT_ZONE_NAME))


def upstream_config(host: Optional[str] = None, port: Optional[int] = None, no_tls: bool = False) -> QueueConfig:
    """Connection details for the upstream server, from `[relay.upstream]` (never from `[server]`).

    `host` and `port` override the configured values; `no_tls` turns TLS off on this connection only.
    """
    section = config.relay.upstream
    tls = None if no_tls else tls_from_namespace(section.get('tls'))
    return QueueConfig(host=host or section.host, port=int(port or section.port), auth=str(section.auth), tls=tls)


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
    FINAL = 5
    HALT = 6


class UpstreamPuller(StateMachine):
    """
    Request work upstream for one zone, insert it durably, then confirm it.

    The gate holds while the tasks in the relay database not yet complete reach the bound: the
    relay-local server's connected clients times `local_bundlesize`. Below the bound it asks for the
    gap, at most `bundlesize`. Every wait is finite and re-enters.
    """

    queue: Any
    engine: Engine
    identity: str
    zone: Optional[str]
    bundlesize: int
    local_bundlesize: int
    poll: float
    wait_request: float
    server_id: str

    size: int
    bundle: List[Dict[str, Any]]
    task_ids: List[str]
    insert_failures: int

    state = PullerState.START
    states = PullerState

    def __init__(self: UpstreamPuller, queue: Any, engine: Engine, identity: str, zone: Optional[str] = None,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, local_bundlesize: int = 1, poll: float = 1,
                 wait: float = DEFAULT_REQUEST_WAIT, server_id: str = INSTANCE) -> None:
        """Initialize with the upstream connection, the relay database and the zone identity."""
        self.queue = queue
        self.engine = engine
        self.identity = identity
        self.zone = zone
        self.bundlesize = max(1, int(bundlesize))
        self.local_bundlesize = max(1, int(local_bundlesize))
        self.poll = poll
        self.wait_request = wait
        self.server_id = server_id
        self.size = 0
        self.bundle = []
        self.task_ids = []
        self.insert_failures = 0
        self.wake = threading.Event()

    @functools.cached_property
    def actions(self: UpstreamPuller) -> Dict[PullerState, Callable[[], PullerState]]:
        return {
            PullerState.START: self.start,
            PullerState.GATE: self.gate,
            PullerState.REQUEST: self.request,
            PullerState.INSERT: self.insert,
            PullerState.CONFIRM: self.confirm,
            PullerState.FINAL: self.finalize,
        }

    def start(self: UpstreamPuller) -> PullerState:
        """Jump to GATE state."""
        log.debug(f'Started (puller: {self.identity})')
        return PullerState.GATE

    def gate(self: UpstreamPuller) -> PullerState:
        """Ask for work only below the backlog bound; end on a drain signal."""
        if (signal := check_signal()) in (SIGUSR1, SIGUSR2):
            log.warning(f'Signal interrupt ({SIGNAL_MAP[signal]}) - no more upstream requests')
            return PullerState.FINAL
        bound = store.count_connected(self.engine, self.server_id) * self.local_bundlesize
        backlog = store.count_backlog(self.engine)
        if backlog >= bound:
            self.wake.wait(self.poll)
            return PullerState.GATE
        self.size = min(bound - backlog, self.bundlesize)
        return PullerState.REQUEST

    def request(self: UpstreamPuller) -> PullerState:
        """Request up to `size` tasks under the zone identity (the upstream server bounds the wait)."""
        text = request_text(self.identity, self.zone, self.size, self.wait_request)
        reply = read_reply(self.queue.dispatcher.request(text))
        if reply['tasks']:
            self.bundle = reply['tasks']
            log.debug(f'Received {len(self.bundle)} tasks ({self.identity})')
            return PullerState.INSERT
        if reply.get('shutdown'):
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
            self.wake.wait(self.poll)
            return PullerState.INSERT
        self.insert_failures = 0
        self.bundle = []
        return PullerState.CONFIRM

    def confirm(self: UpstreamPuller) -> PullerState:
        """Confirm the inserted tasks upstream under the zone identity."""
        info = ClientInfo(client_id=self.identity, client_host=HOSTNAME, task_ids=self.task_ids)
        try:
            self.queue.confirmed.put(info.pack(), timeout=DEFAULT_REMOTE_TIMEOUT)
        except QueueFull:
            return PullerState.CONFIRM
        log.debug(f'Confirmed {len(self.task_ids)} tasks ({self.identity})')
        return PullerState.GATE

    def finalize(self: UpstreamPuller) -> PullerState:
        """Stop pulling."""
        log.debug(f'Done (puller: {self.identity})')
        return PullerState.HALT

    def halt(self: UpstreamPuller) -> None:
        """Halt, ending any wait at once."""
        super().halt()
        self.wake.set()


class UpstreamPullerThread(Thread):
    """Run the upstream puller within a dedicated thread."""

    def __init__(self: UpstreamPullerThread, queue: Any, engine: Engine, identity: str, zone: Optional[str] = None,
                 bundlesize: int = DEFAULT_UPSTREAM_BUNDLESIZE, local_bundlesize: int = 1, poll: float = 1,
                 wait: float = DEFAULT_REQUEST_WAIT, server_id: str = INSTANCE) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-relay-puller')
        self.machine = UpstreamPuller(queue=queue, engine=engine, identity=identity, zone=zone,
                                      bundlesize=bundlesize, local_bundlesize=local_bundlesize, poll=poll,
                                      wait=wait, server_id=server_id)

    def run_with_exceptions(self: UpstreamPullerThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: UpstreamPullerThread, wait: bool = False, timeout: Optional[int] = None) -> None:
        """Stop machine."""
        log.debug('Stopping (puller)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class ReturnerState(State, Enum):
    """Finite states of the upstream returner."""
    START = 0
    COLLECT = 1
    SEND = 2
    MARK = 3
    FINAL = 4
    HALT = 5


class UpstreamReturner(StateMachine):
    """
    Hand completed site results back upstream through the relay's durable outbox.

    Each cycle moves completed rows into the outbox in one transaction, then sends the oldest pending
    results as one bundle once there are `bundlesize` of them or `bundlewait` seconds have passed since
    the last send. A bundle is marked returned only after its put returns; a put that does not return
    leaves it pending, to be sent again. Returned results are pruned after `retention` seconds. Told to
    flush, it sends whatever it holds at once and ends once nothing is left.
    """

    queue: Any
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
        """Initialize with the upstream connection and the relay database."""
        self.queue = queue
        self.engine = engine
        self.bundlesize = max(1, int(bundlesize))
        self.bundlewait = bundlewait
        self.poll = poll
        self.retention = retention
        self.sent = []
        self.previous_send = time.monotonic()
        self.previous_prune = time.monotonic()
        self.flushing = False
        self.wake = threading.Event()

    @functools.cached_property
    def actions(self: UpstreamReturner) -> Dict[ReturnerState, Callable[[], ReturnerState]]:
        return {
            ReturnerState.START: self.start,
            ReturnerState.COLLECT: self.collect,
            ReturnerState.SEND: self.send,
            ReturnerState.MARK: self.mark,
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
            if flushing:
                return ReturnerState.FINAL
            self.wake.wait(self.poll)
            return ReturnerState.COLLECT
        waited = time.monotonic() - self.previous_send
        if flushing or len(pending) >= self.bundlesize or waited >= self.bundlewait:
            self.sent = pending
            return ReturnerState.SEND
        self.wake.wait(min(self.poll, self.bundlewait - waited))
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
        try:
            self.queue.completed.put(bundle, timeout=DEFAULT_REMOTE_TIMEOUT)
        except QueueFull:
            return ReturnerState.SEND
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
    FINAL = 3
    HALT = 4


class UpstreamHeartbeat(StateMachine):
    """Keep a zone identity registered upstream with a heartbeat every `heartrate` seconds."""

    queue: Any
    identity: str
    heartrate: float
    previous: float
    client_state: ClientState

    state = HeartbeatState.START
    states = HeartbeatState

    def __init__(self: UpstreamHeartbeat, queue: Any, identity: str,
                 heartrate: float = DEFAULT_UPSTREAM_HEARTRATE) -> None:
        """Initialize with the upstream connection and the zone identity."""
        self.queue = queue
        self.identity = identity
        self.heartrate = heartrate
        self.previous = time.monotonic()
        self.client_state = ClientState.RUNNING
        self.wake = threading.Event()

    @functools.cached_property
    def actions(self: UpstreamHeartbeat) -> Dict[HeartbeatState, Callable[[], HeartbeatState]]:
        return {
            HeartbeatState.START: self.start,
            HeartbeatState.SUBMIT: self.submit,
            HeartbeatState.WAIT: self.wait,
            HeartbeatState.FINAL: self.finalize,
        }

    def start(self: UpstreamHeartbeat) -> HeartbeatState:
        """Jump to SUBMIT state."""
        log.debug(f'Started (upstream heartbeat: {self.identity})')
        return HeartbeatState.SUBMIT

    def submit(self: UpstreamHeartbeat) -> HeartbeatState:
        """Send one heartbeat under the zone identity."""
        state = self.client_state  # atomic
        beat = Heartbeat.new(uuid=self.identity, state=state)
        try:
            self.queue.heartbeat.put(beat.pack(), timeout=DEFAULT_REMOTE_TIMEOUT)
        except QueueFull:
            return HeartbeatState.SUBMIT
        self.previous = time.monotonic()
        if state is ClientState.RUNNING:
            log.trace(f'Upstream heartbeat - running ({self.identity})')
            return HeartbeatState.WAIT
        log.debug(f'Upstream heartbeat - final ({self.identity})')
        return HeartbeatState.FINAL

    def wait(self: UpstreamHeartbeat) -> HeartbeatState:
        """Wait until the next heartbeat is due, or until told to finish."""
        if self.client_state is not ClientState.RUNNING:
            return HeartbeatState.SUBMIT
        remaining = self.heartrate - (time.monotonic() - self.previous)
        if remaining <= 0:
            return HeartbeatState.SUBMIT
        self.wake.wait(min(remaining, HEARTBEAT_WAIT_STEP))
        return HeartbeatState.WAIT

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
                 heartrate: float = DEFAULT_UPSTREAM_HEARTRATE) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-relay-heartbeat')
        self.machine = UpstreamHeartbeat(queue=queue, identity=identity, heartrate=heartrate)

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
