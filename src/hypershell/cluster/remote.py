# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Remote cluster implementation."""


# Type annotations
from __future__ import annotations
from typing import Tuple, List, Dict, IO, Iterable, Callable, Type, Optional, Final

# Standard libs
import sys
import time
import shlex
import secrets
from enum import Enum
from datetime import datetime, timedelta
from functools import cached_property, partial
from subprocess import Popen

# Internal libs
from hypershell.core.fsm import State, StateMachine
from hypershell.core.tls import TLSConfig
from hypershell.core.config import default
from hypershell.core.thread import Thread
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.data.model import Task, Client
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.server import (ServerThread, DEFAULT_PORT, DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS, DEFAULT_SERVER_POLL,
                               DEFAULT_EVICT)
from hypershell.cluster.launch import ClientLaunch, LaunchActuator, client_launch, redact_secrets
from hypershell.cluster.demand import (AutoScalerPolicy, ZoneRef, ZoneLoad, ScalingKnobs,
                                       read_zone_loads, fleet_demand)

# Public interface
__all__ = ['run_cluster', 'RemoteCluster', 'AutoScalingCluster',
           'DEFAULT_REMOTE_EXE', 'DEFAULT_LAUNCHER',
           'DEFAULT_AUTOSCALE_POLICY', 'DEFAULT_AUTOSCALE_PERIOD', 'DEFAULT_AUTOSCALE_FACTOR',
           'DEFAULT_AUTOSCALE_INIT_SIZE', 'DEFAULT_AUTOSCALE_MIN_SIZE', 'DEFAULT_AUTOSCALE_MAX_SIZE',
           'DEFAULT_AUTOSCALE_LAUNCHER', 'AutoScalerPolicy',
           'redact_secrets']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


# NOTE: retain old name for remote executable (for now)
DEFAULT_REMOTE_EXE: Final[str] = 'hyper-shell'
"""Default remote executable name."""

DEFAULT_LAUNCHER: Final[str] = 'mpirun'
"""Default launcher program."""


