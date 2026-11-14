# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Client launch for cluster modes that start clients as separate processes.

Every such mode builds its client invocation through :func:`client_launch`.
The authentication key never appears on a command line it builds. Launcher modes
hand the key to the client in the launched process's environment, as
:const:`AUTH_ENV`, which every client reads as the default for its key. SSH launches
write the key to ssh's standard input, and a POSIX `sh` step on the remote host reads
it into :const:`AUTH_ENV` before it starts the client.

An autoscaling cluster starts clients zone by zone through a :class:`LaunchActuator`, which
builds one launch per zone; a launch built with `expose_zone` names its zone to the launcher.
"""


# Type annotations
from __future__ import annotations
from typing import Tuple, List, Dict, Set, FrozenSet, Iterable, Mapping, Sequence, Callable, Optional, Final

# Standard libs
import io
import os
import re
import sys
import shlex
from decimal import Decimal
from dataclasses import dataclass, field
from subprocess import Popen, PIPE

# Internal libs
from hypershell.core.config import load_task_env
from hypershell.core.logging import Logger
from hypershell.client import AUTH_ENV, OMPI_ENV_LIST
from hypershell.data.model import DEFAULT_ZONE_NAME
from hypershell.cluster.demand import ZoneRef

# Public interface
__all__ = ['AUTH_ENV', 'OMPI_ENV_LIST', 'ZONE_ENV', 'ZONE_WORD', 'MPI_LAUNCHERS', 'SSH_NO_KEY_MESSAGE',
           'ClientLaunch', 'LaunchActuator', 'client_launch',
           'is_mpi_launcher', 'ssh_env_exports', 'ssh_launch_args', 'ssh_remote_script', 'redact_secrets']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


ZONE_ENV: Final[str] = 'HYPERSHELL_ZONE'
"""Variable that names the zone of a launch to the launcher, in the launched process's environment."""

ZONE_WORD: Final[str] = '{zone}'
"""Text replaced by the zone's name in the launcher and its arguments of a launch that exposes its zone."""

MPI_LAUNCHERS: Final[FrozenSet[str]] = frozenset({'mpirun', 'mpiexec', 'orterun', 'prterun'})
"""Program names of launchers that Open MPI may provide, which forward the key variable only by name."""

MPI_LAUNCHER_VARIANTS: Final[FrozenSet[str]] = frozenset({'mpirun', 'mpiexec'})
"""Names also recognised with a dotted suffix, as packages install them (`mpirun.openmpi`)."""

OMPI_FORWARD: Final[str] = '-x'
"""The `mpirun` option that forwards one environment variable to remote ranks."""

OMPI_FORWARD_FORMS: Final[FrozenSet[str]] = frozenset({'-x', '--x'})
"""Spellings of :const:`OMPI_FORWARD` that count as already given (also with `=value`)."""

SHELL_NAME: Final[re.Pattern] = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
"""A name a POSIX shell can assign in a `NAME=value` word."""

SSH_NO_KEY_MESSAGE: Final[str] = (f'hs cluster: no authentication key arrived on standard input for {AUTH_ENV}; '
                                  f'ssh must pass its standard input through to the remote command '
                                  f'(a wrapper that closes it, or -n in combined ssh flags such as -qn, '
                                  f'prevents this)')
"""Printed on the remote host, before any client starts, when an SSH launch delivers no key."""

SSH_NO_INPUT: Final[str] = '-n'
"""The ssh option that redirects standard input from /dev/null; an SSH launch drops it."""

