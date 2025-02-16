# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Run full cluster with server and managed clients."""


# Type annotations
from __future__ import annotations
from typing import IO, Optional, Iterable, Dict, Callable, Type
from types import TracebackType

# Standard libs
import sys
import shlex
from functools import cached_property

# External libs
from cmdkit.app import Application
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.config import config, blame
from hypershell.core.queue import QueueConfig
from hypershell.core.logging import Logger
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.data import initdb, checkdb
from hypershell.client import DEFAULT_NUM_TASKS, DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.server import DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.cluster.ssh import run_ssh, SSHCluster, NodeList, DEFAULT_REMOTE_EXE
from hypershell.cluster.local import run_local, LocalCluster
from hypershell.cluster.remote import (run_cluster, RemoteCluster, AutoScalingCluster,
                                       DEFAULT_AUTOSCALE_FACTOR, DEFAULT_AUTOSCALE_PERIOD,
                                       DEFAULT_AUTOSCALE_MIN_SIZE, DEFAULT_AUTOSCALE_MAX_SIZE,
                                       DEFAULT_AUTOSCALE_INIT_SIZE)

# Public interface
__all__ = ['run_local', 'run_cluster', 'run_ssh',
           'LocalCluster', 'RemoteCluster', 'AutoScalingCluster', 'SSHCluster',
           'DEFAULT_REMOTE_EXE', 'DEFAULT_AUTOSCALE_FACTOR', 'DEFAULT_AUTOSCALE_PERIOD',
           'DEFAULT_AUTOSCALE_MIN_SIZE', 'DEFAULT_AUTOSCALE_MAX_SIZE', 'DEFAULT_AUTOSCALE_INIT_SIZE',
           'ClusterApp', ]

# Initialize logger
log = Logger.with_name(__name__)


APP_NAME = 'hs cluster'
APP_USAGE = """\
Usage:
  hs cluster [-h] [FILE | --restart | --forever] [-N NUM] [-t CMD] [-b SIZE] [-w SEC]
             [-p PORT] [-r NUM [--eager]] [-f PATH] [--capture | [-o PATH] [-e PATH]]
             [--ssh [HOST... | --ssh-group NAME] [--env] | --mpi | --launcher=ARGS...]
             [--no-db | --initdb] [--no-confirm] [-d SEC] [-T SEC] [-W SEC] [-S SEC]
             [--autoscaling [MODE] [-P SEC] [-F VALUE] [-I NUM] [-X NUM] [-Y NUM]]

  Start cluster locally, over SSH, or with a custom launcher.\
"""

APP_HELP = f"""\
{APP_USAGE}

Arguments:
  FILE                         Path to input task file (default: <stdin>).

Modes:
  --ssh               HOST...  Launch directly with SSH host(s).
  --mpi                        Same as --launcher=mpirun.
  --launcher          ARGS...  Use specific launch interface.

Options:
  -N, --num-tasks     NUM      Number of task executors per client (default: {DEFAULT_NUM_TASKS}).
  -t, --template      CMD      Command-line template pattern (default: "{DEFAULT_TEMPLATE}").
  -p, --port          NUM      Port number (default: {QueueConfig.port}).
  -b, --bundlesize    SIZE     Size of task bundle (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait    SEC      Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
  -r, --max-retries   NUM      Auto-retry failed tasks (default: {DEFAULT_ATTEMPTS - 1}).
      --eager                  Schedule failed tasks before new tasks.
      --no-db                  Disable database (submit directly to clients).
      --initdb                 Auto-initialize database.
      --no-confirm             Disable client confirmation of task bundle received.
      --forever                Schedule forever.
      --restart                Start scheduling from last completed task.
      --ssh-args      ARGS     Command-line arguments for SSH.
      --ssh-group     NAME     SSH nodelist group in config.
  -E, --env                    Send environment variables.
      --remote-exe    PATH     Path to executable on remote hosts.
  -d, --delay-start   SEC      Delay time for launching clients (default: {DEFAULT_DELAY}).
  -c, --capture                Capture individual task <stdout> and <stderr>.
  -o, --output        PATH     File path for task outputs (default: <stdout>).
  -e, --errors        PATH     File path for task errors (default: <stderr>).
  -f, --failures      PATH     File path to write failed task args (default: <none>).
  -T, --timeout       SEC      Automatically shutdown clients if no tasks received (default: never).
  -W, --task-timeout  SEC      Task-level walltime limit (default: none).
  -S, --signalwait    SEC      Task-level signal escalation wait period (default: {DEFAULT_SIGNALWAIT}).
  -A, --autoscaling  [MODE]    Enable autoscaling (default: disabled). Used with --launcher.
  -F, --factor        VALUE    Scaling factor (default: 1).
  -P, --period        SEC      Scaling period in seconds (default: {DEFAULT_AUTOSCALE_PERIOD}).
  -I, --init-size     SIZE     Initial size of cluster (default: {DEFAULT_AUTOSCALE_INIT_SIZE}).
  -X, --min-size      SIZE     Minimum size of cluster (default: {DEFAULT_AUTOSCALE_MIN_SIZE}).
  -Y, --max-size      SIZE     Maximum size of cluster (default: {DEFAULT_AUTOSCALE_MAX_SIZE}).
  -h, --help                   Show this message and exit.\
"""


