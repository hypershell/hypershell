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
from hypershell.core.config import default, config, blame, ConfigurationError
from hypershell.core.thread import Thread, join_waking
from hypershell.core.logging import Logger, INSTANCE
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.data.model import Task, Client
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.server import (ServerThread, DEFAULT_PORT, DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS, DEFAULT_SERVER_POLL,
                               DEFAULT_EVICT)
from hypershell.cluster.launch import ClientLaunch, LaunchActuator, advertised_host, client_launch, redact_secrets
from hypershell.cluster.demand import (AutoScalerPolicy, ZoneRef, ZoneLoad, ScalingKnobs,
                                       read_zone_loads, fleet_demand)
from hypershell.cluster.watch import ClusterLaunchError, exit_text, watch_launched

# Public interface
__all__ = ['run_cluster', 'RemoteCluster', 'AutoScalingCluster',
           'DEFAULT_REMOTE_EXE', 'DEFAULT_LAUNCHER',
           'DEFAULT_AUTOSCALE_POLICY', 'DEFAULT_AUTOSCALE_PERIOD', 'DEFAULT_AUTOSCALE_FACTOR',
           'DEFAULT_AUTOSCALE_INIT_SIZE', 'DEFAULT_AUTOSCALE_MIN_SIZE', 'DEFAULT_AUTOSCALE_MAX_SIZE',
           'DEFAULT_AUTOSCALE_LAUNCHER', 'DEFAULT_AUTOSCALE_FAILED_LAUNCHES', 'AutoScalerPolicy',
           'configured_failed_launches', 'client_registrations', 'redact_secrets']

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
        join_waking(thread)
    except Exception:
        thread.stop()
        raise


class RemoteCluster(Thread):
    """
    Run server with remote clients via external launcher (e.g., `mpirun`).

    The launcher's exit is watched while the server runs (see :func:`~hypershell.cluster.watch.watch_launched`):
    a non-zero exit is warned about, and if it leaves the server with work it would otherwise wait
    on forever, the server is stopped and :class:`~hypershell.cluster.watch.ClusterLaunchError` is raised.

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
            Clients are told to dial the bind address, or this host's own name when it is 0.0.0.0
            (see :func:`~hypershell.cluster.launch.advertised_host`), unless `advertise` names one.
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

        advertise (str, optional):
            Address the clients are told to dial, whatever the bind (default: the bind rule above).

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
    advertised: Tuple[str, int]

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
                 zone: Optional[str] = None,
                 advertise: Optional[str] = None) -> None:
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
        self.advertised = (advertised_host(bind[0], advertise), bind[1])
        self.launch = client_launch(remote_exe, self.advertised, auth,
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
        """Start child threads, watch the launched clients until the server ends."""
        self.server.start()
        self.server.wait_scheduling()  # NOTE: clients never idle while a task file is read
        log.debug('Launching clients: ' + self.launch.describe())
        self.clients = self.launch.start(stdout=sys.stdout, stderr=sys.stderr)
        watch_launched(self.server, [(self.launch.describe(), self.clients)], self.advertised)
        self.server.join()

    def stop(self: RemoteCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread (a server that has already ended is not stopped again)."""
        if self.server.is_alive():
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

DEFAULT_AUTOSCALE_FAILED_LAUNCHES: Final[int] = default.autoscale.failed_launches
"""Default number of launched clients in a row that may exit non-zero, with no client registered, before an autoscaling cluster stops."""


def configured_failed_launches() -> int:
    """
    The `[autoscale] failed_launches` setting, refused naming its source unless a non-negative integer.

    Its environment form, ``HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES``, is split at every underscore when
    read, and so lands at `autoscale.failed.launches`; from there it takes precedence, as the
    environment does over every file.
    """
    varpath = ('autoscale', 'failed_launches')
    try:
        if config.which('autoscale', 'failed', 'launches') == 'env':
            varpath = ('autoscale', 'failed', 'launches')
    except KeyError:
        pass
    value = config
    for name in varpath:
        value = value[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f'Expected a non-negative integer for `autoscale.failed_launches`, '
                                 f'given {value!r} ({blame(config, *varpath)})')
    return value


