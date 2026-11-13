# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Autoscaling with relay sites: plan per-site, per-zone client targets from per-zone demand.

A server autoscaling with sites launches nothing. It reads each zone's load as the zone autoscaler
does, and splits each zone's clients over the live relay sites that serve the zone, within the
bounds each site advertises. The planning functions here are pure: they take the loads and the
relays' latest advertisements as inputs and return targets, so every rule is testable on its own.

The :class:`TopologyPublisher` runs those functions at each check and publishes a new version of
the topology on the server's dispatcher whenever the plan changes; relays fetch their own entries
from there. :class:`AutoScalingServer` runs it beside a server in forever mode.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, IO, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

# Standard libs
import time
from enum import Enum
from datetime import datetime, timedelta
from functools import cached_property
from dataclasses import dataclass, replace

# Internal libs
from hypershell.core.config import config, blame, ConfigurationError, PARAM_UNSET
from hypershell.core.fsm import State, StateMachine
from hypershell.core.thread import Thread, join_waking
from hypershell.core.tls import TLSConfig
from hypershell.core.logging import Logger, INSTANCE
from hypershell.core.dispatch import Dispatcher
from hypershell.core.topology import Bounds, Entry, Topology, SiteRecord, SiteRegistry
from hypershell.cluster.demand import (AutoScalerPolicy, ZoneRef, ZoneLoad, ScalingKnobs, zone_demand,
                                       scheduler_group, read_zone_loads)
from hypershell.server import (ServerThread, DEFAULT_BIND, DEFAULT_PORT, DEFAULT_AUTH, DEFAULT_BUNDLESIZE,
                               DEFAULT_ATTEMPTS, DEFAULT_SERVER_POLL, DEFAULT_EVICT)

# Public interface
__all__ = ['Distribution', 'parse_distribution', 'parse_sites',
           'SiteZone', 'SiteView', 'Duplicate', 'site_views', 'plan_sites', 'entries',
           'TopologyPublisherState', 'TopologyPublisher', 'TopologyPublisherThread', 'AutoScalingServer',
           'serve_with_sites']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


class Distribution(Enum):
    """How a zone's clients are split over the sites that serve it."""
    SPREAD = 1  # Even out across live sites, as peers.
    FILL = 2    # Saturate sites in preference order, each up to its max, before the next.


def parse_distribution(value: Any) -> Distribution:
    """The distribution named by `value` (``spread`` or ``fill``, any case); ValueError otherwise."""
    if isinstance(value, Distribution):
        return value
    if isinstance(value, str):
        try:
            return Distribution[value.strip().upper()]
        except KeyError:
            pass
    raise ValueError(f'Unknown site distribution {value!r} (expected \'spread\' or \'fill\')')


def _site_name(value: Any) -> bool:
    """True for a value that names a site: text, or a number the environment read from text."""
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def parse_sites(value: Any) -> List[str]:
    """
    Site names, in order, from a list or one comma-separated string; nothing when unset.

    Blank names are skipped and a repeated name keeps its first place.
    """
    if value is None or value == PARAM_UNSET:
        return []
    if isinstance(value, str):
        names = value.split(',')
    elif _site_name(value):
        names = [str(value)]
    elif isinstance(value, (list, tuple)) and all(_site_name(name) for name in value):
        names = [str(name) for name in value]
    else:
        raise ValueError(f'Sites must be a list of names or a comma-separated string (given {value!r})')
    return list(dict.fromkeys(name.strip() for name in names if name.strip()))


@dataclass(frozen=True)
class SiteZone:
    """One zone at one site: the bounds its holder advertises, its clients, and whether `init` still holds."""

    zone: ZoneRef
    bounds: Bounds
    running: int    # Launched clients of this zone at this site, over all its live identities.
    starting: bool  # The holder's `init` is still a floor.


@dataclass(frozen=True)
class SiteView:
    """A live site: its name, its first-registration rank on this server, and the zones it serves by id."""

    name: str
    rank: int
    zones: Mapping[str, SiteZone]


