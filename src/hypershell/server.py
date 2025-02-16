# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
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
import sys
import time
from enum import Enum
from datetime import datetime, timedelta
from functools import cached_property
from itertools import islice
from queue import Empty as QueueEmpty, Full as QueueFull

# External libs
from cmdkit.app import Application
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.core.config import config, default, find_available_ports
from hypershell.core.logging import Logger
from hypershell.core.fsm import State, StateMachine
from hypershell.core.thread import Thread
from hypershell.core.queue import QueueServer, QueueConfig
from hypershell.core.signal import check_signal, SIGNAL_MAP, SIGUSR1, SIGUSR2
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.data.model import Task, Client
from hypershell.data import ensuredb, DATABASE_ENABLED
from hypershell.submit import SubmitThread, LiveSubmitThread, DEFAULT_BUNDLEWAIT
from hypershell.client import ClientInfo

# Public interface
__all__ = ['serve_from', 'serve_file', 'serve_forever', 'ServerThread', 'ServerApp',
           'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_ATTEMPTS',
           'DEFAULT_EVICT', 'DEFAULT_EAGER_MODE', 'DEFAULT_QUERY_PAUSE',
           'DEFAULT_BIND', 'DEFAULT_PORT', 'DEFAULT_AUTH']

# Initialize logger
log = Logger.with_name(__name__)


class SchedulerState(State, Enum):
    """Finite states of the scheduler."""
    START = 0
    LOAD = 1
    PACK = 2
    POST = 3
    FINAL = 4
    HALT = 5


DEFAULT_BUNDLESIZE: Final[int] = default.server.bundlesize
"""Default size for task bundles."""

DEFAULT_ATTEMPTS: Final[int] = default.server.attempts
"""Default number of attempts for task retries."""

DEFAULT_EAGER_MODE: Final[bool] = default.server.eager
"""When enabled tasks are retried immediately ahead scheduling new tasks."""

DEFAULT_QUERY_PAUSE: Final[int] = default.server.wait
"""Default polling interval between database queries if no tasks are available."""


class Scheduler(StateMachine):
    """Enqueue tasks from database."""

    tasks: List[Task]
    queue: QueueServer
    bundle: List[bytes]

    bundlesize: int
    attempts: int
    eager: bool
    forever_mode: bool
    restart_mode: bool

    state = SchedulerState.START
    states = SchedulerState

    startup_phase: bool = True

    def __init__(self: Scheduler, queue: QueueServer, bundlesize: int = DEFAULT_BUNDLESIZE,
                 attempts: int = DEFAULT_ATTEMPTS, eager: bool = DEFAULT_EAGER_MODE,
                 forever_mode: bool = False, restart_mode: bool = False) -> None:
        """Initialize queue and parameters."""
        self.queue = queue
        self.bundle = []
        self.bundlesize = bundlesize
        self.attempts = attempts
        self.eager = eager
        self.forever_mode = forever_mode
        self.restart_mode = restart_mode
        if self.restart_mode:
            # NOTE: Halt if everything in the database is already finished
            self.startup_phase = False

    @cached_property
    def actions(self: Scheduler) -> Dict[SchedulerState, Callable[[], SchedulerState]]:
        return {
            SchedulerState.START: self.start,
            SchedulerState.LOAD: self.load_bundle,
            SchedulerState.PACK: self.pack_bundle,
            SchedulerState.POST: self.post_bundle,
            SchedulerState.FINAL: self.finalize,
        }

    def start(self: Scheduler) -> SchedulerState:
        """Initial setup then jump to LOAD state."""
        log.debug('Started (scheduler)')
        if self.forever_mode:
            log.info('Scheduler will run forever')
        task_count = Task.count()
        tasks_remaining = Task.count_remaining()
        if task_count > 0:
            log.warning(f'Database exists ({task_count} previous tasks)')
            if tasks_remaining == 0:
                log.warning(f'All tasks completed - did you mean to use the same database?')
            else:
                tasks_interrupted = Task.count_interrupted()
                log.info(f'Found {tasks_remaining} unfinished task(s)')
                Task.revert_interrupted()
                log.info(f'Reverted {tasks_interrupted} previously interrupted task(s)')
        return SchedulerState.LOAD

    def load_bundle(self: Scheduler) -> SchedulerState:
        """Load the next task bundle from the database."""
        if check_signal() in (SIGUSR1, SIGUSR2):
            log.warning(f'Signal interrupt ({SIGNAL_MAP[check_signal()]})')
            return SchedulerState.FINAL
        self.tasks = Task.next(limit=self.bundlesize, attempts=self.attempts, eager=self.eager)
        if self.tasks:
            self.startup_phase = False
            return SchedulerState.PACK
        # An empty database must wait for at least one task
        elif not self.forever_mode and Task.count() > 0 and Task.count_remaining() == 0 and not self.startup_phase:
            return SchedulerState.FINAL
        else:
            time.sleep(DEFAULT_QUERY_PAUSE)
            return SchedulerState.LOAD

    def pack_bundle(self: Scheduler) -> SchedulerState:
        """Pack tasks into bundle (list)."""
        self.bundle = [task.pack() for task in self.tasks]
        return SchedulerState.POST

    def post_bundle(self: Scheduler) -> SchedulerState:
        """Put bundle on outbound queue."""
        try:
            self.queue.scheduled.put(self.bundle, timeout=2)
            log.debug(f'Scheduled {len(self.tasks)} tasks')
            for task in self.tasks:
                log.debug(f'Scheduled task ({task.id})')
            return SchedulerState.LOAD
        except QueueFull:
            return SchedulerState.POST

    @staticmethod
    def finalize() -> SchedulerState:
        """Stop scheduler."""
        log.debug('Done (scheduler)')
        return SchedulerState.HALT


