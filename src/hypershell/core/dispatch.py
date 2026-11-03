# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Demand-driven dispatch: staging pools cut on a client's request.

The server's scheduler stages ready tasks in one first-in-first-out pool per zone, already stamped
and encoded as JSON text. A client asks for work with one call, ``request``, naming itself, its
zone and how many tasks it can take; the call runs on the server's handler thread for that client's
own connection and returns, on that connection, up to that many tasks cut from its zone's pool.
Requests for a zone are served first-come-first-served, and every cut happens under one lock, so
no task goes to two clients. A request that finds nothing waits a bounded time and returns empty.

Only the scheduler thread touches the database. Everything here is plain Python data: the request
and its reply are JSON text, and staged rows are carried as their ids and encoded records.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Tuple, Final, Iterable, Deque

# Standard libs
import json
import time
import functools
import threading
from collections import deque
from importlib.metadata import version as get_version
from queue import Queue, Empty as QueueEmpty

# Internal libs
from hypershell.core.logging import Logger
from hypershell.data.model import zone_id, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME

# Public interface
__all__ = ['Dispatcher', 'PROTOCOL', 'DEFAULT_BUNDLEMAX', 'MAX_REQUEST_LENGTH', 'build_version']

# Initialize logger
log = Logger.with_name(__name__)


PROTOCOL: Final[int] = 1
"""Version of the request and reply format; both sides must speak the same one."""

DEFAULT_BUNDLEMAX: Final[int] = 1000
"""Default bound on the tasks in one reply (the effective cap is never below the server's bundle size)."""

DEFAULT_MAX_WAIT: Final[float] = 2.0
"""Longest a request waits for tasks before it returns empty (seconds)."""

MAX_REQUEST_LENGTH: Final[int] = 4096
"""Longest request text the server reads (characters)."""

MAX_FIELD_LENGTH: Final[int] = 256
"""Longest client id, host or zone name in a request (characters)."""

# Wait used while a request polls an in-memory server's inbound queue (seconds).
INBOUND_POLL: Final[float] = 0.1


class BadRequest(Exception):
    """A request the server cannot read; `kind` is 'request' or 'protocol'."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@functools.cache
def build_version() -> str:
    """This build's version string."""
    return get_version('hypershell')


def _text(data: dict, key: str, required: bool = False) -> Optional[str]:
    """A short printable string field of a request, or None when absent and optional."""
    value = data.get(key)
    if value is None:
        if required:
            raise BadRequest('request', f'missing \'{key}\'')
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_FIELD_LENGTH:
        raise BadRequest('request', f'\'{key}\' must be a non-empty string of at most {MAX_FIELD_LENGTH} characters')
    if not value.isprintable():
        raise BadRequest('request', f'\'{key}\' must not contain control characters')
    return value


