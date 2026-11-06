# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
A queue connection that rides through outages: call deadlines, reconnection with backoff.

An `UpstreamLink` holds one connected queue client and runs every call on it from a worker thread
of the calling role, waiting at most a deadline for the answer. The stock proxies give a call no
bound of their own: against a peer that stops answering (a half-open connection, a stalled host) a
call would wait forever, and a connection whose answer timed out still holds that answer, so it can
never be used again. A call that fails on the transport, or overruns its deadline, takes the link
down for that connection and raises `LinkDown`; its worker is retired (one stuck in a call is
abandoned, and ends when its socket does). An error that reconnecting cannot cure (another
work-request protocol, a wrong key, a certificate that does not verify) fails the link: it is
raised to every caller from then on, unchanged, so the program exits as a client would. Any other
error (a full queue, say) is the caller's to handle and leaves the link as it was.

While the link is down a reconnector thread dials again, with full-jitter exponential backoff
(each wait drawn uniformly between zero and the base times two to the number of failed attempts,
capped), each attempt with the same deadline. A new connection is a new epoch with new worker
threads, since a proxy reuses a thread's cached connection to an address, and a cached connection
to a server that went away is broken for good. The old client's proxies are released without
dialing out (its manager is marked shut down), so no finalizer waits on an unreachable peer.

The link logs one WARNING when it goes down, INFO on attempts numbered by a power of two and at
least every `REPORT_INTERVAL`, one WARNING when an outage passes `evict_hint` (the upstream server
has then likely evicted this client, and what it held is being run elsewhere), and one WARNING
with the outage's length when it reconnects.

Callers can order themselves after a reconnection with stages: each named stage (say, a first
heartbeat) is passed once per connection, and `ready` asks whether the link is up and the given
stages are passed on the current one. A `FixedLink` serves a connected client with none of this:
calls run in the caller's thread and the link is always up.

Example:
    >>> from hypershell.core.queue import QueueClient, QueueConfig
    >>> link = UpstreamLink(lambda: QueueClient(config=QueueConfig(host='upstream.example', auth=key)))
    >>> link.start()
    >>> link.wait_up(timeout=30)
    >>> link.call('heartbeat', lambda client: client.heartbeat.put(beat, timeout=2))
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterable, Optional, Set, Final

# Standard libs
import ssl
import time
import random
import threading
from enum import Enum
from queue import SimpleQueue

# External libs
from multiprocessing import AuthenticationError
from multiprocessing.managers import State as ManagerState

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.queue import QueueVersionError
from hypershell.core.tls import TLSError

# Public interface
__all__ = ['UpstreamLink', 'FixedLink', 'LinkDown', 'LinkState', 'as_link', 'is_fatal', 'is_transport',
           'backoff_delay', 'DEFAULT_DEADLINE', 'DEFAULT_BACKOFF_BASE', 'DEFAULT_BACKOFF_CAP',
           'DEFAULT_EVICT_HINT', 'REPORT_INTERVAL']

# Initialize logger
log = Logger.with_name(__name__)


DEFAULT_DEADLINE: Final[float] = 30.0
"""Default seconds a call (or a connection attempt) may take before the link counts as down."""

DEFAULT_BACKOFF_BASE: Final[float] = 1.0
"""Seconds of the first reconnection wait's upper bound; each failed attempt doubles it."""

DEFAULT_BACKOFF_CAP: Final[float] = 60.0
"""Default longest bound on a reconnection wait, in seconds."""

DEFAULT_EVICT_HINT: Final[float] = 600.0
"""Default seconds of outage after which the server has likely evicted this client."""

REPORT_INTERVAL: Final[float] = 300.0
"""Longest time in seconds between INFO reports of an outage."""

FATAL_ERRORS: Final[tuple] = (QueueVersionError, AuthenticationError, TLSError, ssl.SSLCertVerificationError)
"""Errors reconnecting cannot cure: the link fails and they are raised as they are."""

