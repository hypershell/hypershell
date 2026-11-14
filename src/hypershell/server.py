# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Schedule and update bundles of tasks from the database.

The server can submit tasks for you so no need to directly submit before
invoking the server (see `hypershell.submit`).

Example:
    >>> from hypershell.server import serve_from
    >>> serve_from(['echo AA', 'echo BB', 'echo CC'])

To run a server process indefinitely (maybe as a service), invoke `serve_forever()`.
Other programs can submit tasks at a later point.

Example:
    >>> from hypershell.server import serve_forever
    >>> serve_forever(bundlesize=10, max_retries=1)

Embed a `ServerThread` in your application directly. Call `stop()` to stop early.
Clients cannot connect from a remote machine unless you set the `bind` address
to 0.0.0.0 (as opposed to localhost which is the default).

Example:
    >>> import sys
    >>> from hypershell.server import ServerThread
    >>> server_thread = ServerThread.new(source=sys.stdin, bind=('0.0.0.0', 8080))

Note:
    In order for the `ServerThread` to actively monitor the state set by `stop` and
    halt execution (a requirement because of how CPython does threading), the implementation
    uses a finite state machine. *You should not instantiate this machine directly*.

Warning:
    Because the `ServerThread` checks state actively to decide whether to halt, if your
    `source` is blocking (e.g., `sys.stdin`) it will not be able to halt immediately. If
    your main program exits however, the thread will be stopped regardless because it
    runs as a `daemon`.
