# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The relay's site: its local server and the fleet of clients it keeps launched there.

The site is a stock autoscaling cluster with a fixed policy, so it mints a key for this run only,
launches the site clients with it, and launches another whenever one exits. It schedules nothing of
its own (no source; its server runs forever on the relay database) and never retries: the upstream
server alone retries tasks, so a failure at the site is a completed row like any other.

Every site client is launched through a `ClientLaunch` (see `hypershell.cluster.launch.client_launch`):
the site key is handed over in the client's environment, never on its command line, and a client
passes it to none of its tasks.

A relay serving several zones keeps one fleet per zone, all served by the one site server. The first
zone's fleet is the cluster's own; each further zone's (`zone_fleet`) is a fixed-policy autoscaler
whose clients are launched exactly as the cluster's are, with the same site key, but serving that
zone (`zone_launch`), and with its own launcher and size if the zone names them.

The upstream key stays with the relay. Site clients inherit the relay's environment when they are
launched, and tasks inherit their client's, so before the first launch `fence_site` refuses a client
invocation or an exported task variable that carries the key, and removes from the relay's own
environment every relay setting and every variable that would hand the key on. A configuration file
named by `HYPERSHELL_CONFIG_FILE` that holds relay settings or the key is not handed on: the site gets
a private copy without them, so its clients keep the file's other settings (`[server.tls]` among them).

The relay owns the clients it launches: `end_site_clients` ends any still running when it stops.

Example:
    >>> from hypershell.relay.site import site_cluster, fence_site
    >>> cluster = site_cluster(address=('0.0.0.0', 50002), size=4, launcher='srun')
    >>> removed = fence_site(cluster, key=upstream_key)
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Optional, Sequence, Tuple, Final, Mapping, MutableMapping

# Standard libs
import os
import time
import shlex
import tomllib
import subprocess

# External libs
import tomlkit

# Internal libs
from hypershell.core.config import config, load_task_env
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.tls import TLSConfig
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.cluster.launch import ClientLaunch, client_launch
from hypershell.cluster.remote import AutoScalingCluster, AutoScalerThread, DEFAULT_REMOTE_EXE

# Public interface
__all__ = ['site_cluster', 'site_launch', 'zone_launch', 'zone_fleet', 'SITE_POLICY', 'DEFAULT_SITE_PERIOD', 'fence_site',
           'scrub_environment', 'check_invocation', 'check_exports', 'CredentialFenceError', 'FENCED_PREFIX',
           'site_config_copy', 'end_site_clients']

# Initialize logger
log = Logger.with_name(__name__)


SITE_POLICY: Final[str] = 'fixed'
"""Autoscaling policy of the site: keep exactly the configured number of clients launched."""

DEFAULT_SITE_PERIOD: Final[int] = config.autoscale.period
"""Default seconds between checks on the site clients (one that exited is replaced at the next)."""

DEFAULT_SITE_EVICT: Final[int] = config.server.evict
"""Default seconds the site server waits on a silent site client before evicting it (`[server] evict`)."""


def site_cluster(address: Tuple[str, int],
                 bundlesize: int = 1,
                 bundlewait: int = 5,
                 poll: int = 1,
                 tls: Optional[TLSConfig] = None,
                 zone: Optional[str] = None,
                 size: int = 1,
                 launcher: Optional[str] = None,
                 launcher_args: Optional[List[str]] = None,
                 num_threads: int = 1,
                 template: str = '{}',
                 client_timeout: Optional[int] = None,
                 period: int = DEFAULT_SITE_PERIOD,
                 evict_after: int = DEFAULT_SITE_EVICT) -> AutoScalingCluster:
    """The relay's site cluster: a forever server on the relay database and `size` site clients.

    Site clients are launched with `launcher` (empty runs them directly on this host) and dial this
    host by name, so the relay must be reachable from wherever they run. They serve `zone` (None is
    the default zone). The site server evicts a site client silent for `evict_after` seconds.

    The site server's confirmations are not strict: a site client confirms a bundle before it runs
    it, but the confirmation and the results reach the site server on separate threads, so a result
    can be written first, and the returner then moves the completed row to the outbox. A
    confirmation naming that row finds it gone; it is settled, as a complete row is, and the rest
    of its bundle is still recorded.
    """
    cluster = AutoScalingCluster(source=None, num_threads=num_threads, template=template,
                                 bundlesize=bundlesize, bundlewait=bundlewait, bind=address,
                                 launcher=launcher or '', launcher_args=launcher_args or None,
                                 max_retries=0, poll=poll, client_timeout=client_timeout,
                                 policy=SITE_POLICY, period=period,
                                 init_size=size, min_size=size, max_size=size,
                                 tls=tls, zone=zone, evict_after=evict_after, discover_zones=False)
    if cluster.server.confirm is not None:
        cluster.server.confirm.machine.strict = False
    return cluster