@dataclass(frozen=True)
class Duplicate:
    """
    Two live relays advertising one site: the newer holds the site's entries, the older is assigned none.

    With `relay`, they are two relay processes, told apart by the instance key each advertises, and
    `holder` and `older` are the first-registered identity of each. Otherwise they are two identities
    advertising one zone at the site from relays that advertise no instance key.
    """

    site: str
    holder: SiteRecord
    older: SiteRecord
    relay: bool = False


# Every live record of one site in one zone, with the record holding that site-zone (None for none).
Held = Tuple[Optional[SiteRecord], List[SiteRecord]]


def _arrange(records: Iterable[SiteRecord]) -> Tuple[Dict[Tuple[str, str], Held], List[Duplicate]]:
    """
    Live records by site and zone id, each with its holder, and every duplicate.

    At each site, the relay instance whose first identity registered most recently holds every zone
    it serves, and every identity of an older instance there is assigned none, so a zone that only an
    older instance serves is left to the other sites. A record that advertises no instance key is held
    per zone: of those and the holding instance's identity in a zone, the most recently registered one
    holds the zone.
    """
    by_site: Dict[str, List[SiteRecord]] = {}
    for record in sorted(records, key=lambda record: record.registered):
        by_site.setdefault(record.site, []).append(record)
    arranged: Dict[Tuple[str, str], Held] = {}
    duplicates: List[Duplicate] = []
    for site, group in by_site.items():
        first: Dict[str, SiteRecord] = {}
        for record in group:
            if record.instance is not None:
                first.setdefault(record.instance, record)
        newest = max(first, key=lambda instance: first[instance].registered) if first else None
        duplicates.extend(Duplicate(site, holder=first[newest], older=record, relay=True)
                          for instance, record in first.items() if instance != newest)
        zones: Dict[str, List[SiteRecord]] = {}
        for record in group:
            zones.setdefault(ZoneRef.from_name(record.zone).id, []).append(record)
        for zone, members in zones.items():
            eligible = sorted((record for record in members if record.instance in (None, newest)),
                              key=lambda record: record.registered, reverse=True)
            holder = eligible[0] if eligible else None
            duplicates.extend(Duplicate(site, holder=holder, older=record) for record in eligible[1:])
            arranged[(site, zone)] = (holder, members)
    return arranged, duplicates


def site_views(records: Iterable[SiteRecord]) -> Tuple[List[SiteView], List[Duplicate]]:
    """
    Group live records into sites, with every duplicated site.

    The holder of each site-zone (see `_arrange`: the most recently registered relay at the site)
    supplies its bounds and its own launched clients as the running count. The clients an older,
    superseded relay last advertised do not count against the site: a relay restarted after a crash
    is planned at once rather than after its predecessor's eviction. If the older relay is in fact
    still running, its clients run beside the holder's until they leave by idle exit.
    """
    arranged, duplicates = _arrange(records)
    zones: Dict[str, Dict[str, SiteZone]] = {}
    ranks: Dict[str, int] = {}
    for (site, zone), (holder, members) in arranged.items():
        if holder is None:
            continue
        ranks[site] = holder.rank
        zones.setdefault(site, {})[zone] = SiteZone(ZoneRef.from_name(holder.zone), holder.bounds,
                                                    running=holder.clients, starting=holder.init_held)
    views = [SiteView(site, ranks[site], zones[site]) for site in sorted(zones, key=lambda name: (ranks[name], name))]
    return views, duplicates


def _preference(sites: Sequence[SiteView], order: Sequence[str]) -> List[SiteView]:
    """Sites in preference order: those listed in `order` as listed, then the rest by rank."""
    listed = {name: place for place, name in enumerate(order)}
    return sorted(sites, key=lambda site: (0, listed[site.name], 0, '') if site.name in listed else
                  (1, 0, site.rank, site.name))