"""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Tuple, Iterable, IO, Optional, Callable, Type, Final
from types import TracebackType

# Standard libs
import io
import sys
import json
import time
from enum import Enum
from numbers import Real
from datetime import datetime, timedelta
from functools import cached_property
from itertools import islice
from queue import Empty as QueueEmpty, Full as QueueFull

# External libs
from cmdkit.app import Application
from cmdkit.cli import Interface, ArgumentError
from sqlalchemy import func

# Internal libs
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.core.config import config, default, find_available_ports, AUTH_ALLOWED_CHARS, AUTH_MINIMUM_LENGTH, resolve_auth
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.core.types import parse_bytes, parse_time
from hypershell.core.logging import Logger
from hypershell.core.fsm import State, StateMachine
from hypershell.core.thread import Thread, join_waking
from hypershell.core.queue import QueueServer, QueueConfig, DEFAULT_REMOTE_TIMEOUT, make_sentinel
from hypershell.core.dispatch import Dispatcher
from hypershell.core.signal import check_signal, SIGNAL_MAP, SIGUSR1, SIGUSR2
from hypershell.core.heartbeat import Heartbeat, ClientState, RosterEntry
from hypershell.core.topology import SiteRegistry
from hypershell.data.model import (Task, Client, Zone, ResultOutcome, CONTENDED_RERUNS, serialize_tasks,
                                   deserialize_tasks, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME)
from hypershell.data import ensuredb, DATABASE_ENABLED
from hypershell.submit import SubmitThread, LiveSubmitThread, DEFAULT_BUNDLEWAIT, DEFAULT_TEMPLATE
from hypershell.client import ClientInfo

# Public interface
__all__ = ['serve_from', 'serve_file', 'serve_forever', 'ServerThread', 'ServerApp', 'is_file_input',
           'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_ATTEMPTS',
           'DEFAULT_EVICT', 'DEFAULT_EAGER_MODE', 'DEFAULT_SERVER_POLL',
           'DEFAULT_BIND', 'DEFAULT_PORT', 'DEFAULT_AUTH']

# Initialize logger
log = Logger.with_name(__name__)


class SchedulerState(State, Enum):
    """Finite states of the scheduler."""
    START = 0
    LOAD = 1
    PACK = 2
    FINAL = 3
    HALT = 4


DEFAULT_BUNDLESIZE: Final[int] = default.server.bundlesize
"""Default size for task bundles."""

DEFAULT_ATTEMPTS: Final[int] = default.server.attempts
"""Default number of attempts for task retries."""

DEFAULT_EAGER_MODE: Final[bool] = default.server.eager
"""When enabled tasks are retried immediately ahead scheduling new tasks."""

DEFAULT_QUERY_PAUSE: Final[int] = default.server.poll  # Backwards compatibility
DEFAULT_SERVER_POLL: Final[int] = default.server.poll
"""Default polling interval in seconds between database queries if no tasks are available."""

# Initial value and maximum value for exponential backoff on database queries
SERVER_POLL_MIN: Final[float] = 0.5
SERVER_POLL_MAX: Final[float] = 30.0


class Scheduler(StateMachine):
    """Stage tasks from database for clients to request.

    Each zone a client serves (and the default zone, always) has a staging pool in the queue
    server's dispatcher. LOAD selects one chunk for every pool at or below its refill mark, with
    the same stamping as always; PACK encodes them and hands them to the dispatcher. With nothing to refill,
    the scheduler waits (bounded) until a client takes tasks or a new zone asks for work.
    """

    tasks: List[Task]
    loaded: List[Tuple[str, List[Task]]]
    queue: QueueServer

    bundlesize: int
    attempts: int
    eager: bool
    forever_mode: bool
    restart_mode: bool
    poll: float
    poll_max: float
    startup_phase: bool
    first_group_pending: bool
    highest_started_group: Optional[int]
    last_scheduled_group: Optional[int]
    current_group: Optional[int]
    submitter: Optional[SubmitThread]

    state = SchedulerState.START
    states = SchedulerState


    def __init__(self: Scheduler, queue: QueueServer, bundlesize: int = DEFAULT_BUNDLESIZE,
                 attempts: int = DEFAULT_ATTEMPTS, eager: bool = DEFAULT_EAGER_MODE,
                 forever_mode: bool = False, restart_mode: bool = False,
                 poll: int = DEFAULT_SERVER_POLL, submitter: Optional[SubmitThread] = None) -> None:
        """Initialize queue and parameters."""
        self.queue = queue
        self.tasks = []
        self.loaded = []
        self._own_dispatcher = None
        self.bundlesize = bundlesize
        self.attempts = attempts
        self.eager = eager
        self.forever_mode = forever_mode
        self.restart_mode = restart_mode
        self.poll = SERVER_POLL_MIN
        self.poll_max = poll
        self.startup_phase = not self.restart_mode  # NOTE: Halt if everything in the database is already finished
        self.first_group_pending = False
        self.highest_started_group = None
        self.last_scheduled_group = None
        self.current_group = None
        self.submitter = submitter
        self.held = set()
        self.woken = False

    @property
    def dispatcher(self: Scheduler) -> Dispatcher:
        """The queue server's dispatcher, which holds the staging pools.

        A scheduler built without a queue server (driven in-process) stages into a dispatcher
        of its own.
        """
        dispatcher = getattr(self.queue, 'dispatcher', None)
        if dispatcher is None:
            if self._own_dispatcher is None:
                self._own_dispatcher = Dispatcher(bundlesize=self.bundlesize)
            dispatcher = self._own_dispatcher
        return dispatcher

    def submission_complete(self: Scheduler) -> bool:
        """True once no more tasks can arrive from a co-running submitter.

        The scheduler shares a process with an optional :class:`~hypershell.submit.SubmitThread`
        (the cluster/server file-ingest path). It must not conclude "all tasks completed —
        stop" while that submitter is still committing rows: a database holding only prior
        *completed* tasks reads as ``remaining == 0`` right up until the first novel row lands,
        so an unguarded early-exit would halt before the new work is ever scheduled. When there
        is no submitter (bare ``--restart`` DB resume) submission is trivially complete.
        """
        return self.submitter is None or not self.submitter.is_alive()

    @cached_property
    def actions(self: Scheduler) -> Dict[SchedulerState, Callable[[], SchedulerState]]:
        return {
            SchedulerState.START: self.start,
            SchedulerState.LOAD: self.load_bundle,
            SchedulerState.PACK: self.pack_bundle,
            SchedulerState.FINAL: self.finalize,
        }

    def start(self: Scheduler) -> SchedulerState:
        """Initial setup then jump to LOAD state."""
        log.debug('Started (scheduler)')
        Zone.ensure(DEFAULT_ZONE_NAME)
        if self.forever_mode:
            log.info('Scheduler will run forever')
        if (total_tasks := Task.count()) > 0:
            if (tasks_remaining := Task.count_remaining()) == 0:
                if self.forever_mode:
                    log.info(f'All previous tasks completed ({total_tasks:,}) - waiting for new tasks')
                elif not self.submission_complete():
                    # A submitter is still ingesting (e.g. `hsx FILE --restart`): the DB looks
                    # done only because its novel rows have not landed yet. Wait in LOAD.
                    log.info(f'All previous tasks completed ({total_tasks:,}) - waiting for incoming tasks')
                else:
                    log.info(f'All previous tasks completed ({total_tasks:,}) - stopping')
                    return SchedulerState.FINAL
            else:
                tasks_interrupted = Task.count_interrupted()
                log.info(f'Found {tasks_remaining} unfinished tasks')
                Task.revert_interrupted()
                log.info(f'Reverted {tasks_interrupted} previously interrupted tasks')
        else:
            log.info('Empty database - waiting for initial tasks')
            self.first_group_pending = True
        group_info = Task.current_group()
        log.debug(f'Starting with task group {group_info.value} ({group_info.reason})')
        self.current_group = self.starting_group(group_info.value)
        return SchedulerState.LOAD

    def load_bundle(self: Scheduler) -> SchedulerState:
        """Load the next chunk of tasks from the database for each pool that needs one."""
        if check_signal() in (SIGUSR1, SIGUSR2):
            log.warning(f'Signal interrupt ({SIGNAL_MAP[check_signal()]})')
            return SchedulerState.FINAL
        self.revert_unstaged()
        self.revert_handed_back()
        generation, refills = self.dispatcher.refills()
        if not refills:
            self.dispatcher.wait_need(generation, timeout=DEFAULT_REMOTE_TIMEOUT)
            return SchedulerState.LOAD
        self.loaded = self.next_tasks(refills)
        if self.loaded:
            self.tasks = [task for _, tasks in self.loaded for task in tasks]
            self.mark_group_started()
            self.startup_phase = False
            self.poll = SERVER_POLL_MIN
            self.woken = False
            return SchedulerState.PACK
        elif (not self.forever_mode and Task.count() > 0 and Task.count_remaining() == 0
              and Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0
              and (not self.startup_phase or self.submission_complete())):
            # Nothing left to schedule, and either real work has already been seen this run
            # or the submitter has finished — safe to stop (won't fire mid-ingest).
            # A failure written back after Task.next() is still retried before the run stops;
            # retryable failures are counted after incomplete tasks so that write is always seen.
            return SchedulerState.FINAL
        else:
            group_info = Task.increment_group(self.current_group, attempts=self.attempts, eager=self.eager)
            if not group_info.viable:
                if self.forever_mode:
                    log.warning(f'Failed task group {group_info.value} ({group_info.reason})')
                    group_info.reason = f'waiting on failed tasks to be cleared'
                    # NOTE: Do not halt in this mode
                else:
                    log.critical(f'Failed task group {group_info.value} ({group_info.reason})')
                    return SchedulerState.FINAL
            elif group_info.value != self.current_group:
                log.info(f'Completed task group {self.current_group} - starting task group {group_info.value}')
                self.current_group = group_info.value
                return SchedulerState.LOAD
            self.report_held(group_info.value)
            # A back-off cut short by a client taking tasks was not an empty poll; only a wait that
            # ran its full length grows the next one.
            if not self.woken:
                self.poll = min(self.poll_max, 2 * self.poll)
            log.trace(f'No tasks available for group ({group_info.reason}) - backing off {int(self.poll)} seconds')
            self.woken = self.dispatcher.wait_need(generation, timeout=self.poll)
            return SchedulerState.LOAD

    def next_tasks(self: Scheduler, refills: List[Tuple[str, int]]) -> List[Tuple[str, List[Task]]]:
        """Select the next tasks to schedule from the current group, for each zone that needs a refill."""
        if self.first_group_pending:
            # On an empty-database start no task is scheduled into a group chosen before any task
            # existed: the first group is the lowest one holding tasks, once there are any.
            # Groups order the whole run, so the first group is chosen across every zone.
            first_group = Task.lowest_incomplete_group()
            if first_group is None:
                return []
            self.current_group = first_group
        loaded = []
        for zone, size in refills:
            tasks = Task.next(limit=size, attempts=self.attempts, eager=self.eager,
                              group=self.current_group, zone=zone)
            if tasks:
                loaded.append((zone, tasks))
        return loaded

    def mark_group_started(self: Scheduler) -> None:
        """Record that the current group has scheduled tasks, warning if a higher group started first."""
        group = self.current_group
        if group != self.last_scheduled_group:
            # SQLite can store a non-integer group as text, which has no order against an integer
            # group: it is never tracked as the highest group and never warned on.
            if isinstance(group, Real):
                highest = self.highest_started_group
                if highest is not None and group < highest:
                    log.warning(f'Starting task group {group} after task group {highest} (tasks submitted late)')
                self.highest_started_group = group if highest is None else max(group, highest)
            self.last_scheduled_group = group
        self.first_group_pending = False

    def starting_group(self: Scheduler, group: int) -> int:
        """
        The task group to start in, given the most recently scheduled `group`.

        That group is kept while it holds failures still to be retried or halted on. Otherwise the
        start is the lowest group holding incomplete tasks, so new tasks on a database whose earlier
        work finished at a higher group run in ascending order. With no incomplete tasks yet, the
        first group is chosen once tasks arrive, as on an empty database.
        """
        if self.first_group_pending:
            return group
        start = Task.starting_group(group, attempts=self.attempts, eager=self.eager)
        if start is None:
            log.debug('No incomplete tasks - first task group chosen once tasks arrive')
            self.first_group_pending = True
            return group
        if start != group:
            log.debug(f'Moving to task group {start} (lowest group with incomplete tasks)')
        return start

    @property
    def active_group(self: Scheduler) -> Optional[int]:
        """
        The task group tasks are loaded from now, or None while it is not yet known.

        None before the scheduler has started, and while it waits for the first tasks to choose
        its first group. Read from other threads (the autoscaler), so it only reads attributes.
        """
        return None if self.first_group_pending else self.current_group

    def pack_bundle(self: Scheduler) -> SchedulerState:
        """Hand the loaded chunks, encoded once here, to their zones' pools."""
        for zone, tasks in self.loaded:
            self.dispatcher.stage(zone, [(task.id, json.dumps(task.to_json())) for task in tasks])
            log.debug(f'Scheduled {len(tasks)} tasks')
            for task in tasks:
                log.trace(f'Scheduled task ({task.id})')
        self.loaded = []
        return SchedulerState.LOAD

    def report_held(self: Scheduler, group: int) -> None:
        """Log, once per zone, that `group` waits on tasks of a zone no client serves.

        A zone counts when it has tasks of the group not yet scheduled and no client asking for
        it. The default zone counts only while some client serves another zone, so a run without
        zones logs nothing new.
        """
        waiting = dict(Task.query(Task.zone, func.count(Task.id))
                       .filter(Task.group == group)
                       .filter(Task.schedule_time.is_(None))
                       .group_by(Task.zone).all())
        if None in waiting:
            waiting[DEFAULT_ZONE_ID] = waiting.pop(None) + self.dispatcher.staged(DEFAULT_ZONE_ID)
        elif self.dispatcher.staged(DEFAULT_ZONE_ID):
            waiting[DEFAULT_ZONE_ID] = self.dispatcher.staged(DEFAULT_ZONE_ID)
        zoned = any(self.dispatcher.clients(zone) for zone in waiting if zone != DEFAULT_ZONE_ID)
        idle = [zone for zone in waiting if (group, zone) not in self.held and not self.dispatcher.clients(zone)
                and (zone != DEFAULT_ZONE_ID or zoned or self.dispatcher.zoned())]
        if not idle:
            return
        names = Zone.names_for_ids([zone for zone in idle if zone != DEFAULT_ZONE_ID])
        names[DEFAULT_ZONE_ID] = DEFAULT_ZONE_NAME
        for zone in idle:
            self.held.add((group, zone))
            log.info(f'Task group {group} waiting on zone {names.get(zone, zone)}: '
                     f'{waiting[zone]} tasks, no client serving it')

    def revert_unstaged(self: Scheduler) -> None:
        """Revert the rows staged for zones whose last client is gone, so another run can take them.

        A row cancelled, completed or deleted while staged is left as it is.
        """
        if ids := self.dispatcher.take_unstaged():
            reverted = Task.revert_staged(ids)
            kept = f'; {len(ids) - reverted} left as completed, cancelled or deleted' if reverted < len(ids) else ''
            log.info(f'Reverted {reverted} staged tasks (zone has no client{kept})')

    def revert_handed_back(self: Scheduler) -> None:
        """Requeue the tasks retired relays handed back, each only while it is still that relay's assignment.

        A task completed, cancelled, dispatched again or deleted since is left as it is.
        """
        for identity, assignments in self.dispatcher.take_handbacks():
            reverted = len(Task.revert_handed_back(identity, assignments))
            kept = f'; {len(assignments) - reverted} already settled' if reverted < len(assignments) else ''
            log.info(f'Requeued {reverted} tasks handed back by relay ({identity}{kept})')

    @staticmethod
    def finalize() -> SchedulerState:
        """Stop scheduler."""
        log.debug('Done (scheduler)')
        return SchedulerState.HALT