SSH_OPTIONS_WITH_VALUE: Final[str] = 'BbcDEeFIiJLlmOoPpQRSWw'
"""Letters of the ssh options that take a value."""


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
        from configuration, and the additions in `env`, in that order; or, with
        `inherit_env`, exactly the inherited environment. The environment of the
        calling process is never modified. When there is `stdin`, it is written to
        the process's standard input, which is then closed.
        """
        env = None if self.inherit_env else {**os.environ, **load_task_env(), **self.env}
        if self.stdin is None:
            return Popen(self.argv, env=env, **popen_options)
        proc = Popen(self.argv, env=env, stdin=PIPE, **popen_options)
        data = self.stdin if isinstance(proc.stdin, io.TextIOBase) else self.stdin.encode()
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except BrokenPipeError:
            pass  # The process is already gone; its exit status reports the failure
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        return proc

    def describe(self: ClientLaunch) -> str:
        """
        Shell-quoted command line for logging, with any key option value redacted.

        A word that assigns :const:`AUTH_ENV` shows the name only: each word is parsed as a
        shell would parse it, and any `NAME=value` it holds whose name is :const:`AUTH_ENV`
        redacts the whole word, however the word is quoted. The last word of an SSH launch
        (the one with `stdin`) is already quoted for the remote shell and ssh sends it as it
        is, so it is shown as it is.
        """
        argv = [f'{AUTH_ENV}=***' if assigns_key(word) else word for word in self.argv]
        if self.stdin is None:
            return redact_secrets(argv, ['-k', '--auth'])
        return redact_secrets(argv[:-1], ['-k', '--auth']) + ' ' + self.argv[-1]


def client_launch(remote_exe: str,
                  address: Tuple[str, int],
                  auth: str,
                  *,
                  launcher: Sequence[str] = (),
                  launcher_args: Sequence[str] = (),
                  ssh_host: Optional[str] = None,
                  ssh_exports: Optional[Mapping[str, str]] = None,
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
                  zone: Optional[str] = None,
                  expose_zone: bool = False) -> ClientLaunch:
    """
    Build the client invocation for a cluster launch.

    Without `ssh_host`, the command line is `launcher`, then `launcher_args`, then
    `remote_exe client` with the shared client options. The key is handed over in the
    environment as :const:`AUTH_ENV`. When the launcher's first word is an Open MPI launcher
    (see :func:`is_mpi_launcher`), Open MPI is also asked to forward that variable, by name, to
    remote ranks: through :const:`OMPI_ENV_LIST`, or, if the launcher's arguments already
    hold `-x` (which Open MPI 4 does not accept together with that parameter), with one more
    `-x` and the variable's name. Other launchers get neither, including one that starts an
    MPI launcher itself (`srun mpirun`).

    With `ssh_host`, the command line is `launcher` and `launcher_args`, then `-T` (no
    remote terminal, whatever those arguments or the ssh configuration ask for), the host,
    one `NAME=value` word for each of `ssh_exports` (see :func:`ssh_env_exports`, which never
    exports :const:`AUTH_ENV`), and a remote `sh -c` step (see :func:`ssh_remote_script`).
    The key is the launch's standard input, and ssh gets the caller's environment unchanged.
    The launch owns that input, so a bare `-n` among the ssh arguments is dropped (see
    :func:`ssh_launch_args`).

    With `expose_zone` (launcher mode only; SSH launches refuse it), the launch names its zone
    to the launcher: :const:`ZONE_WORD` in any word of `launcher` or `launcher_args` is replaced by
    the zone's name, and the environment gets :const:`ZONE_ENV` set to it. The name is `zone`, or
    `default` when `zone` is None. Words are replaced after splitting, so a name with a space stays
    one word and other braces are untouched. The client's own options, its template included, are
    never replaced, and Open MPI is not asked to forward :const:`ZONE_ENV`: the launcher reads it
    where it starts, and the client gets its zone from `--zone` alone.

    The calling process's environment is read, never modified.

    Example:
        >>> launch = client_launch('hs', ('node-1', 50001), 'x' * 32, launcher=['srun'],
        ...                        num_threads=4, bundlesize=1, bundlewait=5, template='{}',
        ...                        delay_start=0, task_signalwait=10, tls_enabled=False)
        >>> launch.describe()
        "srun hs client -H node-1 -p 50001 -N 4 -b 1 -w 5 -t '{}' -d 0 -S 10 --no-tls"
    """
    options = client_options(address, num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                             template=template, delay_start=delay_start, task_signalwait=task_signalwait,
                             no_confirm=no_confirm, capture=capture, monitor=monitor,
                             client_cores=client_cores, client_memory=client_memory,
                             client_timeout=client_timeout, task_timeout=task_timeout, ratelimit=ratelimit,
                             tls_enabled=tls_enabled, zone=zone)
    if ssh_host is not None:
        if expose_zone:
            raise ValueError('An SSH launch does not expose its zone (a launcher mode does)')
        script = ssh_remote_script(remote_exe, options)
        exports = ssh_env_exports(ssh_exports or {})
        return ClientLaunch(argv=[*launcher[:1], *ssh_launch_args([*launcher[1:], *launcher_args]),
                                  '-T', ssh_host, *exports, 'sh', '-c', shlex.quote(script)],
                            env={}, stdin=auth + '\n', inherit_env=True)
    launcher_words, env = [*launcher, *launcher_args], {AUTH_ENV: auth}
    if expose_zone:
        name = DEFAULT_ZONE_NAME if zone is None else zone
        launcher_words = [word.replace(ZONE_WORD, name) for word in launcher_words]
        env[ZONE_ENV] = name
    if launcher and is_mpi_launcher(launcher[0]):
        if any(is_forward_option(word) for word in launcher_words[1:]):
            launcher_words.extend([OMPI_FORWARD, AUTH_ENV])
        else:
            env[OMPI_ENV_LIST] = ompi_env_list()
    return ClientLaunch(argv=[*launcher_words, remote_exe, 'client', *options], env=env)


class LaunchActuator:
    """
    Starts clients zone by zone and keeps the processes it started. It never terminates one.

    The launch of the `own` zone (the default zone if not given) is `launch`. The launch of any
    other zone is built by `launch_for` at the zone's first client and kept for every later one;
    its description is logged once, then. Without `launch_for`, every client is started with
    `launch`, whatever zone is asked for.

    `clients` is the flat list of started processes that have not yet been cleaned, every zone
    together. It may be assigned; a process the actuator did not start counts in the own zone.
    """

    launch: ClientLaunch
    own: ZoneRef
    launch_for: Optional[Callable[[ZoneRef], ClientLaunch]]

    def __init__(self: LaunchActuator, launch: ClientLaunch, own: Optional[ZoneRef] = None,
                 launch_for: Optional[Callable[[ZoneRef], ClientLaunch]] = None) -> None:
        """Initialize with the own zone's launch and the builder of every other zone's."""
        self.launch = launch
        self.own = own if own is not None else ZoneRef.from_name(None)
        self.launch_for = launch_for
        self.launches: Dict[str, ClientLaunch] = {self.own.id: launch}
        self.described: Set[str] = set()
        self._clients: List[Popen] = []
        self.zones: Dict[int, str] = {}

    @property
    def clients(self: LaunchActuator) -> List[Popen]:
        """Started processes not yet cleaned, every zone together."""
        return self._clients

    @clients.setter
    def clients(self: LaunchActuator, value: List[Popen]) -> None:
        """Replace the started processes; zones are kept for those the actuator started."""
        self._clients = value
        kept = {id(proc) for proc in value}
        self.zones = {key: zone for key, zone in self.zones.items() if key in kept}

    def launched(self: LaunchActuator) -> Dict[str, int]:
        """Started processes not yet cleaned, per zone id."""
        counts: Dict[str, int] = {}
        for proc in self._clients:
            zone = self.zones.get(id(proc), self.own.id)
            counts[zone] = counts.get(zone, 0) + 1
        return counts

    def launch_of(self: LaunchActuator, zone: ZoneRef) -> ClientLaunch:
        """The launch of `zone`, built at its first use and kept."""
        if zone.id not in self.launches:
            self.launches[zone.id] = self.launch if self.launch_for is None else self.launch_for(zone)
        return self.launches[zone.id]

    def describe(self: LaunchActuator, zone: Optional[ZoneRef] = None) -> None:
        """Log the launch of `zone` (the own zone if not given), once."""
        zone = zone if zone is not None else self.own
        if zone.id not in self.described:
            self.described.add(zone.id)
            log.debug(f'Autoscale launcher (zone {zone.name}): ' + self.launch_of(zone).describe())

    def launch_one(self: LaunchActuator, zone: Optional[ZoneRef] = None) -> Popen:
        """Start one client in `zone` (the own zone if not given)."""
        zone = zone if zone is not None else self.own
        self.describe(zone)
        proc = self.launch_of(zone).start(stdout=sys.stdout, stderr=sys.stderr, bufsize=0, universal_newlines=True)
        log.info(f'Autoscale adding client ({proc.pid}, zone: {zone.name})')
        self._clients.append(proc)
        self.zones[id(proc)] = zone.id
        return proc

    def apply(self: LaunchActuator, desired: Mapping[ZoneRef, int]) -> int:
        """Start the clients each zone lacks to reach its `desired` count; return how many were started."""
        launched = self.launched()
        started = 0
        for zone, count in desired.items():
            for _ in range(count - launched.get(zone.id, 0)):
                self.launch_one(zone)
                started += 1
        return started

    def clean(self: LaunchActuator) -> List[Popen]:
        """Drop the processes that have exited, and return them."""
        exited = []
        for i, client in enumerate(self._clients):
            log.trace(f'Autoscale clean ({i+1}: {client.pid})')
            status = client.poll()
            if status is not None:
                exited.append(client)
                if status != 0:
                    log.warning(f'Autoscale client ({i+1}) exited with status {status}')
                else:
                    log.info(f'Autoscale client disconnected ({client.pid})')
        if exited:
            gone = {id(client) for client in exited}
            self.clients = [client for client in self._clients if id(client) not in gone]
        return exited


def is_mpi_launcher(program: str) -> bool:
    """
    True if `program`, by its base name, is a launcher Open MPI may provide.

    That is a name in :const:`MPI_LAUNCHERS`, or `mpirun`/`mpiexec` with a dotted suffix
    (`mpirun.openmpi`). Only the program itself counts, never a later word.

    Example:
        >>> [is_mpi_launcher(name) for name in ('/usr/bin/mpirun.openmpi', 'prterun', 'srun')]
        [True, True, False]
    """
    name = os.path.basename(program)
    return name in MPI_LAUNCHERS or name.split('.', 1)[0] in MPI_LAUNCHER_VARIANTS


def is_forward_option(word: str) -> bool:
    """True if `word` gives Open MPI's `-x` option (`-x`, `--x`, or either with `=value`)."""
    return word.split('=', 1)[0] in OMPI_FORWARD_FORMS


