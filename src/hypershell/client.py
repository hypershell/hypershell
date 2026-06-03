# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Connect to server and run tasks.

Example:
    >>> from hypershell.client import run_client
    >>> run_client(num_threads=4, address=('<IP ADDRESS>', 8080), auth='<secret>')

Embed a `ClientThread` in your application directly. Call `stop()` to stop early.
Clients cannot connect to a remote machine unless you set the server's `bind` address
to 0.0.0.0 (as opposed to localhost which is the default).

Example:
    >>> from hypershell.client import ClientThread
    >>> client_thread = ClientThread.new(num_threads=4, address=('<IP ADDRESS>', 8080), auth='<secret>')

Note:
    In order for the `ClientThread` to actively monitor the state set by `stop` and
    halt execution (a requirement because of how CPython does threading), the implementation
    uses a finite state machine. *You should not instantiate this machine directly*.

Warning:
    Because the `ClientThread` checks state actively to decide whether to halt, it may take
    some few moments before it shutsdown on its own. If your main program exits,
    the thread will be stopped regardless because it runs as a `daemon`.
"""


# Type annotations
from __future__ import annotations
from typing import List, Tuple, Optional, Callable, Dict, IO, Type, Final
from types import TracebackType

# Standard libs
import os
import sys
import time
import json
import random
import functools
from enum import Enum
from datetime import datetime, timedelta
from queue import Queue, Empty as QueueEmpty, Full as QueueFull
from threading import Lock
from subprocess import Popen, TimeoutExpired
from socket import gaierror
from dataclasses import dataclass
from collections import defaultdict
from itertools import islice
from multiprocessing import AuthenticationError, cpu_count

# External libs
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import Namespace

# Internal libs
from hypershell.data.model import Task
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.platform import default_path
from hypershell.core.config import default, config, load_task_env, SSH_GROUPS
from hypershell.core.fsm import State, StateMachine
from hypershell.core.pretty_print import format_bytes
from hypershell.core.resource import check_resources, clear_processes, CPU_COUNT, MEMORY_TOTAL
from hypershell.core.types import parse_bytes
from hypershell.core.thread import Thread
from hypershell.core.signal import check_signal, SIGNAL_MAP, SIGUSR1, SIGUSR2, SIGINT
from hypershell.core.queue import QueueClient, QueueConfig
from hypershell.core.logging import HOSTNAME, INSTANCE, Logger
from hypershell.core.template import Template, DEFAULT_TEMPLATE
from hypershell.core.exceptions import (handle_exception, handle_disconnect,
                                        handle_address_unknown, HostAddressInfo, get_shared_exception_mapping)

# Public interface
__all__ = ['run_client', 'ClientThread', 'ClientApp', 'ClientInfo',
           'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_TEMPLATE',
           'DEFAULT_NUM_THREADS', 'DEFAULT_NUM_TASKS',  # DEFAULT_NUM_TASKS for backwards compatibility
           'DEFAULT_DELAY', 'DEFAULT_SIGNALWAIT',
           'DEFAULT_HEARTRATE', 'DEFAULT_HOST', 'DEFAULT_PORT', 'DEFAULT_AUTH',
           'set_client_standalone']

# Initialize logger
log = Logger.with_name(__name__)


# NOTE:
# The UNIX signal facility works on stand-alone server/client, but when running a LocalCluster with
# a client as a local thread, the USR1/USR2 signals prevent clients from sending the proper finalization
# messages. This flag is set by LocalCluster to prevent greedy client-side shutdown behavior.
CLIENT_STANDALONE_MODE: bool = True


def set_client_standalone(mode: bool) -> None:
    """Set global flag to prevent greedy shutdown from USR1/USR2 signals."""
    global CLIENT_STANDALONE_MODE
    CLIENT_STANDALONE_MODE = mode


@dataclass
class ClientInfo:
    """Client instance ID/hostname and task ID mapping."""

    client_id: str
    client_host: str
    task_ids: List[str]

    @classmethod
    def from_dict(cls: Type[ClientInfo], data: dict) -> ClientInfo:
        """Initialize from existing dictionary."""
        return cls(**data)

    def to_dict(self: ClientInfo) -> dict:
        """Export to dictionary."""
        return {'client_id': self.client_id, 'client_host': self.client_host, 'task_ids': self.task_ids}

    def pack(self: ClientInfo) -> bytes:
        """Serialize data."""
        return json.dumps(self.to_dict()).encode('utf-8')

    @classmethod
    def unpack(cls: Type[ClientInfo], data: bytes) -> ClientInfo:
        """Deserialize from raw `data`."""
        return cls.from_dict(json.loads(data.decode('utf-8')))

    @classmethod
    def from_tasks(cls: Type[ClientInfo], tasks: List[Task]) -> ClientInfo:
        """Initialize from list of existing Task instances."""
        return cls(client_id=INSTANCE, client_host=HOSTNAME,
                   task_ids=[task.id for task in tasks])

    def transpose(self: ClientInfo) -> List[Dict[str, str]]:
        """Represent as list of dicts for database update."""
        return [{'id': task_id, 'client_id': self.client_id, 'client_host': self.client_host}
                for task_id in self.task_ids]


class SchedulerState(State, Enum):
    """Finite states for scheduler."""
    START = 0
    GET_REMOTE = 1
    UNPACK = 2
    PUT_CONFIRM = 3
    POP_TASK = 4
    PUT_LOCAL = 5
    FINAL = 6
    HALT = 7


class ClientScheduler(StateMachine):
    """Receive task bundles from server and schedule locally."""

    queue: QueueClient
    local: Queue[Optional[Task]]
    bundle: List[bytes] | None
    client_info: Optional[bytes]
    no_confirm: bool
    timeout: Optional[timedelta]

    previous_received: datetime

    task: Task
    tasks: List[Task]

    state = SchedulerState.START
    states = SchedulerState

    def __init__(self: ClientScheduler,
                 queue: QueueClient,
                 local: Queue[Optional[Task]],
                 no_confirm: bool = False,
                 timeout: int = None) -> None:
        """Assign remote queue client and local task queue."""
        self.queue = queue
        self.local = local
        self.bundle = []
        self.tasks = []
        self.client_info = None
        self.no_confirm = no_confirm
        self.timeout = None if not timeout else timedelta(seconds=timeout)
        self.previous_received = datetime.now()

    @functools.cached_property
    def actions(self: ClientScheduler) -> Dict[SchedulerState, Callable[[], SchedulerState]]:
        return {
            SchedulerState.START: self.start,
            SchedulerState.GET_REMOTE: self.get_remote,
            SchedulerState.UNPACK: self.unpack_bundle,
            SchedulerState.PUT_CONFIRM: self.put_confirm,
            SchedulerState.POP_TASK: self.pop_task,
            SchedulerState.PUT_LOCAL: self.put_local,
            SchedulerState.FINAL: self.finalize,
        }

    def start(self: ClientScheduler) -> SchedulerState:
        """Jump to GET_REMOTE state."""
        timeout_label = self.timeout or 'no'
        log.debug(f'Started (scheduler: {timeout_label} timeout)')
        return SchedulerState.GET_REMOTE

    def get_remote(self: ClientScheduler) -> SchedulerState:
        """Get the next task bundle from the server."""
        if (signal := check_signal()) in (SIGUSR1, SIGUSR2) and CLIENT_STANDALONE_MODE:
            log.warning(f'Signal interrupt ({SIGNAL_MAP[signal]})')
            return SchedulerState.FINAL
        try:
            self.bundle = self.queue.scheduled.get(timeout=2)
            self.queue.scheduled.task_done()
            self.previous_received = datetime.now()
            if self.bundle is not None:
                log.debug(f'Received {len(self.bundle)} tasks ({HOSTNAME}: {INSTANCE})')
                return SchedulerState.UNPACK
            else:
                log.debug('Disconnect received')
                return SchedulerState.FINAL
        except QueueEmpty:
            waited = datetime.now() - self.previous_received
            if self.timeout is None or waited < self.timeout:
                return SchedulerState.GET_REMOTE
            else:
                log.debug(f'Timeout reached ({waited})')
                return SchedulerState.FINAL

    def unpack_bundle(self: ClientScheduler) -> SchedulerState:
        """Unpack latest bundle of tasks."""
        self.tasks = [Task.unpack(data) for data in self.bundle]
        if not self.no_confirm:
            self.client_info = ClientInfo.from_tasks(self.tasks).pack()
            return SchedulerState.PUT_CONFIRM
        else:
            return SchedulerState.POP_TASK

    def put_confirm(self: ClientScheduler) -> SchedulerState:
        """Put confirmation details back on remote queue."""
        try:
            self.queue.confirmed.put(self.client_info, timeout=2)
            log.debug(f'Confirmed {len(self.tasks)} tasks ({HOSTNAME}: {INSTANCE})')
            return SchedulerState.POP_TASK
        except QueueFull:
            return SchedulerState.PUT_CONFIRM

    def pop_task(self: ClientScheduler) -> SchedulerState:
        """Pop next task off current task list."""
        try:
            self.task = self.tasks.pop(0)
            return SchedulerState.PUT_LOCAL
        except IndexError:
            return SchedulerState.GET_REMOTE

    def put_local(self: ClientScheduler) -> SchedulerState:
        """Put latest task on the local task queue."""
        try:
            self.local.put(self.task, timeout=1)
            return SchedulerState.POP_TASK
        except QueueFull:
            return SchedulerState.PUT_LOCAL

    @staticmethod
    def finalize() -> SchedulerState:
        """Stop scheduler."""
        log.debug('Done (scheduler)')
        return SchedulerState.HALT


class ClientSchedulerThread(Thread):
    """Run client scheduler in dedicated thread."""

    def __init__(self: ClientSchedulerThread,
                 queue: QueueClient,
                 local: Queue[Optional[bytes]],
                 no_confirm: bool = False,
                 timeout: int = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-client-scheduler')
        self.machine = ClientScheduler(queue=queue, local=local, no_confirm=no_confirm, timeout=timeout)

    def run_with_exceptions(self: ClientSchedulerThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: ClientSchedulerThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (scheduler)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_BUNDLESIZE: Final[int] = default.client.bundlesize
"""Default size of task bundles."""

DEFAULT_BUNDLEWAIT: Final[int] = default.client.bundlewait
"""Default waiting period before forcing task bundle push."""


class CollectorState(State, Enum):
    """Finite states of collector."""
    START = 0
    GET_LOCAL = 1
    CHECK_BUNDLE = 2
    PACK_BUNDLE = 3
    PUT_REMOTE = 4
    FINAL = 5
    HALT = 6


class ClientCollector(StateMachine):
    """Collect finished tasks and bundle for outgoing queue."""

    tasks: List[Task]
    bundle: List[bytes]

    queue: QueueClient
    local: Queue[Optional[Task]]

    bundlesize: int
    bundlewait: int
    previous_send: datetime

    state = CollectorState.START
    states = CollectorState

    def __init__(self: ClientCollector, queue: QueueClient, local: Queue[Optional[Task]],
                 bundlesize: int = DEFAULT_BUNDLESIZE, bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Collect tasks from local queue of finished tasks and push them to the server."""
        self.tasks = []
        self.bundle = []
        self.local = local
        self.queue = queue
        self.bundlesize = bundlesize
        self.bundlewait = bundlewait

    @functools.cached_property
    def actions(self: ClientCollector) -> Dict[CollectorState, Callable[[], CollectorState]]:
        return {
            CollectorState.START: self.start,
            CollectorState.GET_LOCAL: self.get_local,
            CollectorState.CHECK_BUNDLE: self.check_bundle,
            CollectorState.PACK_BUNDLE: self.pack_bundle,
            CollectorState.PUT_REMOTE: self.put_remote,
            CollectorState.FINAL: self.finalize,
        }

    def start(self: ClientCollector) -> CollectorState:
        """Jump to GET_LOCAL state."""
        log.debug('Started (collector)')
        self.previous_send = datetime.now()
        return CollectorState.GET_LOCAL

    def get_local(self: ClientCollector) -> CollectorState:
        """Get the next task from the local completed task queue."""
        try:
            task = self.local.get(timeout=1)
            self.local.task_done()
            if task:
                self.tasks.append(task)
                return CollectorState.CHECK_BUNDLE
            else:
                return CollectorState.FINAL
        except QueueEmpty:
            return CollectorState.CHECK_BUNDLE

    def check_bundle(self: ClientCollector) -> CollectorState:
        """Check state of task bundle and proceed with return if necessary."""
        wait_time = (datetime.now() - self.previous_send)
        since_last = wait_time.total_seconds()
        if len(self.tasks) >= self.bundlesize:
            log.trace(f'Bundle size reached ({len(self.tasks)} tasks)')
            return CollectorState.PACK_BUNDLE
        elif since_last >= self.bundlewait:
            log.trace(f'Bundle wait exceeded ({wait_time})')
            return CollectorState.PACK_BUNDLE
        else:
            return CollectorState.GET_LOCAL

    def pack_bundle(self: ClientCollector) -> CollectorState:
        """Pack tasks into bundle before pushing back to server."""
        self.bundle = [task.pack() for task in self.tasks]
        return CollectorState.PUT_REMOTE

    def put_remote(self: ClientCollector) -> CollectorState:
        """Push out bundle of completed tasks."""
        try:
            if self.bundle:
                self.queue.completed.put(self.bundle, timeout=2)
                log.trace(f'Bundle returned ({len(self.bundle)} tasks)')
                self.tasks.clear()
                self.bundle.clear()
                self.previous_send = datetime.now()
            else:
                log.trace('Bundle empty')
            return CollectorState.GET_LOCAL
        except QueueFull:
            return CollectorState.PUT_REMOTE

    def finalize(self: ClientCollector) -> CollectorState:
        """Push out any remaining tasks and halt."""
        self.put_remote()
        log.debug('Done (collector)')
        return CollectorState.HALT


