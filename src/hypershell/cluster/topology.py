# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Autoscaling with relay sites: plan per-site, per-zone client targets from per-zone demand.

A server autoscaling with sites launches nothing. It reads each zone's load as the zone autoscaler
does, and splits each zone's clients over the live relay sites that serve the zone, within the
bounds each site advertises. The planning functions here are pure: they take the loads and the
relays' latest advertisements as inputs and return targets, so every rule is testable on its own.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

# Standard libs
from enum import Enum
from dataclasses import dataclass, replace

# Internal libs
from hypershell.core.topology import Bounds, Entry, SiteRecord
from hypershell.cluster.demand import ZoneRef, ZoneLoad, ScalingKnobs, zone_demand

# Public interface
__all__ = ['Distribution', 'parse_distribution', 'parse_sites',
           'SiteZone', 'SiteView', 'Duplicate', 'site_views', 'plan_sites', 'entries']


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


def parse_sites(value: Any) -> List[str]:
    """
    Site names, in order, from a list or one comma-separated string; nothing when unset.

    Blank names are skipped and a repeated name keeps its first place.
    """
    if value is None:
        return []
    if isinstance(value, str):
        names = value.split(',')
    elif isinstance(value, (list, tuple)) and all(isinstance(name, str) for name in value):
        names = value
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
    """Two live identities advertising one zone at one site: the newer holds it, the older is assigned none."""

    site: str
    holder: SiteRecord
    older: SiteRecord


def _holders(records: Iterable[SiteRecord]) -> Dict[Tuple[str, str], List[SiteRecord]]:
    """Live records by site and zone id, newest registration first."""
    groups: Dict[Tuple[str, str], List[SiteRecord]] = {}
    for record in sorted(records, key=lambda record: record.registered, reverse=True):
        groups.setdefault((record.site, ZoneRef.from_name(record.zone).id), []).append(record)
    return groups


def site_views(records: Iterable[SiteRecord]) -> Tuple[List[SiteView], List[Duplicate]]:
    """
    Group live records into sites, with every duplicated site and zone.

    Within a site, the most recently registered identity of a zone holds that zone and supplies its
    bounds; the zone's running count sums every identity of the site in that zone.
    """
    zones: Dict[str, Dict[str, SiteZone]] = {}
    ranks: Dict[str, int] = {}
    duplicates: List[Duplicate] = []
    for (site, zone), group in _holders(records).items():
        holder = group[0]
        ranks[site] = holder.rank
        zones.setdefault(site, {})[zone] = SiteZone(ZoneRef.from_name(holder.zone), holder.bounds,
                                                    running=sum(record.clients for record in group),
                                                    starting=holder.init_held)
        duplicates.extend(Duplicate(site, holder, older) for older in group[1:])
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

    The holder of a site's zone is asked for the site's target less what the site's older
    identities in that zone still run; an older identity is assigned none, so its clients retire
    as they idle out. Each entry's `seen` is the identity's own launched count.
    """
    result: Dict[str, Entry] = {}
    for (site, zone), group in _holders(records).items():
        holder, older = group[0], group[1:]
        target = targets.get(site, {}).get(zone, 0) - sum(record.clients for record in older)
        result[holder.identity] = Entry(holder.zone, max(0, target), holder.clients)
        result.update({record.identity: Entry(record.zone, 0, record.clients) for record in older})
    return result