def plan_sites(loads: Mapping[str, ZoneLoad], knobs: ScalingKnobs, sites: Sequence[SiteView], *,
               distribution: Distribution = Distribution.SPREAD,
               order: Sequence[str] = ()) -> Dict[str, Dict[str, int]]:
    """
    Site name -> zone id -> client target, for every zone some site serves.

    Each site starts from its base: what it runs, lifted to its floor (`min`, or `init` while it is
    starting), and never above its `max`, so a site running more than its `max` gets no launch and
    sheds clients only as they exit. A zone's demand is the zone autoscaler's own rule, with the
    zone's total capped at the sum of its sites' `max`; growth a floor already adds counts toward
    it. What demand still asks for is placed one client at a time on a site with room: the least
    loaded under `spread`, the first in preference order under `fill`. Preference order is the sites
    named in `order`, as named, then the others by rank. A zone with work that no site serves is
    left out.
    """
    preferred = _preference(sites, order)
    place = {site.name: index for index, site in enumerate(preferred)}
    targets: Dict[str, Dict[str, int]] = {}
    for zone in sorted({zone for site in sites for zone in site.zones}):
        serving = [site for site in preferred if zone in site.zones]
        here = {site.name: site.zones[zone] for site in serving}
        base = {}
        for name, sz in here.items():
            floor = max(sz.bounds.min, sz.bounds.init) if sz.starting else sz.bounds.min
            base[name] = min(sz.bounds.max, max(sz.running, floor))
        running = sum(sz.running for sz in here.values())
        cap = sum(sz.bounds.max for sz in here.values())
        ref = next(iter(here.values())).zone
        load = loads.get(zone) or ZoneLoad(ref, remaining=0, retryable=0, pressure=None)
        demand = zone_demand(load, running, replace(knobs, init_size=0, min_size=0, max_size=cap),
                             own=False, initial=False)
        lifted = sum(max(0, base[name] - sz.running) for name, sz in here.items())
        extra = max(0, (demand - running) - lifted)
        for _ in range(extra):
            room = [name for name, sz in here.items() if base[name] < sz.bounds.max]
            if not room:
                break
            if distribution is Distribution.SPREAD:
                chosen = min(room, key=lambda name: (base[name], place[name]))
            else:
                chosen = min(room, key=lambda name: place[name])
            base[chosen] += 1
        for name, target in base.items():
            targets.setdefault(name, {})[zone] = target
    return targets


def entries(targets: Mapping[str, Mapping[str, int]], records: Iterable[SiteRecord]) -> Dict[str, Entry]:
    """
    The entry of each live identity, from per-site, per-zone targets.

    The holder of a site's zone is asked for the site's target; every other identity is assigned
    none, so its clients retire as they idle out. Each entry's `seen` is the identity's own launched
    count.
    """
    arranged, _ = _arrange(records)
    result: Dict[str, Entry] = {}
    for (site, zone), (holder, members) in arranged.items():
        result.update({record.identity: Entry(record.zone, 0, record.clients)
                       for record in members if record is not holder})
        if holder is not None:
            result[holder.identity] = Entry(holder.zone, targets.get(site, {}).get(zone, 0), holder.clients)
    return result


class TopologyPublisherState(State, Enum):
    """Finite states for TopologyPublisher."""
    START = 0
    WAIT = 1
    CHECK = 2
    PUBLISH = 3
    FINAL = 4
    HALT = 5