def ssh_env_exports(env: Mapping[str, str]) -> List[str]:
    """
    One `NAME=value` word per variable in `env`, for the remote shell of an SSH launch.

    :const:`AUTH_ENV` is left out by name before anything is quoted. Each value is quoted
    with :func:`shlex.quote`, so a POSIX shell reads it back as exactly that value, and
    no value can join or split words. A name that a shell cannot assign is left out.

    Example:
        >>> ssh_env_exports({'HYPERSHELL_A': 'a b', AUTH_ENV: 'secret', 'HYPERSHELL_B': 'b'})
        ["HYPERSHELL_A='a b'", 'HYPERSHELL_B=b']
    """
    words = []
    for name, value in env.items():
        if name == AUTH_ENV:
            continue
        if not SHELL_NAME.fullmatch(name):
            log.warning(f'Not exporting {name!r} to remote clients: not a shell variable name')
            continue
        words.append(f'{name}={shlex.quote(value)}')
    return words


def assigns_key(word: str) -> bool:
    """True if `word`, parsed as a shell parses it, holds a `NAME=value` whose name is :const:`AUTH_ENV`."""
    try:
        tokens = shlex.split(word)
    except ValueError:  # Unbalanced quotes: look at the word as it is
        tokens = word.split()
    return any(token.split('=', 1)[0] == AUTH_ENV and '=' in token for token in tokens)


