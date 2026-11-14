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
passes it to none of its tasks. Site clients dial the site server's bind address, or this host's own
name when it binds every interface (`hypershell.cluster.launch.advertised_host`).

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

Every fleet's autoscaler is a `SiteScaler`. Against an upstream server that publishes a site topology
(a server autoscaling with relay sites), it follows its zone's entry there: it launches what the
server asks for beyond the count the server is known to have planned on (at a new version, the count
the server saw; once the server has planned again on the clients running, that count), and nothing
more. It replaces a client that exits only when the server still asks for it, and never ends one, so
a lowered target, or a target of zero, takes effect as the zone's clients exit when idle. Without a
topology it is the fixed fleet above. Once the relay has read that a newer relay superseded it at its
site (`TopologyView.retired`), no fleet launches anything again, and none ends a client.
Every launch names its zone to the launcher (`{zone}` and `HYPERSHELL_ZONE`), in both modes.

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
import threading
import subprocess

# External libs
import tomlkit

# Internal libs
from hypershell.core.config import config, default, load_task_env
from hypershell.core.logging import Logger
from hypershell.core.tls import TLSConfig
from hypershell.client import DEFAULT_DELAY, DEFAULT_SIGNALWAIT
from hypershell.core.topology import Entry, TopologyReply
from hypershell.cluster.launch import ClientLaunch, advertised_host, client_launch
from hypershell.cluster.remote import (AutoScalingCluster, AutoScaler, AutoScalerThread, AutoScalerState,
                                       AutoScalerPhase, DEFAULT_REMOTE_EXE)
from hypershell.cluster.demand import ZoneRef

# Public interface
__all__ = ['site_cluster', 'site_launch', 'zone_launch', 'zone_fleet', 'SITE_POLICY', 'DEFAULT_SITE_PERIOD', 'fence_site',
           'scrub_environment', 'check_invocation', 'check_exports', 'CredentialFenceError', 'FENCED_PREFIX',
           'site_config_copy', 'end_site_clients', 'TopologyView', 'SiteScaler', 'site_scaler', 'launched_now']

# Initialize logger
log = Logger.with_name(__name__)


SITE_POLICY: Final[str] = 'fixed'
"""Autoscaling policy of the site: keep exactly the configured number of clients launched."""