class TopologyPublisher(StateMachine):
    """
    Plan the relay sites' client targets each period and publish them when they change.

    Each check reads every zone's load in the group the scheduler loads from (as the scheduler
    reports it through `scheduler_group`, else its start rule), takes a snapshot of the relay
    advertisements in `registry`, and plans per site and zone. A plan whose entries differ from the
    last published ones is published on `dispatcher` under the next version; either way the
    topology on the dispatcher carries the count of checks finished, so a relay can tell that the
    server planned again. The publisher never launches or terminates a client and never ends by
    itself.
    """

    dispatcher: Dispatcher
    registry: SiteRegistry
    policy: AutoScalerPolicy
    factor: float
    distribution: Distribution
    order: List[str]
    attempts: int
    eager: bool
    scheduler_group: Optional[Callable[[], Optional[int]]]

    count: int
    checks: int
    topology: Optional[Topology]
    published: Optional[Dict[str, Entry]]
    pending: Optional[Dict[str, Entry]]
    targets: Dict[str, Dict[str, int]]
    views: List[SiteView]
    warned: Set[Tuple[str, str]]
    last_check: datetime
    wait_check: timedelta

    state: TopologyPublisherState = TopologyPublisherState.START
    states = TopologyPublisherState

    def __init__(self: TopologyPublisher,
                 dispatcher: Dispatcher,
                 registry: SiteRegistry,
                 policy: str = 'fixed',
                 factor: float = 1.0,
                 period: int = 60,
                 distribution: Distribution = Distribution.SPREAD,
                 order: Sequence[str] = (),
                 attempts: int = 1,
                 eager: bool = False,
                 scheduler_group: Optional[Callable[[], Optional[int]]] = None) -> None:
        """Initialize with the server's dispatcher, its site registry and the scaling parameters."""
        self.dispatcher = dispatcher
        self.registry = registry
        self.policy = AutoScalerPolicy.from_name(policy)
        self.factor = factor
        self.distribution = parse_distribution(distribution)
        self.order = parse_sites(list(order))
        self.attempts = attempts
        self.eager = eager
        self.scheduler_group = scheduler_group
        self.count = 0
        self.checks = 0
        self.topology = None
        self.published = None
        self.pending = None
        self.targets = {}
        self.views = []
        self.warned = set()
        self.last_check = datetime.now()
        self.wait_check = timedelta(seconds=period)

    @property
    def knobs(self: TopologyPublisher) -> ScalingKnobs:
        """The demand knobs; sizes come from the sites' advertised bounds, never from the server."""
        return ScalingKnobs(self.policy, self.factor, init_size=0, min_size=0, max_size=0)

    @cached_property
    def actions(self: TopologyPublisher) -> Dict[TopologyPublisherState, Callable[[], TopologyPublisherState]]:
        return {
            TopologyPublisherState.START: self.start,
            TopologyPublisherState.WAIT: self.wait,
            TopologyPublisherState.CHECK: self.check,
            TopologyPublisherState.PUBLISH: self.publish,
            TopologyPublisherState.FINAL: self.finalize,
        }

    def start(self: TopologyPublisher) -> TopologyPublisherState:
        """Check at once."""
        order = f', sites: {", ".join(self.order)}' if self.order else ''
        log.info(f'Autoscale start (policy: {self.policy.name.lower()}, '
                 f'distribution: {self.distribution.name.lower()}{order})')
        return TopologyPublisherState.CHECK

    def wait(self: TopologyPublisher) -> TopologyPublisherState:
        """Wait for the period, a second at a time."""
        waited = datetime.now() - self.last_check
        if waited > self.wait_check:
            return TopologyPublisherState.CHECK
        log.trace(f'Autoscale wait ({timedelta(seconds=round(waited.total_seconds()))})')
        time.sleep(1)
        return TopologyPublisherState.WAIT

    def current_group(self: TopologyPublisher) -> int:
        """The task group the scheduler loads from."""
        reported = None if self.scheduler_group is None else self.scheduler_group()
        return scheduler_group(reported, attempts=self.attempts, eager=self.eager)

    def check(self: TopologyPublisher) -> TopologyPublisherState:
        """Plan every zone over the live sites; publish if the plan changed, else count the check under the same version."""
        self.last_check = datetime.now()
        group = self.current_group()
        loads = read_zone_loads(group, self.knobs, own=None, discover=True,
                                attempts=self.attempts, eager=self.eager)
        records = self.registry.snapshot()
        views, duplicates = site_views(records)
        self.warn_duplicates(duplicates)
        self.warn_dropped(views)
        targets = plan_sites(loads, self.knobs, views, distribution=self.distribution, order=self.order)
        self.log_check(loads, views, group)
        self.views, self.targets = views, targets
        planned = entries(targets, records)
        self.checks += 1
        if planned != self.published:
            self.pending = planned
            return TopologyPublisherState.PUBLISH
        self.topology = replace(self.topology, checks=self.checks)
        self.dispatcher.publish(self.topology)
        return TopologyPublisherState.WAIT

    def publish(self: TopologyPublisher) -> TopologyPublisherState:
        """Publish the pending entries under the next version."""
        self.count += 1
        version = f'{INSTANCE}:{self.count}'
        self.topology = Topology(version, dict(self.pending), checks=self.checks)
        self.dispatcher.publish(self.topology)
        self.published, self.pending = self.pending, None
        log.info(f'Topology {version} published ({len(self.published)} relay identities)')
        for view in self.views:
            for zone, sz in view.zones.items():
                log.info(f'Topology {version} (site: {view.name}, zone: {sz.zone.name}, '
                         f'target: {self.targets.get(view.name, {}).get(zone, 0)}, launched: {sz.running})')
        return TopologyPublisherState.WAIT

    def warn_duplicates(self: TopologyPublisher, duplicates: List[Duplicate]) -> None:
        """Warn once for each pair of relays advertising one site (or, for relays with no instance key, one site's zone)."""
        for dup in duplicates:
            older = dup.older.instance if dup.relay else dup.older.identity
            newer = dup.holder.instance if dup.relay else dup.holder.identity
            if (older, newer) not in self.warned:
                self.warned.add((older, newer))
                zone = '' if dup.relay else f' (zone: {dup.holder.zone})'
                log.warning(f'Site {dup.site} advertised by two relays '
                            f'(older: {older} on {dup.older.host}, newer: {newer} on {dup.holder.host}) '
                            f'- the newer holds its entries{zone}')

    def warn_dropped(self: TopologyPublisher, views: List[SiteView]) -> None:
        """Warn for each site of the last plan that has no live relay left."""
        live = {view.name for view in views}
        for view in self.views:
            if view.name not in live:
                log.warning(f'Site {view.name} left the topology (no live relay) - '
                            f'its share is planned over the remaining sites')

    def log_check(self: TopologyPublisher, loads: Mapping[str, ZoneLoad], views: List[SiteView], group: int) -> None:
        """Log each zone's reading: its sites, its launched clients and its work."""
        dynamic = self.policy is AutoScalerPolicy.DYNAMIC
        served = {zone: [view for view in views if zone in view.zones] for view in views for zone in view.zones}
        for zone, load in loads.items():
            if zone not in served:
                log.debug(f'Autoscale check (zone: {load.zone.name}, no live site serves it, '
                          f'tasks: {load.work} in group {group if load.group is None else load.group})')
        for zone, sites in served.items():
            ref = sites[0].zones[zone].zone
            load = loads.get(zone) or ZoneLoad(ref, remaining=0, retryable=0, pressure=None)
            pressure = '' if not dynamic else (
                ', pressure: unknown' if load.pressure is None else f', pressure: {load.pressure:.2f}')
            running = sum(view.zones[zone].running for view in sites)
            log.debug(f'Autoscale check (zone: {ref.name}, sites: {len(sites)}, clients: {running}, '
                      f'tasks: {load.work} in group {group if load.group is None else load.group}{pressure})')

    @staticmethod
    def finalize() -> TopologyPublisherState:
        """Finalize."""
        log.debug('Done (topology publisher)')
        return TopologyPublisherState.HALT