class SchedulerThread(Thread):
    """Run scheduler within dedicated thread."""

    def __init__(self: SchedulerThread, queue: QueueServer, bundlesize: int = DEFAULT_BUNDLESIZE,
                 attempts: int = DEFAULT_ATTEMPTS, eager: bool = DEFAULT_EAGER_MODE,
                 forever_mode: bool = False, restart_mode: bool = False) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-scheduler')
        self.machine = Scheduler(queue=queue, bundlesize=bundlesize, attempts=attempts, eager=eager,
                                 forever_mode=forever_mode, restart_mode=restart_mode)

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

    def __init__(self: Confirm, queue: QueueServer, in_memory: bool = False) -> None:
        """Initialize machine."""
        self.in_memory = in_memory
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
            self.client_data = self.queue.confirmed.get(timeout=2)
            self.queue.confirmed.task_done()
            return ConfirmState.UNPACK if self.client_data else ConfirmState.FINAL
        except QueueEmpty:
            return ConfirmState.UNLOAD

    def unpack_info(self: Confirm) -> ConfirmState:
        """Unpack received client info."""
        self.client_info = ClientInfo.unpack(self.client_data)
        log.debug(f'Confirmed {len(self.client_info.task_ids)} tasks '
                  f'({self.client_info.client_host}: {self.client_info.client_id})')
        return ConfirmState.UPDATE

    def update_info(self: Confirm) -> ConfirmState:
        """Update client info in database for confirmed task bundle."""
        if not self.in_memory:
            Task.update_all(self.client_info.transpose())
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

    tasks: List[Task]
    queue: QueueServer
    bundle: List[bytes]

    in_memory: bool
    redirect_failures: IO

    state = ReceiverState.START
    states = ReceiverState

    def __init__(self: Receiver, queue: QueueServer, in_memory: bool = False, redirect_failures: IO = None) -> None:
        """Initialize receiver."""
        self.queue = queue
        self.bundle = []
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
            self.bundle = self.queue.completed.get(timeout=2)
            self.queue.completed.task_done()
            return ReceiverState.UNPACK if self.bundle else ReceiverState.FINAL
        except QueueEmpty:
            log.trace('No completed tasks returned - waiting')
            return ReceiverState.UNLOAD

    def unpack_bundle(self: Receiver) -> ReceiverState:
        """Unpack previous bundle into list of tasks."""
        self.tasks = [Task.unpack(data) for data in self.bundle]
        return ReceiverState.UPDATE

    def update_tasks(self: Receiver) -> ReceiverState:
        """Update tasks in database with run details."""
        if not self.in_memory:
            Task.update_all([task.to_dict() for task in self.tasks])
        for task in self.tasks:
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
    """Collect heartbeat messages from connected clients."""

    in_memory: bool
    no_confirm: bool
    queue: QueueServer
    beats: Dict[str, Heartbeat]
    last_check: datetime
    wait_check: timedelta
    evict_after: timedelta

    startup_phase: bool = True  # should not halt until at least one client
    scheduler_done: bool = False  # set by parent thread when scheduling is over
    should_signal: bool = False  # set by parent thread to signal clients
    latest_heartbeat: Heartbeat = None

    state = HeartbeatState.START
    states = HeartbeatState

    def __init__(self: HeartMonitor, queue: QueueServer, evict_after: int = DEFAULT_EVICT,
                 in_memory: bool = False, no_confirm: bool = False) -> None:
        """Initialize with queue server."""
        self.queue = queue
        self.last_check = datetime.now().astimezone()
        self.beats = {}
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
            hb_data = self.queue.heartbeat.get(timeout=2)
            self.queue.heartbeat.task_done()
            self.startup_phase = False
            if not hb_data:
                return HeartbeatState.FINAL
            else:
                self.latest_heartbeat = Heartbeat.unpack(hb_data)
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
                if not self.in_memory:
                    new_client = Client.from_heartbeat(hb)
                    try:
                        # Check to see if we are re-registering a falsely-evicted client (Issue #29)
                        old_client = Client.from_id(new_client.id)
                        log.warning(f'Existing client re-registered ({old_client.host}: {old_client.id})')
                        Client.update(old_client.id, disconnected_at=None, evicted=False)
                    except Client.NotFound:
                        Client.add(new_client)
            self.beats[hb.uuid] = hb
            return HeartbeatState.SWITCH
        else:
            log.trace(f'Client disconnected ({hb.host}: {hb.uuid})')
            if hb.uuid in self.beats:
                self.beats.pop(hb.uuid)
                if not self.in_memory:
                    Client.update(hb.uuid, disconnected_at=datetime.now().astimezone())
            return HeartbeatState.SWITCH

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
        """Check last heartbeat on all clients and evict if necessary."""
        log.debug(f'Checking clients ({len(self.beats)} connected)')
        for uuid in list(self.beats):
            hb = self.beats.get(uuid)
            age = self.last_check - hb.time
            if age > self.evict_after:
                log.warning(f'Evicting client ({hb.host}: {uuid})')
                self.beats.pop(uuid)
                if not self.in_memory:
                    Client.update(hb.uuid, disconnected_at=datetime.now().astimezone(), evicted=True)
                if not self.in_memory and not self.no_confirm:
                    log.warning(f'Reverting orphaned tasks ({hb.host}: {uuid})')
                    Task.revert_orphaned(uuid)
        return HeartbeatState.SWITCH

    def signal_clients(self: HeartMonitor) -> HeartbeatState:
        """Send shutdown signal to all connected clients."""
        log.debug(f'Signaling clients ({len(self.beats)} connected)')
        for hb in self.beats.values():
            self.queue.scheduled.put(None)
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
                 in_memory: bool = False, no_confirm: bool = False) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-heartmonitor')
        self.machine = HeartMonitor(queue=queue, evict_after=evict_after,
                                    in_memory=in_memory, no_confirm=no_confirm)

    def run_with_exceptions(self: HeartMonitorThread) -> None:
        """Run machine."""
        self.machine.run()

    def signal_clients(self: HeartMonitorThread) -> None:
        """Set signal flag to post sentinel for each connected clients."""
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

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        forever_mode (bool, optional):
            Regardless of `source`, if enabled schedule forever.
            Conflicts with `restart_mode` and `in_memory`. Default is `False`.

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

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

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (IO, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

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
    submitter: Optional[SubmitThread]
    scheduler: Optional[SchedulerThread]
    confirm: Optional[ConfirmThread]
    receiver: ReceiverThread
    heartmonitor: HeartMonitorThread
    in_memory: bool
    no_confirm: bool

    def __init__(self: ServerThread,
                 source: Iterable[str] = None,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 in_memory: bool = False,
                 no_confirm: bool = False,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
                 auth: str = DEFAULT_AUTH,
                 max_retries: int = DEFAULT_ATTEMPTS - 1,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 evict_after: int = DEFAULT_EVICT) -> None:
        """Initialize queue manager and child threads."""
        self.in_memory = in_memory
        self.no_confirm = no_confirm
        if not self.in_memory and not DATABASE_ENABLED:
            log.warning('No database configured - automatically disabled')
            self.in_memory = True
        if self.in_memory and max_retries > 0:
            log.warning('Retries disabled when database disabled')
        queue_config = QueueConfig(host=address[0], port=address[1], auth=auth, size=config.server.queuesize)
        self.queue = QueueServer(config=queue_config)
        if self.in_memory:
            self.scheduler = None
            self.submitter = None if not source else LiveSubmitThread(
                source, queue_config=queue_config, bundlesize=bundlesize, bundlewait=bundlewait)
        else:
            self.submitter = None if not source else SubmitThread(source, bundlesize=bundlesize, bundlewait=bundlewait)
            self.scheduler = SchedulerThread(queue=self.queue, bundlesize=bundlesize, attempts=max_retries + 1,
                                             eager=eager, forever_mode=forever_mode, restart_mode=restart_mode)
        if self.no_confirm:
            self.confirm = None
        else:
            self.confirm = ConfirmThread(queue=self.queue, in_memory=self.in_memory)
        self.receiver = ReceiverThread(queue=self.queue, in_memory=self.in_memory, redirect_failures=redirect_failures)
        self.heartmonitor = HeartMonitorThread(queue=self.queue, evict_after=evict_after,
                                               in_memory=in_memory, no_confirm=no_confirm)
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

    def start_threads(self: ServerThread) -> None:
        """Start child threads."""
        if self.submitter is not None:
            self.submitter.start()
        if self.scheduler is not None:
            self.scheduler.start()
        if not self.no_confirm:
            self.confirm.start()
        self.heartmonitor.start()
        self.receiver.start()

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

    def wait_receiver(self: ServerThread) -> None:
        """Wait for receiver to stop."""
        log.trace('Waiting (receiver)')
        self.queue.completed.put(None)
        self.receiver.join()

    def wait_confirm(self: ServerThread) -> None:
        """Wait for confirm thread to stop."""
        if not self.no_confirm:
            log.trace('Waiting (confirm)')
            self.queue.confirmed.put(None)
            self.confirm.join()

    def stop(self: ServerThread, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        log.warning('Stopping')
        if self.submitter is not None:
            self.submitter.stop(wait=wait, timeout=timeout)
        if self.scheduler is not None:
            self.scheduler.stop(wait=wait, timeout=timeout)
        self.heartmonitor.stop(wait=wait, timeout=timeout)
        self.queue.completed.put(None)
        self.receiver.stop(wait=wait, timeout=timeout)
        if not self.no_confirm:
            self.queue.confirmed.put(None)
            self.confirm.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


def serve_from(source: Iterable[str],
               restart_mode: bool = False,
               bundlesize: int = DEFAULT_BUNDLESIZE,
               bundlewait: int = DEFAULT_BUNDLEWAIT,
               in_memory: bool = False,
               no_confirm: bool = False,
               address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
               auth: str = DEFAULT_AUTH,
               max_retries: int = DEFAULT_ATTEMPTS - 1,
               eager: bool = DEFAULT_EAGER_MODE,
               redirect_failures: IO = None,
               evict_after: int = DEFAULT_EVICT) -> None:
    """
    Start server with given task `source`, run until complete.

    Args:
        source (Iterable[str]):
            Any iterable of command-line tasks.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

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

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

    Example:
        >>> from hypershell.server import serve_from
        >>> serve_from(['echo AA', 'echo BB', 'echo CC'], auth='my-secret-key')

    See Also:
        - :meth:`serve_file`
        - :meth:`serve_forever`
    """
    thread = ServerThread.new(source=source,
                              restart_mode=restart_mode,
                              bundlesize=bundlesize,
                              bundlewait=bundlewait,
                              in_memory=in_memory,
                              no_confirm=no_confirm,
                              address=address, auth=auth,
                              max_retries=max_retries,
                              eager=eager,
                              redirect_failures=redirect_failures,
                              evict_after=evict_after)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


def serve_file(path: str,
               bundlesize: int = DEFAULT_BUNDLESIZE,
               bundlewait: int = DEFAULT_BUNDLEWAIT,
               in_memory: bool = False,
               no_confirm: bool = False,
               address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
               auth: str = DEFAULT_AUTH,
               max_retries: int = DEFAULT_ATTEMPTS - 1,
               eager: bool = DEFAULT_EAGER_MODE,
               redirect_failures: IO = None,
               evict_after: int = DEFAULT_EVICT,
               **file_options) -> None:
    """
    Run server with tasks from a local file `path`, run until complete.

    Args:
        path (str):
            Path to file containing command-line tasks.

        bundlesize (int, optional):
            Size of task bundles. See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

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

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

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
                   bundlesize=bundlesize,
                   bundlewait=bundlewait,
                   in_memory=in_memory,
                   no_confirm=no_confirm,
                   address=address,
                   auth=auth,
                   max_retries=max_retries,
                   eager=eager,
                   redirect_failures=redirect_failures,
                   evict_after=evict_after)


def serve_forever(bundlesize: int = DEFAULT_BUNDLESIZE,
                  in_memory: bool = False,
                  no_confirm: bool = False,
                  address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
                  auth: str = DEFAULT_AUTH,
                  max_retries: int = DEFAULT_ATTEMPTS - 1,
                  eager: bool = DEFAULT_EAGER_MODE,
                  redirect_failures: IO = None,
                  evict_after: int = DEFAULT_EVICT) -> None:
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

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`DEFAULT_EAGER_MODE`.

        redirect_failures (bool, optional):
            Open file descriptor to write failed tasks.

        evict_after (int, optional):
            Period in seconds to wait before evicting clients that miss heartbeats.
            See :const:`DEFAULT_EVICT`.

    Example:
        >>> from hypershell.server import serve_forever
        >>> serve_forever(address=('0.0.0.0', 54321), auth='my-secret-key',
        ...               max_retries=2, eager=True, evict_after=600)

    See Also:
        - :meth:`serve_from`
        - :meth:`serve_file`
    """
    thread = ServerThread.new(source=None,
                              bundlesize=bundlesize,
                              in_memory=in_memory,
                              no_confirm=no_confirm,
                              address=address,
                              auth=auth,
                              redirect_failures=redirect_failures,
                              forever_mode=True,
                              max_retries=max_retries,
                              eager=eager,
                              evict_after=evict_after)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


APP_NAME = 'hs server'
APP_USAGE = f"""\
Usage:
  hs server [-h] [FILE | --forever | --restart] [-b NUM] [-w SEC] [-r NUM [--eager]]
            [-H ADDR] [-p PORT] [-k KEY] [--no-db | --initdb] [--print | -f PATH] [--no-confirm]

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

Arguments:
  FILE                        Path to input task file (default: <stdin>).

Options:
  -H, --bind            ADDR  Bind address (default: {DEFAULT_BIND}).
  -p, --port            NUM   Port number (default: {DEFAULT_PORT}).
  -k, --auth            KEY   Cryptographic key to secure server.
      --forever               Schedule forever.
      --restart               Start scheduling from last completed task.
  -b, --bundlesize      NUM   Size of task bundle (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait      SEC   Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
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

    eager_mode: bool = config.server.eager
    max_retries: int = config.server.attempts - 1
    interface.add_argument('-r', '--max-retries', type=int, default=max_retries)
    interface.add_argument('--eager', action='store_true')

    forever_mode: bool = False
    interface.add_argument('--forever', action='store_true', dest='forever_mode')

    restart_mode: bool = False
    interface.add_argument('--restart', action='store_true', dest='restart_mode')

    host: str = config.server.bind
    interface.add_argument('-H', '--bind', default=host, dest='host')

    port: int = config.server.port
    interface.add_argument('-p', '--port', type=int, default=port)

    auth: str = config.server.auth
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

    # Hidden options used as helpers for shell completion
    interface.add_argument('--available-ports', action='version',
                           version='\n'.join(map(str, islice(find_available_ports(), 10))))

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ServerApp) -> None:
        """Run server."""
        if self.forever_mode:
            serve_forever(bundlesize=self.bundlesize,
                          address=(self.host, self.port),
                          auth=self.auth,
                          in_memory=self.in_memory,
                          no_confirm=self.no_confirm,
                          max_retries=self.max_retries,
                          eager=self.eager_mode,
                          redirect_failures=self.failure_stream,
                          evict_after=config.server.evict)
        else:
            serve_from(source=self.input_stream,
                       bundlesize=self.bundlesize,
                       bundlewait=self.bundlewait,
                       address=(self.host, self.port),
                       auth=self.auth,
                       in_memory=self.in_memory,
                       no_confirm=self.no_confirm,
                       restart_mode=self.restart_mode,
                       max_retries=self.max_retries,
                       eager=self.eager_mode,
                       redirect_failures=self.failure_stream,
                       evict_after=config.server.evict)

    def check_args(self: ServerApp):
        """Fail particular argument combinations."""
        if self.filepath and self.forever_mode:
            raise ArgumentError('Cannot specify both FILE and --forever')
        if self.filepath is None and not self.forever_mode:
            self.filepath = '-'  # NOTE: assume STDIN
        if self.restart_mode and self.forever_mode:
            raise ArgumentError('Using --forever with --restart is invalid')

    @cached_property
    def input_stream(self: ServerApp) -> Optional[IO]:
        """Input IO stream for task args."""
        if self.forever_mode or self.restart_mode:
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
        self.check_args()
        ensuredb()
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