def site_launch(cluster: AutoScalingCluster) -> ClientLaunch:
    """The invocation every site client of `cluster` is launched with: its command line holds no key."""
    return cluster.launch


def zone_launch(cluster: AutoScalingCluster,
                zone: Optional[str],
                launcher: Optional[str] = None,
                launcher_args: Optional[List[str]] = None,
                num_threads: int = 1,
                bundlesize: int = 1,
                bundlewait: int = 5,
                template: str = '{}',
                client_timeout: Optional[int] = None,
                tls: Optional[TLSConfig] = None) -> ClientLaunch:
    """The invocation of a site client of `cluster` serving `zone`, built as the cluster builds its own.

    The site key and the address the clients dial are the cluster's; every other value must be the
    one `site_cluster` was given (with `launcher` and `launcher_args` the zone's own, if it has any),
    so a client of this zone differs from one of the cluster's only in the zone it serves.
    """
    queue = cluster.server.queue.config
    args = [arg for group in (launcher_args or []) for arg in shlex.split(group)]
    return client_launch(DEFAULT_REMOTE_EXE, (HOSTNAME, queue.port), queue.auth,
                         launcher=shlex.split(launcher or ''), launcher_args=args,
                         num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                         template=template, delay_start=DEFAULT_DELAY, task_signalwait=DEFAULT_SIGNALWAIT,
                         client_timeout=client_timeout, tls_enabled=tls is not None, zone=zone)


def zone_fleet(launch: ClientLaunch, zone: Optional[str], size: int = 1,
               period: int = DEFAULT_SITE_PERIOD) -> AutoScalerThread:
    """A fixed fleet of `size` site clients launched with `launch`, one launched again whenever one exits."""
    fleet = AutoScalerThread(launch, policy=SITE_POLICY, period=period, init_size=size, min_size=size, max_size=size)
    fleet.name = f'hypershell-autoscaler-{zone}'
    return fleet


FENCED_PREFIX: Final[str] = 'HYPERSHELL_RELAY_'
"""Environment variables of relay settings, which never reach the site."""

CONFIG_FILE_VAR: Final[str] = 'HYPERSHELL_CONFIG_FILE'
"""Environment variable naming the one configuration file a process reads."""


class CredentialFenceError(RuntimeError):
    """The upstream key would reach the site's clients or their tasks."""


_DROPPED: Final[object] = object()
"""Marks a configuration value left out of the site's copy."""


def without_key(value: Any, key: str) -> Any:
    """`value` (parsed TOML) with every string holding `key`, and every entry named with it, left out."""
    if isinstance(value, dict):
        kept = {name: without_key(item, key) for name, item in value.items() if not (key and key in name)}
        return {name: item for name, item in kept.items() if item is not _DROPPED}
    if isinstance(value, list):
        return [item for item in (without_key(item, key) for item in value) if item is not _DROPPED]
    if isinstance(value, str) and key and key in value:
        return _DROPPED
    return value


def site_config_copy(path: str, key: str, target: Optional[str]) -> Optional[str]:
    """The configuration file the site should be given in place of the one at `path`.

    That is `path` itself when the file holds neither relay settings nor the key. Otherwise a copy
    without the `[relay]` table (and so `[relay.upstream]`) and without any value holding the key is
    written to `target`, readable by its owner only, and `target` is returned. None if `path` cannot
    be read or parsed, or a copy is needed and cannot be written.
    """
    try:
        with open(os.path.expanduser(path), mode='rb') as stream:
            original = tomllib.load(stream)
    except (OSError, ValueError) as error:
        log.warning(f'Could not read configuration file for the site ({error.__class__.__name__}) - '
                    f'site clients will run without it')
        return None
    copy = without_key({name: value for name, value in original.items() if name != 'relay'}, key)
    if copy == original:
        return path
    if not target:
        log.warning('Nowhere to write a copy of the configuration file for the site - '
                    'site clients will run without it')
        return None
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, mode='w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(tomlkit.dumps(copy))
    except OSError as error:
        log.warning(f'Could not write a copy of the configuration file for the site ({error}) - '
                    f'site clients will run without it')
        return None
    return target