def _number(data: dict, key: str) -> Optional[float]:
    """A numeric field of a request (not a boolean), or None when absent."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadRequest('request', f'\'{key}\' must be a number')
    return value


class Request:
    """A decoded and checked request."""

    __slots__ = ('client', 'host', 'zone', 'name', 'size', 'wait')

    def __init__(self, client: str, host: Optional[str], name: Optional[str],
                 size: Optional[int], wait: Optional[float]) -> None:
        self.client = client
        self.host = host
        self.zone = zone_id(name)
        self.name = DEFAULT_ZONE_NAME if self.zone == DEFAULT_ZONE_ID else name
        self.size = size
        self.wait = wait

    @classmethod
    def decode(cls, text: str) -> Request:
        """Read and check request `text`, raising `BadRequest`."""
        if not isinstance(text, str):
            raise BadRequest('request', 'request must be text')
        if len(text) > MAX_REQUEST_LENGTH:
            raise BadRequest('request', f'request longer than {MAX_REQUEST_LENGTH} characters')
        try:
            data = json.loads(text)
        except ValueError:
            raise BadRequest('request', 'request is not JSON') from None
        if not isinstance(data, dict):
            raise BadRequest('request', 'request must be a JSON object')
        protocol = data.get('protocol')
        if protocol != PROTOCOL or isinstance(protocol, bool):
            raise BadRequest('protocol', f'work-request protocol {protocol!r} is not supported '
                                         f'(this server speaks protocol {PROTOCOL})')
        size = _number(data, 'size')
        if size is not None and size != int(size):
            raise BadRequest('request', '\'size\' must be a whole number')
        return cls(client=_text(data, 'client', required=True), host=_text(data, 'host'),
                   name=_text(data, 'zone'), size=None if size is None else int(size), wait=_number(data, 'wait'))


def _reply(texts: Iterable[str] = (), shutdown: bool = False) -> str:
    """Reply text carrying pre-encoded task records."""
    return (f'{{"protocol": {PROTOCOL}, "tasks": [' + ', '.join(texts) + '], "shutdown": '
            + ('true' if shutdown else 'false') + '}')


def _error(error: BadRequest) -> str:
    """Error reply text for a request the server cannot serve."""
    return json.dumps({'protocol': PROTOCOL, 'error': error.kind, 'message': str(error),
                       'server_version': build_version()})


class Pool:
    """One zone's staged rows and its waiting requests in arrival order, each with its own condition.

    Only the request at the head of the line can take rows, so only it is woken when rows are
    staged or when the request before it leaves; the others sleep until their turn (or shutdown).
    """

    __slots__ = ('rows', 'ids', 'tickets', 'name')

    def __init__(self, name: str) -> None:
        self.rows: Deque[Tuple[str, str, bool]] = deque()  # (task id, record as JSON text, stamped)
        self.ids: set = set()
        self.tickets: Deque[threading.Condition] = deque()
        self.name = name

    def wake_head(self) -> None:
        """Wake the request at the head of the line, if any (lock held)."""
        if self.tickets:
            self.tickets[0].notify()

    def wake_all(self) -> None:
        """Wake every waiting request (lock held)."""
        for ticket in self.tickets:
            ticket.notify()

    def cut(self, size: int) -> List[Tuple[str, str, bool]]:
        """Take up to `size` rows from the front."""
        rows = [self.rows.popleft() for _ in range(min(size, len(self.rows)))]
        for row in rows:
            self.ids.discard(row[0])
        return rows


class Dispatcher:
    """
    Staging pools per zone, cut on request.

    The scheduler fills a pool with :meth:`stage` while :meth:`refills` lists it, and waits for
    demand with :meth:`wait_need`; clients take from it with :meth:`request`. The default zone is
    always demanded, at no less than the server's bundle size. A zone's chunk size is the largest
    size its clients ask for, capped, and its pool is refilled while it holds at most `queuesize`
    chunks, so it never holds more than `queuesize + 1` of them.

    In memory (a server without a database), default-zone requests take their tasks from the
    bundles submitted to the `inbound` queue instead, re-cut to the requested size.
    """

    def __init__(self: Dispatcher, bundlesize: int = 1, queuesize: int = 1, bundlemax: int = DEFAULT_BUNDLEMAX,
                 max_wait: float = DEFAULT_MAX_WAIT, inbound: Optional[Queue] = None) -> None:
        """Initialize empty pools with the server's bundle size, queue depth and cap."""
        self.bundlesize = max(1, int(bundlesize))
        self.queuesize = max(1, int(queuesize))
        self.bundlemax = max(1, int(bundlemax))
        self.max_wait = max_wait
        self.in_memory = inbound is not None
        self.inbound = inbound
        self.lock = threading.Lock()
        self.need = threading.Condition(self.lock)
        self.generation = 0
        self.finishing = False
        self.pools: Dict[str, Pool] = {DEFAULT_ZONE_ID: Pool(DEFAULT_ZONE_NAME)}
        self.demand: Dict[str, Tuple[str, int]] = {}
        self.unstaging: set = set()

    def configure(self: Dispatcher, bundlesize: Optional[int] = None, queuesize: Optional[int] = None,
                  bundlemax: Optional[int] = None, in_memory: Optional[bool] = None) -> Dispatcher:
        """Set the server's parameters before serving."""
        with self.lock:
            if bundlesize is not None:
                self.bundlesize = max(1, int(bundlesize))
            if queuesize is not None:
                self.queuesize = max(1, int(queuesize))
            if bundlemax is not None:
                self.bundlemax = max(1, int(bundlemax))
            if in_memory is not None:
                self.in_memory = in_memory
        return self

    def attach(self: Dispatcher, inbound: Queue) -> None:
        """Take default-zone tasks from `inbound` when in memory (called once the server's queues exist)."""
        with self.lock:
            if self.in_memory:
                self.inbound = inbound

    @property
    def cap(self: Dispatcher) -> int:
        """Most tasks one reply may carry: the configured bound, never below the server's bundle size."""
        return max(self.bundlemax, self.bundlesize)

    def _signal_need(self: Dispatcher) -> None:
        """Wake the scheduler (lock held)."""
        self.generation += 1
        self.need.notify_all()

    def _pool(self: Dispatcher, zone: str, name: str) -> Pool:
        """The pool for `zone`, created on first use (lock held)."""
        pool = self.pools.get(zone)
        if pool is None:
            pool = self.pools[zone] = Pool(name)
            self._signal_need()
        return pool

    def _size(self: Dispatcher, zone: str) -> int:
        """Chunk size for `zone`: the largest its clients ask for, capped (lock held)."""
        sizes = [size for z, size in self.demand.values() if z == zone]
        if zone == DEFAULT_ZONE_ID:
            sizes.append(self.bundlesize)
        return min(self.cap, max(sizes, default=self.bundlesize))

    def _register(self: Dispatcher, request: Request, size: int) -> Pool:
        """Record what the requesting client serves and asks for; wake the scheduler on news (lock held)."""
        pool = self._pool(request.zone, request.name)
        previous = self.demand.get(request.client)
        if previous != (request.zone, size):
            before = self._size(request.zone)
            self.demand[request.client] = (request.zone, size)
            self.unstaging.discard(request.zone)
            if previous is None or self._size(request.zone) != before:
                self._signal_need()
        return pool

    def _drain_inbound(self: Dispatcher, pool: Pool, size: int) -> None:
        """Move submitted bundles from the inbound queue into `pool` until it holds `size` (lock held)."""
        while len(pool.rows) < size:
            try:
                data = self.inbound.get_nowait()
            except QueueEmpty:
                return
            self.inbound.task_done()
            self._add_inbound(pool, data)

    @staticmethod
    def _add_inbound(pool: Pool, data: bytes) -> None:
        """Stage the tasks of one submitted bundle (unstamped; the server has no database)."""
        records = json.loads(data.decode('utf-8'))
        for record in records or ():
            if record['id'] not in pool.ids:
                pool.ids.add(record['id'])
                pool.rows.append((record['id'], json.dumps(record), False))

    def _await_inbound(self: Dispatcher, pool: Pool, timeout: float) -> None:
        """Wait for one submitted bundle without holding the lock, then stage it (lock held on entry and exit)."""
        self.lock.release()
        try:
            data = self.inbound.get(timeout=max(0.0, timeout))
            self.inbound.task_done()
        except QueueEmpty:
            data = None
        finally:
            self.lock.acquire()
        if data is not None:
            self._add_inbound(pool, data)

    def _done(self: Dispatcher, pool: Pool) -> bool:
        """Whether a request may be told to shut down: finishing, nothing staged, nothing inbound (lock held)."""
        return self.finishing and not pool.rows and (self.inbound is None or self.inbound.empty())

    def request(self: Dispatcher, text: str) -> str:
        """
        Serve one client request (JSON text) and return the reply (JSON text).

        Waits at most the request's `wait` (bounded by the server) for its zone's pool to hold tasks
        and for earlier requests to be served, then cuts up to the requested size: a partial reply
        when fewer are staged, an empty one at the deadline. A bad request gets an error reply; this
        method never raises for one.
        """
        try:
            request = Request.decode(text)
        except BadRequest as error:
            return _error(error)
        size = max(1, min(self.cap, self.bundlesize if request.size is None else request.size))
        wait = self.max_wait if request.wait is None else max(0.0, min(self.max_wait, request.wait))
        deadline = time.monotonic() + wait
        in_memory = self.inbound is not None and request.zone == DEFAULT_ZONE_ID
        ticket = threading.Condition(self.lock)
        with self.lock:
            pool = self._register(request, size)
            pool.tickets.append(ticket)
            try:
                while True:
                    if pool.tickets[0] is ticket:
                        if in_memory:
                            self._drain_inbound(pool, size)
                        if pool.rows:
                            rows = pool.cut(size)
                            self._signal_need()
                            break
                    if self._done(pool):
                        return _reply(shutdown=True)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return _reply()
                    if in_memory and pool.tickets[0] is ticket:
                        self._await_inbound(pool, min(remaining, INBOUND_POLL))
                    else:
                        ticket.wait(remaining)
            finally:
                pool.tickets.remove(ticket)
                pool.wake_head()
        log.debug(f'Dispatched {len(rows)} tasks to client {request.client} (zone {pool.name})')
        return _reply(row[1] for row in rows)

    def refills(self: Dispatcher) -> Tuple[int, List[Tuple[str, int]]]:
        """The zones to load and how many tasks each, with the generation to pass to :meth:`wait_need`.

        A zone is listed while its pool holds at most `queuesize` chunks: the default zone always,
        any other zone while a client serves it.
        """
        with self.lock:
            demanded = {zone for zone, _ in self.demand.values()}
            refills = []
            for zone, pool in self.pools.items():
                if zone != DEFAULT_ZONE_ID and (zone not in demanded or zone in self.unstaging):
                    continue
                size = self._size(zone)
                if len(pool.rows) <= self.queuesize * size:
                    refills.append((zone, size))
            return self.generation, refills

    def wait_need(self: Dispatcher, generation: int, timeout: float) -> None:
        """Wait up to `timeout` for a cut or new demand, unless one came since `generation` was read."""
        with self.lock:
            if self.generation == generation:
                self.need.wait(timeout)

    def stage(self: Dispatcher, zone: str, rows: Iterable[Tuple[str, str]], stamped: bool = True) -> int:
        """Append loaded rows (id, record as JSON text) to `zone`'s pool and wake its requests; return the count."""
        count = 0
        with self.lock:
            pool = self.pools.get(zone) or self._pool(zone, zone)
            for task_id, record in rows:
                if task_id not in pool.ids:
                    pool.ids.add(task_id)
                    pool.rows.append((task_id, record, stamped))
                    count += 1
            pool.wake_head()
        return count

    def staged(self: Dispatcher, zone: str = None) -> int:
        """Number of rows staged in `zone`'s pool, or in every pool."""
        with self.lock:
            pools = self.pools.values() if zone is None else [self.pools[zone]] if zone in self.pools else []
            return sum(len(pool.rows) for pool in pools)

    def clients(self: Dispatcher, zone: str) -> int:
        """Number of clients currently serving `zone`."""
        with self.lock:
            return sum(1 for z, _ in self.demand.values() if z == zone)

    def zoned(self: Dispatcher) -> bool:
        """Whether any client serves a zone other than the default zone."""
        with self.lock:
            return any(zone != DEFAULT_ZONE_ID for zone, _ in self.demand.values())

    def zone_name(self: Dispatcher, zone: str) -> Optional[str]:
        """Name a client used for `zone`, if any client has asked for it."""
        with self.lock:
            pool = self.pools.get(zone)
            return pool.name if pool is not None else None

    def forget(self: Dispatcher, client_id: str) -> None:
        """Drop a finished or evicted client's demand; a zone it was the last client of is marked for unstaging."""
        with self.lock:
            entry = self.demand.pop(client_id, None)
            if entry is None:
                return
            zone = entry[0]
            if zone != DEFAULT_ZONE_ID and not any(z == zone for z, _ in self.demand.values()):
                pool = self.pools.get(zone)
                if pool is not None and pool.rows:
                    self.unstaging.add(zone)
            self._signal_need()

    def take_unstaged(self: Dispatcher) -> List[str]:
        """Remove and return the ids of stamped rows staged for zones that lost their last client."""
        with self.lock:
            demanded = {zone for zone, _ in self.demand.values()}
            ids = []
            for zone in list(self.unstaging):
                self.unstaging.discard(zone)
                if zone in demanded or zone not in self.pools:
                    continue
                ids.extend(self._take(self.pools[zone]))
            return ids

    def take_staged(self: Dispatcher) -> List[str]:
        """Remove and return the ids of every stamped row still staged (at shutdown)."""
        with self.lock:
            ids = []
            for pool in self.pools.values():
                ids.extend(self._take(pool))
            return ids

    @staticmethod
    def _take(pool: Pool) -> List[str]:
        """Empty `pool`, returning the ids of its stamped rows (lock held)."""
        ids = [task_id for task_id, _, stamped in pool.rows if stamped]
        pool.rows.clear()
        pool.ids.clear()
        return ids

    def finish(self: Dispatcher) -> None:
        """Begin shutdown: requests drain what is staged, then are told to shut down. Idempotent."""
        with self.lock:
            self.finishing = True
            for pool in self.pools.values():
                pool.wake_all()
            self._signal_need()