DEFAULT_SITE_PERIOD: Final[int] = default.autoscale.period
"""Built-in seconds between checks on the site clients (one that exited is replaced at the next).

The relay passes the configured period (`autoscale.period`); this default never reads configuration.
"""

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
                 evict_after: int = DEFAULT_SITE_EVICT,
                 init_size: Optional[int] = None,
                 min_size: Optional[int] = None,
                 max_size: Optional[int] = None,
                 view: Optional[TopologyView] = None) -> AutoScalingCluster:
    """The relay's site cluster: a forever server on the relay database and its first zone's site clients.

    Without a topology the fleet is fixed: `init_size` clients at start and never fewer than
    `min_size` (each `size` when not given). With a topology in `view`, the fleet follows its zone's
    entry (see `SiteScaler`).

    Site clients are launched with `launcher` (empty runs them directly on this host) and dial the
    bind address in `address`, or this host by name when it is 0.0.0.0, so the relay must be
    reachable there from wherever they run. They serve `zone` (None is the default zone), which is
    named to the launcher as `{zone}` and `HYPERSHELL_ZONE` (see `zone_launch`). The site server
    evicts a site client silent for `evict_after` seconds.

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
                                 init_size=size if init_size is None else init_size,
                                 min_size=size if min_size is None else min_size,
                                 max_size=size if max_size is None else max_size,
                                 tls=tls, zone=zone, evict_after=evict_after, discover_zones=False)
    if cluster.server.confirm is not None:
        cluster.server.confirm.machine.strict = False
    # The first zone's clients are launched as every further zone's are, naming their zone.
    cluster.launch = zone_launch(cluster, zone, launcher=launcher, launcher_args=launcher_args,
                                 num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                                 template=template, client_timeout=client_timeout, tls=tls)
    cluster.autoscaler.machine = site_scaler(cluster.autoscaler.machine, cluster.launch, view)
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
    return client_launch(DEFAULT_REMOTE_EXE, (advertised_host(queue.host), queue.port), queue.auth,
                         launcher=shlex.split(launcher or ''), launcher_args=args,
                         num_threads=num_threads, bundlesize=bundlesize, bundlewait=bundlewait,
                         template=template, delay_start=DEFAULT_DELAY, task_signalwait=DEFAULT_SIGNALWAIT,
                         client_timeout=client_timeout, tls_enabled=tls is not None, zone=zone,
                         expose_zone=True)


def zone_fleet(launch: ClientLaunch, zone: Optional[str], size: int = 1,
               period: int = DEFAULT_SITE_PERIOD,
               init_size: Optional[int] = None,
               min_size: Optional[int] = None,
               max_size: Optional[int] = None,
               view: Optional[TopologyView] = None) -> AutoScalerThread:
    """The fleet of `zone`'s site clients, launched with `launch`.

    Without a topology it is fixed: `init_size` clients at start and one launched again whenever one
    exits while fewer than `min_size` run (each `size` when not given). With a topology in `view`, it
    follows its zone's entry (see `SiteScaler`).
    """
    fleet = AutoScalerThread(launch, policy=SITE_POLICY, period=period,
                             init_size=size if init_size is None else init_size,
                             min_size=size if min_size is None else min_size,
                             max_size=size if max_size is None else max_size,
                             own_zone=ZoneRef.from_name(zone))
    fleet.machine = site_scaler(fleet.machine, launch, view)
    fleet.name = f'hypershell-autoscaler-{zone}'
    return fleet


class TopologyView:
    """
    The site topology as this relay last read it from the upstream server, shared by its fleets.

    None until the relay reads one, and again whenever the upstream server publishes none (an
    earlier release, or a server not autoscaling with relay sites). A failed read leaves it as it
    was, so an outage never moves the relay's fleets between modes.

    Each read is stamped with `beat_at`, the time (monotonic) the heartbeat sent just before it was
    built, so a fleet can tell a read that follows a beat reporting its clients from one that does not.

    `retired` is set the first time a read says a newer relay at the site superseded this one, and is
    never cleared, whatever later reads say.
    """

    def __init__(self: TopologyView, reply: Optional[TopologyReply] = None) -> None:
        """Initialize with nothing read."""
        self._lock = threading.Lock()
        self._reply = reply
        self._beat_at = time.monotonic()
        self._retired = threading.Event()

    @property
    def retired(self: TopologyView) -> bool:
        """True once a read has said a newer relay at the site superseded this one (never cleared)."""
        return self._retired.is_set()

    def retire(self: TopologyView) -> bool:
        """Mark the relay superseded; true only for the call that set it."""
        with self._lock:
            if self._retired.is_set():
                return False
            self._retired.set()
            return True

    @property
    def reply(self: TopologyView) -> Optional[TopologyReply]:
        """The topology last read (None: none published)."""
        with self._lock:
            return self._reply

    @property
    def beat_at(self: TopologyView) -> float:
        """When the heartbeat before the last read was built (monotonic seconds)."""
        with self._lock:
            return self._beat_at

    def set(self: TopologyView, reply: Optional[TopologyReply], beat_at: Optional[float] = None) -> None:
        """Replace the topology with the one just read (None: none published), after a beat built at `beat_at` (now)."""
        with self._lock:
            self._reply = reply
            self._beat_at = time.monotonic() if beat_at is None else beat_at

    def read(self: TopologyView) -> Tuple[Optional[TopologyReply], float]:
        """The topology last read and when the heartbeat before it was built, together."""
        with self._lock:
            return self._reply, self._beat_at

    def assignment(self: TopologyView, zone: str) -> Optional[Tuple[str, Entry]]:
        """The version and the entry of `zone` (a zone it does not name is assigned none); None with no topology."""
        reply = self.reply
        return None if reply is None else (reply.version, reply.assignment(zone))


class SiteScaler(AutoScaler):
    """
    The autoscaler of one zone's fleet at a relay's site.

    Without a topology (no `view`, or nothing published upstream) it is the fixed fleet its parent
    runs. With one, the zone's entry decides, by one rule. The fleet launches `target - max(basis,
    live)` clients (none when that is not positive) each time it learns a new basis, and at no
    other time. `live` is the zone's clients launched here and still running. The basis is the
    count of them the server's current plan is known to rest on:

    - a version not yet applied gives its `seen`, the count the server planned from;
    - otherwise, while `live` stays below `target`, the server's own evidence gives `live` itself:
      the server has finished `RECHECKS` checks since a topology read that followed a beat
      reporting `live`, so its plan rests on that count, and it still asks for `target`.

    So a fleet never launches beyond the target in hand, and never on its own period alone: a
    client that exits is replaced only when the server, having planned on the exit, still asks for
    it, whether its plan came back as a new version or unchanged (a client that left before any
    beat counted it, or one the server counted that left before the relay read the plan made with
    it, changes nothing the server publishes). A target at or below what runs launches nothing, and
    no client is ever ended, so a lowered target, or a target of zero, takes effect as the zone's
    clients exit when idle. A new version is applied as soon as it is read, not only at the next
    period.

    One check is not enough evidence: it may have read the relay's beats before that one arrived.
    The count of checks comes with every read (`checks`), and each read is stamped with when the
    beat before it was built, so a read that follows no beat reporting `live` never counts.

    Once the view is retired (a newer relay superseded this one at its site), the fleet launches
    nothing, with a topology or without one.
    """

    RECHECKS: Final[int] = 2

    view: Optional[TopologyView]
    applied: Optional[str]
    held: Optional[int]
    since: float
    mark: Optional[int]

    def __init__(self: SiteScaler, launch: ClientLaunch, *, view: Optional[TopologyView] = None, **options: Any) -> None:
        """Initialize as the fleet's autoscaler, following `view` when it holds a topology."""
        super().__init__(launch, **options)
        self.view = view
        self.applied = None
        self.held = None
        self.since = 0.0
        self.mark = None

    def assignment(self: SiteScaler) -> Optional[Tuple[str, Entry]]:
        """The version and the entry of this fleet's zone, or None without a topology."""
        return None if self.view is None else self.view.assignment(self.own_zone.name)

    def evidence(self: SiteScaler) -> Tuple[int, float]:
        """The server's count of finished checks in the last read, and when the beat before that read was built."""
        reply, beat_at = (None, 0.0) if self.view is None else self.view.read()
        return (0 if reply is None else reply.checks), beat_at

    @property
    def retired(self: SiteScaler) -> bool:
        """Whether the relay was superseded at its site, so the fleet launches nothing."""
        return self.view is not None and self.view.retired

    def init(self: SiteScaler) -> AutoScalerState:
        """Launch the initial clients: the topology's, or without one the fixed fleet's (none once retired)."""
        if self.retired:
            self.phase = AutoScalerPhase.STEADY
            return AutoScalerState.WAIT
        assignment = self.assignment()
        if assignment is None:
            return super().init()
        self.phase = AutoScalerPhase.STEADY
        return self.reconcile(*assignment)

    def wait(self: SiteScaler) -> AutoScalerState:
        """Wait for the period, or check at once when a version not yet applied has been read (never once retired)."""
        if self.phase is AutoScalerPhase.STEADY and not self.retired:
            assignment = self.assignment()
            if assignment is not None and assignment[0] != self.applied:
                return AutoScalerState.CHECK
        return super().wait()

    def check_zones(self: SiteScaler) -> AutoScalerState:
        """Follow the topology's entry, or without one keep the fixed fleet (launch nothing once retired)."""
        if self.retired:
            return AutoScalerState.WAIT
        assignment = self.assignment()
        if assignment is None:
            return super().check_zones()
        return self.reconcile(*assignment)

    def reconcile(self: SiteScaler, version: str, entry: Entry) -> AutoScalerState:
        """Launch `target - max(basis, live)` clients if a new basis is known (see the class), else nothing."""
        if self.retired:
            return AutoScalerState.WAIT
        live = self.launched().get(self.own_zone.id, 0)
        planned = self.planned_on(live)
        if version != self.applied:
            self.applied = version
            count = max(0, entry.target - max(entry.seen, live))
            log.info(f'Topology {version} (zone: {self.own_zone.name}, target: {entry.target}, seen: {entry.seen}, '
                     f'launched: {live}) - launching {count}')
        elif planned and live < entry.target:
            count = entry.target - live
            log.info(f'Topology {version} (zone: {self.own_zone.name}, target: {entry.target}, launched: {live}): '
                     f'the server planned again on {live} running - applying again, launching {count}')
        else:
            return AutoScalerState.WAIT
        if count == 0:
            return AutoScalerState.WAIT
        self.held, self.mark = None, None
        self.pending = {self.own_zone: live + count}
        return AutoScalerState.SCALE

    def planned_on(self: SiteScaler, live: int) -> bool:
        """Whether the server has finished `RECHECKS` checks since a read that followed a beat reporting `live`."""
        checks, beat_at = self.evidence()
        if live != self.held:
            self.held, self.since, self.mark = live, time.monotonic(), None
            return False
        if self.mark is None or checks < self.mark:  # Not yet marked, or a new server counting afresh.
            self.mark = checks if beat_at > self.since else None
            return False
        return checks >= self.mark + self.RECHECKS


def site_scaler(machine: AutoScaler, launch: ClientLaunch, view: Optional[TopologyView]) -> SiteScaler:
    """A `SiteScaler` in place of a fleet's autoscaler `machine`, with its settings, launching with `launch`."""
    return SiteScaler(launch, view=view, policy=machine.policy.name.lower(), factor=machine.factor,
                      period=machine.period, init_size=machine.init_size, min_size=machine.min_size,
                      max_size=machine.max_size, own_zone=machine.own_zone, attempts=machine.attempts,
                      eager=machine.eager, registered=machine.registered, scheduler_group=machine.scheduler_group)


def launched_now(machine: AutoScaler) -> int:
    """The clients `machine` launched that are still running (read from any thread)."""
    return sum(1 for proc in list(machine.clients) if proc.poll() is None)


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