class SchedulerThread(Thread):
    """Run scheduler within dedicated thread."""

    def __init__(self: SchedulerThread,
                 queue: QueueServer,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 attempts: int = DEFAULT_ATTEMPTS,
                 eager: bool = DEFAULT_EAGER_MODE,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 poll: int = DEFAULT_SERVER_POLL,
                 submitter: Optional[SubmitThread] = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-scheduler')
        self.machine = Scheduler(queue=queue, bundlesize=bundlesize, attempts=attempts, eager=eager,
                                 forever_mode=forever_mode, restart_mode=restart_mode, poll=poll,
                                 submitter=submitter)

    def run_with_exceptions(self: SchedulerThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: SchedulerThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (scheduler)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class ConfirmState(State, Enum):
    """Finite states for task confirmation machine."""
    START = 0
    UNLOAD = 1
    UNPACK = 2
    UPDATE = 3
    FINAL = 4
    HALT = 5


class Confirm(StateMachine):
    """Collect task bundle confirmations from clients and update database."""

    in_memory: bool
    queue: QueueServer
    client_data: Optional[bytes]
    client_info: Optional[ClientInfo]

    state = ConfirmState.START
    states = ConfirmState

    def __init__(self: Confirm, queue: QueueServer, in_memory: bool = False, strict: bool = True) -> None:
        """Initialize machine; with `strict` false, a confirmed task whose row is gone is skipped, not raised."""
        self.in_memory = in_memory
        self.strict = strict
        self.queue = queue
        self.client_data = None
        self.client_info = None

    @cached_property
    def actions(self: Confirm) -> Dict[ConfirmState, Callable[[], ConfirmState]]:
        return {
            ConfirmState.START: self.start,
            ConfirmState.UNLOAD: self.unload_info,
            ConfirmState.UNPACK: self.unpack_info,
            ConfirmState.UPDATE: self.update_info,
            ConfirmState.FINAL: self.finalize,
        }

    @staticmethod
    def start() -> ConfirmState:
        """Jump to UNLOAD state."""
        log.debug('Started (confirm)')
        return ConfirmState.UNLOAD

    def unload_info(self: Confirm) -> ConfirmState:
        """Get the next task bundle confirmation from shared queue."""
        try:
            self.client_data = self.queue.confirmed.get(timeout=DEFAULT_REMOTE_TIMEOUT)
            self.queue.confirmed.task_done()
            return ConfirmState.UNPACK
        except QueueEmpty:
            return ConfirmState.UNLOAD

    def unpack_info(self: Confirm) -> ConfirmState:
        """Unpack received client info."""
        self.client_info = ClientInfo.unpack_or_none(self.client_data)
        if self.client_info is None:
            return ConfirmState.FINAL
        else:
            log.debug(f'Confirmed {len(self.client_info.task_ids)} tasks '
                      f'({self.client_info.client_host}: {self.client_info.client_id})')
            return ConfirmState.UPDATE

    def update_info(self: Confirm) -> ConfirmState:
        """Update client info in database for confirmed task bundle."""
        if not self.in_memory:
            info = self.client_info
            Task.confirm_all(info.task_ids, info.client_id, info.client_host, strict=self.strict)
        return ConfirmState.UNLOAD

    @staticmethod
    def finalize() -> ConfirmState:
        """Return HALT."""
        log.debug('Done (confirm)')
        return ConfirmState.HALT


class ConfirmThread(Thread):
    """Run Confirm machine within dedicated thread."""

    def __init__(self: ConfirmThread, queue: QueueServer, in_memory: bool = False) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-confirm')
        self.machine = Confirm(queue=queue, in_memory=in_memory)

    def run_with_exceptions(self: ConfirmThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: ConfirmThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (confirm)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class ReceiverState(State, Enum):
    """Finite states for receiver."""
    START = 0
    UNLOAD = 1
    UNPACK = 2
    UPDATE = 3
    FINAL = 4
    HALT = 5


class Receiver(StateMachine):
    """Collect incoming finished task bundles and update database."""

    queue: QueueServer
    tasks: List[Task]
    held: List[Task]
    bundle: Optional[bytes]

    in_memory: bool
    redirect_failures: IO

    state = ReceiverState.START
    states = ReceiverState

    def __init__(self: Receiver, queue: QueueServer, in_memory: bool = False, redirect_failures: IO = None) -> None:
        """Initialize receiver."""
        self.queue = queue
        self.tasks = []
        self.held = []  # Results whose row still qualifies but whose write did not land yet.
        self.bundle = None
        self.in_memory = in_memory
        self.redirect_failures = redirect_failures

    @cached_property
    def actions(self: Receiver) -> Dict[ReceiverState, Callable[[], ReceiverState]]:
        return {
            ReceiverState.START: self.start,
            ReceiverState.UNLOAD: self.unload_bundle,
            ReceiverState.UNPACK: self.unpack_bundle,
            ReceiverState.UPDATE: self.update_tasks,
            ReceiverState.FINAL: self.finalize,
        }

    @staticmethod
    def start() -> ReceiverState:
        """Jump to UNLOAD state."""
        log.debug('Started (receiver)')
        return ReceiverState.UNLOAD

    def unload_bundle(self: Receiver) -> ReceiverState:
        """Get the next bundle from the completed task queue."""
        try:
            self.bundle = self.queue.completed.get(timeout=DEFAULT_REMOTE_TIMEOUT)
            self.queue.completed.task_done()
            return ReceiverState.UNPACK
        except QueueEmpty:
            if self.held:
                # Held results are presented again on every cycle, not only when a bundle arrives.
                self.tasks = []
                return ReceiverState.UPDATE
            log.trace('No completed tasks returned - waiting')
            return ReceiverState.UNLOAD

    def unpack_bundle(self: Receiver) -> ReceiverState:
        """Unpack previous bundle into list of tasks."""
        data = deserialize_tasks(self.bundle)
        if data is None:
            for task in self.held:
                log.warning(f'Result for task ({task.id}) not written at shutdown - task left interrupted')
            return ReceiverState.FINAL
        else:
            self.tasks = data
            return ReceiverState.UPDATE

    def update_tasks(self: Receiver) -> ReceiverState:
        """Update tasks in database with run details."""
        # A result applies only to the dispatch that produced it; anything else is logged and dropped.
        # A result whose write did not land while its row still qualifies is held and presented again.
        held = {id(task) for task in self.held}
        results = self.held + self.tasks
        if self.in_memory:
            outcomes = [ResultOutcome.APPLIED] * len(results)
        else:
            for task in self.held:
                log.debug(f'Retrying held result for task ({task.id})')
            outcomes = Task.apply_results(results)
        self.held = []
        for task, outcome in zip(results, outcomes):
            if outcome is ResultOutcome.DEFERRED:
                if id(task) not in held:
                    log.warning(f'Contended result write did not land for task ({task.id}) '
                                f'after {1 + CONTENDED_RERUNS} attempts - result held for retry')
                self.held.append(task)
                continue
            if outcome is ResultOutcome.STALE:
                log.warning(f'Refused stale result for task ({task.id}) '
                            f'from client ({task.client_host}: {task.client_id})')
                continue
            if outcome is ResultOutcome.DUPLICATE:
                log.warning(f'Ignored duplicate result for task ({task.id}) '
                            f'from client ({task.client_host}: {task.client_id})')
                continue
            log.debug(f'Completed task ({task.id})')
            if task.exit_status != 0:
                log.warning(f'Non-zero exit status ({task.exit_status}) for task ({task.id})')
                if self.redirect_failures:
                    print(task.args, file=self.redirect_failures)
        return ReceiverState.UNLOAD

    @staticmethod
    def finalize() -> ReceiverState:
        """Return HALT."""
        log.debug('Done (receiver)')
        return ReceiverState.HALT


class ReceiverThread(Thread):
    """Run receiver within dedicated thread."""

    def __init__(self: ReceiverThread,
                 queue: QueueServer,
                 in_memory: bool = False,
                 redirect_failures: IO = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-receiver')
        self.machine = Receiver(queue=queue, in_memory=in_memory, redirect_failures=redirect_failures)

    def run_with_exceptions(self: ReceiverThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: ReceiverThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (receiver)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class HeartbeatState(State, Enum):
    """Finite states of the heartbeat machine."""
    START = 0
    NEXT = 1
    UPDATE = 2
    SWITCH = 3
    CHECK = 4
    SIGNAL = 5
    FINAL = 6
    HALT = 7


DEFAULT_EVICT: Final[int] = default.server.evict
"""Default client eviction period in seconds."""


class HeartMonitor(StateMachine):
    """
    Collect heartbeat messages from connected clients.

    A relay's heartbeat also carries its site and its roster, the site clients connected to it. The
    site clients are registered (attributed to the site and the relay) and disconnected as they
    come and go from the roster, but never enter `beats`: only their relay is evicted for missed
    heartbeats, and its eviction or final heartbeat disconnects its whole roster.

    With a site registry (a server autoscaling with relay sites), each beat of a relay identity
    that advertises its zone is recorded there, overwriting that identity's previous record, and
    the record is dropped when the identity is evicted or finishes. A relay identity that
    advertises no zone (a relay of an earlier release) takes no part in the topology.

    A client is aged by the server's own monotonic clock, from when its last heartbeat was received,
    never by the time the client stamped on it, so a client that keeps beating is never evicted
    however far its clock is from the server's.
    """

    in_memory: bool
    no_confirm: bool
    queue: QueueServer
    beats: Dict[str, Heartbeat]
    received: Dict[str, float]
    rosters: Dict[str, Dict[str, RosterEntry]]
    sites: Optional[SiteRegistry]
    ignored_sites: set
    last_check: datetime
    wait_check: timedelta
    evict_after: timedelta

    startup_phase: bool = True  # should not halt until at least one client
    scheduler_done: bool = False  # set by parent thread when scheduling is over
    should_signal: bool = False  # set by parent thread to signal clients
    latest_heartbeat: Heartbeat = None
    latest_received: float = None

    state = HeartbeatState.START
    states = HeartbeatState

    def __init__(self: HeartMonitor, queue: QueueServer, evict_after: int = DEFAULT_EVICT,
                 in_memory: bool = False, no_confirm: bool = False, sites: Optional[SiteRegistry] = None) -> None:
        """Initialize with queue server."""
        self.queue = queue
        self.last_check = datetime.now().astimezone()
        self.beats = {}
        self.received = {}
        self.rosters = {}
        self.sites = sites
        self.ignored_sites = set()
        self.in_memory = in_memory
        self.no_confirm = no_confirm
        if evict_after >= 10:
            self.wait_check = timedelta(seconds=int(evict_after / 10))
            self.evict_after = timedelta(seconds=evict_after)
        else:
            raise RuntimeError(f'Evict period must be greater than 10 seconds: given {evict_after}')

    @cached_property
    def actions(self: HeartMonitor) -> Dict[HeartbeatState, Callable[[], HeartbeatState]]:
        return {
            HeartbeatState.START: self.start,
            HeartbeatState.NEXT: self.get_next,
            HeartbeatState.UPDATE: self.update_client,
            HeartbeatState.SWITCH: self.switch_mode,
            HeartbeatState.CHECK: self.check_clients,
            HeartbeatState.SIGNAL: self.signal_clients,
            HeartbeatState.FINAL: self.finalize,
        }

    @staticmethod
    def start() -> HeartbeatState:
        """Jump to NEXT state."""
        log.debug('Started (heartbeat)')
        return HeartbeatState.NEXT

    def get_next(self: HeartMonitor) -> HeartbeatState:
        """Get and stash heartbeat from clients."""
        try:
            hb_data = self.queue.heartbeat.get(timeout=DEFAULT_REMOTE_TIMEOUT)
            received = time.monotonic()
            self.queue.heartbeat.task_done()
            self.startup_phase = False
            hb = Heartbeat.unpack_or_none(hb_data)
            if hb is None:
                return HeartbeatState.FINAL
            else:
                self.latest_heartbeat = hb
                self.latest_received = received
                return HeartbeatState.UPDATE
        except QueueEmpty:
            return HeartbeatState.SWITCH

    def update_client(self: HeartMonitor) -> HeartbeatState:
        """Update client with heartbeat or disconnect."""
        hb = self.latest_heartbeat
        if hb.state is not ClientState.FINISHED:
            if hb.uuid in self.beats:
                log.trace(f'Heartbeat - running ({hb.host}: {hb.uuid})')
            else:
                log.debug(f'Registered client ({hb.host}: {hb.uuid})')
                if hb.site is not None:
                    self.check_relay(hb)
                if not self.in_memory:
                    new_client = Client.from_heartbeat(hb)
                    try:
                        # Check to see if we are re-registering a falsely-evicted client (Issue #29)
                        old_client = Client.from_id(new_client.id)
                        log.warning(f'Existing client re-registered ({old_client.host}: {old_client.id})')
                        Client.reconnect(old_client.id, **self.relay_fields(hb))
                    except Client.NotFound:
                        Client.add(new_client)
            self.beats[hb.uuid] = hb
            self.received[hb.uuid] = self.latest_received
            if hb.roster is not None:
                self.update_roster(hb)
            if self.sites is not None and hb.site is not None:
                self.record_site(hb)
            return HeartbeatState.SWITCH
        else:
            log.trace(f'Client disconnected ({hb.host}: {hb.uuid})')
            if hb.uuid in self.beats:
                self.beats.pop(hb.uuid)
                self.received.pop(hb.uuid, None)
                if not self.in_memory:
                    Client.update(hb.uuid, disconnected_at=datetime.now().astimezone())
            if hb.site is not None or hb.uuid in self.rosters:
                self.end_roster(hb.uuid)
            self.drop_site(hb.uuid)
            self.queue.dispatcher.forget(hb.uuid)
            return HeartbeatState.SWITCH

    def record_site(self: HeartMonitor, hb: Heartbeat) -> None:
        """Record a relay identity's advertisement, overwriting its previous one."""
        if hb.zone is None or hb.bounds is None or hb.clients is None:
            if hb.uuid not in self.ignored_sites:
                self.ignored_sites.add(hb.uuid)
                log.info(f'Relay ({hb.host}: {hb.uuid}, site {hb.site}) advertises no zone or bounds: '
                         f'it takes no part in the site topology')
            return
        known = hb.uuid in self.sites
        record = self.sites.record(hb.uuid, site=hb.site, zone=hb.zone, bounds=hb.bounds,
                                   clients=hb.clients, host=hb.host, instance=hb.instance, retiring=hb.retiring)
        if not known:
            log.info(f'Relay site registered (site: {record.site}, zone: {record.zone}, '
                     f'min: {record.bounds.min}, init: {record.bounds.init}, max: {record.bounds.max}, '
                     f'clients: {record.clients}) ({hb.host}: {hb.uuid})')

    def drop_site(self: HeartMonitor, uuid: str) -> None:
        """Forget a relay identity's advertisement as it is evicted or finishes."""
        if self.sites is not None:
            self.ignored_sites.discard(uuid)
            record = self.sites.drop(uuid)
            if record is not None:
                log.info(f'Relay site left (site: {record.site}, zone: {record.zone}) ({record.host}: {uuid})')

    @staticmethod
    def relay_fields(hb: Heartbeat) -> Dict[str, str]:
        """Client columns a relay's heartbeat sets on its own row (none for an ordinary client)."""
        return {} if hb.site is None else {'site': hb.site, 'relay': hb.uuid}

    def check_relay(self: HeartMonitor, hb: Heartbeat) -> None:
        """A relay registering here relies on confirmation and the database to fail over; say so if off."""
        if self.in_memory or self.no_confirm:
            mode = 'without a database' if self.in_memory else 'without confirmation'
            log.error(f'Relay registered ({hb.host}: {hb.uuid}, site {hb.site}) with a server running {mode}: '
                      f'tasks it holds are not put back if it fails')

    def update_roster(self: HeartMonitor, hb: Heartbeat) -> None:
        """Register the site clients new to a relay's roster and disconnect those gone from it.

        An entry naming a client already attributed to another relay is ignored: a roster applies only
        to clients with no relay or with the sender as their relay, so it never re-attributes or
        disconnects another relay's site clients.
        """
        relay, previous = hb.uuid, self.rosters.get(hb.uuid, {})
        current = {entry.uuid: entry for entry in hb.roster
                   if entry.state is not ClientState.FINISHED and entry.uuid != relay and entry.uuid not in self.beats}
        self.rosters[relay] = current
        added = [entry for uuid, entry in current.items() if uuid not in previous]
        dropped = [uuid for uuid in previous if uuid not in current]
        for uuid in dropped:
            log.debug(f'Site client disconnected ({previous[uuid].host}: {uuid}) via relay {relay}')
        if self.in_memory:
            for entry in added:
                log.debug(f'Registered site client ({entry.host}: {entry.uuid}) via relay {relay}')
            return
        for entry in added:
            fields = {'site': hb.site, 'relay': relay}
            try:
                holder = Client.from_id(entry.uuid, caching=False).relay
            except Client.NotFound:
                Client.add(Client.new(id=entry.uuid, host=entry.host, connected_at=hb.time, **fields))
            else:
                if holder is not None and str(holder) != relay:
                    log.warning(f'Ignored roster entry ({entry.host}: {entry.uuid}) from {relay} '
                                f'(a site client of relay {holder})')
                    current.pop(entry.uuid)
                    continue
                Client.reconnect(entry.uuid, **fields)
            log.debug(f'Registered site client ({entry.host}: {entry.uuid}) via relay {relay}')
        Client.disconnect_all(dropped, datetime.now().astimezone())

    def end_roster(self: HeartMonitor, relay: str) -> None:
        """Disconnect every site client a relay forwarded, as the relay itself goes."""
        roster = self.rosters.pop(relay, {})
        if roster:
            log.debug(f'Site clients disconnected with relay {relay} ({len(roster)})')
        if not self.in_memory:
            Client.disconnect_roster(relay, datetime.now().astimezone())

    def switch_mode(self: HeartMonitor) -> HeartbeatState:
        """Decide to bail, signal, check, or get another heartbeat."""
        if self.startup_phase:
            return HeartbeatState.NEXT
        if self.should_signal:
            return HeartbeatState.SIGNAL
        if not self.beats and self.scheduler_done:
            return HeartbeatState.FINAL
        now = datetime.now().astimezone()
        if (now - self.last_check) > self.wait_check:
            self.last_check = now
            return HeartbeatState.CHECK
        else:
            return HeartbeatState.NEXT

    def check_clients(self: HeartMonitor) -> HeartbeatState:
        """Evict every client whose last heartbeat arrived more than the eviction period ago.

        Ages are taken by the server's monotonic clock against when each last heartbeat was received.
        A client with no receive time is never evicted.
        """
        log.debug(f'Checking clients ({len(self.beats)} connected)')
        now = time.monotonic()
        for uuid in list(self.beats):
            hb = self.beats.get(uuid)
            received = self.received.get(uuid)
            if received is None:
                continue
            age = timedelta(seconds=now - received)
            if age > self.evict_after:
                log.warning(f'Evicting client ({hb.host}: {uuid})')
                self.beats.pop(uuid)
                self.received.pop(uuid, None)
                if not self.in_memory:
                    Client.update(hb.uuid, disconnected_at=datetime.now().astimezone(), evicted=True)
                if not self.in_memory and not self.no_confirm:
                    log.warning(f'Reverting orphaned tasks ({hb.host}: {uuid})')
                    Task.revert_orphaned(uuid)
                if hb.site is not None or uuid in self.rosters:
                    self.end_roster(uuid)
                self.drop_site(uuid)
                self.queue.dispatcher.forget(uuid)
        return HeartbeatState.SWITCH

    def signal_clients(self: HeartMonitor) -> HeartbeatState:
        """Tell clients to shut down once they have drained what is staged for them."""
        log.debug(f'Signaling clients ({len(self.beats)} connected)')
        self.queue.dispatcher.finish()
        for hb in self.beats.values():
            log.debug(f'Disconnect requested ({hb.host}: {hb.uuid})')
        self.should_signal = False
        return HeartbeatState.SWITCH

    @staticmethod
    def finalize() -> HeartbeatState:
        """Stop heart monitor."""
        log.debug('Done (heartbeat)')
        return HeartbeatState.HALT


class HeartMonitorThread(Thread):
    """Run heart monitor within dedicated thread."""

    def __init__(self: HeartMonitorThread, queue: QueueServer, evict_after: int = DEFAULT_EVICT,
                 in_memory: bool = False, no_confirm: bool = False, sites: Optional[SiteRegistry] = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-heartmonitor')
        self.machine = HeartMonitor(queue=queue, evict_after=evict_after,
                                    in_memory=in_memory, no_confirm=no_confirm, sites=sites)

    def run_with_exceptions(self: HeartMonitorThread) -> None:
        """Run machine."""
        self.machine.run()

    def signal_clients(self: HeartMonitorThread) -> None:
        """Tell clients to shut down once they have drained what is staged for them.

        Dispatch finishes at once, so a request is told to shut down without waiting for the heart
        monitor's next turn (which can wait out a heartbeat read); the heart monitor then logs the
        signal and the clients it was sent to.
        """
        log.debug('Finishing dispatch - clients drain what is staged, then are told to shut down')
        self.machine.queue.dispatcher.finish()
        self.machine.should_signal = True

    def signal_scheduler_done(self: HeartMonitorThread) -> None:
        """Set flag to tell heart monitor that scheduler is done."""
        self.machine.scheduler_done = True

    def stop(self: HeartMonitorThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (heartbeat)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_BIND: Final[str] = QueueConfig.host
"""Default bind address for server."""

DEFAULT_PORT: Final[int] = QueueConfig.port
"""Default port number for server."""

DEFAULT_AUTH: Final[str] = QueueConfig.auth
"""Default authentication key for server (**DO NOT USE THIS**)."""


class ServerThread(Thread):
    """
    Run server within dedicated thread.

    Args:
        source (Iterable[str], optional):
            Any iterable of command-line tasks.
            A new `source` results in a :class:`~hypershell.submit.SubmitThread` populating
            either the database or the queue directly depending on `in_memory`.

        task_cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        task_memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        task_timeout (int, optional):
            Task-level walltime limit in seconds (default: none).

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        forever_mode (bool, optional):
            Regardless of `source`, if enabled schedule forever.
            Conflicts with `restart_mode` and `in_memory`. Default is `False`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            If True, do not check for client confirmation messages.

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

        address (tuple, optional):
            Bind address for server with port number.
            See :const:`DEFAULT_BIND` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key. See :const:`DEFAULT_AUTH`.

        max_retries (int, optional):
            Number of allowed task retries. See :const:`DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (IO, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

        sites (SiteRegistry, optional):
            Record the advertisements of relay identities here (a server autoscaling with
            relay sites). Without it, nothing is recorded.

    Example:
        >>> from hypershell.server import ServerThread
        >>> server = ServerThread.new(restart_mode=True, auth='my-secret-key')
        >>> server.join()

    See Also:
        - :meth:`serve_from`
        - :meth:`serve_file`
        - :meth:`serve_forever`
    """

    queue: QueueServer
    submitter: Optional[SubmitThread | LiveSubmitThread]
    scheduler: Optional[SchedulerThread]
    confirm: Optional[ConfirmThread]
    receiver: ReceiverThread
    heartmonitor: HeartMonitorThread
    in_memory: bool
    no_confirm: bool
    sites: Optional[SiteRegistry]

    def __init__(self: ServerThread,
                 source: Iterable[str] = None,
                 task_cores: int = None,
                 task_memory: int = None,
                 task_timeout: int = None,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 in_memory: bool = False,
                 no_confirm: bool = False,
                 poll: int = DEFAULT_SERVER_POLL,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
                 auth: str = DEFAULT_AUTH,
                 max_retries: int = DEFAULT_ATTEMPTS - 1,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 evict_after: int = DEFAULT_EVICT,
                 template: str = DEFAULT_TEMPLATE,
                 from_json: bool = False,
                 tls: Optional[TLSConfig] = None,
                 sites: Optional[SiteRegistry] = None) -> None:
        """Initialize queue manager and child threads."""
        self.in_memory = in_memory
        self.no_confirm = no_confirm
        self.sites = sites
        if not self.in_memory and not DATABASE_ENABLED:
            log.warning('No database configured - automatically disabled')
            self.in_memory = True
        if self.in_memory and max_retries > 0:
            log.warning('Retries disabled when database disabled')
        queue_config = QueueConfig(host=address[0], port=address[1], auth=auth, size=config.server.queuesize, tls=tls)
        self.queue = QueueServer(config=queue_config)
        self.queue.dispatcher.configure(bundlesize=bundlesize, in_memory=self.in_memory)
        # NOTE: In JSON mode the template is expanded submit-side (here) against each
        # record's context; otherwise the flat source is passed through verbatim and the
        # client applies the template. See hypershell.submit.Loader.
        submit_template = template if from_json else DEFAULT_TEMPLATE
        if self.in_memory:
            self.scheduler = None
            self.submitter = None if not source else LiveSubmitThread(
                source, cores=task_cores, memory=task_memory, timeout=task_timeout, queue_config=queue_config,
                bundlesize=bundlesize, bundlewait=bundlewait, template=submit_template)
        else:
            self.submitter = None if not source else SubmitThread(
                source, cores=task_cores, memory=task_memory, timeout=task_timeout,
                bundlesize=bundlesize, bundlewait=bundlewait, template=submit_template)
            self.scheduler = SchedulerThread(queue=self.queue, bundlesize=bundlesize, attempts=max_retries + 1,
                                             eager=eager, forever_mode=forever_mode, restart_mode=restart_mode,
                                             poll=poll, submitter=self.submitter)
        if self.no_confirm:
            self.confirm = None
        else:
            self.confirm = ConfirmThread(queue=self.queue, in_memory=self.in_memory)
        self.receiver = ReceiverThread(queue=self.queue, in_memory=self.in_memory, redirect_failures=redirect_failures)
        self.heartmonitor = HeartMonitorThread(queue=self.queue, evict_after=evict_after,
                                               in_memory=self.in_memory, no_confirm=no_confirm, sites=sites)
        super().__init__(name='hypershell-server')

    def run_with_exceptions(self: ServerThread) -> None:
        """Start child threads, wait."""
        log.debug('Started')
        with self.queue:
            self.start_threads()
            self.wait_submitter()
            self.wait_scheduler()
            self.wait_heartbeat()
            self.wait_receiver()
            self.wait_confirm()
        log.debug('Done')

    def start(self: ServerThread) -> None:
        """Override default start method to wait for queue to be ready.

        A failure to start the queue (e.g., the address is in use) is raised here, in the caller.
        """
        super().start()
        while not self.queue:  # None until initialized by __init__()
            time.sleep(0.1)
        while not self.queue.ready:
            if not self.is_alive():
                self.join()  # Re-raises the exception that ended the thread before the queue was ready
            time.sleep(0.1)
        log.info(f'Server listening on {self.queue.config.host}:{self.queue.config.port}')

    def start_threads(self: ServerThread) -> None:
        """Start child threads; with file input, scheduling starts once the whole file is submitted."""
        if self.submitter is not None:
            self.submitter.start()
        if self.confirm is not None:
            self.confirm.start()
        self.heartmonitor.start()
        self.receiver.start()
        if self.scheduler is not None:
            if self.submitter is not None and is_file_input(self.submitter.source):
                # The scheduler sees every group in the file before it chooses the first one,
                # so the file's groups run in ascending order whatever order they are listed.
                log.info('Waiting for task file to be submitted before scheduling')
                self.wait_submitter()
            self.scheduler.start()

    def wait_scheduling(self: ServerThread) -> None:
        """Wait until scheduling starts, which for file input is once the whole file is submitted.

        Clients started after this never idle while a task file is read. If the server stops
        first, its error (if any) is raised here.
        """
        while self.scheduler is not None and self.scheduler.ident is None and self.is_alive():
            time.sleep(0.1)
        if not self.is_alive():
            self.join()

    def wait_submitter(self: ServerThread) -> None:
        """Wait on task submission to complete."""
        if self.submitter is not None:
            log.trace('Waiting (submitter)')
            self.submitter.join()

    def wait_scheduler(self: ServerThread) -> None:
        """Wait scheduling until complete."""
        if self.scheduler is not None:
            log.trace('Waiting (scheduler)')
            self.scheduler.join()

    def wait_heartbeat(self: ServerThread) -> None:
        """Wait for heartmonitor to stop."""
        log.trace('Waiting (heartbeat)')
        self.heartmonitor.signal_scheduler_done()
        self.heartmonitor.signal_clients()
        self.heartmonitor.join()
        self.revert_staged()

    def revert_staged(self: ServerThread) -> None:
        """Revert rows still staged once no client is left to take them.

        A row cancelled, completed or deleted while staged is left as it is.
        """
        ids = self.queue.dispatcher.take_staged()
        if ids and not self.in_memory:
            reverted = Task.revert_staged(ids)
            kept = f' ({len(ids) - reverted} left as completed, cancelled or deleted)' if reverted < len(ids) else ''
            log.info(f'Reverted {reverted} staged tasks never delivered{kept}')

    def wait_receiver(self: ServerThread) -> None:
        """Wait for receiver to stop."""
        log.trace('Waiting (receiver)')
        self.queue.completed.put(make_sentinel())
        self.receiver.join()

    def wait_confirm(self: ServerThread) -> None:
        """Wait for confirm thread to stop."""
        if not self.no_confirm:
            log.trace('Waiting (confirm)')
            self.queue.confirmed.put(make_sentinel())
            self.confirm.join()

    def stop(self: ServerThread, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        log.warning('Stopping')
        if self.submitter is not None:
            self.submitter.stop(wait=wait, timeout=timeout)
        if self.scheduler is not None:
            # A scheduler not yet started, because a task file is still being submitted, has
            # nothing to join.
            self.scheduler.stop(wait=wait and self.scheduler.ident is not None, timeout=timeout)
        self.heartmonitor.stop(wait=wait, timeout=timeout)
        self.queue.completed.put(make_sentinel())
        self.receiver.stop(wait=wait, timeout=timeout)
        if not self.no_confirm:
            self.queue.confirmed.put(make_sentinel())
            self.confirm.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


def is_file_input(source: Iterable[str | dict]) -> bool:
    """True if `source` is a whole task file: a seekable file (not standard input), or a list or tuple.

    Standard input, pipes, FIFOs, process substitution and other iterables are streams, which
    are scheduled as they arrive.
    """
    # A gated source carries the stream it wraps as `iterable`.
    inner = getattr(source, 'iterable', source)
    if isinstance(inner, (list, tuple)):
        return True
    if isinstance(inner, io.IOBase) and inner is not sys.stdin:
        try:
            return inner.seekable()
        except (OSError, ValueError):
            return False
    return False


def serve_from(source: Iterable[str],
               restart_mode: bool = False,
               task_cores: int = None,
               task_memory: int = None,
               task_timeout: int = None,
               bundlesize: int = DEFAULT_BUNDLESIZE,
               bundlewait: int = DEFAULT_BUNDLEWAIT,
               in_memory: bool = False,
               no_confirm: bool = False,
               poll: int = DEFAULT_SERVER_POLL,
               address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
               auth: str = DEFAULT_AUTH,
               max_retries: int = DEFAULT_ATTEMPTS - 1,
               eager: bool = DEFAULT_EAGER_MODE,
               redirect_failures: IO = None,
               evict_after: int = DEFAULT_EVICT,
               tls: Optional[TLSConfig] = None) -> None:
    """
    Start server with given task `source`, run until complete.

    Args:
        source (Iterable[str]):
            Any iterable of command-line tasks.

        task_cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        task_memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        task_timeout (int, optional):
            Task-level walltime limit in seconds (default: none).

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            If True, do not check for client confirmation messages.

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

        address (tuple, optional):
            Bind address for server with port number.
            See :const:`DEFAULT_BIND` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key. See :const:`DEFAULT_AUTH`.

        max_retries (int, optional):
            Number of allowed task retries. See :const:`DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

    Example:
        >>> from hypershell.server import serve_from
        >>> serve_from(['echo AA', 'echo BB', 'echo CC'], auth='my-secret-key')

    See Also:
        - :meth:`serve_file`
        - :meth:`serve_forever`
    """
    thread = ServerThread.new(source=source,
                              task_cores=task_cores,
                              task_memory=task_memory,
                              task_timeout=task_timeout,
                              bundlesize=bundlesize,
                              bundlewait=bundlewait,
                              in_memory=in_memory,
                              no_confirm=no_confirm,
                              poll=poll,
                              address=address,
                              auth=auth,
                              restart_mode=restart_mode,
                              max_retries=max_retries,
                              eager=eager,
                              redirect_failures=redirect_failures,
                              evict_after=evict_after,
                              tls=tls)
    try:
        join_waking(thread)
    except Exception:
        thread.stop()
        raise


def serve_file(path: str,
               task_cores: Optional[int] = None,
               task_memory: Optional[int] = None,
               task_timeout: Optional[int] = None,
               bundlesize: int = DEFAULT_BUNDLESIZE,
               bundlewait: int = DEFAULT_BUNDLEWAIT,
               in_memory: bool = False,
               no_confirm: bool = False,
               poll: int = DEFAULT_SERVER_POLL,
               address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
               auth: str = DEFAULT_AUTH,
               max_retries: int = DEFAULT_ATTEMPTS - 1,
               eager: bool = DEFAULT_EAGER_MODE,
               redirect_failures: Optional[IO] = None,
               evict_after: int = DEFAULT_EVICT,
               tls: Optional[TLSConfig] = None,
               **file_options) -> None:
    """
    Run server with tasks from a local file `path`, run until complete.

    Args:
        path (str):
            Path to file containing command-line tasks.

        task_cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        task_memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        task_timeout (int, optional):
            Task-level walltime limit in seconds (default: none).

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            If True, do not check for client confirmation messages.

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

        address (tuple, optional):
            Bind address for server with port number.
            See :const:`DEFAULT_BIND` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key. See :const:`DEFAULT_AUTH`.

        max_retries (int, optional):
            Number of allowed task retries. See :const:`DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

    Keyword Args:
        file_options (Any, optional):
            Forwarded to :meth:`open` function.

    Example:
        >>> from hypershell.server import serve_file
        >>> serve_from('/tmp/tasks.in', auth='my-secret-key', bundlesize=10)

    See Also:
        - :meth:`serve_from`
        - :meth:`serve_forever`
    """
    with open(path, mode='r', **file_options) as stream:
        serve_from(stream,
                   task_cores=task_cores,
                   task_memory=task_memory,
                   task_timeout=task_timeout,
                   bundlesize=bundlesize,
                   bundlewait=bundlewait,
                   in_memory=in_memory,
                   no_confirm=no_confirm,
                   poll=poll,
                   address=address,
                   auth=auth,
                   max_retries=max_retries,
                   eager=eager,
                   redirect_failures=redirect_failures,
                   evict_after=evict_after,
                   tls=tls)


def serve_forever(bundlesize: int = DEFAULT_BUNDLESIZE,
                  in_memory: bool = False,
                  no_confirm: bool = False,
                  poll: int = DEFAULT_SERVER_POLL,
                  address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
                  auth: str = DEFAULT_AUTH,
                  max_retries: int = DEFAULT_ATTEMPTS - 1,
                  eager: bool = DEFAULT_EAGER_MODE,
                  redirect_failures: IO = None,
                  evict_after: int = DEFAULT_EVICT,
                  tls: Optional[TLSConfig] = None) -> None:
    """
    Run server forever.

    Args:
        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            If True, do not check for client confirmation messages.

        address (tuple, optional):
            Bind address for server with port number.
            See :const:`DEFAULT_BIND` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key. See :const:`DEFAULT_AUTH`.

        max_retries (int, optional):
            Number of allowed task retries. See :const:`DEFAULT_ATTEMPTS`.

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

    Example:
        >>> from hypershell.server import serve_forever
        >>> serve_forever(address=('0.0.0.0', 54321), auth='my-secret-key',
        ...               max_retries=2, eager=True, evict_after=600)

    See Also:
        - :meth:`serve_from`
        - :meth:`serve_file`
    """
    thread = ServerThread.new(bundlesize=bundlesize,
                              in_memory=in_memory,
                              no_confirm=no_confirm,
                              poll=poll,
                              address=address,
                              auth=auth,
                              redirect_failures=redirect_failures,
                              forever_mode=True,
                              max_retries=max_retries,
                              eager=eager,
                              evict_after=evict_after,
                              tls=tls)
    try:
        join_waking(thread)
    except Exception:
        thread.stop()
        raise


APP_NAME = 'hs server'
APP_USAGE = f"""\
Usage:
  hs server [-h] [FILE | --forever | --restart] [-b NUM] [-w SEC] [-r NUM [--eager]]
            [-c NUM] [-m MEM] [-W SEC] [-H ADDR] [-p PORT] [-k KEY] [-Q NUM]
            [--no-db | --initdb] [--print | -f PATH] [--no-confirm]
            [--no-tls | [--tls-ca PATH] [--tls-cert PATH] [--tls-key PATH]]
            [--autoscaling [MODE] [-F VALUE] [-P SEC]]

  Launch server, schedule directly or asynchronously from database.\
"""

APP_HELP = f"""\
{APP_USAGE}

  The server includes a scheduler component that pulls tasks from the database and offers
  them up on a distributed queue to clients. It also has a receiver that collects the results
  of finished tasks. Optionally, the server can submit tasks (FILE). When submitting tasks,
  the -w/--bundlewait and -b/--bundlesize options are the same as for 'hs submit'.

  With --max-retries greater than zero and with the database configured, the scheduler will 
  check for a non-zero exit status for tasks and re-submit them if their previous number of 
  attempts is less.

  Tasks are bundled and clients pull them in these bundles. However, by default the bundle
  size is one, meaning that at small scales there is greater responsiveness.

  With --autoscaling the server schedules forever and launches nothing. Relays connected with
  its key advertise the zones they serve and each zone's min/max/init clients; the server plans
  each zone's clients over the live relay sites (autoscale.distribution: spread or fill) and
  publishes them for each relay to launch with its own launcher.

Arguments:
  FILE                        Path to input task file (default: <stdin>).

Options:
  -H, --bind            ADDR  Bind address (default: {DEFAULT_BIND}).
  -p, --port            NUM   Port number (default: {DEFAULT_PORT}).
  -k, --auth            KEY   Cryptographic key to secure server.
      --no-tls                Disable TLS for queue interface (not recommended).
      --tls-key               Path to TLS private key file (default: <auto>).
      --tls-cert              Path to TLS certificate file (default: <auto>).
      --tls-ca                Path to TLS CA certificate file (default: <auto>).
      --forever               Schedule forever.
      --restart               Start scheduling from last completed task.
  -A, --autoscaling   [MODE]  Publish client targets for relay sites (fixed or dynamic; implies --forever).
  -F, --factor        VALUE   Scaling factor (default: {default.autoscale.factor}).
  -P, --period        SEC     Scaling period in seconds (default: {default.autoscale.period}).
  -c, --task-cores      NUM   Number of cores to assign each task (default: none).
  -m, --task-memory     MEM   Memory to assign each task; K/M/G/T[B|iB] are powers of 1024 (default: none).
  -W, --task-timeout    SEC   Task-level walltime limit; seconds, 1h30m, 1.5h or [D-]HH:MM:SS (default: none).
  -b, --bundlesize      NUM   Size of task bundle (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait      SEC   Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
  -Q, --poll            NUM   Polling interval between database queries if no tasks available.
  -r, --max-retries     NUM   Auto-retry failed tasks (default: {DEFAULT_ATTEMPTS - 1}).
      --eager                 Schedule failed tasks before new tasks.
      --no-db                 Disable database (submit directly to clients).
      --initdb                Auto-initialize database.
      --no-confirm            Disable client confirmation of task bundle received.
      --print                 Print failed task args to <stdout>.
  -f, --failures        PATH  File path to redirect failed task args.
  -h, --help                  Show this message and exit.\
"""


class ServerApp(Application):
    """Run server in stand-alone mode."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    filepath: str
    interface.add_argument('filepath', nargs='?', default=None)

    bundlesize: int = config.server.bundlesize
    interface.add_argument('-b', '--bundlesize', type=int, default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    interface.add_argument('-w', '--bundlewait', type=int, default=bundlewait)

    task_cores: Optional[int] = config.task.cores or None
    task_memory: Optional[int] = config.task.memory or None
    task_timeout: Optional[int] = config.task.timeout or None
    interface.add_argument('-c', '--task-cores', type=int, default=task_cores)
    interface.add_argument('-m', '--task-memory', type=parse_bytes, default=task_memory)
    interface.add_argument('-W', '--task-timeout', type=parse_time, default=task_timeout)

    eager_mode: bool = config.server.eager
    max_retries: int = config.server.attempts - 1
    interface.add_argument('-r', '--max-retries', type=int, default=max_retries)
    interface.add_argument('--eager', action='store_true', dest='eager_mode')

    forever_mode: bool = False
    interface.add_argument('--forever', action='store_true', dest='forever_mode')

    restart_mode: bool = False
    interface.add_argument('--restart', action='store_true', dest='restart_mode')

    autoscaling_policy: Optional[str] = None
    autoscaling_factor: float = config.autoscale.factor
    autoscaling_period: int = config.autoscale.period
    interface.add_argument('-A', '--autoscaling', nargs='?', default=None,
                           const=config.autoscale.policy, dest='autoscaling_policy')
    interface.add_argument('-F', '--factor', type=float, default=autoscaling_factor, dest='autoscaling_factor')
    interface.add_argument('-P', '--period', type=int, default=autoscaling_period, dest='autoscaling_period')

    poll: int = config.server.poll
    interface.add_argument('-Q', '--poll', type=int, default=poll)

    host: str = config.server.bind
    interface.add_argument('-H', '--bind', default=host, dest='host')

    port: int = config.server.port
    interface.add_argument('-p', '--port', type=int, default=port)

    auth: Optional[str] = None  # Without -k, the key is resolve_auth('server') (see run)
    interface.add_argument('-k', '--auth', default=auth)

    in_memory: bool = False
    auto_initdb: bool = False
    db_interface = interface.add_mutually_exclusive_group()
    db_interface.add_argument('--no-db', action='store_true', dest='in_memory')
    db_interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    no_confirm: bool = False
    interface.add_argument('--no-confirm', action='store_true')

    print_mode: bool = False
    failure_path: str = None
    output_interface = interface.add_mutually_exclusive_group()
    output_interface.add_argument('--print', action='store_true', dest='print_mode')
    output_interface.add_argument('-f', '--failures', default=None, dest='failure_path')

    tls: Optional[TLSConfig] = None
    tls_enabled: bool = True
    tls_cert: str = config.server.tls.cert
    tls_key: str = config.server.tls.key
    tls_ca: str = config.server.tls.cafile
    interface.add_argument('--no-tls', action='store_false', dest='tls_enabled')
    interface.add_argument('--tls-key', default=tls_key)
    interface.add_argument('--tls-cert', default=tls_cert)
    interface.add_argument('--tls-ca', default=tls_ca)

    # Hidden options used as helpers for shell completion
    show_available_ports: bool = False
    interface.add_argument('--available-ports', action='store_true', dest='show_available_ports')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ServerApp) -> None:
        """Run server."""
        if self.special_options():
            return
        if self.auth is None:
            self.auth = resolve_auth('server')
        self.check_args()
        self.enable_tls()
        ensuredb()
        if self.autoscaling_policy is not None:
            # Imported here: the cluster package imports this module.
            from hypershell.cluster.topology import serve_with_sites
            serve_with_sites(bundlesize=self.bundlesize,
                             address=(self.host, self.port),
                             auth=self.auth,
                             poll=self.poll,
                             max_retries=self.max_retries,
                             eager=self.eager_mode,
                             redirect_failures=self.failure_stream,
                             evict_after=config.server.evict,
                             tls=self.tls,
                             policy=self.autoscaling_policy,
                             factor=self.autoscaling_factor,
                             period=self.autoscaling_period)
        elif self.forever_mode:
            serve_forever(bundlesize=self.bundlesize,
                          address=(self.host, self.port),
                          auth=self.auth,
                          in_memory=self.in_memory,
                          no_confirm=self.no_confirm,
                          poll=self.poll,
                          max_retries=self.max_retries,
                          eager=self.eager_mode,
                          redirect_failures=self.failure_stream,
                          evict_after=config.server.evict,
                          tls=self.tls)
        else:
            serve_from(source=self.input_stream,
                       task_cores=self.task_cores,
                       task_memory=self.task_memory,
                       task_timeout=self.task_timeout,
                       bundlesize=self.bundlesize,
                       bundlewait=self.bundlewait,
                       address=(self.host, self.port),
                       auth=self.auth,
                       in_memory=self.in_memory,
                       no_confirm=self.no_confirm,
                       poll=self.poll,
                       restart_mode=self.restart_mode,
                       max_retries=self.max_retries,
                       eager=self.eager_mode,
                       redirect_failures=self.failure_stream,
                       evict_after=config.server.evict,
                       tls=self.tls)

    def check_args(self: ServerApp) -> None:
        """Fail particular argument combinations."""
        if self.autoscaling_policy is not None:
            self.check_autoscaling()
        if self.filepath and self.forever_mode:
            raise ArgumentError('Cannot specify both FILE and --forever')
        if self.filepath and self.restart_mode:
            raise ArgumentError('Cannot specify both FILE and --restart')
        if not self.filepath and not self.forever_mode:
            self.filepath = '-'  # NOTE: assume STDIN
        if self.restart_mode and self.forever_mode:
            raise ArgumentError('Using --forever with --restart is invalid')
        if self.auth == DEFAULT_AUTH:
            raise ArgumentError('Refusing to run server without authentication key')
        if len(self.auth) < AUTH_MINIMUM_LENGTH:
            raise ArgumentError(f'Authentication key must be at least {AUTH_MINIMUM_LENGTH} characters')
        if not AUTH_ALLOWED_CHARS.fullmatch(self.auth):
            raise ArgumentError(f'Authentication key must contain only "{AUTH_ALLOWED_CHARS.pattern}"')
        if not self.tls_enabled:
            log.warning('TLS is disabled - this is not recommended for production!')

    def check_autoscaling(self: ServerApp) -> None:
        """Fail combinations with --autoscaling, which runs forever and relies on the database and confirmation."""
        if self.autoscaling_policy not in ('fixed', 'dynamic'):
            raise ArgumentError(f'Autoscaling mode must be \'fixed\' or \'dynamic\' '
                                f'(given \'{self.autoscaling_policy}\')')
        if self.filepath:
            raise ArgumentError('Cannot specify both FILE and --autoscaling')
        if self.restart_mode:
            raise ArgumentError('Using --autoscaling with --restart is invalid')
        if self.in_memory:
            raise ArgumentError('Cannot use --autoscaling with --no-db')
        if self.no_confirm:
            raise ArgumentError('Cannot use --autoscaling with --no-confirm')
        self.forever_mode = True

    def special_options(self: ServerApp) -> bool:
        """Special terminating options print information lazily (not import time)."""
        if self.show_available_ports:
            print('\n'.join(map(str, islice(find_available_ports(), 10))))
            return True
        else:
            return False

    def enable_tls(self: ServerApp) -> None:
        """Configure TLS if enabled."""
        if self.tls_enabled:
            self.tls = tls_from_namespace({
                **config.server.tls,
                'cert': self.tls_cert,
                'key': self.tls_key,
                'cafile': self.tls_ca,
            })

    @cached_property
    def input_stream(self: ServerApp) -> Optional[IO]:
        """Input IO stream for task args (none with --autoscaling, which never reads one)."""
        if self.forever_mode or self.restart_mode or self.autoscaling_policy is not None or not self.filepath:
            return None
        else:
            return sys.stdin if self.filepath == '-' else open(self.filepath, mode='r')

    @cached_property
    def failure_stream(self: ServerApp) -> Optional[IO]:
        """IO stream for failed task args."""
        if self.print_mode:
            return sys.stdout
        elif self.failure_path:
            return sys.stdout if self.failure_path == '-' else open(self.failure_path, mode='w')
        else:
            return None

    def __enter__(self: ServerApp) -> ServerApp:
        """Ensure context and database ready."""
        return self

    def __exit__(self: ServerApp,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Clean up IO if necessary."""
        if self.input_stream is not None and self.input_stream is not sys.stdin:
            self.input_stream.close()
        if self.failure_stream is not None and self.failure_path is not sys.stdout:
            self.failure_stream.close()
