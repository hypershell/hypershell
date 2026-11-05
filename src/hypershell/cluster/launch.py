# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Client launch for cluster modes that start clients as separate processes.

Every such mode builds its client invocation through :func:`client_launch`.
The authentication key never appears on a command line it builds: launcher modes
hand the key to the client in the launched process's environment, as
:const:`AUTH_ENV`, which every client reads as the default for its key.
"""


# Type annotations
from __future__ import annotations
from typing import Tuple, List, Dict, Iterable, Sequence, Optional, Final

# Standard libs
import os
import shlex
from dataclasses import dataclass, field
from subprocess import Popen

# Internal libs
from hypershell.core.config import load_task_env
from hypershell.client import AUTH_ENV

# Public interface
__all__ = ['AUTH_ENV', 'OMPI_ENV_LIST', 'ClientLaunch', 'client_launch', 'redact_secrets']


OMPI_ENV_LIST: Final[str] = 'OMPI_MCA_mca_base_env_list'
"""Open MPI parameter naming the environment variables `mpirun` forwards to remote ranks."""


@dataclass(frozen=True)
class ClientLaunch:
    """
    A built client invocation: the command line to run and what the client receives besides it.

    The command line never holds the authentication key; `env` and `stdin` may,
    so neither is part of the representation or of :meth:`describe`.
    """

    argv: List[str]
    env: Dict[str, str] = field(repr=False)
    stdin: Optional[str] = field(default=None, repr=False)
    inherit_env: bool = False

    def start(self: ClientLaunch, **popen_options) -> Popen:
        """
        Start one client process.

        The launched process gets the inherited environment, the task environment
        from configuration, and the additions in `env`, in that order. The environment
        of the calling process is never modified.
        """
        env = None if self.inherit_env else {**os.environ, **load_task_env(), **self.env}
        return Popen(self.argv, env=env, **popen_options)

    def describe(self: ClientLaunch) -> str:
        """Shell-quoted command line for logging, with any key option value redacted."""
        return redact_secrets(self.argv, ['-k', '--auth'])


def client_launch(remote_exe: str,
                  address: Tuple[str, int],
                  auth: str,
                  *,
                  launcher: Sequence[str] = (),
                  launcher_args: Sequence[str] = (),
                  ssh_host: Optional[str] = None,
                  ssh_exports: Sequence[str] = (),  # noqa: unused until ssh launches build here
                  num_threads: int,
                  bundlesize: int,
                  bundlewait: int,
                  template: str,
                  delay_start: float,
                  task_signalwait: int,
                  no_confirm: bool = False,
                  capture: bool = False,
                  monitor: bool = False,
                  client_cores: int = None,
                  client_memory: int = None,
                  client_timeout: int = None,
                  task_timeout: int = None,
                  ratelimit: int = None,
                  tls_enabled: bool = True,
                  zone: Optional[str] = None) -> ClientLaunch:
    """
    Build the client invocation for a cluster launch.

    The command line is `launcher`, then `launcher_args`, then `remote_exe client` with the
    shared client options. The key is handed over in the environment as :const:`AUTH_ENV`,
    and :const:`OMPI_ENV_LIST` names that variable so Open MPI's `mpirun` forwards it to
    remote ranks (other launchers ignore it). The calling process's environment is read,
    never modified.

    Example:
        >>> launch = client_launch('hs', ('node-1', 50001), 'x' * 32, launcher=['srun'],
        ...                        num_threads=4, bundlesize=1, bundlewait=5, template='{}',
        ...                        delay_start=0, task_signalwait=10, tls_enabled=False)
        >>> launch.describe()
        "srun hs client -H node-1 -p 50001 -N 4 -b 1 -w 5 -t '{}' -d 0 -S 10 --no-tls"
    """
    if ssh_host is not None:
        raise NotImplementedError('SSH client launch is not built here yet')
    options = client_options(address, num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                             template=template, delay_start=delay_start, task_signalwait=task_signalwait,
                             no_confirm=no_confirm, capture=capture, monitor=monitor,
                             client_cores=client_cores, client_memory=client_memory,
                             client_timeout=client_timeout, task_timeout=task_timeout, ratelimit=ratelimit,
                             tls_enabled=tls_enabled, zone=zone)
    return ClientLaunch(argv=[*launcher, *launcher_args, remote_exe, 'client', *options],
                        env={AUTH_ENV: auth, OMPI_ENV_LIST: ompi_env_list()})


def client_options(address: Tuple[str, int],
                   *,
                   num_threads: int,
                   bundlesize: int,
                   bundlewait: int,
                   template: str,
                   delay_start: float,
                   task_signalwait: int,
                   no_confirm: bool,
                   capture: bool,
                   monitor: bool,
                   client_cores: Optional[int],
                   client_memory: Optional[int],
                   client_timeout: Optional[int],
                   task_timeout: Optional[int],
                   ratelimit: Optional[int],
                   tls_enabled: bool,
                   zone: Optional[str]) -> List[str]:
    """The `hs client` options shared by every launch mode (never the key)."""
    host, port = address
    options = ['-H', host, '-p', str(port), '-N', str(num_threads), '-b', str(bundlesize), '-w', str(bundlewait),
               '-t', template, '-d', str(delay_start), '-S', str(task_signalwait)]
    if no_confirm:
        options.append('--no-confirm')
    if capture:
        options.append('--capture')
    if monitor:
        options.append('--monitor')
    if client_cores is not None:
        options.extend(['-C', str(client_cores)])
    if client_memory is not None:
        options.extend(['-M', str(client_memory)])
    if client_timeout is not None:
        options.extend(['-T', str(client_timeout)])
    if task_timeout is not None:
        options.extend(['-W', str(task_timeout)])
    if ratelimit is not None:
        options.extend(['-R', str(ratelimit)])
    if not tls_enabled:
        # TLS material is resolved by each client from its own site/config (identical to
        # the server's on a shared filesystem); only the disabled state must be propagated.
        options.append('--no-tls')
    if zone is not None:
        options.extend(['--zone', zone])
    return options


def ompi_env_list() -> str:
    """Value of :const:`OMPI_ENV_LIST` that adds :const:`AUTH_ENV` to any names already set."""
    current = {**os.environ, **load_task_env()}.get(OMPI_ENV_LIST, '')
    names = [name for name in current.split(';') if name]
    if AUTH_ENV not in names:
        names.append(AUTH_ENV)
    return ';'.join(names)


def redact_secrets(argv: List[str], options: Iterable[str], *, redaction: str = '***') -> str:
    """
    Return a shell-quoted command-line string with the *values* following
    any of the specified options redacted.

    Example:
        >>> redact_secrets(['client', '-k', 'supersecret', '--verbose', '--auth', 'another'],
        ...                ['-k', '--auth'])
        "client -k '***' --verbose --auth '***'"
    """
    redact_set = set(options)
    it = iter(argv)
    redacted: List[str] = []
    for arg in it:
        redacted.append(arg)
        if arg in redact_set:
            try:
                next(it) # Consume the secret value
                redacted.append(redaction)
            except StopIteration:
                pass     # Option had no following value
    return ' '.join(shlex.quote(x) for x in redacted)