TRANSPORT_ERRORS: Final[tuple] = (EOFError, OSError)
"""Errors of the connection itself (`OSError` covers resets, refusals, timeouts and other socket and TLS errors)."""


def is_fatal(error: BaseException) -> bool:
    """Whether `error` ends the link for good (see `FATAL_ERRORS`)."""
    return isinstance(error, FATAL_ERRORS)


def is_transport(error: BaseException) -> bool:
    """Whether `error` is a failure of the connection, which a new connection may cure."""
    return isinstance(error, TRANSPORT_ERRORS) and not is_fatal(error)


def backoff_delay(attempt: int, fraction: float, base: float = DEFAULT_BACKOFF_BASE,
                  cap: float = DEFAULT_BACKOFF_CAP) -> float:
    """The wait before reconnection attempt number `attempt` (from 0): `fraction` of min(cap, base · 2^attempt)."""
    return fraction * min(cap, base * 2 ** min(attempt, 64))


class LinkDown(ConnectionError):
    """The link is down: the call was not made, or its outcome is unknown."""


class LinkState(Enum):
    """States of an upstream link."""
    DOWN = 0
    UP = 1
    FAILED = 2
    HALTED = 3


class _Job:
    """One call handed to a worker thread."""

    def __init__(self: _Job, fn: Callable[[Any], Any]) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


class _Worker:
    """A thread that makes one role's calls on one connection, one at a time."""

    def __init__(self: _Worker, role: str, client: Any, epoch: int) -> None:
        self.role = role
        self.client = client
        self.epoch = epoch
        self.retired = False
        self.jobs: SimpleQueue = SimpleQueue()
        self.thread = threading.Thread(target=self.run, name=f'hypershell-link-{role}', daemon=True)
        self.thread.start()

    def submit(self: _Worker, fn: Callable[[Any], Any]) -> _Job:
        job = _Job(fn)
        self.jobs.put(job)
        return job

    def run(self: _Worker) -> None:
        """Make each call handed over, until retired (a call outstanding then is finished first)."""
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                job.result = job.fn(self.client)
            except BaseException as error:  # Handed to the caller, which classifies it.
                job.error = error
            job.done.set()
            if self.retired:
                break
        self.client = None

    def retire(self: _Worker) -> None:
        """End once idle; one stuck in a call is abandoned and ends when the call does."""
        self.retired = True
        self.jobs.put(None)


def release_client(client: Any) -> None:
    """Drop a client of a connection that is over, so its proxies are released without dialing out."""
    state = getattr(client, '_state', None)
    if state is not None:
        state.value = ManagerState.SHUTDOWN


