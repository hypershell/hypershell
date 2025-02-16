# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Remote cluster implementation."""


# Type annotations
from __future__ import annotations
from typing import Tuple, List, Dict, IO, Iterable, Callable, Type, Final

# Standard libs
import os
import sys
import time
import shlex
import secrets
from enum import Enum
from datetime import datetime, timedelta
from functools import cached_property
from subprocess import Popen

# Internal libs
from hypershell.core.fsm import State, StateMachine
from hypershell.core.config import default, load_task_env
from hypershell.core.thread import Thread
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.data.model import Task, Client
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.server import ServerThread, DEFAULT_PORT, DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS

# Public interface
__all__ = ['run_cluster', 'RemoteCluster', 'AutoScalingCluster',
           'DEFAULT_REMOTE_EXE', 'DEFAULT_LAUNCHER',
           'DEFAULT_AUTOSCALE_POLICY', 'DEFAULT_AUTOSCALE_PERIOD', 'DEFAULT_AUTOSCALE_FACTOR',
           'DEFAULT_AUTOSCALE_INIT_SIZE', 'DEFAULT_AUTOSCALE_MIN_SIZE', 'DEFAULT_AUTOSCALE_MAX_SIZE',
           'DEFAULT_AUTOSCALE_LAUNCHER', ]

# Initialize logger
log = Logger.with_name('hypershell.cluster')


# NOTE: retain old name for remote executable (for now)
DEFAULT_REMOTE_EXE: Final[str] = 'hyper-shell'
"""Default remote executable name."""

DEFAULT_LAUNCHER: Final[str] = 'mpirun'
"""Default launcher program."""


def run_cluster(autoscaling: bool = False, **options) -> None:
    """
    Run cluster with remote clients until completion.

    All function arguments are forwarded directly into either the
    :class:`~hypershell.cluster.remote.RemoteCluster` or
    :class:`~hypershell.cluster.remote.AutoScalingCluster` thread.

    If `autoscaling` is enabled then we use the
    :class:`~hypershell.cluster.remote.AutoScalingCluster` instead
    of the :class:`~hypershell.cluster.remote.RemoteCluster`.

    Example:
        >>> from hypershell.cluster import run_cluster
        >>> run_cluster(
        ...     restart_mode=True, launcher='srun', max_retries=2, eager=True,
        ...     client_timeout=600, task_timeout=300, capture=True
        ... )

    See Also:
        - :class:`~hypershell.cluster.remote.RemoteCluster`
        - :class:`~hypershell.cluster.remote.AutoScalingCluster`
    """
    if autoscaling:
        thread = AutoScalingCluster.new(**options)
    else:
        thread = RemoteCluster.new(**options)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