def client_registrations() -> Tuple[int, int]:
    """
    Clients registered with this process's server: every one since it started, and those still connected.

    The server stamps its process's :const:`~hypershell.core.logging.INSTANCE` on a client's row at
    every registration and re-registration, and never deletes a row, so the first count only rises
    and counts a client that registered and left between two reads. Rows written by any earlier
    server carry that server's own id and are never counted.
    """
    ever = Client.query().filter_by(server_id=INSTANCE).count()
    now = Client.query().filter_by(server_id=INSTANCE, disconnected_at=None).count()
    return ever, now


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
    tasks count as work. `registered`, when given, counts the clients a zone has registered; it is
    called with a zone id. `scheduler_group`, when given, reports the task group the scheduler loads
    from now (None while it is not known); otherwise, and while it reports None, the group is the
    one the scheduler would start from (:meth:`~hypershell.data.model.Task.starting_group`).

    With `failed_launches` above 0, each check counts the launched clients that exited non-zero in a
    row, in launch order, while no client is registered: `registrations` reports how many clients
    have registered with the server since it started and how many are connected now. A client that
    exits 0, a client connected now, or a registration since the last check ends the run of failures.
    Once the run reaches `failed_launches`, nothing more is launched, `exhausted` is set and the
    machine ends. With 0 (the default) launches are never counted and `registrations` is never called.
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
    scheduler_group: Optional[Callable[[], Optional[int]]]
    failed_launches: int
    registrations: Optional[Callable[[], Tuple[int, int]]]

    actuator: LaunchActuator
    pending: Dict[ZoneRef, int]
    last_check: datetime
    wait_check: timedelta
    seen: int
    failures: List[Tuple[int, str, int]]
    exhausted: bool = False

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
                 scheduler_group: Optional[Callable[[], Optional[int]]] = None,
                 failed_launches: int = 0,
                 registrations: Optional[Callable[[], Tuple[int, int]]] = None,
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
        self.scheduler_group = scheduler_group
        self.failed_launches = failed_launches
        self.registrations = registrations
        self.seen = 0
        self.failures = []
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
        """Check if we need to scale up (end once too many launched clients in a row have failed)."""
        exited = self.clean()
        self.last_check = datetime.now()
        if self.count_failures(exited):
            return AutoScalerState.FINAL
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

    def current_group(self: AutoScaler) -> int:
        """The task group the scheduler loads from: as it reports it, else the group it would start from."""
        group = None if self.scheduler_group is None else self.scheduler_group()
        if group is None:
            recent = Task.current_group().value
            group = Task.starting_group(recent, attempts=self.attempts, eager=self.eager)
            group = recent if group is None else group
        return group

    def check_zones(self: AutoScaler) -> AutoScalerState:
        """Decide the clients each zone needs under the policy, and scale if any zone needs one."""
        group = self.current_group()
        launched = self.launched()
        connected = None if self.registered is not None else Client.count_connected()
        registered: Dict[str, int] = {}

        def registered_in(zone_id: str) -> int:
            if zone_id not in registered:
                registered[zone_id] = connected if self.registered is None else self.registered(zone_id)
            return registered[zone_id]

        own = self.own_zone
        own_idle = launched.get(own.id, 0) == 0 and registered_in(own.id) == 0
        loads = read_zone_loads(group, self.knobs, own=own, discover=self.discover,
                                attempts=self.attempts, eager=self.eager, own_idle=own_idle)
        desired = fleet_demand(loads, launched, self.knobs, own=own, initial=False)
        self.pending = {}
        for zone, count in desired.items():
            have = launched.get(zone.id, 0)
            count = min(count, have + 1)  # NOTE: at most one launch per zone per check
            load = loads.get(zone.id) or ZoneLoad(zone, remaining=0, retryable=0, pressure=None)
            self.log_check(zone, load, group if load.group is None else load.group, have, registered_in(zone.id), count)
            if count > have:
                self.pending[zone] = count
        return AutoScalerState.SCALE if self.pending else AutoScalerState.WAIT

    def log_check(self: AutoScaler, zone: ZoneRef, load: ZoneLoad, group: int,
                  launched: int, registered: int, desired: int) -> None:
        """Log one zone's reading and the decision taken for it."""
        dynamic = self.policy is AutoScalerPolicy.DYNAMIC
        pressure = '' if not dynamic else (
            'pressure: unknown, ' if load.pressure is None else f'pressure: {load.pressure:.2f}, ')
        log.debug(f'Autoscale check (zone: {zone.name}, {pressure}clients: {registered}/{launched}, '
                  f'tasks: {load.work} in group {group})')
        if desired > launched and zone == self.own_zone and launched < self.min_size:
            log.debug(f'Autoscale min-size reached (zone: {zone.name}, {launched} < {self.min_size})')
            return
        if dynamic and load.pressure is not None:
            level = 'high' if load.pressure > 1 else 'low'
            log.debug(f'Autoscale pressure {level} (zone: {zone.name}, {load.pressure:.2f})')
        if desired > launched:
            log.debug(f'Autoscale adding client (zone: {zone.name}, {load.work} tasks remaining in group {group})')
        elif load.pressure is not None and load.pressure > 1:
            log.debug(f'Autoscale max-size reached (zone: {zone.name}, {launched} >= {self.max_size})')
        elif dynamic and load.pressure is None:
            reason = ('no clients and no active tasks' if launched == 0 else
                      'waiting on clients to complete initial tasks')
            log.debug(f'Autoscale pause (zone: {zone.name}, {reason})')

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

    def count_failures(self: AutoScaler, exited: List[Tuple[Popen, ZoneRef]]) -> bool:
        """Count the `exited` clients into the run of failures; True once it reaches `failed_launches` (see the class)."""
        if self.failed_launches <= 0:
            return False
        ever, now = (0, 0) if self.registrations is None else self.registrations()
        if now > 0 or ever > self.seen:
            # A client registered: the launches work, whatever this check's exits were.
            self.seen = ever
            self.failures = []
            return False
        for proc, zone in exited:
            status = proc.poll()
            if status == 0:
                self.failures = []
            else:
                self.failures.append((proc.pid, zone.name, status))
        if len(self.failures) < self.failed_launches:
            return False
        self.exhausted = True
        log.debug(f'Autoscale stop ({len(self.failures)} launched clients in a row exited non-zero)')
        return True

    def clean(self: AutoScaler) -> List[Tuple[Popen, ZoneRef]]:
        """Remove clients that have exited, and return them in launch order, each with its zone."""
        zones = {id(proc): self.actuator.zone_of(proc) for proc in self.actuator.clients}
        return [(proc, zones[id(proc)]) for proc in self.actuator.clean()]

    @staticmethod
    def finalize() -> AutoScalerState:
        """Finalize."""
        log.debug(f'Done (autoscaler)')
        return AutoScalerState.HALT