class UpstreamLink:
    """
    A queue client connection with call deadlines and reconnection (see the module notes).

    Args:
        factory (callable):
            Builds a new, unconnected queue client; its `connect` is called for each attempt.

        name (str, optional):
            How the peer is named in log messages (e.g. ``host:port``).

        deadline (float, optional):
            Seconds a call or a connection attempt may take. See :const:`DEFAULT_DEADLINE`.

        evict_hint (float, optional):
            Seconds of outage after which the peer has likely evicted this client.
            See :const:`DEFAULT_EVICT_HINT`.

        backoff_base (float, optional):
            Bound in seconds on the first reconnection wait. See :const:`DEFAULT_BACKOFF_BASE`.

        backoff_cap (float, optional):
            Longest bound in seconds on a reconnection wait. See :const:`DEFAULT_BACKOFF_CAP`.

        stages (iterable of str, optional):
            Names of the stages callers pass on each new connection (see `passed` and `ready`).

        clock (callable, optional):
            Monotonic clock in seconds (for outage lengths and log cadence).

        rng (callable, optional):
            Uniform random fraction in [0, 1) (for the backoff jitter).

        sleep (callable, optional):
            Waits the given seconds; returns true if the link was halted meanwhile.
            Default: a wait on the link's halt event.
    """

    def __init__(self: UpstreamLink,
                 factory: Callable[[], Any],
                 name: str = 'upstream server',
                 deadline: float = DEFAULT_DEADLINE,
                 evict_hint: float = DEFAULT_EVICT_HINT,
                 backoff_base: float = DEFAULT_BACKOFF_BASE,
                 backoff_cap: float = DEFAULT_BACKOFF_CAP,
                 stages: Iterable[str] = (),
                 clock: Callable[[], float] = time.monotonic,
                 rng: Callable[[], float] = random.random,
                 sleep: Optional[Callable[[float], bool]] = None) -> None:
        self.factory = factory
        self.name = name
        self.deadline = float(deadline)
        self.evict_hint = float(evict_hint)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.stages: Set[str] = set(stages)
        self.clock = clock
        self.rng = rng
        self.halted = threading.Event()
        self.sleep = sleep or self.halted.wait
        self.condition = threading.Condition()
        self.state = LinkState.DOWN
        self.client: Any = None
        self.epoch = 0
        self.connected = False  # Ever connected (the first connection is not a reconnection).
        self.last_ok = clock()
        self.down_since = self.last_ok
        self.outage = 0.0
        self.attempts = 0
        self.reported_at = self.last_ok
        self.hinted = False
        self.failure: Optional[BaseException] = None
        self.reason = 'not yet connected'
        self.workers: Dict[str, _Worker] = {}
        self.passed_stages: Set[str] = set()
        self.thread = threading.Thread(target=self.run, name='hypershell-link', daemon=True)

    # --- Calls -----------------------------------------------------------------------------------

    def call(self: UpstreamLink, role: str, fn: Callable[[Any], Any], deadline: Optional[float] = None) -> Any:
        """Run `fn(client)` on the worker thread of `role` and return its result, waiting at most `deadline`.

        Raises `LinkDown` if the link is down, or if the call fails on the transport or overruns
        (the link is then down). A fatal error fails the link and is raised as it is; any other
        error is raised as it is and leaves the link up.
        """
        with self.condition:
            self.raise_failure()
            if self.state is not LinkState.UP:
                raise LinkDown(f'Upstream link down ({self.reason})')
            epoch = self.epoch
            worker = self.workers.get(role)
            if worker is None:
                worker = self.workers[role] = _Worker(role, self.client, epoch)
        job = worker.submit(fn)
        deadline = self.deadline if deadline is None else deadline
        if not job.done.wait(deadline):
            reason = f'{role} call took longer than {deadline:g} seconds'
            self.mark_down(epoch, reason)
            raise LinkDown(f'Upstream link down ({reason})')
        if job.error is None:
            with self.condition:
                if epoch == self.epoch:
                    self.last_ok = self.clock()
            return job.result
        error = job.error
        if is_fatal(error):
            self.fail(error)
            raise error
        if is_transport(error):
            self.mark_down(epoch, f'{error.__class__.__name__}: {error}')
            raise LinkDown(f'Upstream link down ({error.__class__.__name__}: {error})') from error
        raise error

    # --- State -----------------------------------------------------------------------------------

    def is_up(self: UpstreamLink) -> bool:
        """Whether the link is up now."""
        return self.state is LinkState.UP

    def ready(self: UpstreamLink, after: Iterable[str] = ()) -> bool:
        """Whether the link is up and every stage of `after` it knows is passed on this connection.

        Raises the link's failure, if it has failed.
        """
        with self.condition:
            self.raise_failure()
            return self.state is LinkState.UP and self.stages.intersection(after) <= self.passed_stages

    def wait_ready(self: UpstreamLink, after: Iterable[str] = (), timeout: float = 1.0) -> bool:
        """Wait at most `timeout` until `ready(after)`; return whether it is (raises the link's failure)."""
        after = tuple(after)
        end = time.monotonic() + timeout
        with self.condition:
            while not self.ready(after):
                remaining = end - time.monotonic()
                if remaining <= 0 or self.halted.is_set():
                    return False
                self.condition.wait(remaining)
            return True

    def wait_up(self: UpstreamLink, timeout: float = 1.0) -> bool:
        """Wait at most `timeout` for the link to be up; return whether it is (raises the link's failure)."""
        return self.wait_ready((), timeout)

    def passed(self: UpstreamLink, stage: str, epoch: Optional[int] = None) -> None:
        """Mark `stage` passed on the current connection (or only if that is still `epoch`)."""
        with self.condition:
            if stage in self.passed_stages or (epoch is not None and epoch != self.epoch):
                return
            if self.state is LinkState.UP:
                self.passed_stages.add(stage)
                self.condition.notify_all()

    def is_passed(self: UpstreamLink, stage: str) -> bool:
        """Whether `stage` is passed on the current connection."""
        return stage in self.passed_stages

    def raise_failure(self: UpstreamLink) -> None:
        """Raise the error that failed the link, if any."""
        if self.failure is not None:
            raise self.failure

    def mark_down(self: UpstreamLink, epoch: int, reason: str) -> None:
        """Take the link down, unless that connection (`epoch`) is already over; the reconnector dials again."""
        with self.condition:
            if epoch != self.epoch or self.state is not LinkState.UP:
                return
            self.state = LinkState.DOWN
            self.reason = reason
            self.down_since = self.clock()
            self.attempts = 0
            self.reported_at = self.down_since
            self.hinted = False
            self.retire()
            self.condition.notify_all()
        log.warning(f'Lost connection to {self.name} ({reason}) - reconnecting (the site keeps running)')

    def fail(self: UpstreamLink, error: BaseException) -> None:
        """Fail the link for good with `error`, which every caller is given from now on."""
        with self.condition:
            if self.failure is None:
                self.failure = error
            self.state = LinkState.FAILED
            self.retire()
            self.condition.notify_all()

    def retire(self: UpstreamLink) -> None:
        """Retire the workers and release the client of the connection that is over (lock held)."""
        for worker in self.workers.values():
            worker.retire()
        self.workers = {}
        if self.client is not None:
            release_client(self.client)
            self.client = None
        self.passed_stages = set()

    # --- Reconnection ----------------------------------------------------------------------------

    def start(self: UpstreamLink) -> None:
        """Start the reconnector, which makes the first connection at once."""
        self.thread.start()

    def halt(self: UpstreamLink) -> None:
        """Stop reconnecting and close the connection; calls from now on raise `LinkDown`."""
        with self.condition:
            self.halted.set()
            if self.state is not LinkState.FAILED:
                self.state = LinkState.HALTED
                self.reason = 'link closed'
            self.retire()
            self.condition.notify_all()

    def stop(self: UpstreamLink, timeout: Optional[float] = None) -> None:
        """Halt and wait for the reconnector to end."""
        self.halt()
        if self.thread.ident is not None:
            self.thread.join(timeout)

    def run(self: UpstreamLink) -> None:
        """Connect, and connect again whenever the link goes down, until halted or failed."""
        while not self.halted.is_set():
            with self.condition:
                while self.state is LinkState.UP and not self.halted.is_set():
                    self.condition.wait()
                if self.state is not LinkState.DOWN:
                    return
            if self.connected or self.attempts:
                # A reconnection waits before its first attempt; the first connection tries at once.
                failed = self.attempts if self.connected else self.attempts - 1
                delay = backoff_delay(failed, self.rng(), self.backoff_base, self.backoff_cap)
                if self.sleep(delay):
                    return
            if self.halted.is_set():
                return
            self.attempt()

    def attempt(self: UpstreamLink) -> bool:
        """Make one connection attempt; return whether the link is up."""
        try:
            client = self.dial()
        except BaseException as error:
            if is_fatal(error) or not is_transport(error):
                # Raised to every caller as it is, so the program reports it once, as a client would.
                log.debug(f'Cannot use {self.name} ({error.__class__.__name__}: {error})')
                self.fail(error)
                return False
            self.failed_attempt(error)
            return False
        with self.condition:
            if self.halted.is_set():
                release_client(client)
                return False
            now = self.clock()
            self.client = client
            self.epoch += 1
            self.state = LinkState.UP
            self.outage = now - self.last_ok if self.connected else 0.0
            attempts = self.attempts + 1
            first = not self.connected
            self.connected = True
            self.last_ok = now
            self.attempts = 0
            self.passed_stages = set()
            self.condition.notify_all()
        if first:
            log.info(f'Connected to {self.name}')
        else:
            log.warning(f'Reconnected to {self.name} after {self.outage:.0f} seconds ({attempts} attempts)')
        return True

    def dial(self: UpstreamLink) -> Any:
        """A new connected client, from a thread given at most the deadline (left behind if it overruns)."""
        box: Dict[str, Any] = {}
        done = threading.Event()
        abandoned = threading.Event()

        def connect() -> None:
            try:
                client = self.factory()
                client.connect()
                box['client'] = client
            except BaseException as error:  # Handed to the reconnector.
                box['error'] = error
            finally:
                done.set()
                if abandoned.is_set() and 'client' in box:
                    release_client(box.pop('client'))

        thread = threading.Thread(target=connect, name='hypershell-link-connect', daemon=True)
        thread.start()
        if not done.wait(self.deadline):
            abandoned.set()
            if done.is_set() and 'client' in box:  # Finished just now, after all.
                release_client(box.pop('client'))
            raise TimeoutError(f'connection attempt took longer than {self.deadline:g} seconds')
        if 'error' in box:
            raise box['error']
        return box['client']

    def failed_attempt(self: UpstreamLink, error: BaseException) -> None:
        """Count a failed attempt and report the outage at the logging cadence."""
        with self.condition:
            self.attempts += 1
            attempts, now = self.attempts, self.clock()
            first = not self.connected and attempts == 1
            report = (attempts & (attempts - 1)) == 0 or now - self.reported_at >= REPORT_INTERVAL
            if report:
                self.reported_at = now
            hint = self.connected and not self.hinted and now - self.last_ok >= self.evict_hint
            if hint:
                self.hinted = True
            unreachable = now - (self.last_ok if self.connected else self.down_since)
        reason = f'{error.__class__.__name__}: {error}'
        if first:
            log.warning(f'Cannot reach {self.name} ({reason}) - trying again')
        elif report:
            log.info(f'Still cannot reach {self.name} after {unreachable:.0f} seconds '
                     f'({attempts} attempts; last: {reason})')
        if hint:
            log.warning(f'No contact with {self.name} for {unreachable:.0f} seconds, past {self.evict_hint:g} '
                        f'- it has likely evicted this relay, and results held for it will likely be refused '
                        f'as stale')