def scrub_environment(key: str, environ: Optional[MutableMapping[str, str]] = None,
                      site_config: Optional[str] = None) -> List[str]:
    """Remove from `environ` (the process environment by default) what must not reach the site.

    Removed: every relay setting (`HYPERSHELL_RELAY_*`) and any variable whose name or value contains
    the key. The configuration file variable, when the file it names holds relay settings or the key,
    is pointed at a copy without them written to `site_config` (see `site_config_copy`); it is removed
    only if no such copy can be made. Returns the names removed, sorted; values are never returned or
    logged.
    """
    environ = os.environ if environ is None else environ
    removed = []
    for name, value in list(environ.items()):
        if name.startswith(FENCED_PREFIX) or (key and (key in name or key in value)):
            del environ[name]
            removed.append(name)
        elif name == CONFIG_FILE_VAR and value:
            given = site_config_copy(value, key, site_config)
            if given is None:
                del environ[name]
                removed.append(name)
            elif given != value:
                environ[name] = given
                log.debug('Site configuration file is a copy without relay settings')
    return sorted(removed)


def check_invocation(launch: ClientLaunch, key: str) -> None:
    """Raise `CredentialFenceError` if the site client invocation carries the key.

    That is any argument of its command line, or any variable it adds to a client's environment
    (which holds the site key, never the upstream one).
    """
    added = [text for item in launch.env.items() for text in item]
    if key and any(key in str(arg) for arg in [*launch.argv, *added]):
        raise CredentialFenceError('Upstream key found in the site client command line '
                                   '(launcher, launcher arguments or template) - not launching site clients')


def check_exports(exports: Mapping[str, str], key: str) -> None:
    """Raise `CredentialFenceError` if a task variable exported from configuration carries the key."""
    if key and any(key in str(name) or key in str(value) for name, value in exports.items()):
        raise CredentialFenceError('Upstream key found in an exported task variable ([export]) '
                                   '- not launching site clients')


def fence_site(cluster: AutoScalingCluster, key: str,
               environ: Optional[MutableMapping[str, str]] = None,
               exports: Optional[Mapping[str, str]] = None,
               site_config: Optional[str] = None,
               launches: Sequence[ClientLaunch] = ()) -> List[str]:
    """Keep the upstream key from the site before its first client is launched.

    Every client invocation (the cluster's, and `launches`, those of the fleets of further zones) and
    the exported task variables are checked (a leak there is refused, with
    `CredentialFenceError`); the environment the clients will inherit is scrubbed, and a configuration
    file it names is replaced by a copy written to `site_config` if need be. Read the key, and anything
    configured through the environment, before calling this: relay settings are gone after. Returns
    the names of the variables removed.
    """
    for launch in (site_launch(cluster), *launches):
        check_invocation(launch, key)
    check_exports(load_task_env() if exports is None else exports, key)
    removed = scrub_environment(key, environ, site_config=site_config)
    if removed:
        shown = ['<redacted>' if key and key in name else name for name in removed]
        log.debug(f'Removed from site environment: {", ".join(shown)}')
    return removed


def end_site_clients(cluster: AutoScalingCluster, grace: float, patience: float = 0,
                     fleets: Sequence[AutoScalerThread] = ()) -> int:
    """End every site client the cluster, or one of `fleets`, launched that is still running; return how many.

    Each is given `patience` seconds to exit by itself, then SIGTERM, then SIGKILL if it is still
    running `grace` seconds later. Stop the autoscalers first, or they may launch more.
    """
    launched = [proc for autoscaler in (cluster.autoscaler, *fleets) for proc in list(autoscaler.machine.clients)]
    running = [proc for proc in launched if proc.poll() is None]
    deadline = time.monotonic() + patience
    for proc in running:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    running = [proc for proc in running if proc.poll() is None]
    for proc in running:
        log.debug(f'Terminating site client ({proc.pid})')
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    for proc in running:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            log.warning(f'Killing site client ({proc.pid}) still running {grace} seconds after SIGTERM')
            try:
                proc.kill()
                proc.wait(timeout=grace)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
    return len(running)