class AutoScalerThread(Thread):
    """Run AutoScaler within dedicated thread; it ends without raising when the machine is exhausted."""

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
                 scheduler_group: Optional[Callable[[], Optional[int]]] = None,
                 failed_launches: int = 0,
                 registrations: Optional[Callable[[], Tuple[int, int]]] = None,
                 ) -> None:
        """Initialize task executor."""
        super().__init__(name=f'hypershell-autoscaler')
        self.machine = AutoScaler(launch, policy=policy, factor=factor, period=period,
                                  init_size=init_size, min_size=min_size, max_size=max_size,
                                  own_zone=own_zone, launch_for=launch_for, attempts=attempts, eager=eager,
                                  registered=registered, scheduler_group=scheduler_group,
                                  failed_launches=failed_launches, registrations=registrations)

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
            Clients are told to dial the bind address, or this host's own name when it is 0.0.0.0
            (see :func:`~hypershell.cluster.launch.advertised_host`), unless `advertise` names one.
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

        advertise (str, optional):
            Address the clients are told to dial, whatever the bind (default: the bind rule above).

        failed_launches (int, optional):
            Launched clients in a row that may exit non-zero while no client is registered (default: 3).
            When that many have, nothing more is launched, the server is stopped, and
            :class:`~hypershell.cluster.watch.ClusterLaunchError` is raised naming them and the address
            the clients were told. With 0, failed launches are relaunched without end.
            See :const:`~hypershell.cluster.remote.DEFAULT_AUTOSCALE_FAILED_LAUNCHES`.

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
    advertised: Tuple[str, int]

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
                 advertise: Optional[str] = None,
                 failed_launches: int = DEFAULT_AUTOSCALE_FAILED_LAUNCHES,
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
        self.advertised = (advertised_host(bind[0], advertise), bind[1])
        build = partial(client_launch, remote_exe, self.advertised, auth,
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
        # Registered clients per zone come from the dispatcher, looked up at each check.
        registered = None if not discover_zones else (lambda zone_id: self.server.queue.dispatcher.clients(zone_id))
        # Every zone is read from the group the scheduler loads from, as the scheduler reports it.
        scheduler = self.server.scheduler
        scheduler_group = None if scheduler is None else (lambda: scheduler.machine.active_group)
        self.autoscaler = AutoScalerThread(self.launch, policy=policy, factor=factor, period=period,
                                           init_size=init_size, min_size=min_size, max_size=max_size,
                                           own_zone=own, launch_for=launch_for, attempts=max_retries + 1,
                                           eager=eager, registered=registered, scheduler_group=scheduler_group,
                                           failed_launches=failed_launches, registrations=client_registrations)
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: AutoScalingCluster) -> None:
        """Start child threads, wait; stop the server and raise if the autoscaler ended on failed launches.

        That stop does not wait on input still being read: no client is left to run it, and a producer
        that stays open would hold the stop forever. Tasks already committed are kept.
        """
        self.server.start()
        self.server.wait_scheduling()  # NOTE: clients never idle while a task file is read
        self.autoscaler.start()
        self.autoscaler.join()
        if self.autoscaler.machine.exhausted:
            self.server.stop(wait=True, leave_input=True)
            failures = self.autoscaler.machine.failures
            exits = ', '.join(f'{pid} (zone: {zone}) {exit_text(status)}' for pid, zone, status in failures)
            host, port = self.advertised
            raise ClusterLaunchError(f'{len(failures)} launched clients in a row exited non-zero with no client '
                                     f'registered ({exits}); clients were told to dial {host}:{port}')
        self.server.join()

    def stop(self: AutoScalingCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread (a server that has already ended is not stopped again)."""
        if self.server.is_alive():
            self.server.stop(wait=wait, timeout=timeout)
        if not self.autoscaler.machine.exhausted:
            self.autoscaler.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)