class ClusterApp(Application):
    """Run managed cluster."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    filepath: str
    interface.add_argument('filepath', nargs='?', default=None)

    num_tasks: int = 1
    interface.add_argument('-N', '--num-tasks', type=int, default=num_tasks)

    template: str = DEFAULT_TEMPLATE
    interface.add_argument('-t', '--template', default=template)

    bundlesize: int = config.server.bundlesize
    interface.add_argument('-b', '--bundlesize', type=int, default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    interface.add_argument('-w', '--bundlewait', type=int, default=bundlewait)

    delay_start: float = DEFAULT_DELAY
    interface.add_argument('-d', '--delay-start', type=float, default=delay_start)

    eager_mode: bool = False
    max_retries: int = DEFAULT_ATTEMPTS - 1
    interface.add_argument('-r', '--max-retries', type=int, default=max_retries)
    interface.add_argument('--eager', action='store_true', dest='eager_mode')

    in_memory: bool = False
    auto_initdb: bool = False
    db_interface = interface.add_mutually_exclusive_group()
    db_interface.add_argument('--no-db', action='store_true', dest='in_memory')
    db_interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    no_confirm: bool = False
    interface.add_argument('--no-confirm', action='store_true')

    forever_mode: bool = False
    interface.add_argument('--forever', action='store_true', dest='forever_mode')

    restart_mode: bool = False
    interface.add_argument('--restart', action='store_true', dest='restart_mode')

    ssh_mode: str = None
    mpi_mode: bool = False
    launch_mode: str = None
    mode_interface = interface.add_mutually_exclusive_group()
    mode_interface.add_argument('--ssh', nargs='?', const='<default>', default=None, dest='ssh_mode')
    mode_interface.add_argument('--mpi', action='store_true', dest='mpi_mode')
    mode_interface.add_argument('--launcher', default=None, dest='launch_mode')

    ssh_args: str = ''
    interface.add_argument('--ssh-args', default=ssh_args)

    ssh_group: str = None
    interface.add_argument('--ssh-group', default=None)

    remote_exe: str = sys.argv[0]
    interface.add_argument('--remote-exe', default=remote_exe)

    export_env: bool = False
    interface.add_argument('-E', '--env', action='store_true', dest='export_env')

    port: int = QueueConfig.port
    interface.add_argument('-p', '--port', default=port, type=int)

    capture: bool = False
    output_path: str = None
    errors_path: str = None
    interface.add_argument('-o', '--output', default=None, dest='output_path')
    interface.add_argument('-e', '--errors', default=None, dest='errors_path')
    interface.add_argument('-c', '--capture', action='store_true')

    failure_path: str = None
    interface.add_argument('-f', '--failures', default=None, dest='failure_path')

    task_timeout: int = config.task.timeout
    client_timeout: int = config.client.timeout
    interface.add_argument('-T', '--timeout', type=int, default=client_timeout, dest='client_timeout')
    interface.add_argument('-W', '--task-timeout', type=int, default=task_timeout, dest='task_timeout')

    task_signalwait: int = config.task.signalwait
    interface.add_argument('-S', '--signalwait', type=int, default=task_signalwait, dest='task_signalwait')

    autoscaling_policy: str = None
    autoscaling_factor: float = config.autoscale.factor
    autoscaling_period: int = config.autoscale.period
    autoscaling_minimum: int = config.autoscale.size.min
    autoscaling_maximum: int = config.autoscale.size.max
    autoscaling_initial: int = config.autoscale.size.init
    interface.add_argument('-A', '--autoscaling', nargs='?', default=None,
                           const=config.autoscale.policy, dest='autoscaling_policy',)
    interface.add_argument('-F', '--factor', type=float, default=autoscaling_factor, dest='autoscaling_factor')
    interface.add_argument('-P', '--period', type=int, default=autoscaling_period, dest='autoscaling_period')
    interface.add_argument('-X', '--min-size', type=int, default=autoscaling_minimum, dest='autoscaling_minimum')
    interface.add_argument('-Y', '--max-size', type=int, default=autoscaling_maximum, dest='autoscaling_maximum')
    interface.add_argument('-I', '--init-size', type=int, default=autoscaling_initial, dest='autoscaling_initial')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ClusterApp) -> None:
        """Run cluster."""
        launcher = self.launchers.get(self.mode)
        launcher(source=self.source, num_tasks=self.num_tasks, template=self.template,
                 bundlesize=self.bundlesize, bundlewait=self.bundlewait, max_retries=self.max_retries,
                 in_memory=self.in_memory, no_confirm=self.no_confirm, forever_mode=self.forever_mode,
                 restart_mode=self.restart_mode, redirect_failures=self.failure_stream,
                 delay_start=self.delay_start, capture=self.capture,
                 client_timeout=self.client_timeout, task_timeout=self.task_timeout,
                 task_signalwait=self.task_signalwait)

    def run_local(self: ClusterApp, **options) -> None:
        """Run local cluster."""
        run_local(**options, redirect_output=self.output_stream, redirect_errors=self.errors_stream)

    def run_launch(self: ClusterApp, **options) -> None:
        """Run remote cluster with custom launcher."""
        run_cluster(**options, launcher=self.launch_mode,
                    remote_exe=self.remote_exe, bind=('0.0.0.0', self.port))

    def run_mpi(self: ClusterApp, **options) -> None:
        """Run remote cluster with 'mpirun'."""
        run_cluster(**options, launcher='mpirun',
                    remote_exe=self.remote_exe, bind=('0.0.0.0', self.port))

    def run_ssh(self: ClusterApp, **options) -> None:
        """Run remote cluster with SSH."""
        if self.ssh_group:
            nodelist = NodeList.from_config(self.ssh_group)
        else:
            nodelist = NodeList.from_cmdline(self.ssh_mode if self.ssh_mode != '<default>' else None)
        run_ssh(**options, launcher='ssh', launcher_args=shlex.split(self.ssh_args), nodelist=nodelist,
                remote_exe=self.remote_exe, bind=('0.0.0.0', self.port), export_env=self.export_env)

    def run_autoscaling(self: ClusterApp, **options) -> None:
        """Run remote cluster with custom launcher and autoscaling."""
        run_cluster(**options, autoscaling=True, launcher=(self.launch_mode or ''),
                    policy=self.autoscaling_policy, factor=self.autoscaling_factor,
                    period=self.autoscaling_period, init_size=self.autoscaling_initial,
                    min_size=self.autoscaling_minimum, max_size=self.autoscaling_maximum,
                    remote_exe=self.remote_exe, bind=('0.0.0.0', self.port))

    @cached_property
    def launchers(self: ClusterApp) -> Dict[str, Callable]:
        """Map of launchers."""
        return {
            'autoscaling': self.run_autoscaling,
            'local': self.run_local,
            'launch': self.run_launch,
            'mpi': self.run_mpi,
            'ssh': self.run_ssh
        }

    @cached_property
    def mode(self: ClusterApp) -> str:
        """The launch mode to run the cluster."""
        if self.autoscaling_policy is not None:
            return 'autoscaling'
        for name in ['ssh', 'mpi', 'launch']:
            if getattr(self, f'{name}_mode'):
                return name
        else:
            return 'local'

    def check_arguments(self: ClusterApp) -> None:
        """Various checks on input arguments."""
        given_filepath = self.filepath is not None
        if self.filepath is None and not self.restart_mode:
            self.filepath = '-'  # NOTE: assume STDIN
        if self.restart_mode and self.in_memory:
            raise ArgumentError('Cannot restart without database (given --no-db)')
        if self.output_path and self.mode != 'local':
            raise ArgumentError('Cannot specify -o/--output PATH with remote clients')
        if self.errors_path and self.mode != 'local':
            raise ArgumentError('Cannot specify -e/--errors PATH with remote clients')
        if self.capture and self.output_path:
            raise ArgumentError('Cannot specify -c/--capture with -o/--output')
        if self.capture and self.errors_path:
            raise ArgumentError('Cannot specify -c/--capture with -e/--error')
        if self.in_memory and self.forever_mode:
            raise ArgumentError('Using --forever with --no-db is invalid')
        if self.in_memory and self.restart_mode:
            raise ArgumentError('Using --restart with --no-db is invalid')
        if self.forever_mode and self.restart_mode:
            raise ArgumentError('Using --forever with --restart is invalid')
        if self.ssh_args and not self.ssh_mode:
            raise ArgumentError('Unexpected --ssh-args when not in --ssh mode')
        if self.ssh_group and self.ssh_mode != '<default>':
            raise ArgumentError('Cannot specify --ssh with target with --ssh-group')
        if self.autoscaling_policy is not None and self.ssh_mode:
            raise ArgumentError('Cannot use --autoscaling with --ssh mode')
        if self.autoscaling_policy is not None and self.mpi_mode:
            raise ArgumentError('Cannot use --autoscaling with --mpi mode')
        if self.autoscaling_policy is not None and self.in_memory:
            raise ArgumentError('Cannot use --autoscaling without database (given --no-db)')
        if self.autoscaling_policy is not None and self.restart_mode:
            log.warning('Using --restart is redundant with --autoscaling (implies --forever)')
        if self.autoscaling_policy is not None and self.forever_mode:
            log.warning('Using --forever is redundant with --autoscaling')
        if self.autoscaling_policy is not None and given_filepath:
            log.warning(f'Task file ({self.input_stream.name}) in use but server will '
                        f'not halt (--autoscaling implies --forever)')
        if self.autoscaling_policy is not None and self.client_timeout is not None and self.autoscaling_minimum > 0:
            log.warning(f'Use of --autoscaling with --timeout={self.client_timeout} for clients and '
                        f'--min-size={self.autoscaling_minimum} will cause repeated client stop/start cycles')
        autoscaling_enabled = self.autoscaling_policy is not None
        policy_is_dynamic = (
            self.autoscaling_policy in ('dynamic', 'DYNAMIC') or
            config.autoscale.policy in ('dynamic', 'DYNAMIC')
        )
        if autoscaling_enabled and policy_is_dynamic and self.client_timeout is None:
            if config.autoscale.policy.lower() == 'dynamic':
                label = blame(config, 'autoscale', 'policy')
                log.warning(f'Use of --autoscaling with policy set to \'dynamic\' ({label}) without client '
                            f'--timeout does not allow for scaling down after task pressure subsides')
            else:
                log.warning(f'Use of --autoscaling=dynamic without client --timeout does not allow '
                            f'for scaling down after task pressure subsides')

    @cached_property
    def output_stream(self: ClusterApp) -> IO:
        """IO stream to write task outputs."""
        return sys.stdout if not self.output_path else open(self.output_path, mode='w')

    @cached_property
    def errors_stream(self: ClusterApp) -> IO:
        """IO stream to write task errors."""
        return sys.stderr if not self.errors_path else open(self.errors_path, mode='w')

    @cached_property
    def failure_stream(self: ClusterApp) -> Optional[IO]:
        """IO stream to write failed task args."""
        return None if not self.failure_path else open(self.failure_path, mode='w')

    @cached_property
    def input_stream(self: ClusterApp) -> Optional[IO]:
        """IO stream to read task command-line args."""
        if self.restart_mode:
            return None
        else:
            return sys.stdin if self.filepath == '-' else open(self.filepath, mode='r')

    @cached_property
    def source(self: ClusterApp) -> Iterable[str]:
        """Input source for task command-line args."""
        return [] if self.restart_mode else self.input_stream

    def __enter__(self: ClusterApp) -> ClusterApp:
        """Set up resources and attributes."""
        self.check_arguments()
        if config.database.provider == 'sqlite' or self.auto_initdb:
            initdb()  # Auto-initialize if local sqlite provider
        elif not self.in_memory:
            checkdb()
        return self

    def __exit__(self: ClusterApp,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Close IO streams if not standard streams."""
        if self.input_stream and self.input_stream is not sys.stdin:
            self.input_stream.close()
        if self.output_stream is not sys.stdout:
            self.output_stream.close()
        if self.errors_stream is not sys.stderr:
            self.errors_stream.close()
        if self.failure_stream:
            self.failure_stream.close()