class FixedLink:
    """A connected client used as a link: calls run in the caller's thread, and it is always up."""

    def __init__(self: FixedLink, client: Any) -> None:
        self.client = client
        self.evict_hint = DEFAULT_EVICT_HINT
        self.outage = 0.0
        self.epoch = 1

    def call(self: FixedLink, role: str, fn: Callable[[Any], Any], deadline: Optional[float] = None) -> Any:  # noqa
        return fn(self.client)

    def is_up(self: FixedLink) -> bool:
        return True

    def ready(self: FixedLink, after: Iterable[str] = ()) -> bool:  # noqa: unused
        return True

    def wait_ready(self: FixedLink, after: Iterable[str] = (), timeout: float = 1.0) -> bool:  # noqa: unused
        return True

    def wait_up(self: FixedLink, timeout: float = 1.0) -> bool:  # noqa: unused
        return True

    def passed(self: FixedLink, stage: str, epoch: Optional[int] = None) -> None:
        pass

    def is_passed(self: FixedLink, stage: str) -> bool:  # noqa: unused
        return True


def as_link(queue: Any) -> Any:
    """`queue` as a link: an `UpstreamLink` (or anything with `call`) as it is, a connected client as a `FixedLink`."""
    return queue if callable(getattr(queue, 'call', None)) else FixedLink(queue)