def run_cluster(*args, autoscaling: bool = False, **kwargs) -> None:
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
        thread = AutoScalingCluster.new(*args, **kwargs)
    else:
        thread = RemoteCluster.new(*args, **kwargs)
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

        num_threads (int, optional):
            Number of executor threads (use 0 for auto-detection).
            See :const:`~hypershell.client.DEFAULT_NUM_THREADS`.

        template (str, optional):
            Template command pattern.
            See :const:`~hypershell.client.DEFAULT_TEMPLATE`.

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        client_cores (int, optional):
            Set core limit for clients (default: all available cores).

        client_memory (int, optional):
            Set memory limit for clients (default: all available memory).

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

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

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
            Isolate task <stdout> and <stderr> in discrete files (default: False).

        monitor (bool, optional):
            Track CPU cores and memory usage of each task (default: False).

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds (default: none).
            May be overridden by inline-comment.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`~hypershell.client.DEFAULT_SIGNALWAIT`.

        ratelimit (int, optional):
            Maximum allowed tasks per second per client (default: none).
            There is no limit on task throughput unless specified.

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

        zone (str, optional):
            Zone whose tasks the clients run (default: the default zone).
            Tasks this cluster submits are placed in the same zone.

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
    clients: Optional[Popen] = None
    launch: ClientLaunch

    def __init__(self: RemoteCluster,
                 source: Iterable[str] = None,
                 num_threads: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 cores: int = None,
                 memory: int = None,
                 client_cores: int = None,
                 client_memory: int = None,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 bind: Tuple[str, int] = ('0.0.0.0', DEFAULT_PORT),
                 launcher: str = DEFAULT_LAUNCHER,
                 launcher_args: List[str] = None,
                 remote_exe: str = DEFAULT_REMOTE_EXE,
                 in_memory: bool = False,
                 no_confirm: bool = False,
                 poll: int = DEFAULT_SERVER_POLL,
                 forever_mode: bool = False,
                 restart_mode: bool = False,
                 max_retries: int = DEFAULT_ATTEMPTS,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 delay_start: float = DEFAULT_DELAY,
                 capture: bool = False,
                 monitor: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT,
                 ratelimit: int = None,
                 from_json: bool = False,
                 tls: Optional[TLSConfig] = None,
                 zone: Optional[str] = None) -> None:
        """Initialize server and client threads with external launcher."""
        auth = secrets.token_hex(64)
        # In JSON mode the template is expanded submit-side by the server; remote clients
        # run the fully-resolved commands verbatim (avoids double expansion).
        client_template = DEFAULT_TEMPLATE if from_json else template
        self.server = ServerThread(source=source,
                                   task_cores=cores,
                                   task_memory=memory,
                                   task_timeout=task_timeout,
                                   bundlesize=bundlesize,
                                   bundlewait=bundlewait,
                                   in_memory=in_memory,
                                   no_confirm=no_confirm,
                                   poll=poll,
                                   address=bind,
                                   auth=auth,
                                   max_retries=max_retries,
                                   eager=eager,
                                   forever_mode=forever_mode,
                                   restart_mode=restart_mode,
                                   redirect_failures=redirect_failures,
                                   template=template,
                                   from_json=from_json,
                                   tls=tls)
        if launcher_args is None:
            launcher_args = []
        else:
            launcher_args = [arg for arg_group in launcher_args for arg in shlex.split(arg_group)]
        target = HOSTNAME if bind[0] == '0.0.0.0' else bind[0]
        self.launch = client_launch(remote_exe, (target, bind[1]), auth,
                                    launcher=shlex.split(launcher), launcher_args=launcher_args,
                                    num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                                    template=client_template, delay_start=delay_start,
                                    task_signalwait=task_signalwait, no_confirm=no_confirm,
                                    capture=capture, monitor=monitor,
                                    client_cores=client_cores, client_memory=client_memory,
                                    client_timeout=client_timeout, task_timeout=task_timeout,
                                    ratelimit=ratelimit, tls_enabled=tls is not None, zone=zone)
        super().__init__(name='hypershell-cluster')

    @property
    def client_argv(self: RemoteCluster) -> List[str]:
        """Command line that launches the clients (holds no key)."""
        return self.launch.argv

    def run_with_exceptions(self: RemoteCluster) -> None:
        """Start child threads, wait."""
        self.server.start()
        self.server.wait_scheduling()  # NOTE: clients never idle while a task file is read
        log.debug('Launching clients: ' + self.launch.describe())
        self.clients = self.launch.start(stdout=sys.stdout, stderr=sys.stderr)
        self.clients.wait()
        self.server.join()

    def stop(self: RemoteCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        if self.clients is not None:
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


class AutoScalerPhase(Enum):
    """Launch phase."""
    INIT = 1
    STEADY = 2
    STOP = 3


class AutoScaler(StateMachine):
    """
    Monitor task pressure and scale accordingly.

    Clients are launched through a :class:`~hypershell.cluster.launch.LaunchActuator`: `launch`
    starts the clients of the `own_zone` (the default zone if not given), and `launch_for`, when
    given, builds the launch of any other zone. With `launch_for`, each check also discovers every
    zone with work in the current task group and sizes it under the same knobs; without it, only
    the own zone is scaled. `attempts` and `eager` are the server's retry rule, under which failed
    tasks count as work. `registered`, when given, counts the clients a zone has registered, for
    logging; it is called with a zone id.
    """

    policy: AutoScalerPolicy
    factor: float
    period: int
    init_size: int
    min_size: int
    max_size: int
    own_zone: ZoneRef
    attempts: int
    eager: bool
    registered: Optional[Callable[[str], int]]

    actuator: LaunchActuator
    pending: Dict[ZoneRef, int]
    last_check: datetime
    wait_check: timedelta

    phase: AutoScalerPhase = AutoScalerPhase.INIT
    state: AutoScalerState = AutoScalerState.START
    states: Type[State] = AutoScalerState

    def __init__(self: AutoScaler,
                 launch: ClientLaunch,
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 own_zone: Optional[ZoneRef] = None,
                 launch_for: Optional[Callable[[ZoneRef], ClientLaunch]] = None,
                 attempts: int = 1,
                 eager: bool = False,
                 registered: Optional[Callable[[str], int]] = None,
                 ) -> None:
        """Initialize with scaling parameters."""
        self.policy = AutoScalerPolicy.from_name(policy)
        self.factor = factor
        self.period = period
        self.init_size = init_size
        self.min_size = min_size
        self.max_size = max_size
        self.own_zone = own_zone if own_zone is not None else ZoneRef.from_name(None)
        self.attempts = attempts
        self.eager = eager
        self.registered = registered
        self.actuator = LaunchActuator(launch, own=self.own_zone, launch_for=launch_for)
        self.last_check = datetime.now()
        self.wait_check = timedelta(seconds=period)
        self.pending = {}

    @property
    def launch(self: AutoScaler) -> ClientLaunch:
        """The launch of the own zone's clients."""
        return self.actuator.launch

    @property
    def clients(self: AutoScaler) -> List[Popen]:
        """Launched clients not yet exited, every zone together."""
        return self.actuator.clients

    @clients.setter
    def clients(self: AutoScaler, value: List[Popen]) -> None:
        """Replace the launched clients."""
        self.actuator.clients = value

    @property
    def knobs(self: AutoScaler) -> ScalingKnobs:
        """The sizing knobs, applied to each zone alike."""
        return ScalingKnobs(self.policy, self.factor, self.init_size, self.min_size, self.max_size)

    def launched(self: AutoScaler) -> Dict[str, int]:
        """Launched clients per zone id."""
        return self.actuator.launched()

    @property
    def discover(self: AutoScaler) -> bool:
        """Whether zones other than the own zone are discovered and scaled."""
        return self.actuator.launch_for is not None

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
        self.actuator.describe()
        return AutoScalerState.INIT

    def init(self: AutoScaler) -> AutoScalerState:
        """Launch initial clients."""
        launched = self.launched()
        desired = fleet_demand({}, launched, self.knobs, own=self.own_zone, initial=True)
        if any(count > launched.get(zone.id, 0) for zone, count in desired.items()):
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
        return self.check_zones()

    def check_dynamic(self: AutoScaler) -> AutoScalerState:
        """Scaling procedure for a dynamic policy cluster."""
        return self.check_zones()

    def check_zones(self: AutoScaler) -> AutoScalerState:
        """Decide the clients each zone needs under the policy, and scale if any zone needs one."""
        group = Task.current_group().value
        loads = read_zone_loads(group, self.knobs, own=self.own_zone, discover=self.discover,
                                attempts=self.attempts, eager=self.eager)
        launched = self.launched()
        desired = fleet_demand(loads, launched, self.knobs, own=self.own_zone, initial=False)
        connected = None if self.registered is not None else Client.count_connected()
        self.pending = {}
        for zone, count in desired.items():
            have = launched.get(zone.id, 0)
            count = min(count, have + 1)  # NOTE: at most one launch per zone per check
            load = loads.get(zone.id) or ZoneLoad(zone, remaining=0, retryable=0, pressure=None)
            registered = connected if self.registered is None else self.registered(zone.id)
            self.log_check(zone, load, group if load.group is None else load.group, have, registered, count)
            if count > have:
                self.pending[zone] = count
        return AutoScalerState.SCALE if self.pending else AutoScalerState.WAIT

    def log_check(self: AutoScaler, zone: ZoneRef, load: ZoneLoad, group: int,
                  launched: int, registered: int, desired: int) -> None:
        """Log one zone's reading and the decision taken for it."""
        pressure = '' if self.policy is AutoScalerPolicy.FIXED else (
            'pressure: unknown, ' if load.pressure is None else f'pressure: {load.pressure:.2f}, ')
        log.debug(f'Autoscale check (zone: {zone.name}, {pressure}clients: {registered}/{launched}, '
                  f'tasks: {load.work} in group {group})')
        if desired > launched:
            if zone == self.own_zone and launched < self.min_size:
                log.debug(f'Autoscale min-size reached (zone: {zone.name}, {launched} < {self.min_size})')
            else:
                log.debug(f'Autoscale adding client (zone: {zone.name}, {load.work} tasks remaining in group {group})')
        elif load.pressure is not None and load.pressure > 1:
            log.debug(f'Autoscale max-size reached (zone: {zone.name}, {launched} >= {self.max_size})')

    def scale(self: AutoScaler) -> AutoScalerState:
        """Launch the clients each zone needs, or one client of the own zone when none is pending."""
        if self.pending:
            self.actuator.apply(self.pending)
            self.pending = {}
        else:
            self.actuator.launch_one()
        if self.phase is AutoScalerPhase.INIT:
            return AutoScalerState.INIT
        else:
            return AutoScalerState.WAIT

    def clean(self: AutoScaler) -> None:
        """Remove clients that have exited."""
        self.actuator.clean()

    @staticmethod
    def finalize() -> AutoScalerState:
        """Finalize."""
        log.debug(f'Done (autoscaler)')
        return AutoScalerState.HALT


class AutoScalerThread(Thread):
    """Run AutoScaler within dedicated thread."""

    def __init__(self: AutoScalerThread,
                 launch: ClientLaunch,
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 own_zone: Optional[ZoneRef] = None,
                 launch_for: Optional[Callable[[ZoneRef], ClientLaunch]] = None,
                 attempts: int = 1,
                 eager: bool = False,
                 registered: Optional[Callable[[str], int]] = None,
                 ) -> None:
        """Initialize task executor."""
        super().__init__(name=f'hypershell-autoscaler')
        self.machine = AutoScaler(launch, policy=policy, factor=factor, period=period,
                                  init_size=init_size, min_size=min_size, max_size=max_size,
                                  own_zone=own_zone, launch_for=launch_for, attempts=attempts, eager=eager,
                                  registered=registered)

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

        num_threads (int, optional):
            Number of executor threads (use 0 for auto-detection).
            See :const:`~hypershell.client.DEFAULT_NUM_THREADS`.

        template (str, optional):
            Template command pattern.
            See :const:`~hypershell.client.DEFAULT_TEMPLATE`.

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        client_cores (int, optional):
            Set core limit for clients (default: all available cores).

        client_memory (int, optional):
            Set memory limit for clients (default: all available memory).

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
            With `discover_zones`, every launch names its zone to the launcher: `{zone}` in the
            launcher or its arguments is replaced by the zone's name (`default` for the default
            zone), and the launched process has it in `HYPERSHELL_ZONE`.

        launcher_args (List[str], optional):
            Additional command-line arguments for launcher program (`{zone}` is replaced as in `launcher`).

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
            Isolate task <stdout> and <stderr> in discrete files (default: False).

        monitor (bool, optional):
            Track CPU cores and memory usage of each task (default: False).

        client_timeout (int, optional):
            Timeout in seconds before disconnecting from server.
            By default, the client waits for server tor request disconnect.

        task_timeout (int, optional):
            Task-level walltime limit in seconds (default: none).
            May be overridden by inline-comment.

        task_signalwait (int, optional):
            Signal escalation waiting period in seconds on task timeout.
            See :const:`~hypershell.client.DEFAULT_SIGNALWAIT`.

        ratelimit (int, optional):
            Maximum allowed tasks per second per client (default: none).
            There is no limit on task throughput unless specified.

        poll (int, optional):
            Polling interval in seconds between database queries if no tasks are available.
            See :const:`~hypershell.server.DEFAULT_SERVER_POLL`.

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

        tls: (TLSConfig, optional):
            TLS configuration for queue interface.
            Clients must connect with compatible configuration.
            See :ref:`security <security>` documentation for details.

        zone (str, optional):
            Zone whose tasks the clients run (default: the default zone).
            Tasks this cluster submits are placed in the same zone.

        evict_after (int, optional):
            Period in seconds the server waits on a client's heartbeat before evicting it.
            See :const:`~hypershell.server.DEFAULT_EVICT`.

        discover_zones (bool, optional):
            Scale clients zone by zone, each launch naming its zone to the launcher (default: True).
            When disabled, every client is launched exactly as given, in the cluster's own zone.

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
    launch: ClientLaunch

    def __init__(self: AutoScalingCluster,
                 source: Iterable[str] = None,
                 num_threads: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 cores: int = None,
                 memory: int = None,
                 client_cores: int = None,
                 client_memory: int = None,
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
                 monitor: bool = False,
                 client_timeout: int = None,
                 task_timeout: int = None,
                 task_signalwait: int = DEFAULT_SIGNALWAIT,
                 ratelimit: int = None,
                 poll: int = DEFAULT_SERVER_POLL,
                 policy: str = DEFAULT_AUTOSCALE_POLICY,
                 period: int = DEFAULT_AUTOSCALE_PERIOD,
                 factor: float = DEFAULT_AUTOSCALE_FACTOR,
                 init_size: int = DEFAULT_AUTOSCALE_INIT_SIZE,
                 min_size: int = DEFAULT_AUTOSCALE_MIN_SIZE,
                 max_size: int = DEFAULT_AUTOSCALE_MAX_SIZE,
                 from_json: bool = False,
                 tls: Optional[TLSConfig] = None,
                 zone: Optional[str] = None,
                 evict_after: int = DEFAULT_EVICT,
                 discover_zones: bool = True,
                 ) -> None:
        """Initialize server and autoscaler."""
        auth = secrets.token_hex(64)
        # In JSON mode the template is expanded submit-side by the server; remote clients
        # run the fully-resolved commands verbatim (avoids double expansion).
        client_template = DEFAULT_TEMPLATE if from_json else template
        self.server = ServerThread(source=source,
                                   auth=auth,
                                   address=bind,
                                   task_cores=cores,
                                   task_memory=memory,
                                   task_timeout=task_timeout,
                                   bundlesize=bundlesize,
                                   bundlewait=bundlewait,
                                   poll=poll,
                                   max_retries=max_retries,
                                   eager=eager,
                                   forever_mode=True,
                                   redirect_failures=redirect_failures,
                                   template=template,
                                   from_json=from_json,
                                   evict_after=evict_after,
                                   tls=tls)
        if launcher_args is None:
            launcher_args = []
        else:
            launcher_args = [arg for arg_group in launcher_args for arg in shlex.split(arg_group)]
        # Every launch of a zone is built from the same values; only the zone differs.
        build = partial(client_launch, remote_exe, (HOSTNAME, bind[1]), auth,
                        launcher=shlex.split(launcher), launcher_args=launcher_args,
                        num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                        template=client_template, delay_start=delay_start,
                        task_signalwait=task_signalwait, capture=capture, monitor=monitor,
                        client_cores=client_cores, client_memory=client_memory,
                        client_timeout=client_timeout, task_timeout=task_timeout,
                        ratelimit=ratelimit, tls_enabled=tls is not None)
        own = ZoneRef.from_name(zone)
        # The own zone keeps the cluster's --zone as given (none for the default zone); any other is named.
        launch_for = None if not discover_zones else (
            lambda ref: build(zone=zone if ref.id == own.id else ref.name, expose_zone=True))
        self.launch = build(zone=zone) if launch_for is None else launch_for(own)
        # Failed tasks count as a zone's work under the server's own retry rule.
        # Registered clients per zone come from the dispatcher, looked up at each check (logging only).
        registered = None if not discover_zones else (lambda zone_id: self.server.queue.dispatcher.clients(zone_id))
        self.autoscaler = AutoScalerThread(self.launch, policy=policy, factor=factor, period=period,
                                           init_size=init_size, min_size=min_size, max_size=max_size,
                                           own_zone=own, launch_for=launch_for, attempts=max_retries + 1,
                                           eager=eager, registered=registered)
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: AutoScalingCluster) -> None:
        """Start child threads, wait."""
        self.server.start()
        self.server.wait_scheduling()  # NOTE: clients never idle while a task file is read
        self.autoscaler.start()
        self.autoscaler.join()
        self.server.join()

    def stop(self: AutoScalingCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        self.autoscaler.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)
