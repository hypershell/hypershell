# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""SSH-based cluster implementation."""


# Type annotations
from __future__ import annotations
from typing import Type, List, Iterable, Tuple, IO, Final

# Standard libs
import re
import sys
import time
import shlex
import secrets
from subprocess import Popen

# External libs
from cmdkit.config import ConfigurationError, Namespace

# Internal libs
from hypershell.core.config import config, blame
from hypershell.core.thread import Thread
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.template import DEFAULT_TEMPLATE
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.submit import DEFAULT_BUNDLEWAIT
from hypershell.server import ServerThread, DEFAULT_PORT, DEFAULT_BUNDLESIZE, DEFAULT_ATTEMPTS

# Public interface
__all__ = ['run_ssh', 'SSHCluster', 'NodeList', 'DEFAULT_REMOTE_EXE']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


# NOTE: retain old name for remote executable (for now)
DEFAULT_REMOTE_EXE: Final[str] = 'hyper-shell'
"""Default remote executable name."""


def run_ssh(**options) -> None:
    """
    Run cluster with remote clients via SSH until completion.

    All function arguments are forwarded directly into the
    :class:`~hypershell.cluster.ssh.SSHCluster` thread.

    Example:
        >>> from hypershell.cluster import run_ssh
        >>> run_ssh(
        ...     nodelist=['a00.cluster', 'a01.cluster', 'a02.cluster'],
        ...     restart_mode=True, max_retries=2, eager=True,
        ...     client_timeout=600, task_timeout=300, capture=True
        ... )

    See Also:
        - :class:`~hypershell.cluster.ssh.SSHCluster`
    """
    thread = SSHCluster.new(**options)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise


class SSHCluster(Thread):
    """
    Run server with individual external ssh clients.

    Args:
        source (Iterable[str], optional):
            Any iterable of command-line tasks.
            A new `source` results in a :class:`~hypershell.submit.SubmitThread` populating
            either the database or the queue directly depending on `in_memory`.

        nodelist (List[str], required):
            List of hostnames for launching clients.
            See also: :class:`~hypershell.cluster.ssh.NodeList`.

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
            Defaults to 'ssh'. Use `launcher_args` to provide command options.

        launcher_args (List[str], optional):
            Additional command-line arguments for launcher program.

        remote_exe (str, optional):
            Program name or path for remote executable.
            See :const:`~hypershell.cluster.ssh.DEFAULT_REMOTE_EXE`.

        export_env (bool, optional):
            If enabled, embed local configuration as environment variables
            and forward with client launch command to remote hosts.

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
        >>> from hypershell.cluster import SSHCluster
        >>> cluster = SSHCluster.new(
        ...     nodelist=['a00.cluster', 'a01.cluster', 'a02.cluster'],
        ...     restart_mode=True, max_retries=2, eager=True,
        ...     client_timeout=600, task_timeout=300, capture=True
        ... )
        >>> cluster.join()

    See Also:
        - :class:`~hypershell.server.ServerThread`
        - :meth:`~hypershell.cluster.ssh.run_ssh`
    """

    server: ServerThread
    clients: List[Popen]
    client_argv: List[List[str]]

    def __init__(self: SSHCluster,
                 source: Iterable[str] = None,
                 nodelist: List[str] = None,
                 num_tasks: int = 1,
                 template: str = DEFAULT_TEMPLATE,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 bind: Tuple[str, int] = ('0.0.0.0', DEFAULT_PORT),
                 launcher: str = 'ssh',
                 launcher_args: List[str] = None,
                 remote_exe: str = DEFAULT_REMOTE_EXE,
                 export_env: bool = False,
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
        """Initialize server and client threads."""
        if nodelist is None:
            raise AttributeError('Expected nodelist')
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
        launcher_env = shlex.split('' if not export_env else compile_env())
        if launcher_args is None:
            launcher_args = shlex.split(config.ssh.get('args', ''))
        client_args = []
        if capture:
            client_args.append('--capture')
        if no_confirm:
            client_args.append('--no-confirm')
        if client_timeout is not None:
            client_args.extend(['-T', str(client_timeout)])
        if task_timeout is not None:
            client_args.extend(['-W', str(task_timeout)])
        self.client_argv = [
            [*launcher, *launcher_args, host, *launcher_env, remote_exe, 'client', '-H', HOSTNAME,
             '-p', str(bind[1]), '-N', str(num_tasks), '-b', str(bundlesize), '-w', str(bundlewait),
             '-t', f'\'{template}\'', '-k', auth, '-d', str(delay_start),
             '-S', str(task_signalwait), *client_args]
            for host in nodelist
        ]
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: SSHCluster) -> None:
        """Start child threads, wait."""
        self.server.start()
        while not self.server.queue.ready:
            time.sleep(0.1)
        self.clients = []
        for argv in self.client_argv:
            log.debug(f'Launching client: {argv}')
            self.clients.append(Popen(argv, stdout=sys.stdout, stderr=sys.stderr))
        for client in self.clients:
            client.wait()
        self.server.join()

    def stop(self: SSHCluster, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        self.server.stop(wait=wait, timeout=timeout)
        for client in self.clients:
            client.terminate()
        super().stop(wait=wait, timeout=timeout)


class NodeList(list):
    """A list of hostnames."""

    group_pattern: re.Pattern = re.compile(r',(?![^[]*])')
    range_pattern: re.Pattern = re.compile(r'\[((\d+|\d+-\d+)(,(\d+|\d+-\d+))*)]')

    @classmethod
    def from_cmdline(cls: Type[NodeList], arg: str = None) -> NodeList:
        """Smart initialization via some command-line `arg`."""
        if not arg:
            return cls.from_config()
        if ',' in arg or cls.range_pattern.search(arg):
            return cls.from_pattern(arg)
        else:
            return cls([arg, ])

    @classmethod
    def from_config(cls: Type[NodeList], groupname: str = None) -> NodeList:
        """Load list of hostnames from configuration file."""

        if 'ssh' not in config:
            raise ConfigurationError('No `ssh` section found in configuration')
        if 'nodelist' not in config.ssh:
            raise ConfigurationError('No `ssh.nodelist` section found in configuration')

        label = blame(config, 'ssh', 'nodelist')
        nodelist = config.ssh.nodelist

        if groupname is None:
            if isinstance(nodelist, str):
                return cls.from_pattern(nodelist)
            elif isinstance(nodelist, list):
                return cls(nodelist)
            elif isinstance(nodelist, dict):
                raise ConfigurationError(f'SSH group unspecified but multiple groups in `ssh.nodelist` ({label})')
            else:
                raise ConfigurationError(f'Expected list for `ssh.nodelist` ({label})')

        if isinstance(nodelist, dict):
            nodelist = nodelist.get(groupname, None)
            if not nodelist:
                raise ConfigurationError(f'No list \'{groupname}\' found in `ssh.nodelist` section ({label})')
            elif isinstance(nodelist, str):
                return cls.from_pattern(nodelist)
            elif isinstance(nodelist, list):
                return cls(nodelist)
            else:
                raise ConfigurationError(f'Expected list for `ssh.nodelist.{groupname}` ({label})')
        else:
            raise ConfigurationError(f'Expected either list or section for `ssh.nodelist` ({label})')

    @classmethod
    def from_pattern(cls: Type[NodeList], pattern: str) -> NodeList:
        """Expand a `pattern` to multiple hostnames."""
        pattern = pattern.strip(',')
        if match := cls.group_pattern.search(pattern):
            idx = match.start()
            return cls(cls.from_pattern(pattern[0:idx]) + cls.from_pattern(pattern[idx+1:]))
        else:
            if match := cls.range_pattern.search(pattern):
                range_spec = match.group()
                prefix, suffix = pattern.split(range_spec)
                segments = cls.expand_pattern(range_spec)
                return cls([f'{prefix}{segment}{suffix}' for segment in segments])
            else:
                return cls([pattern, ])

    @staticmethod
    def expand_pattern(spec: str) -> List[str]:
        """Take a range spec (e.g., [4,6-8]) and expand to [4,6,7,8]."""
        spec = spec.strip('[]')
        result = []
        for group in spec.split(','):
            if '-' in group:
                start_chars, stop_chars = group.strip().split('-')
                apply_padding = str if start_chars[0] != '0' else lambda s: str(s).zfill(len(start_chars))
                start, stop = int(start_chars), int(stop_chars)
                if stop < start:
                    start, stop = stop, start
                result.extend([apply_padding(value) for value in range(start, stop + 1)])
            else:
                result.extend([group, ])
        return result


def compile_env() -> str:
    """Build environment variable argument expansion for remote client launch command."""
    return ' '.join([f'{key}="{value}"' for key, value in Namespace.from_env('HYPERSHELL').items()])