class TopologyPublisherThread(Thread):
    """Run TopologyPublisher within dedicated thread."""

    def __init__(self: TopologyPublisherThread, dispatcher: Dispatcher, registry: SiteRegistry, **options) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-topology')
        self.machine = TopologyPublisher(dispatcher, registry, **options)

    def run_with_exceptions(self: TopologyPublisherThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: TopologyPublisherThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (topology publisher)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


def configured_distribution() -> Distribution:
    """The `[autoscale] distribution` setting, refused naming its source when unknown."""
    try:
        return parse_distribution(config.autoscale.distribution)
    except ValueError as error:
        raise ConfigurationError(f'{error} ({blame(config, "autoscale", "distribution")})') from error


def configured_sites() -> List[str]:
    """The `[autoscale] sites` setting, refused naming its source when not a list of names."""
    try:
        return parse_sites(config.autoscale.sites)
    except ValueError as error:
        raise ConfigurationError(f'{error} ({blame(config, "autoscale", "sites")})') from error


class AutoScalingServer(Thread):
    """
    Run a server in forever mode that publishes a site topology for its relays, and launches nothing.

    Relays connected with the server's key advertise their zones and bounds on their heartbeats; the
    server records them, and its topology publisher plans per-site, per-zone client targets from
    the zones' loads, which each relay fetches and reconciles with its own launchers.

    Args:
        policy (str, optional):
            Scaling policy for each zone's demand, ``fixed`` or ``dynamic``.

        factor (float, optional):
            Scaling factor for the dynamic policy's task pressure.

        period (int, optional):
            Seconds between checks.

        distribution (str or Distribution, optional):
            How a zone's clients are split over its sites (default: ``[autoscale] distribution``).

        sites (list or str, optional):
            Preferred site order for ``fill`` (default: ``[autoscale] sites``).

    The remaining arguments are those of :class:`~hypershell.server.ServerThread`.
    """

    server: ServerThread
    publisher: TopologyPublisherThread
    registry: SiteRegistry

    def __init__(self: AutoScalingServer,
                 address: Tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
                 auth: str = DEFAULT_AUTH,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 poll: int = DEFAULT_SERVER_POLL,
                 max_retries: int = DEFAULT_ATTEMPTS - 1,
                 eager: bool = False,
                 redirect_failures: IO = None,
                 evict_after: int = DEFAULT_EVICT,
                 tls: Optional[TLSConfig] = None,
                 policy: str = 'fixed',
                 factor: float = 1.0,
                 period: int = 60,
                 distribution: Optional[Any] = None,
                 sites: Optional[Any] = None) -> None:
        """Initialize server and topology publisher."""
        distribution = configured_distribution() if distribution is None else parse_distribution(distribution)
        order = configured_sites() if sites is None else parse_sites(sites)
        self.registry = SiteRegistry()
        self.server = ServerThread(address=address, auth=auth, bundlesize=bundlesize, poll=poll,
                                   max_retries=max_retries, eager=eager, forever_mode=True,
                                   redirect_failures=redirect_failures, evict_after=evict_after, tls=tls,
                                   sites=self.registry)
        # Every zone is read from the group the scheduler loads from, as the scheduler reports it.
        scheduler = self.server.scheduler
        reported = None if scheduler is None else (lambda: scheduler.machine.active_group)
        self.publisher = TopologyPublisherThread(self.server.queue.dispatcher, self.registry,
                                                 policy=policy, factor=factor, period=period,
                                                 distribution=distribution, order=order,
                                                 attempts=max_retries + 1, eager=eager,
                                                 scheduler_group=reported)
        # A relay that connects before the first check reads a topology with no entries, never none:
        # it waits for its entries rather than launching its local fleet.
        self.server.queue.dispatcher.publish(Topology(f'{INSTANCE}:0', {}))
        super().__init__(name='hypershell-cluster')

    def run_with_exceptions(self: AutoScalingServer) -> None:
        """Start the server, then the publisher once scheduling starts; wait."""
        self.server.start()
        self.server.wait_scheduling()
        self.publisher.start()
        self.publisher.join()
        self.server.join()

    def stop(self: AutoScalingServer, wait: bool = False, timeout: int = None) -> None:
        """Stop the publisher, then the server."""
        self.publisher.stop(wait=wait and self.publisher.ident is not None, timeout=timeout)
        self.server.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)


def serve_with_sites(**options) -> None:
    """Run a server autoscaling with relay sites until stopped (see :class:`AutoScalingServer`)."""
    thread = AutoScalingServer.new(**options)
    try:
        join_waking(thread)
    except Exception:
        thread.stop()
        raise