class ClientCollectorThread(Thread):
    """Run client collector within dedicated thread."""

    def __init__(self: ClientCollectorThread, queue: QueueClient, local: Queue[Optional[bytes]],
                 bundlesize: int = DEFAULT_BUNDLESIZE, bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-client-collector')
        self.machine = ClientCollector(queue=queue, local=local, bundlesize=bundlesize, bundlewait=bundlewait)

    def run_with_exceptions(self: ClientCollectorThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: ClientCollectorThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (collector)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_SIGNALWAIT: Final[int] = default.task.signalwait
"""Default signal escalation wait period in seconds."""

DEFAULT_TEMPLATE: Final[str] = DEFAULT_TEMPLATE  # redefined for documentation purposes
"""Default command pattern for template expansion."""


def task_env(task: Task) -> Dict[str, str]:
    """Build environment dictionary for the given `task`."""
    task_data = task.to_json()
    try:
        # We have to flatten tag data separately, otherwise we'd have TASK_TAG='{...}'
        tag_data = Namespace(task_data.pop('tag')).to_env().flatten(prefix='TASK_TAG')
    except Exception:  # noqa: any exception
        tag_data = {}
    return {
        **os.environ,
        **load_task_env(),
        **Namespace.from_dict(task_data).to_env().flatten(prefix='TASK'),
        **tag_data,
        'TASK_CWD': config.task.cwd,
        'TASK_OUTPATH': os.path.join(default_path.lib, 'task', f'{task.id}.out'),
        'TASK_ERRPATH': os.path.join(default_path.lib, 'task', f'{task.id}.err'),
        'TASK_CSVPATH': os.path.join(default_path.lib, 'task', f'{task.id}.csv'),
    }


# Global CPU count and main memory tracking variables
executor_lock: Lock = Lock()
cores_total: int = CPU_COUNT
cores_active: int = 0
memory_total: int = MEMORY_TOTAL
memory_active: int = 0
tasks_priority: Dict[str, int] = defaultdict(int)
tasks_active: Dict[str, Task] = {}
tasks_pending: Dict[str, Task] = {}

# Tolerances for resource limit exceeded warnings
CPU_TOL: Final[float] = 0.05
MEM_TOL: Final[int] = parse_bytes('5MB')


def backfill_possible(task: Task) -> bool:
    """Check if possible to backfill task before highest priority task start."""

    # This function should never be called directly.
    # It relies on the global lock within acquire_resources()

    # If we are here than the task has either core or memory requirements and is not the highest-priority task.
    # This procedure determines what the worst-case expected start-time of the highest-priority task is
    # given the current running tasks' resources and when they will be reclaimed.
    # If and only if we can account for those resources and the given task has a known walltime time that
    # is less than this expected start-time do we allow it to start (return True).

    if not task.timeout:
        return False  # Cannot backfill if we don't know when the task will end

    max_priority = max(tasks_priority.values())
    priority_task, = islice(filter(lambda t: tasks_priority[t.id] == max_priority, tasks_pending.values()), 1)
    available_cores = cores_total - cores_active
    available_memory = memory_total - memory_active

    # Walk active tasks in order of expected worst-case completion time if known,
    # accumulate cores and memory until we have enough for next highest priority task,
    # if current task can finish in less time than this expected worse-case time, then let it start
    now = datetime.now().astimezone()
    end_times = {t.id: (t.start_time or now) + timedelta(seconds=t.timeout)
                 for t in tasks_active.values() if t.timeout}
    for next_task_id in sorted(end_times.keys(), key=lambda task_id: end_times[task_id]):
        next_task = tasks_active[next_task_id]
        time_remaining = end_times[next_task_id] - now
        available_cores += next_task.cores or 0
        available_memory += next_task.memory or 0
        if (available_cores >= (priority_task.cores or 0) and
            available_memory >= (priority_task.memory or 0) and
            task.timeout < (dt := time_remaining.total_seconds())
        ):
            log.debug(f'Backfilling task ({task.id}): timeout={task.timeout} < {dt:.1f} seconds')
            return True
    else:
        return False


def set_resources(cores: Optional[int] = None, memory: Optional[int] = None) -> None:
    """Set total resources (if we are not using the whole node)."""
    global cores_total, memory_total
    with executor_lock:
        cores_total = cores or CPU_COUNT
        memory_total = memory or MEMORY_TOTAL


def acquire_resources(task: Task) -> Tuple[bool, str, float]:
    """
    Track resources and return (can_start, reason, priority_ratio).
    The priority_ratio is used to compute an adaptive sleep time for waiting tasks.
    We build a range of [0.5, 1.0] seconds to naturally wake up in an orderly fashion.
    """

    global cores_active, memory_active
    with executor_lock:

        # Cores may be unspecified or 0, we bypass this if no resources required
        if task.cores and (available_cores := cores_total - cores_active) < task.cores:
            tasks_priority[task.id] += 1
            tasks_pending[task.id] = task
            max_priority = max(tasks_priority.values())
            return (False,
                    f'cores: {available_cores} < {task.cores}',
                    tasks_priority[task.id] / max_priority)

        # Memory may be unspecified or 0, we bypass this if no resources required
        if task.memory and (available_memory := memory_total - memory_active) < task.memory:
            tasks_priority[task.id] += 1
            tasks_pending[task.id] = task
            max_priority = max(tasks_priority.values())
            return (False,
                    f'memory: {format_bytes(available_memory)} < {format_bytes(task.memory)}',
                    tasks_priority[task.id] / max_priority)

        priority = tasks_priority[task.id]  # Force initialization to 0 if first pass
        max_priority = max(tasks_priority.values())  # Might be the same task

        # If these are the same task we short-circuit the backfill process (priority == max_priority)
        if priority < max_priority and not backfill_possible(task):
            tasks_priority[task.id] += 1
            tasks_pending[task.id] = task
            return (False,
                    f'priority: {priority} < {max_priority}',
                    tasks_priority[task.id] / max_priority)
        else:
            cores_active += task.cores or 0
            memory_active += task.memory or 0
            tasks_priority.pop(task.id, None)
            tasks_pending.pop(task.id, None)
            tasks_active[task.id] = task
            return True, '', 1.0


def release_resources(task: Task) -> None:
    """Release cores and memory."""
    global cores_active, memory_active
    with executor_lock:
        cores_active -= task.cores or 0
        memory_active -= task.memory or 0
        tasks_active.pop(task.id, None)


def insufficient_resources(task: Task) -> Tuple[bool, str]:
    """Signal if and why a client cannot run a task due to insufficient resources."""
    if task.cores and cores_total < task.cores:
        return False, f'cores: {cores_total} < {task.cores}'
    if task.memory and memory_total < task.memory:
        return False, f'memory: {memory_total} < {task.memory}'
    else:
        return True, ''


class TaskState(State, Enum):
    """Finite states for task executor."""
    START = 0
    GET_LOCAL = 1
    CREATE_TASK = 2
    START_TASK = 3
    WAIT_TASK = 4
    CHECK_TASK = 5
    WAIT_SIGNAL = 6
    STOP_TASK = 7
    TERM_TASK = 8
    KILL_TASK = 9
    WAIT_RATE = 10
    PUT_LOCAL = 11
    FINAL = 12
    HALT = 13


# Special exit status indicates cancellation
TASK_TEMPLATE_ERROR: Final[int] = -2
TASK_RESOURCE_ERROR: Final[int] = -3


class TaskExecutor(StateMachine):
    """Run tasks locally."""

    id: int
    task: Task
    process: Popen
    template: Template
    redirect_output: IO
    redirect_errors: IO
    redirect_monitor: IO | None
    monitor: bool
    capture: bool

    received: datetime
    elapsed: timedelta
    timeout: Optional[int]
    signalwait: int
    ratelimit: Optional[int]
    stop_requested: Optional[datetime]
    attempted_sigint: bool
    attempted_sigterm: bool
    attempted_sigkill: bool
    resource_limit_warned_cores: bool
    resource_limit_warned_memory: bool

    inbound: Queue[Optional[Task]]
    outbound: Queue[Optional[Task]]

    state = TaskState.START
    states = TaskState

    def __init__(self: TaskExecutor,
                 id: int,
                 inbound: Queue[Optional[Task]],
                 outbound: Queue[Optional[Task]],
                 template: str = DEFAULT_TEMPLATE,
                 redirect_output: IO = None,
                 redirect_errors: IO = None,
                 capture: bool = False,
                 monitor: bool = False,
                 timeout: int = None,
                 signalwait: int = DEFAULT_SIGNALWAIT,
                 ratelimit: float = None) -> None:
        """Initialize task executor."""
        self.id = id
        self.template = Template(template)
        self.inbound = inbound
        self.outbound = outbound
        self.redirect_output = redirect_output or sys.stdout
        self.redirect_errors = redirect_errors or sys.stderr
        self.redirect_monitor = None
        self.capture = capture
        self.monitor = monitor
        self.timeout = timeout
        self.signalwait = signalwait
        self.ratelimit = ratelimit

    @functools.cached_property
    def actions(self: TaskExecutor) -> Dict[TaskState, Callable[[], TaskState]]:
        return {
            TaskState.START: self.start,
            TaskState.GET_LOCAL: self.get_local,
            TaskState.CREATE_TASK: self.create_task,
            TaskState.START_TASK: self.start_task,
            TaskState.WAIT_TASK: self.wait_task,
            TaskState.CHECK_TASK: self.check_task,
            TaskState.WAIT_SIGNAL: self.wait_signal,
            TaskState.STOP_TASK: self.stop_task,
            TaskState.TERM_TASK: self.term_task,
            TaskState.KILL_TASK: self.kill_task,
            TaskState.WAIT_RATE: self.wait_ratelimit,
            TaskState.PUT_LOCAL: self.put_local,
            TaskState.FINAL: self.finalize,
        }

    def start(self: TaskExecutor) -> TaskState:
        """Jump to GET_LOCAL state."""
        log.debug(f'Started (executor-{self.id})')
        return TaskState.GET_LOCAL

    def get_local(self: TaskExecutor) -> TaskState:
        """Get the next task from the local queue of new tasks."""
        try:
            self.task = self.inbound.get(timeout=1)
            self.inbound.task_done()
            self.received = datetime.now()
            return TaskState.CREATE_TASK if self.task else TaskState.FINAL
        except QueueEmpty:
            return TaskState.GET_LOCAL

    def create_task(self: TaskExecutor) -> TaskState:
        """Expand template and initialize task command-line."""
        try:
            self.task.client_id = INSTANCE
            self.task.client_host = HOSTNAME
            self.task.command = self.template.expand(self.task.args)
        except Exception as error:
            log.error(f'Template expansion failed for task ({self.task.id}): {error} (cancelled)')
            self.task.start_time = datetime.now().astimezone()
            self.task.completion_time = datetime.now().astimezone()
            self.task.exit_status = TASK_TEMPLATE_ERROR
            return TaskState.PUT_LOCAL
        eligible, reason = insufficient_resources(self.task)
        if not eligible:
            log.error(f'Insufficient resources for task ({self.task.id}): {reason} (cancelled)')
            self.task.start_time = datetime.now().astimezone()
            self.task.completion_time = datetime.now().astimezone()
            self.task.exit_status = TASK_RESOURCE_ERROR
            return TaskState.PUT_LOCAL
        else:
            return TaskState.START_TASK

    def start_task(self: TaskExecutor) -> TaskState:
        """Start current task locally."""
        resources_available, reason, priority_ratio = acquire_resources(self.task)
        if not resources_available:
            # Adaptive sleep based on priority ratio with added jitter
            # High priority (ratio=1.0) sleeps ~0.5s, low priority (ratio=0.0) sleeps ~1.0s
            base_sleep = 1.0 - 0.5 * priority_ratio  # Linear mapping to [0.5, 1.0]
            jitter = random.uniform(-0.05, 0.05)  # Prevent exact collisions
            sleep_time = max(0.5, min(1.0, base_sleep + jitter))  # Safety clamp
            log.trace(f'Waiting task start ({self.task.id}): {reason} '
                      f'(priority: {priority_ratio:.3f}, waiting: {sleep_time:.3f}s)')
            time.sleep(sleep_time)
            return TaskState.START_TASK
        self.task.start_time = datetime.now().astimezone()  # Possible TZ aware submit_time
        self.task.waited = int((self.task.start_time - self.task.submit_time.astimezone()).total_seconds())
        env = task_env(self.task)
        if self.capture:
            self.task.outpath = env['TASK_OUTPATH']
            self.task.errpath = env['TASK_ERRPATH']
            self.redirect_output = open(env['TASK_OUTPATH'], mode='w')
            self.redirect_errors = open(env['TASK_ERRPATH'], mode='w')
        if self.monitor:
            self.task.csvpath = env['TASK_CSVPATH']
            self.redirect_monitor = open(env['TASK_CSVPATH'], mode='w')
            print('time,cores,memory', file=self.redirect_monitor)
        self.stop_requested = None
        self.attempted_sigint = False
        self.attempted_sigterm = False
        self.attempted_sigkill = False
        self.resource_limit_warned_cores = False
        self.resource_limit_warned_memory = False
        self.process = Popen(self.task.command, shell=True,
                             stdout=self.redirect_output, stderr=self.redirect_errors,
                             cwd=config.task.cwd, env=env)
        log.info(f'Running task ({self.task.id})')
        log.debug(f'Running task ({self.task.id})[{self.process.pid}]: {self.task.command}')
        return TaskState.WAIT_TASK

    def wait_task(self: TaskExecutor) -> TaskState:
        """Wait for current task to complete."""
        try:
            self.task.exit_status = self.process.wait(timeout=1)
            self.task.completion_time = datetime.now().astimezone()
            elapsed = self.task.completion_time - self.task.start_time
            elapsed_nearest_second = timedelta(seconds=round(elapsed.total_seconds()))
            self.task.duration = int(elapsed.total_seconds())
            log.debug(f'Completed task ({self.task.id}) elapsed: {elapsed_nearest_second}')
            if self.capture:
                self.redirect_output.close()
                self.redirect_errors.close()
            if self.monitor:
                self.redirect_monitor.close()
                clear_processes(self.process.pid)
            release_resources(self.task)
            return TaskState.WAIT_RATE
        except TimeoutExpired:
            self.elapsed = datetime.now().astimezone() - self.task.start_time
            elapsed_nearest_second = timedelta(seconds=round(self.elapsed.total_seconds()))
            if not self.monitor:
                log.trace(f'Waiting task completion ({self.task.id}) elapsed: {elapsed_nearest_second}')
            else:
                ts, cores, memory = check_resources(self.process.pid)
                log.trace(f'Waiting task completion ({self.task.id}) '
                          f'elapsed: {elapsed_nearest_second} '
                          f'cores: {cores:.2f} '
                          f'memory: {format_bytes(memory)}')
                self.task.cores_max = max(self.task.cores_max or 0, cores)
                self.task.memory_max = max(self.task.memory_max or 0, memory)
                print(f'{ts},{cores:.2f},{memory}', file=self.redirect_monitor)
                if (self.task.cores and
                    self.task.cores_max > (self.task.cores + CPU_TOL) and
                    not self.resource_limit_warned_cores
                ):
                    self.resource_limit_warned_cores = True
                    log.warning(f'Resource limit exceeded ({self.task.id}): '
                                f'cores {self.task.cores_max:.2f} (used) > {self.task.cores:.2f} (allocated)')
                if (self.task.memory and
                    self.task.memory_max > (self.task.memory + MEM_TOL) and
                    not self.resource_limit_warned_memory
                ):
                    self.resource_limit_warned_memory = True
                    log.warning(f'Resource limit exceeded ({self.task.id}): memory '
                                f'{format_bytes(self.task.memory_max)} (used) > '
                                f'{format_bytes(self.task.memory)} (allocated)')
            if self.stop_requested:
                return TaskState.WAIT_SIGNAL
            else:
                return TaskState.CHECK_TASK

    def check_task(self: TaskExecutor) -> TaskState:
        """Check for timeout or interrupts."""
        if check_signal() == SIGUSR2:  # NOTE: regardless of CLIENT_STANDALONE_MODE
            log.warning(f'Signal interrupt (SIGUSR2: executor-{self.id})')
            self.stop_requested = datetime.now()
            return TaskState.WAIT_SIGNAL
        timeout = min(filter(None, [self.timeout, self.task.timeout]), default=None)
        if not timeout or self.elapsed.total_seconds() < timeout:
            return TaskState.WAIT_TASK
        else:
            log.warning(f'Walltime limit exceeded ({self.task.id}): {self.elapsed} > {timeout}')
            self.stop_requested = datetime.now()
            return TaskState.WAIT_SIGNAL

    def wait_signal(self: TaskExecutor) -> TaskState:
        """Wait on interrupts."""
        if not self.attempted_sigint:
            return TaskState.STOP_TASK
        elif (datetime.now() - self.stop_requested).total_seconds() < 1 * self.signalwait:
            return TaskState.WAIT_TASK
        elif not self.attempted_sigterm:
            log.error(f'Interrupt ignored ({self.task.id})')
            return TaskState.TERM_TASK
        elif (datetime.now() - self.stop_requested).total_seconds() < 2 * self.signalwait:
            return TaskState.WAIT_TASK
        elif not self.attempted_sigkill:
            log.error(f'Terminate ignored ({self.task.id})')
            return TaskState.KILL_TASK
        elif (datetime.now() - self.stop_requested).total_seconds() < 3 * self.signalwait:
            return TaskState.WAIT_TASK
        else:
            log.critical(f'Process ignored SIGKILL ({self.task.id}: {self.process.pid})')
            log.critical(f'Shutting down executor ({self.id})')
            return TaskState.FINAL

    def stop_task(self: TaskExecutor) -> TaskState:
        """Send SIGINT to task process."""
        log.debug(f'Sending SIGINT ({self.task.id}: {self.process.pid})')
        self.process.send_signal(SIGINT)
        self.attempted_sigint = True
        return TaskState.WAIT_TASK

    def term_task(self: TaskExecutor) -> TaskState:
        """Send SIGTERM to task process."""
        log.debug(f'Sending SIGTERM ({self.task.id}: {self.process.pid})')
        self.process.terminate()
        self.attempted_sigterm = True
        return TaskState.WAIT_TASK

    def kill_task(self: TaskExecutor) -> TaskState:
        """Send SIGKILL or halt executor if ignored."""
        log.debug(f'Sending SIGKILL ({self.task.id}: {self.process.pid})')
        self.process.kill()
        self.attempted_sigkill = True
        return TaskState.WAIT_TASK

    def wait_ratelimit(self: TaskExecutor) -> TaskState:
        """Ensure the rate limit is not exceeded."""
        if not self.ratelimit:
            return TaskState.PUT_LOCAL
        elapsed = datetime.now() - self.received
        min_seconds = timedelta(seconds=1/self.ratelimit)
        if elapsed > min_seconds:
            return TaskState.PUT_LOCAL
        remaining = (min_seconds - elapsed).total_seconds()
        if remaining <= 1:
            log.trace(f'Rate limit exceeded ({self.task.id}): pausing for {remaining:.2f} seconds')
            time.sleep(remaining)
            return TaskState.PUT_LOCAL
        else:
            log.trace(f'Rate limit exceeded ({self.task.id}): pausing for 1 second ({remaining:.2f} remaining)')
            time.sleep(1)
            return TaskState.WAIT_RATE

    def put_local(self: TaskExecutor) -> TaskState:
        """Put completed task on outbound queue."""
        try:
            self.outbound.put(self.task, timeout=1)
            return TaskState.GET_LOCAL
        except QueueFull:
            return TaskState.PUT_LOCAL

    def finalize(self: TaskExecutor) -> TaskState:
        """Push out any remaining tasks and halt."""
        log.debug(f'Done (executor-{self.id})')
        if self.redirect_output is not sys.stdout:
            self.redirect_output.close()
        if self.redirect_errors is not sys.stderr:
            self.redirect_errors.close()
        if self.redirect_monitor is not None:
            self.redirect_monitor.close()
        return TaskState.HALT


class TaskThread(Thread):
    """Run task executor within dedicated thread."""

    id: int

    def __init__(self: TaskThread,
                 id: int,
                 inbound: Queue[Optional[str]],
                 outbound: Queue[Optional[str]],
                 template: str = DEFAULT_TEMPLATE,
                 capture: bool = False,
                 monitor: bool = False,
                 redirect_output: IO = None,
                 redirect_errors: IO = None,
                 timeout: int = None,
                 signalwait: int = DEFAULT_SIGNALWAIT,
                 ratelimit: float = None) -> None:
        """Initialize task executor."""
        self.id = id
        super().__init__(name=f'hypershell-executor-{id}')
        self.machine = TaskExecutor(id=id, inbound=inbound, outbound=outbound, template=template,
                                    redirect_output=redirect_output, redirect_errors=redirect_errors,
                                    capture=capture, monitor=monitor, timeout=timeout,
                                    signalwait=signalwait, ratelimit=ratelimit)

    def run_with_exceptions(self: TaskThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: TaskThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning(f'Stopping (executor-{self.id})')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class HeartbeatState(State, Enum):
    """Finite states for heartbeat machine."""
    START = 0
    SUBMIT = 1
    WAIT = 2
    FINAL = 3
    HALT = 4


DEFAULT_HEARTRATE: Final[int] = default.client.heartrate
"""Period in seconds to wait between heartbeats."""


class ClientHeartbeat(StateMachine):
    """Register heartbeats with remote server."""

    queue: QueueClient
    heartrate: timedelta
    previous: datetime = None

    no_wait: bool = False
    client_state: ClientState = ClientState.RUNNING

    state = HeartbeatState.START
    states = HeartbeatState

    def __init__(self: ClientHeartbeat, queue: QueueClient, heartrate: int = DEFAULT_HEARTRATE) -> None:
        """Initialize heartbeat machine."""
        self.queue = queue
        self.previous = datetime.now()
        self.heartrate = timedelta(seconds=heartrate)

    @functools.cached_property
    def actions(self: ClientHeartbeat) -> Dict[HeartbeatState, Callable[[], HeartbeatState]]:
        return {
            HeartbeatState.START: self.start,
            HeartbeatState.SUBMIT: self.submit,
            HeartbeatState.WAIT: self.wait,
            HeartbeatState.FINAL: self.finalize,
        }

    @staticmethod
    def start() -> HeartbeatState:
        """Jump to SUBMIT state."""
        log.debug(f'Started (heartbeat)')
        return HeartbeatState.SUBMIT

    def submit(self: ClientHeartbeat) -> HeartbeatState:
        """Publish new heartbeat to remote queue."""
        try:
            client_state = self.client_state  # atomic
            heartbeat = Heartbeat.new(state=client_state)
            self.queue.heartbeat.put(heartbeat.pack(), timeout=2)
            if client_state is ClientState.RUNNING:
                log.trace(f'Heartbeat - running ({heartbeat.host}: {heartbeat.uuid})')
                return HeartbeatState.WAIT
            else:
                log.trace(f'Heartbeat - final ({heartbeat.host}: {heartbeat.uuid})')
                return HeartbeatState.FINAL
        except QueueFull:
            return HeartbeatState.SUBMIT

    def wait(self: ClientHeartbeat) -> HeartbeatState:
        """Wait until next needed heartbeat."""
        if self.no_wait:
            return HeartbeatState.SUBMIT
        now = datetime.now()
        if (now - self.previous) < self.heartrate:
            time.sleep(1)
            return HeartbeatState.WAIT
        else:
            self.previous = now
            return HeartbeatState.SUBMIT

    @staticmethod
    def finalize() -> HeartbeatState:
        """Stop heartbeats."""
        log.debug(f'Done (heartbeat)')
        return HeartbeatState.HALT


class ClientHeartbeatThread(Thread):
    """Run heartbeat machine within dedicated thread."""

    def __init__(self: ClientHeartbeatThread, queue: QueueClient, heartrate: int = DEFAULT_HEARTRATE) -> None:
        """Initialize heartbeat machine."""
        super().__init__(name=f'hypershell-heartbeat')
        self.machine = ClientHeartbeat(queue=queue, heartrate=heartrate)

    def run_with_exceptions(self: ClientHeartbeatThread) -> None:
        """Run machine."""
        self.machine.run()

    def signal_finished(self: ClientHeartbeatThread) -> None:
        """Set client state to communicate completion."""
        self.machine.client_state = ClientState.FINISHED
        self.machine.no_wait = True

    def stop(self: ClientHeartbeatThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (heartbeat)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_NUM_THREADS: Final[int] = 1
"""Default number of executor threads per client."""

# Backwards compatibility alias
DEFAULT_NUM_TASKS: Final[int] = DEFAULT_NUM_THREADS

# We do not delay connecting to the server unless explicitly specified
DEFAULT_DELAY: Final[int] = 0
"""Default delay in seconds on client startup."""

DEFAULT_HOST: Final[str] = QueueConfig.host
"""Default host for server connection."""

DEFAULT_PORT: Final[int] = QueueConfig.port
"""Default port for server connection."""

DEFAULT_AUTH: Final[str] = QueueConfig.auth
"""Default authentication key for server (**DO NOT USE THIS**)."""


class ClientThread(Thread):
    """
    Run client within dedicated thread.
    Run until either disconnect requested from server or `client_timeout` reached.
    Will also halt for SIGUSR2 signal on UNIX.

    Args:
        num_threads (int, optional):
            Number of executor threads (use 0 for auto-detection based on cores).
            See :const:`DEFAULT_NUM_THREADS`.

        bundlesize (int optional):
            Size of task bundles returned to server.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int optional):
            Waiting period in seconds before forcing return of task bundle to server.
            See :const:`DEFAULT_BUNDLEWAIT`.

        address (tuple, optional):
            Server host address for server with port number.
            See :const:`DEFAULT_HOST` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key.
            See :const:`DEFAULT_AUTH`.

        template (str, optional):
            Template command pattern. See :const:`DEFAULT_TEMPLATE`.

        redirect_output (IO, optional):
            Optional file-like object for <stdout> redirect.

        redirect_errors (IO, optional):
            Optional file-like object for <stderr> redirect.

        heartrate (int, optional):
            Period in seconds to wait between heartbeats.
            See :const:`DEFAULT_HEARTRATE`,

        capture (bool, optional):
            Isolate task <stdout> and <stderr> in discrete files (default: False).

        monitor (bool, optional):
            Track CPU cores and memory usage of each task (default: False).

        cores (int, optional):
            Set core limit for client (default: all available cores).

        memory (int, optional):
            Set memory limit for client (default: all available memory).

        delay_start (float, optional):
            Delay in seconds before connecting to server.
            See :const:`DEFAULT_DELAY`.

        no_confirm (bool, optional):
            Disable client confirmation of tasks received.

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds.
            By default, the client waits indefinitely on tasks.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`DEFAULT_SIGNALWAIT`.

        ratelimit (int, optional):
            Maximum allowed tasks per second (default: none).
            There is no limit on task throughput unless specified.

    Example:
        >>> from hypershell.client import ClientThread
        >>> client = ClientThread.new(num_threads=16, address=('localhost', 54321),
        ...                           auth='my-secret-key', capture=True)
        >>> client.join()

    See Also:
        - :meth:`run_client`
    """

    client: QueueClient
    num_threads: int
    delay_start: float
    no_confirm: bool

    inbound: Queue[Optional[Task]]
    outbound: Queue[Optional[Task]]
    scheduler: ClientSchedulerThread
    heartbeat: ClientHeartbeatThread
    collector: ClientCollectorThread
    executors: List[TaskThread]

    def __init__(self: ClientThread,
                 num_threads: int = DEFAULT_NUM_THREADS,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 address: Tuple[str, int] = (DEFAULT_HOST, DEFAULT_PORT),
                 auth: str = DEFAULT_AUTH,
                 template: str = DEFAULT_TEMPLATE,
                 redirect_output: IO = None,
                 redirect_errors: IO = None,
                 heartrate: int = DEFAULT_HEARTRATE,
                 capture: bool = False,
                 monitor: bool = False,
                 cores: int = None,
                 memory: int = None,
                 delay_start: float = DEFAULT_DELAY,
                 no_confirm: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT,
                 ratelimit: int = None) -> None:
        """Initialize queue manager and child threads."""
        super().__init__(name='hypershell-client')
        set_resources(cores, memory)
        if num_threads == 0:
            num_threads = CPU_COUNT
            log.debug(f'Auto-detected {num_threads} executor threads (based on available cores)')
        self.num_threads = num_threads
        self.delay_start = delay_start
        self.no_confirm = no_confirm
        self.client = QueueClient(config=QueueConfig(host=address[0], port=address[1], auth=auth))
        self.inbound = Queue(maxsize=bundlesize)
        self.outbound = Queue(maxsize=bundlesize)
        self.scheduler = ClientSchedulerThread(queue=self.client, local=self.inbound,
                                               no_confirm=no_confirm, timeout=client_timeout)
        self.heartbeat = ClientHeartbeatThread(queue=self.client, heartrate=heartrate)
        self.collector = ClientCollectorThread(queue=self.client, local=self.outbound,
                                               bundlesize=bundlesize, bundlewait=bundlewait)
        if ratelimit is not None:
            ratelimit = ratelimit / num_threads
            log.debug(f'Rate limit enabled: {ratelimit:.2} tasks per second per thread')
        self.executors = [TaskThread(id=count+1,
                                     inbound=self.inbound, outbound=self.outbound,
                                     redirect_output=redirect_output, redirect_errors=redirect_errors,
                                     template=template, capture=capture, monitor=monitor, timeout=task_timeout,
                                     signalwait=task_signalwait, ratelimit=ratelimit)
                          for count in range(num_threads)]

    def run_with_exceptions(self: ClientThread) -> None:
        """Start child threads, wait."""
        log.debug(f'Started ({self.num_threads} executors)')
        self.wait_start()
        with self.client:
            self.start_threads()
            self.wait_scheduler()
            self.wait_executors()
            self.wait_collector()
            self.wait_heartbeat()
        log.debug('Done')

    def wait_start(self: ClientThread) -> None:
        """Wait constant period or random interval."""
        if self.delay_start == 0:
            return
        if self.delay_start > 0:
            log.debug(f'Waiting ({self.delay_start} seconds)')
            time.sleep(self.delay_start)
        else:
            delay = random.uniform(0, -1 * self.delay_start)
            log.debug(f'Waiting random ({delay:.1f} seconds)')
            time.sleep(delay)

    def start_threads(self: ClientThread) -> None:
        """Start child threads."""
        self.scheduler.start()
        self.collector.start()
        self.heartbeat.start()
        for executor in self.executors:
            executor.start()

    def wait_scheduler(self: ClientThread) -> None:
        """Wait for all tasks to be completed."""
        log.trace('Waiting (scheduler)')
        self.scheduler.join()

    def wait_collector(self: ClientThread) -> None:
        """Signal collector to halt."""
        log.trace('Waiting (collector)')
        self.outbound.put(None)
        self.collector.join()

    def wait_executors(self: ClientThread) -> None:
        """Send disconnect signal to each task executor thread."""
        for _ in self.executors:
            self.inbound.put(None)  # signal executors to shut down
        for thread in self.executors:
            log.trace(f'Waiting (executor-{thread.id})')
            thread.join()

    def wait_heartbeat(self: ClientThread) -> None:
        """Signal HALT on heartbeat."""
        log.trace('Waiting (heartbeat)')
        self.heartbeat.signal_finished()
        self.heartbeat.join()

    def stop(self: ClientThread, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        log.warning('Stopping')
        self.scheduler.stop(wait=wait, timeout=timeout)
        self.collector.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


def run_client(num_threads: int = DEFAULT_NUM_THREADS,
               bundlesize: int = DEFAULT_BUNDLESIZE,
               bundlewait: int = DEFAULT_BUNDLEWAIT,
               address: Tuple[str, int] = (DEFAULT_HOST, DEFAULT_PORT),
               auth: str = DEFAULT_AUTH,
               template: str = DEFAULT_TEMPLATE,
               redirect_output: IO = None,
               redirect_errors: IO = None,
               capture: bool = False,
               monitor: bool = False,
               cores: int = None,
               memory: int = None,
               heartrate: int = DEFAULT_HEARTRATE,
               delay_start: float = DEFAULT_DELAY,
               no_confirm: bool = False,
               client_timeout: int = None,
               task_timeout: int = None,
               task_signalwait: int = DEFAULT_SIGNALWAIT,
               ratelimit: int = None) -> None:
    """
    Run client until disconnect signal received or `client_timeout` reached.

    Args:
        num_threads (int, optional):
            Number of executor threads (use 0 for auto-detection based on cores).
            See :const:`DEFAULT_NUM_THREADS`.

        bundlesize (int optional):
            Size of task bundles returned to server.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int optional):
            Waiting period in seconds before forcing return of task bundle to server.
            See :const:`DEFAULT_BUNDLEWAIT`.

        address (tuple, optional):
            Server host address for server with port number.
            See :const:`DEFAULT_HOST` and :const:`DEFAULT_PORT`.

        auth (str, optional):
            Server authentication key.
            See :const:`DEFAULT_AUTH`.

        template (str, optional):
            Template command pattern. See :const:`DEFAULT_TEMPLATE`.

        redirect_output (IO, optional):
            Optional file-like object for <stdout> redirect.

        redirect_errors (IO, optional):
            Optional file-like object for <stderr> redirect.

        heartrate (int, optional):
            Period in seconds to wait between heartbeats.
            See :const:`DEFAULT_HEARTRATE`,

        capture (bool, optional):
            Isolate task <stdout> and <stderr> in discrete files (default: False).

        monitor (bool, optional):
            Track CPU cores and memory usage of each task (default: False).

        cores (int, optional):
            Set core limit for client (default: all available cores).

        memory (int, optional):
            Set memory limit for client (default: all available memory).

        delay_start (float, optional):
            Delay in seconds before connecting to server.
            See :const:`DEFAULT_DELAY`.

        no_confirm (bool, optional):
            Disable client confirmation of tasks received.

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds.
            By default, the client waits indefinitely on tasks.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`DEFAULT_SIGNALWAIT`.

        ratelimit (int, optional):
            Maximum allowed tasks per second (default: none).
            There is no limit on task throughput unless specified.

    Example:
        >>> from hypershell.client import run_client
        >>> run_client(num_threads=16, address=('localhost', 54321),
        ...            auth='my-secret-key', capture=True)

    See Also:
        - :meth:`ClientThread`
    """
    thread = ClientThread.new(num_threads=num_threads,
                              bundlesize=bundlesize,
                              bundlewait=bundlewait,
                              address=address,
                              auth=auth,
                              template=template,
                              capture=capture,
                              monitor=monitor,
                              cores=cores,
                              memory=memory,
                              redirect_output=redirect_output,
                              redirect_errors=redirect_errors,
                              heartrate=heartrate,
                              delay_start=delay_start,
                              no_confirm=no_confirm,
                              client_timeout=client_timeout,
                              task_timeout=task_timeout,
                              task_signalwait=task_signalwait,
                              ratelimit=ratelimit)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


APP_NAME = 'hs client'
APP_USAGE = f"""\
Usage:
  hs client [-h] [-H ADDR] [-p PORT] [-k KEY] [-N THREADS] [-C CORES] [-M MEMORY]   
            [--template CMD] [-d DELAY] [-T TIMEOUT] [-W TIMEOUT] [-b SIZE] [-S WAIT] [-w WAIT]
            [--capture | [-o PATH] [-e PATH]] [--no-confirm] [--monitor]

  Launch client directly, run tasks in parallel.\
"""

APP_HELP = f"""\
{APP_USAGE}

  Tasks are pulled from server in bundles and run locally within the client environment. 
  By default the bundle size is one, meaning that at small scales there is greater responsiveness. 
  It is recommended to coordinate these parameters with the server.
  
  By default the client uses all available cores and memory on the server.
  More than one client can run per server by limiting resources allocated to them.
  The number of executor threads (-N) represents parallel execution slots.
  Use -N0 for auto-detection (sets threads equal to available cores).
  With resource management, actual parallelism depends on task requirements.
  
Options:
  -N, --num-threads   NUM   Number executor threads, 0=auto (default: {DEFAULT_NUM_THREADS}).
  -t, --template      CMD   Command-line template pattern (default: "{DEFAULT_TEMPLATE}").
  -b, --bundlesize    SIZE  Bundle size for finished tasks (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait    SEC   Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
  -H, --host          ADDR  Hostname for server (default: {DEFAULT_HOST}).
  -p, --port          NUM   Port number for server (default: {DEFAULT_PORT}).
  -k, --auth          KEY   Cryptographic key to connect to server.
  -d, --delay-start   SEC   Seconds to wait before start-up (default: {DEFAULT_DELAY}).
      --no-confirm          Disable confirmation of task bundle received.
  -o, --output        PATH  Redirect task output (default: <stdout>).
  -e, --errors        PATH  Redirect task errors (default: <stderr>).
      --capture             Capture individual task <stdout> and <stderr>.
      --monitor             Capture cores and memory used by tasks.
  -C, --client-cores  NUM   Limit available cores for client (default: all cores).
  -M, --client-memory MEM   Limit available memory for client (default: all memory).
  -T, --timeout       SEC   Automatically shutdown if no tasks received (default: never).
  -W, --task-timeout  SEC   Task-level walltime limit (default: none).
  -R, --ratelimit     NUM   Maximum allowed tasks per second (default: none).
  -S, --signalwait    SEC   Task-level signal escalation wait period (default: {DEFAULT_SIGNALWAIT}).
  -h, --help                Show this message and exit.\
"""


class ClientApp(Application):
    """Run individual client directly."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    num_threads: int = DEFAULT_NUM_THREADS
    interface.add_argument('-N', '--num-threads', '--num-tasks', type=int, default=num_threads, dest='num_threads')
    # NOTE: --num-tasks maintained for backwards compatibility

    host: str = config.server.bind
    interface.add_argument('-H', '--host', default=host)

    port: int = config.server.port
    interface.add_argument('-p', '--port', type=int, default=port)

    auth: str = config.server.auth
    interface.add_argument('-k', '--auth', default=auth)

    template: str = DEFAULT_TEMPLATE
    interface.add_argument('-t', '--template', default=template)

    bundlesize: int = config.submit.bundlesize
    interface.add_argument('-b', '--bundlesize', type=int, default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    interface.add_argument('-w', '--bundlewait', type=int, default=bundlewait)

    delay_start: float = DEFAULT_DELAY
    interface.add_argument('-d', '--delay-start', type=float, default=delay_start)

    task_timeout: Optional[int] = config.task.timeout or None
    client_timeout: Optional[int] = config.client.timeout or None
    ratelimit: Optional[int] = config.client.ratelimit or None
    interface.add_argument('-T', '--timeout', type=int, default=client_timeout, dest='client_timeout')
    interface.add_argument('-W', '--task-timeout', type=int, default=task_timeout, dest='task_timeout')
    interface.add_argument('-R', '--ratelimit', type=int, default=ratelimit)

    task_signalwait: int = config.task.signalwait
    interface.add_argument('-S', '--signalwait', type=int, default=task_signalwait, dest='task_signalwait')

    no_confirm: bool = False
    interface.add_argument('--no-confirm', action='store_true')

    output_path: str = None
    errors_path: str = None
    interface.add_argument('-o', '--output', default=None, dest='output_path')
    interface.add_argument('-e', '--errors', default=None, dest='errors_path')

    capture: bool = False
    monitor: bool = False
    interface.add_argument('--capture', action='store_true')
    interface.add_argument('--monitor', action='store_true')

    client_cores: Optional[int] = config.client.cores or None
    client_memory: Optional[int] = config.client.memory or None
    interface.add_argument('-C', '--client-cores', type=int, default=client_cores, dest='client_cores')
    interface.add_argument('-M', '--client-memory', type=int, default=client_memory, dest='client_memory')

    # Hidden options used as helpers for shell completion
    interface.add_argument('--available-cores', action='version', version=str(cpu_count()))
    interface.add_argument('--available-ssh-groups', action='version', version='\n'.join(SSH_GROUPS))

    exceptions = {
        EOFError: functools.partial(handle_disconnect, logger=log),
        ConnectionResetError: functools.partial(handle_disconnect, logger=log),
        ConnectionRefusedError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        AuthenticationError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        HostAddressInfo: functools.partial(handle_address_unknown, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__),
    }

    def run(self: ClientApp) -> None:
        """Run client."""
        try:
            self.check_args()
            run_client(num_threads=self.num_threads,
                       bundlesize=self.bundlesize,
                       bundlewait=self.bundlewait,
                       address=(self.host, self.port),
                       auth=self.auth,
                       template=self.template,
                       redirect_output=self.output_stream,
                       redirect_errors=self.errors_stream,
                       capture=self.capture,
                       monitor=self.monitor,
                       cores=self.client_cores,
                       memory=self.client_memory,
                       delay_start=self.delay_start,
                       no_confirm=self.no_confirm,
                       heartrate=config.client.heartrate,
                       client_timeout=self.client_timeout,
                       task_timeout=self.task_timeout,
                       task_signalwait=self.task_signalwait,
                       ratelimit=self.ratelimit)
        except gaierror:
            raise HostAddressInfo(f'Could not resolve host \'{self.host}\'')

    def check_args(self: ClientApp) -> None:
        """Check for logical errors in command-line arguments."""
        if self.capture and (self.output_path or self.errors_path):
            raise ArgumentError('Cannot specify --capture with either --output or --errors')
        if self.client_timeout is not None and self.client_timeout <= 0:
            raise ArgumentError('Client --timeout should be positive integer')
        if self.task_timeout is not None and self.task_timeout <= 0:
            raise ArgumentError('Client --task-timeout should be positive integer')

    @functools.cached_property
    def output_stream(self: ClientApp) -> IO:
        """IO stream for task outputs."""
        return sys.stdout if not self.output_path else open(self.output_path, mode='w')

    @functools.cached_property
    def errors_stream(self: ClientApp) -> IO:
        """IO stream for task errors."""
        return sys.stderr if not self.errors_path else open(self.errors_path, mode='w')

    def __exit__(self: ClientApp,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Close IO streams if necessary."""
        if self.output_stream is not sys.stdout:
            self.output_stream.close()
        if self.errors_stream is not sys.stderr:
            self.errors_stream.close()