def ssh_launch_args(args: Sequence[str]) -> List[str]:
    """
    The ssh arguments `args` without a bare `-n`, with one warning if any was dropped.

    An SSH launch writes the key to ssh's standard input, which `-n` would replace with
    `/dev/null`. The value of an option that takes one is kept as it is, even if it is `-n`.

    Example:
        >>> ssh_launch_args(['-n', '-o', 'BatchMode=yes'])
        ['-o', 'BatchMode=yes']
    """
    kept: List[str] = []
    dropped = 0
    words = iter(args)
    for word in words:
        if word == SSH_NO_INPUT:
            dropped += 1
            continue
        kept.append(word)
        if len(word) == 2 and word[0] == '-' and word[1] in SSH_OPTIONS_WITH_VALUE:
            value = next(words, None)
            if value is not None:
                kept.append(value)
    if dropped:
        log.warning(f'Ignoring {SSH_NO_INPUT!r} in the ssh arguments: client launches send the '
                    f'authentication key on ssh\'s standard input')
    return kept


def ssh_remote_script(remote_exe: str, options: List[str]) -> str:
    """
    The POSIX `sh` script an SSH launch runs on the remote host.

    It reads one line from standard input into :const:`AUTH_ENV` with shell builtins only,
    so the key is on no command line on the remote host either. If no key arrives it prints
    :const:`SSH_NO_KEY_MESSAGE` and exits 1 before any client starts. Otherwise it exports
    the variable and replaces itself with the client. The options are shell-quoted, so any
    template or name survives; `remote_exe` is not, so `~` and variables in it still expand.

    The launch passes the script as one single-quoted word to `sh -c`, so the remote
    account's login shell only has to parse that one word, whatever shell it is.

    Example:
        >>> script = ssh_remote_script('hs', ['-H', 'node-1', '-t', "echo {} it's"])
        >>> print(script.split('; exec ')[1])
        hs client -H node-1 -t 'echo {} it'"'"'s'
    """
    return (f'IFS= read -r {AUTH_ENV}; '
            f'case ${AUTH_ENV} in "") echo "{SSH_NO_KEY_MESSAGE}" >&2; exit 1;; esac; '
            f'export {AUTH_ENV}; '
            f'exec {remote_exe} client {shlex.join(options)}')


def plain_seconds(value: float) -> str:
    """
    Seconds written as a plain decimal, never with an exponent.

    A client of an earlier release reads the word with float(), and a client of this release with
    the duration forms, which take no exponent. A value str() writes without one is written the same.

    Example:
        >>> plain_seconds(-600.0), plain_seconds(0), plain_seconds(1e-05)
        ('-600.0', '0', '0.00001')
    """
    return format(Decimal(repr(value)), 'f')


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
               '-t', template, '-d', plain_seconds(delay_start), '-S', str(task_signalwait)]
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