class RemoteCluster(Thread):
    """
    Run server with remote clients via external launcher (e.g., `mpirun`).

    Args:
        source (Iterable[str], optional):
            Any iterable of command-line tasks.
            A new `source` results in a :class:`~hypershell.submit.SubmitThread` populating
            either the database or the queue directly depending on `in_memory`.

        num_tasks (int, optional):
            Number of parallel task executor threads.
            See :const:`~hypershell.client.DEFAULT_NUM_TASKS`.

        template (str, optional):
            Template command pattern.
            See :const:`~hypershell.client.DEFAULT_TEMPLATE`.

        bundlesize (int optional):
            Size of task bundles returned to server.
            See :const:`~hypershell.server.DEFAULT_BUNDLESIZE`.

        bundlewait (int optional):
            Waiting period in seconds before forcing return of task bundle to server.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

        bind (tuple, optional):
            Bind address for server with port number (default: 0.0.0.0).
            See :const:`~hypershell.server.DEFAULT_PORT`.

        launcher (str, optional):
            Launcher program used to bring up clients on other hosts.
            See :const:`~hypershell.cluster.remote.DEFAULT_LAUNCHER`.

        launcher_args (List[str], optional):
            Additional command-line arguments for launcher program.

        remote_exe (str, optional):
            Program name or path for remote executable.
            See :const:`~hypershell.cluster.remote.DEFAULT_REMOTE_EXE`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            Disable client confirmation of tasks received.

        forever_mode (bool, optional):
            Regardless of `source`, if enabled schedule forever.
            Conflicts with `restart_mode` and `in_memory`. Default is `False`.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        max_retries (int, optional):
            Number of allowed task retries.
            See :const:`~hypershell.server.DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`~hypershell.server.DEFAULT_EAGER_MODE`.

        redirect_failures (IO, optional):
            Open file-like object to write failed tasks.

        delay_start (float, optional):
            Delay in seconds before connecting to server.
            See :const:`~hypershell.client.DEFAULT_DELAY`.

        capture (bool, optional):
            Isolate task <stdout> and <stderr> in discrete files.
            Defaults to `False`.

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds.
            By default, the client waits indefinitely on tasks.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`~hypershell.client.DEFAULT_SIGNALWAIT`.

    Example:
        >>> from hypershell.cluster import RemoteCluster
        >>> cluster = RemoteCluster.new(
        ...     restart_mode=True, launcher='srun', max_retries=2, eager=True,
        ...     client_timeout=600, task_timeout=300, capture=True
        ... )
        >>> cluster.join()

    See Also:
        - :class:`~hypershell.server.ServerThread`
        - :meth:`~hypershell.cluster.remote.run_cluster`
    """

    server: ServerThread
    clients: Popen
    client_argv: List[str]

    def __init__(self: RemoteCluster,
                 source: Iterable[str] = None,
                 num_tasks: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 bind: Tuple[str, int] = ('0.0.0.0', DEFAULT_PORT),
                 launcher: str = DEFAULT_LAUNCHER,
                 launcher_args: List[str] = None,
                 remote_exe: str = DEFAULT_REMOTE_EXE,
                 in_memory: bool = False,
                 no_confirm: bool = False,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 max_retries: int = DEFAULT_ATTEMPTS,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 delay_start: float = DEFAULT_DELAY,
                 capture: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT) -> None:
        """Initialize server and client threads with external launcher."""
        auth = secrets.token_hex(64)
        self.server = ServerThread(source=source,
                                   bundlesize=bundlesize,
                                   bundlewait=bundlewait,
                                   in_memory=in_memory,
                                   no_confirm=no_confirm,
                                   address=bind,
                                   auth=auth,
                                   max_retries=max_retries,
                                   eager=eager,
                                   forever_mode=forever_mode,
                                   restart_mode=restart_mode,
                                   redirect_failures=redirect_failures)
        launcher = shlex.split(launcher)
        if launcher_args is None:
            launcher_args = []
        else:
            launcher_args = [arg for arg_group in launcher_args for arg in shlex.split(arg_group)]
        client_args = []
        if capture is True:
            client_args.append('--capture')
        if no_confirm is True:
            client_args.append('--no-confirm')
        if client_timeout is not None:
            client_args.extend(['-T', str(client_timeout)])
        if task_timeout is not None:
            client_args.extend(['-W', str(task_timeout)])
        self.client_argv = [
            *launcher, *launcher_args, remote_exe, 'client',
            '-H', HOSTNAME, '-p', str(bind[1]), '-N', str(num_tasks), '-b', str(bundlesize), '-w', str(bundlewait),
            '-t', template, '-k', auth, '-d', str(delay_start), '-S', str(task_signalwait), *client_args
        ]
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: RemoteCluster) -> None:
        """Start child threads, wait."""
        self.server.start()
        while not self.server.queue.ready:
            time.sleep(0.1)
        log.debug(f'Launching clients: {self.client_argv}')
        self.clients = Popen(self.client_argv,
                             stdout=sys.stdout, stderr=sys.stderr,
                             env={**os.environ, **load_task_env()})
        self.clients.wait()
        self.server.join()

    def stop(self: RemoteCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        self.clients.terminate()
        super().stop(wait=wait, timeout=timeout)


DEFAULT_AUTOSCALE_POLICY: Final[str] = default.autoscale.policy
"""Default autoscaling policy."""

DEFAULT_AUTOSCALE_FACTOR: Final[float] = default.autoscale.factor
"""Default scaling factor."""

DEFAULT_AUTOSCALE_PERIOD: Final[int] = default.autoscale.period
"""Default period in seconds between autoscaling events."""

DEFAULT_AUTOSCALE_INIT_SIZE: Final[int] = default.autoscale.size.init
"""Default initial size of cluster (number of clients)."""

DEFAULT_AUTOSCALE_MIN_SIZE: Final[int] = default.autoscale.size.min
"""Default minimum size of cluster (number of clients)."""

DEFAULT_AUTOSCALE_MAX_SIZE: Final[int] = default.autoscale.size.max
"""Default maximum size of cluster (number of clients)."""

DEFAULT_AUTOSCALE_LAUNCHER: Final[str] = default.autoscale.launcher
"""Default launcher program for scaling clients."""


class AutoScalerState(State, Enum):
    """Finite states for AutoScaler."""
    START = 0
    INIT = 1
    WAIT = 2
    CHECK = 3
    CHECK_FIXED = 4
    CHECK_DYNAMIC = 5
    SCALE = 6
    CLEAN = 7
    FINAL = 8
    HALT = 9


class AutoScalerPolicy(Enum):
    """Allowed scaling policies."""
    FIXED = 1
    DYNAMIC = 2

    @classmethod
    def from_name(cls: Type[AutoScalerPolicy], name: str) -> AutoScalerPolicy:
        """Return decided enum type from name."""
        try:
            return cls[name.upper()]
        except KeyError:
            raise RuntimeError(f'Unknown {cls.__name__} \'{name}\'')


class AutoScalerPhase(Enum):
    """Launch phase."""
    INIT = 1
    STEADY = 2
    STOP = 3


class AutoScaler(StateMachine):
    """Monitor task pressure and scale accordingly."""

    policy: AutoScalerPolicy
    factor: float
    period: int
    init_size: int
    min_size: int
    max_size: int
    launcher: List[str]

    clients: List[Popen]
    last_check: datetime
    wait_check: timedelta

    phase: AutoScalerPhase = AutoScalerPhase.INIT
    state: AutoScalerState = AutoScalerState.START
    states: Type[State] = AutoScalerState

    def __init__(self: AutoScaler,
                 launcher: List[str],
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 ) -> None:
        """Initialize with scaling parameters."""
        self.policy = AutoScalerPolicy.from_name(policy)
        self.factor = factor
        self.period = period
        self.init_size = init_size
        self.min_size = min_size
        self.max_size = max_size
        self.launcher = launcher
        self.last_check = datetime.now()
        self.wait_check = timedelta(seconds=period)
        self.clients = []

    @cached_property
    def actions(self: AutoScaler) -> Dict[AutoScalerState, Callable[[], AutoScalerState]]:
        return {
            AutoScalerState.START: self.start,
            AutoScalerState.INIT: self.init,
            AutoScalerState.WAIT: self.wait,
            AutoScalerState.CHECK: self.check,
            AutoScalerState.CHECK_FIXED: self.check_fixed,
            AutoScalerState.CHECK_DYNAMIC: self.check_dynamic,
            AutoScalerState.SCALE: self.scale,
            AutoScalerState.CLEAN: self.clean,
            AutoScalerState.FINAL: self.finalize,
        }

    def start(self: AutoScaler) -> AutoScalerState:
        """Jump to INIT state."""
        log.info(f'Autoscale start (policy: {self.policy.name.lower()}, init-size: {self.init_size})')
        log.debug(f'Autoscale launcher: {self.launcher}')
        return AutoScalerState.INIT

    def init(self: AutoScaler) -> AutoScalerState:
        """Launch initial clients."""
        if len(self.clients) < self.init_size:
            return AutoScalerState.SCALE
        else:
            self.phase = AutoScalerPhase.STEADY
            return AutoScalerState.WAIT

    def wait(self: AutoScaler) -> AutoScalerState:
        """Wait for specified period of time."""
        if self.phase is AutoScalerPhase.STEADY:
            waited = datetime.now() - self.last_check
            if waited > self.wait_check:
                return AutoScalerState.CHECK
            else:
                log.trace(f'Autoscale wait ({timedelta(seconds=round(waited.total_seconds()))})')
                time.sleep(1)
                return AutoScalerState.WAIT
        else:
            return AutoScalerState.FINAL

    def check(self: AutoScaler) -> AutoScalerState:
        """Check if we need to scale up."""
        self.clean()
        self.last_check = datetime.now()
        if self.policy is AutoScalerPolicy.FIXED:
            return AutoScalerState.CHECK_FIXED
        else:
            return AutoScalerState.CHECK_DYNAMIC

    def check_fixed(self: AutoScaler) -> AutoScalerState:
        """Scaling procedure for a fixed policy cluster."""
        launched_size = len(self.clients)
        registered_size = Client.count_connected()
        task_count = Task.count_remaining()
        log.debug(f'Autoscale check (clients: {registered_size}/{launched_size}, tasks: {task_count})')
        if launched_size < self.min_size:
            log.debug(f'Autoscale min-size reached ({launched_size} < {self.min_size})')
            return AutoScalerState.SCALE
        if launched_size == 0 and task_count == 0:
            return AutoScalerState.WAIT
        if launched_size == 0 and task_count > 0:
            log.debug(f'Autoscale adding client ({task_count} tasks remaining)')
            return AutoScalerState.SCALE
        else:
            return AutoScalerState.WAIT

    def check_dynamic(self: AutoScaler) -> AutoScalerState:
        """Scaling procedure for a dynamic policy cluster."""
        launched_size = len(self.clients)
        registered_size = Client.count_connected()
        task_count = Task.count_remaining()
        pressure = Task.task_pressure(self.factor)
        pressure_val = 'unknown' if pressure is None else f'{pressure:.2f}'
        log.debug(f'Autoscale check (pressure: {pressure_val}, '
                  f'clients: {registered_size}/{launched_size}, tasks: {task_count})')
        if launched_size < self.min_size:
            log.debug(f'Autoscale min-size reached ({launched_size} < {self.min_size})')
            return AutoScalerState.SCALE
        if pressure is not None:
            if pressure > 1:
                log.debug(f'Autoscale pressure high ({pressure:.2f})')
                if launched_size >= self.max_size:
                    log.debug(f'Autoscale max-size reached ({launched_size} >= {self.max_size})')
                    return AutoScalerState.WAIT
                else:
                    return AutoScalerState.SCALE
            else:
                log.debug(f'Autoscale pressure low ({pressure:.2f})')
                return AutoScalerState.WAIT
        else:
            if launched_size == 0 and task_count == 0:
                log.debug(f'Autoscale pause (no clients and no tasks)')
                return AutoScalerState.WAIT
            if launched_size == 0 and task_count > 0:
                log.debug(f'Autoscale adding client ({task_count} tasks remaining)')
                return AutoScalerState.SCALE
            else:
                log.debug('Autoscale pause (waiting on clients to complete initial tasks)')
                return AutoScalerState.WAIT

    def scale(self: AutoScaler) -> AutoScalerState:
        """Launch new client."""
        proc = Popen(self.launcher, stdout=sys.stdout, stderr=sys.stderr,
                     bufsize=0, universal_newlines=True, env={**os.environ, **load_task_env()})
        log.trace(f'Autoscale adding client ({proc.pid})')
        self.clients.append(proc)
        if self.phase is AutoScalerPhase.INIT:
            return AutoScalerState.INIT
        else:
            return AutoScalerState.WAIT

    def clean(self: AutoScaler) -> None:
        """Remove clients that have exited."""
        marked = []
        for i, client in enumerate(self.clients):
            log.trace(f'Autoscale clean ({i+1}: {client.pid})')
            status = client.poll()
            if status is not None:
                marked.append(i)
                if status != 0:
                    log.warning(f'Autoscale client ({i+1}) exited with status {status}')
                else:
                    log.debug(f'Autoscale client disconnected ({client.pid})')
        self.clients = [client for i, client in enumerate(self.clients) if i not in marked]

    @staticmethod
    def finalize() -> AutoScalerState:
        """Finalize."""
        log.debug(f'Done (autoscaler)')
        return AutoScalerState.HALT


class AutoScalerThread(Thread):
    """Run AutoScaler within dedicated thread."""

    def __init__(self: AutoScalerThread,
                 launcher: List[str],
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 ) -> None:
        """Initialize task executor."""
        super().__init__(name=f'hypershell-autoscaler')
        self.machine = AutoScaler(launcher, policy=policy, factor=factor, period=period,
                                  init_size=init_size, min_size=min_size, max_size=max_size)

    def run_with_exceptions(self: AutoScalerThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: AutoScalerThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning(f'Stopping (autoscaler)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class AutoScalingCluster(Thread):
    """
    Run cluster with autoscaling remote clients via external launcher.

    Args:
        source (Iterable[str], optional):
            Any iterable of command-line tasks.
            A new `source` results in a :class:`~hypershell.submit.SubmitThread` populating
            either the database or the queue directly depending on `in_memory`.

        num_tasks (int, optional):
            Number of parallel task executor threads.
            See :const:`~hypershell.client.DEFAULT_NUM_TASKS`.

        template (str, optional):
            Template command pattern.
            See :const:`~hypershell.client.DEFAULT_TEMPLATE`.

        bundlesize (int optional):
            Size of task bundles returned to server.
            See :const:`~hypershell.server.DEFAULT_BUNDLESIZE`.

        bundlewait (int optional):
            Waiting period in seconds before forcing return of task bundle to server.
            See :const:`~hypershell.submit.DEFAULT_BUNDLEWAIT`.

        bind (tuple, optional):
            Bind address for server with port number (default: 0.0.0.0).
            See :const:`~hypershell.server.DEFAULT_PORT`.

        launcher (str, optional):
            Launcher program used to bring up clients on other hosts.
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_LAUNCHER`.

        launcher_args (List[str], optional):
            Additional command-line arguments for launcher program.

        remote_exe (str, optional):
            Program name or path for remote executable.
            See :const:`~hypershell.cluster.remote.DEFAULT_REMOTE_EXE`.

        in_memory (bool, optional):
            If True, revert to basic in-memory queue.

        no_confirm (bool, optional):
            Disable client confirmation of tasks received.

        forever_mode (bool, optional):
            Regardless of `source`, if enabled schedule forever.
            Conflicts with `restart_mode` and `in_memory`. Default is `False`.

        restart_mode (bool, optional):
            If `source` is empty, this option allows for the server to continue
            with scheduling from the database until complete.
            Conflicts with `in_memory`. Default is `False`.

        max_retries (int, optional):
            Number of allowed task retries.
            See :const:`~hypershell.server.DEFAULT_ATTEMPTS`.

        eager (bool, optional):
            When enabled tasks are retried immediately ahead scheduling new tasks.
            See :const:`~hypershell.server.DEFAULT_EAGER_MODE`.

        redirect_failures (IO, optional):
            Open file-like object to write failed tasks.

        delay_start (float, optional):
            Delay in seconds before connecting to server.
            See :const:`~hypershell.client.DEFAULT_DELAY`.

        capture (bool, optional):
            Isolate task <stdout> and <stderr> in discrete files.
            Defaults to `False`.

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds.
            By default, the client waits indefinitely on tasks.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`~hypershell.client.DEFAULT_SIGNALWAIT`.

        policy (str, optional):
            Autoscaling policy (either 'fixed' or 'dynamic').
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_POLICY`
        
        period (int, optional):
            Period in seconds between autoscaling events.    
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_PERIOD`
        
        factor (float, optional):
            Autoscaling factor.
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_FACTOR`
        
        init_size (int, optional):
            Initial size of cluster (number of clients).
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_INIT_SIZE`

        min_size (int, optional):
            Minimum size of cluster (number of clients).
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_MIN_SIZE`

        max_size (int, optional):
            Maximum size of cluster (number of clients).
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_MAX_SIZE`

    Example:
        >>> from hypershell.cluster import AutoScalingCluster
        >>> cluster = AutoScalingCluster.new(
        ...     restart_mode=True, max_retries=2, eager=True,
        ...     client_timeout=600, task_timeout=300, capture=True,
        ...     launcher='srun -Q -A standby -t 01:00:00 --exclusive --signal=USR1@600'
        ... )
        >>> cluster.join()

    See Also:
        - :class:`~hypershell.server.ServerThread`
        - :meth:`~hypershell.cluster.remote.run_cluster`
    """

    server: ServerThread
    clients: Dict[str, Popen]
    launch_argv: str

    def __init__(self: AutoScalingCluster,
                 source: Iterable[str] = None,
                 num_tasks: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 bind: Tuple[str, int] = ('0.0.0.0', DEFAULT_PORT),
                 launcher: str = DEFAULT_AUTOSCALE_LAUNCHER,
                 launcher_args: List[str] = None,
                 remote_exe: str = DEFAULT_REMOTE_EXE,
                 in_memory: bool = False,  # noqa: ignored (passed by ClusterApp)
                 no_confirm: bool = False,  # noqa: ignored (passed by ClusterApp)
                 forever_mode: bool = False,  # noqa: ignored (passed by ClusterApp)
                 restart_mode: bool = False,  # noqa: ignored (passed by ClusterApp)
                 max_retries: int = DEFAULT_ATTEMPTS,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 delay_start: float = DEFAULT_DELAY,
                 capture: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT,
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 ) -> None:
        """Initialize server and autoscaler."""
        auth = secrets.token_hex(64)
        self.server = ServerThread(source=source, auth=auth, bundlesize=bundlesize, bundlewait=bundlewait,
                                   max_retries=max_retries, eager=eager, address=bind, forever_mode=True,
                                   redirect_failures=redirect_failures)
        launcher = shlex.split(launcher)
        if launcher_args is None:
            launcher_args = []
        else:
            launcher_args = [arg for arg_group in launcher_args for arg in shlex.split(arg_group)]
        client_args = []
        if capture:
            client_args.append('--capture')
        if client_timeout is not None:
            client_args.extend(['-T', str(client_timeout)])
        if task_timeout is not None:
            client_args.extend(['-W', str(task_timeout)])
        launcher.extend([
            *launcher_args, remote_exe, 'client',
            '-H', HOSTNAME, '-p', str(bind[1]), '-N', str(num_tasks), '-b', str(bundlesize), '-w', str(bundlewait),
            '-t', template, '-k', auth, '-d', str(delay_start), '-S', str(task_signalwait), *client_args
        ])
        self.autoscaler = AutoScalerThread(launcher, policy=policy, factor=factor, period=period,
                                           init_size=init_size, min_size=min_size, max_size=max_size)
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: AutoScalingCluster) -> None:
        """Start child threads, wait."""
        self.server.start()
        while not self.server.queue.ready:
            time.sleep(0.1)
        self.autoscaler.start()
        self.autoscaler.join()
        self.server.join()

    def stop(self: AutoScalingCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        self.autoscaler.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)
