# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Site topology on its own: the advertisement wire, the registry and the pure per-site planner.

The planner splits each zone's clients over the live relay sites that serve it, within the bounds
each site advertises, from the zone autoscaler's own per-zone demand. Its rules are table-tested
here with no server, relay or database; the scheduler-group helper the publisher reads its group
through is pinned equal to the autoscaler's own rule on a real SQLite file.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Mapping, Optional, Tuple

# Standard libs
import json
from datetime import datetime

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from tests.test_zone_model import task_db, add_tasks, finish  # noqa: fixture
from tests.test_zone_autoscale import zoned_scaler, scheduled
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.core.topology import (Bounds, Entry, Topology, TopologyReply, SiteRecord, SiteRegistry,
                                      TOPOLOGY_PROTOCOL, request_text, read_request, reply_text, read_topology)
from hypershell.core.types import serialize, deserialize
from hypershell.cluster.demand import AutoScalerPolicy, ScalingKnobs, ZoneLoad, ZoneRef, scheduler_group
from hypershell.cluster.topology import (Distribution, parse_distribution, parse_sites, SiteZone, SiteView,
                                         Duplicate, site_views, plan_sites, entries)
from hypershell.data.model import Zone, zone_id


CPU = ZoneRef(zone_id('cpu'), 'cpu')
GPU = ZoneRef(zone_id('gpu'), 'gpu')
HIGHMEM = ZoneRef(zone_id('highmem'), 'highmem')
REFS = {'cpu': CPU, 'gpu': GPU, 'highmem': HIGHMEM}

# A site-zone as a table row writes it: (min, max, init, running, starting).
Spec = Tuple[int, int, int, int, bool]


def knobs(policy: str = 'dynamic', factor: float = 1.0) -> ScalingKnobs:
    """Server knobs; the sizes are deliberately odd, since the planner must replace them."""
    return ScalingKnobs(AutoScalerPolicy.from_name(policy), factor, init_size=7, min_size=5, max_size=99)


def loads(**zones: Tuple[int, Optional[float]]) -> Dict[str, ZoneLoad]:
    """Zone loads from `name=(work, pressure)`."""
    return {REFS[name].id: ZoneLoad(REFS[name], remaining=work, retryable=0, pressure=pressure)
            for name, (work, pressure) in zones.items()}


def site(name: str, rank: int, **zones: Spec) -> SiteView:
    """A site view from `zone=(min, max, init, running, starting)`."""
    return SiteView(name, rank, {REFS[zone].id: SiteZone(REFS[zone], Bounds(min=lo, max=hi, init=init),
                                                         running=running, starting=starting)
                                 for zone, (lo, hi, init, running, starting) in zones.items()})


def by_name(targets: Mapping[str, Mapping[str, int]]) -> Dict[str, Dict[str, int]]:
    """Targets with zone names in place of ids, for readable expectations."""
    names = {ref.id: name for name, ref in REFS.items()}
    return {site_name: {names[zone]: target for zone, target in zones.items()}
            for site_name, zones in targets.items()}


# --- The planner -----------------------------------------------------------------------------------

PLAN_ROWS = [
    # Spread: growth goes to the least loaded site.
    ('spread-to-least', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 2, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 2}, 'B': {'cpu': 1}}),
    # Spread: a tie goes to the first site in preference order, by rank when nothing is listed.
    ('spread-tie-by-rank', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 0, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 1}, 'B': {'cpu': 0}}),
    ('spread-tie-by-order', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 0, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', ('B',), {'A': {'cpu': 0}, 'B': {'cpu': 1}}),
    # Fill: the first site in preference order takes growth until its max.
    ('fill-first', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 1, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'fill', (), {'A': {'cpu': 2}, 'B': {'cpu': 0}}),
    ('fill-spills', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 3, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'fill', (), {'A': {'cpu': 3}, 'B': {'cpu': 1}}),
    # Fill: listed sites first, in listed order, then the unlisted by rank.
    ('fill-listed-first', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 1, 0, 0, False)), site('B', 1, cpu=(0, 1, 0, 0, False)),
      site('C', 2, cpu=(0, 1, 0, 0, False))],
     'fill', ('C',), {'A': {'cpu': 0}, 'B': {'cpu': 0}, 'C': {'cpu': 1}}),
    ('fill-unlisted-by-rank', 'dynamic', loads(cpu=(9, 2.0)),
     [site('B', 1, cpu=(0, 1, 0, 0, False)), site('A', 0, cpu=(0, 1, 0, 0, False)),
      site('C', 2, cpu=(0, 1, 0, 1, False))],
     'fill', ('C',), {'A': {'cpu': 1}, 'B': {'cpu': 0}, 'C': {'cpu': 1}}),
    ('fill-ignores-unknown-listed', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 1, 0, 0, False)), site('B', 1, cpu=(0, 1, 0, 0, False))],
     'fill', ('Z', 'B'), {'A': {'cpu': 0}, 'B': {'cpu': 1}}),
    # A zone goes only to the sites that serve it.
    ('serving-only', 'dynamic', loads(gpu=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 0, False)), site('B', 1, gpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 0}, 'B': {'gpu': 1}}),
    # A zone with work and no serving site is left out.
    ('unserved-left-out', 'dynamic', loads(highmem=(9, 2.0)),
     [site('A', 0, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 0}}),
    # A gated zone (no load) keeps its explicit floor; a floor of 0 gets nothing.
    ('gated-min-floor', 'dynamic', loads(cpu=(9, 2.0)),
     [site('B', 0, gpu=(1, 3, 1, 0, False)), site('A', 1, highmem=(0, 3, 0, 0, False))],
     'spread', (), {'B': {'gpu': 1}, 'A': {'highmem': 0}}),
    # Init is a floor while starting, and lapses after.
    ('init-while-starting', 'fixed', {},
     [site('A', 0, cpu=(0, 3, 2, 0, True))], 'spread', (), {'A': {'cpu': 2}}),
    ('init-lapsed', 'fixed', {},
     [site('A', 0, cpu=(0, 3, 2, 0, False))], 'spread', (), {'A': {'cpu': 0}}),
    ('init-over-min', 'fixed', {},
     [site('A', 0, cpu=(1, 3, 2, 0, True))], 'spread', (), {'A': {'cpu': 2}}),
    # A site never drops below what it runs, unless its max is lower: then its max, so no launch.
    ('keeps-running', 'dynamic', loads(cpu=(0, 0.0)),
     [site('A', 0, cpu=(0, 3, 0, 2, False))], 'spread', (), {'A': {'cpu': 2}}),
    ('max-below-running', 'dynamic', loads(cpu=(9, 9.0)),
     [site('A', 0, cpu=(0, 1, 0, 3, False)), site('B', 1, cpu=(0, 3, 0, 3, False))],
     'spread', (), {'A': {'cpu': 1}, 'B': {'cpu': 3}}),
    # The zone's total is capped at the sum of its sites' max.
    ('cap-is-sum-of-max', 'dynamic', loads(cpu=(9, 9.0)),
     [site('A', 0, cpu=(0, 2, 0, 2, False)), site('B', 1, cpu=(0, 1, 0, 1, False))],
     'spread', (), {'A': {'cpu': 2}, 'B': {'cpu': 1}}),
    # Fixed: one client when none runs and the zone has work, else none, and no growth past one.
    ('fixed-one-with-work', 'fixed', loads(cpu=(5, None)),
     [site('A', 0, cpu=(0, 3, 0, 0, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 1}, 'B': {'cpu': 0}}),
    ('fixed-none-without-work', 'fixed', loads(cpu=(0, None)),
     [site('A', 0, cpu=(0, 3, 0, 0, False))], 'spread', (), {'A': {'cpu': 0}}),
    ('fixed-no-growth', 'fixed', loads(cpu=(50, None)),
     [site('A', 0, cpu=(0, 3, 0, 1, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 1}, 'B': {'cpu': 0}}),
    # A floor that adds a client counts toward the zone's growth of one.
    ('floor-lift-is-growth', 'dynamic', loads(cpu=(9, 2.0)),
     [site('A', 0, cpu=(2, 3, 2, 1, False)), site('B', 1, cpu=(0, 3, 0, 0, False))],
     'spread', (), {'A': {'cpu': 2}, 'B': {'cpu': 0}}),
    # Pressure at or below one asks for no growth.
    ('no-pressure-no-growth', 'dynamic', loads(cpu=(9, 1.0)),
     [site('A', 0, cpu=(0, 3, 0, 1, False))], 'spread', (), {'A': {'cpu': 1}}),
    # No live site: nothing to plan.
    ('no-sites', 'dynamic', loads(cpu=(9, 2.0)), [], 'spread', (), {}),
]


@mark.unit
@mark.parametrize('name, policy, zone_loads, sites, distribution, order, expected', PLAN_ROWS,
                  ids=[row[0] for row in PLAN_ROWS])
def test_plan_sites_table(name: str, policy: str, zone_loads: Dict[str, ZoneLoad], sites: List[SiteView],
                          distribution: str, order: Tuple[str, ...], expected: Dict[str, Dict[str, int]]) -> None:
    """Each row of the planner's table: loads, knobs, sites, distribution and order to targets."""
    targets = plan_sites(zone_loads, knobs(policy), sites, distribution=parse_distribution(distribution),
                         order=order)
    assert by_name(targets) == expected


def run_plan(distribution: str, checks: int, maxes: Tuple[int, int] = (3, 3), order: Tuple[str, ...] = ()
             ) -> List[Tuple[int, int]]:
    """Plan `checks` times under constant pressure, each site running its last target; return A's and B's."""
    running = {'A': 0, 'B': 0}
    history = []
    for _ in range(checks):
        sites = [site('A', 0, cpu=(0, maxes[0], 0, running['A'], False)),
                 site('B', 1, cpu=(0, maxes[1], 0, running['B'], False))]
        targets = plan_sites(loads(cpu=(99, 5.0)), knobs(), sites,
                             distribution=parse_distribution(distribution), order=order)
        running = {name: zones[CPU.id] for name, zones in targets.items()}
        history.append((running['A'], running['B']))
    return history


@mark.unit
def test_spread_evens_out_across_checks() -> None:
    """Under spread, one client per check alternates between peers and stops at their max."""
    history = run_plan('spread', 8)
    assert history == [(1, 0), (1, 1), (2, 1), (2, 2), (3, 2), (3, 3), (3, 3), (3, 3)]


@mark.unit
def test_fill_saturates_in_order_across_checks() -> None:
    """Under fill, the preferred site reaches its max before the next site gets its first client."""
    assert run_plan('fill', 6) == [(1, 0), (2, 0), (3, 0), (3, 1), (3, 2), (3, 3)]
    assert run_plan('fill', 3, order=('B',)) == [(0, 1), (0, 2), (0, 3)]


@mark.unit
def test_a_dead_site_regrows_at_the_survivors() -> None:
    """Under dynamic, a site that is gone leaves its share to regrow at the survivors, one client per check."""
    before = [site('A', 0, cpu=(0, 4, 0, 2, False)), site('B', 1, cpu=(0, 4, 0, 2, False))]
    assert by_name(plan_sites(loads(cpu=(99, 5.0)), knobs(), before)) == {'A': {'cpu': 3}, 'B': {'cpu': 2}}
    running = 2
    for expected in (3, 4, 4):
        after = [site('A', 0, cpu=(0, 4, 0, running, False))]
        targets = by_name(plan_sites(loads(cpu=(99, 5.0)), knobs(), after))
        assert targets == {'A': {'cpu': expected}}
        running = expected


@mark.unit
def test_zones_are_planned_independently() -> None:
    """Each zone at each site is planned on its own: two zones at one site each grow by their own demand."""
    sites = [site('A', 0, cpu=(0, 3, 0, 1, False), highmem=(0, 3, 0, 0, False)),
             site('B', 1, cpu=(0, 3, 0, 0, False), gpu=(0, 3, 0, 1, False))]
    targets = plan_sites(loads(cpu=(9, 2.0), gpu=(9, 0.5), highmem=(9, 2.0)), knobs(), sites)
    assert by_name(targets) == {'A': {'cpu': 1, 'highmem': 1}, 'B': {'cpu': 1, 'gpu': 1}}


@mark.unit
def test_parse_distribution_and_sites() -> None:
    """The distribution is spread or fill in any case; sites are a list or one comma string."""
    assert parse_distribution('spread') is Distribution.SPREAD
    assert parse_distribution(' FILL ') is Distribution.FILL
    assert parse_distribution(Distribution.FILL) is Distribution.FILL
    for bad in ('even', '', None, 1):
        with pytest.raises(ValueError, match='site distribution'):
            parse_distribution(bad)
    assert parse_sites(None) == []
    assert parse_sites('') == []
    assert parse_sites('east, west,,east') == ['east', 'west']
    assert parse_sites(['B', 'A']) == ['B', 'A']
    assert parse_sites('<none>') == []
    assert parse_sites(7) == ['7']
    assert parse_sites(['east', 2]) == ['east', '2']
    for bad in (True, ['A', None], {'A': 1}):
        with pytest.raises(ValueError, match='Sites must be'):
            parse_sites(bad)


# --- Site views and entries ------------------------------------------------------------------------

def record(identity: str, site_name: str, zone: str, clients: int, registered: int, rank: int,
           bounds: Bounds = Bounds(min=0, max=3, init=0), init_held: bool = False, host: str = 'node',
           instance: Optional[str] = None) -> SiteRecord:
    """One recorded advertisement."""
    return SiteRecord(identity=identity, site=site_name, zone=zone, bounds=bounds, clients=clients, host=host,
                      registered=registered, rank=rank, init_held=init_held, instance=instance)


@mark.unit
def test_site_views_group_records_by_site_and_zone() -> None:
    """Records group into one view per site in rank order, with each zone's holder bounds and running count."""
    records = [record('b-gpu', 'B', 'gpu', 1, registered=3, rank=1, bounds=Bounds(min=1, max=2, init=1)),
               record('a-cpu', 'A', 'cpu', 2, registered=0, rank=0),
               record('a-mem', 'A', 'highmem', 0, registered=1, rank=0, init_held=True,
                      bounds=Bounds(min=0, max=4, init=2)),
               record('b-cpu', 'B', 'cpu', 0, registered=2, rank=1)]
    views, duplicates = site_views(records)
    assert duplicates == []
    assert [view.name for view in views] == ['A', 'B']
    assert views[0].zones[HIGHMEM.id] == SiteZone(HIGHMEM, Bounds(min=0, max=4, init=2), running=0, starting=True)
    assert views[0].zones[CPU.id].running == 2
    assert set(views[1].zones) == {CPU.id, GPU.id}
    assert site_views([]) == ([], [])


@mark.unit
def test_a_duplicated_site_is_held_by_the_newest_registration() -> None:
    """Two identities advertising one site's zone: the newer holds it with its own bounds and its own clients,
    the older gets nothing and its clients no longer count against the site, and one duplicate names both."""
    older = record('a1-cpu', 'A', 'cpu', 3, registered=0, rank=0, bounds=Bounds(min=0, max=3, init=0), host='h1')
    newer = record('a2-cpu', 'A', 'cpu', 0, registered=5, rank=0, bounds=Bounds(min=0, max=1, init=0), host='h2')
    views, duplicates = site_views([older, newer])
    assert duplicates == [Duplicate('A', holder=newer, older=older)]
    assert views == [SiteView('A', 0, {CPU.id: SiteZone(CPU, Bounds(min=0, max=1, init=0), running=0,
                                                        starting=False)})]
    targets = plan_sites(loads(cpu=(9, 5.0)), knobs(), views)
    assert by_name(targets) == {'A': {'cpu': 1}}
    assert entries(targets, [older, newer]) == {'a2-cpu': Entry('cpu', 1, 0), 'a1-cpu': Entry('cpu', 0, 3)}
    # Once the older identity's clients have idled out, the holder's target is the same.
    older = record('a1-cpu', 'A', 'cpu', 0, registered=0, rank=0, host='h1')
    targets = plan_sites(loads(cpu=(9, 5.0)), knobs(), site_views([older, newer])[0])
    assert entries(targets, [older, newer]) == {'a2-cpu': Entry('cpu', 1, 0), 'a1-cpu': Entry('cpu', 0, 0)}


# The relay-wide rule: (case, records, expected views by site -> zone -> (holder bounds max, running),
# expected relay duplicates as (older instance, newer instance), expected entries by identity).
CPU_1 = Bounds(min=0, max=1, init=0)
INSTANCE_ROWS = [
    # The newer relay holds every zone it serves; the older relay's identities are all assigned none.
    ('newest-holds-the-site',
     [record('a1-cpu', 'A', 'cpu', 2, registered=0, rank=0, instance='i1', host='h1'),
      record('a1-gpu', 'A', 'gpu', 1, registered=1, rank=0, instance='i1', host='h1'),
      record('a2-cpu', 'A', 'cpu', 0, registered=5, rank=0, instance='i2', host='h2', bounds=CPU_1),
      record('a2-gpu', 'A', 'gpu', 0, registered=6, rank=0, instance='i2', host='h2')],
     {'A': {'cpu': (1, 0), 'gpu': (3, 0)}},
     [('i1', 'i2')],
     {'a1-cpu': Entry('cpu', 0, 2), 'a1-gpu': Entry('gpu', 0, 1)}),
    # A zone only the older relay serves leaves the site: it is not held there by the older relay.
    ('older-only-zone-leaves-the-site',
     [record('a1-cpu', 'A', 'cpu', 1, registered=0, rank=0, instance='i1'),
      record('a1-mem', 'A', 'highmem', 2, registered=1, rank=0, instance='i1'),
      record('a2-cpu', 'A', 'cpu', 0, registered=5, rank=0, instance='i2'),
      record('b-mem', 'B', 'highmem', 0, registered=7, rank=1, instance='j1')],
     {'A': {'cpu': (3, 0)}, 'B': {'highmem': (3, 0)}},
     [('i1', 'i2')],
     {'a1-cpu': Entry('cpu', 0, 1), 'a1-mem': Entry('highmem', 0, 2)}),
    # The newer relay is the one whose first identity registered last, whatever re-registered later.
    ('newest-by-first-registration',
     [record('a1-cpu', 'A', 'cpu', 0, registered=0, rank=0, instance='i1'),
      record('a2-cpu', 'A', 'cpu', 0, registered=5, rank=0, instance='i2'),
      record('a1-gpu', 'A', 'gpu', 0, registered=9, rank=0, instance='i1')],
     {'A': {'cpu': (3, 0)}},
     [('i1', 'i2')],
     {'a1-cpu': Entry('cpu', 0, 0), 'a1-gpu': Entry('gpu', 0, 0)}),
    # One relay's identities are never duplicates of each other.
    ('one-relay-is-no-duplicate',
     [record('a-cpu', 'A', 'cpu', 1, registered=0, rank=0, instance='i1'),
      record('a-gpu', 'A', 'gpu', 0, registered=1, rank=0, instance='i1')],
     {'A': {'cpu': (3, 1), 'gpu': (3, 0)}},
     [],
     {}),
]


@mark.unit
@mark.parametrize('case, records, views, relays, zeroed', INSTANCE_ROWS, ids=[row[0] for row in INSTANCE_ROWS])
def test_a_duplicated_site_is_held_by_the_newest_relay(case, records, views, relays, zeroed) -> None:
    """Identities that advertise an instance key are held relay-wide: per site, the most recently
    registered relay holds every zone it serves, planned on its own clients alone, and every other relay
    there is assigned none."""
    found, duplicates = site_views(records)
    names = {ref.id: name for name, ref in REFS.items()}
    assert {view.name: {names[zone]: (sz.bounds.max, sz.running) for zone, sz in view.zones.items()}
            for view in found} == views
    assert [(dup.older.instance, dup.holder.instance) for dup in duplicates] == relays
    assert all(dup.relay for dup in duplicates)
    targets = plan_sites(loads(cpu=(9, 5.0), gpu=(9, 5.0), highmem=(9, 5.0)), knobs(), found)
    planned = entries(targets, records)
    assert set(planned) == {record.identity for record in records}
    for identity, entry in zeroed.items():
        assert planned[identity] == entry
    for view in found:
        for zone in view.zones:
            holder, = [record for record in records if record.site == view.name and REFS[record.zone].id == zone
                       and record.identity not in zeroed]
            assert view.zones[zone].running == holder.clients
            assert planned[holder.identity] == Entry(holder.zone, targets[view.name][zone], holder.clients)


# A relay restarted at its site after a crash: its predecessor is dead but not yet evicted, and still
# advertises the clients it last reported. (case, the restarted relay's bounds, loads, its target).
BOUNCE_ROWS = [
    ('init-at-once', Bounds(min=0, max=2, init=2), {}, 2),
    ('min-at-once', Bounds(min=1, max=2, init=1), {}, 1),
    ('demand-at-once', Bounds(min=0, max=2, init=0), {'cpu': (9, 5.0)}, 1),
]


@mark.unit
@mark.parametrize('case, bounds, work, target', BOUNCE_ROWS, ids=[row[0] for row in BOUNCE_ROWS])
def test_a_restarted_relay_is_planned_at_once(case, bounds, work, target) -> None:
    """The restarted relay holds its site and is planned at once, as if alone there: the superseded
    relay's last-advertised clients no longer count against the site, so it does not wait out the
    eviction window. The superseded relay is assigned none."""
    records = [record('r1-cpu', 'A', 'cpu', 2, registered=0, rank=0, instance='i1', bounds=Bounds(min=0, max=2, init=2)),
               record('r2-cpu', 'A', 'cpu', 0, registered=3, rank=0, instance='i2', bounds=bounds,
                      init_held=bounds.init > 0)]
    views, duplicates = site_views(records)
    assert [(dup.older.instance, dup.holder.instance) for dup in duplicates] == [('i1', 'i2')]
    assert views[0].zones[CPU.id].running == 0
    targets = plan_sites(loads(**work), knobs(), views)
    assert by_name(targets) == {'A': {'cpu': target}}
    assert entries(targets, records) == {'r1-cpu': Entry('cpu', 0, 2), 'r2-cpu': Entry('cpu', target, 0)}


@mark.unit
def test_a_zone_the_older_relay_alone_serves_goes_to_other_sites() -> None:
    """The older relay's zone is planned over the other sites that serve it, never back to the older relay."""
    records = [record('a1-cpu', 'A', 'cpu', 0, registered=0, rank=0, instance='i1'),
               record('a1-mem', 'A', 'highmem', 0, registered=1, rank=0, instance='i1'),
               record('a2-cpu', 'A', 'cpu', 0, registered=5, rank=0, instance='i2'),
               record('b-mem', 'B', 'highmem', 0, registered=7, rank=1, instance='j1')]
    targets = plan_sites(loads(highmem=(9, 5.0)), knobs(), site_views(records)[0])
    assert by_name(targets) == {'A': {'cpu': 0}, 'B': {'highmem': 1}}
    planned = entries(targets, records)
    assert planned['a1-mem'] == Entry('highmem', 0, 0) and planned['b-mem'] == Entry('highmem', 1, 0)


@mark.unit
def test_records_without_an_instance_keep_the_per_zone_rule() -> None:
    """A record with no instance key competes per zone with the holding relay's identity in that zone."""
    records = [record('a1-cpu', 'A', 'cpu', 0, registered=0, rank=0, instance='i1'),
               record('a1-gpu', 'A', 'gpu', 0, registered=1, rank=0, instance='i1'),
               record('old-cpu', 'A', 'cpu', 2, registered=4, rank=0)]
    views, duplicates = site_views(records)
    assert duplicates == [Duplicate('A', holder=records[2], older=records[0])]
    assert views[0].zones[CPU.id].running == 2 and set(views[0].zones) == {CPU.id, GPU.id}
    planned = entries({'A': {CPU.id: 3, GPU.id: 1}}, records)
    assert planned == {'old-cpu': Entry('cpu', 3, 2), 'a1-cpu': Entry('cpu', 0, 0), 'a1-gpu': Entry('gpu', 1, 0)}


@mark.unit
def test_entries_carry_each_identity_target_and_seen() -> None:
    """Each identity's entry carries its zone name, its site-zone target and its own launched count."""
    records = [record('a-cpu', 'A', 'cpu', 2, registered=0, rank=0),
               record('b-cpu', 'B', 'cpu', 1, registered=1, rank=1),
               record('b-gpu', 'B', 'gpu', 0, registered=2, rank=1)]
    targets = {'A': {CPU.id: 3}, 'B': {CPU.id: 1, GPU.id: 2}}
    assert entries(targets, records) == {'a-cpu': Entry('cpu', 3, 2), 'b-cpu': Entry('cpu', 1, 1),
                                         'b-gpu': Entry('gpu', 2, 0)}
    assert entries({}, records)['a-cpu'] == Entry('cpu', 0, 2)


# --- The wire --------------------------------------------------------------------------------------

@mark.unit
def test_bounds_validate_and_round_trip() -> None:
    """Bounds are whole numbers with 0 <= min <= init <= max and max >= 1, and pack as a plain mapping."""
    bounds = Bounds(min=1, max=4, init=2)
    assert bounds.to_dict() == {'min': 1, 'max': 4, 'init': 2}
    assert Bounds.from_dict(bounds.to_dict()) == bounds
    for lo, hi, init in [(-1, 2, 0), (2, 3, 1), (0, 2, 3), (0, 0, 0), (1.0, 2, 1), (True, 2, 1)]:
        with pytest.raises(ValueError):
            Bounds(min=lo, max=hi, init=init)
    for bad in ({'min': 0, 'max': 2}, [0, 2, 1], None):
        with pytest.raises(ValueError):
            Bounds.from_dict(bad)


@mark.unit
def test_beat_with_advertisement_round_trips() -> None:
    """A zone identity's beat carries its zone, its bounds and its launched count, and they survive packing."""
    bounds = Bounds(min=0, max=3, init=1)
    beat = Heartbeat.new(uuid='r1', site='A', zone='cpu', bounds=bounds, clients=2)
    packed = deserialize(beat.pack())
    assert set(packed) == {'uuid', 'host', 'time', 'state', 'site', 'zone', 'bounds', 'clients'}
    assert packed['bounds'] == {'min': 0, 'max': 3, 'init': 1}
    again = Heartbeat.unpack_or_none(beat.pack())
    assert (again.site, again.zone, again.bounds, again.clients) == ('A', 'cpu', bounds, 2)
    zero = Heartbeat.unpack_or_none(Heartbeat.new(uuid='r1', site='A', zone='cpu', bounds=bounds, clients=0).pack())
    assert zero.clients == 0 and zero.instance is None
    keyed = Heartbeat.new(uuid='r1', site='A', zone='cpu', bounds=bounds, clients=2, instance='i1')
    assert deserialize(keyed.pack())['instance'] == 'i1'
    assert Heartbeat.unpack_or_none(keyed.pack()).instance == 'i1'


@mark.unit
def test_plain_and_legacy_beats_carry_no_advertisement() -> None:
    """A plain beat packs exactly its four keys, and a beat without the new keys decodes with them unset."""
    assert set(deserialize(Heartbeat.new(uuid='c1').pack())) == {'uuid', 'host', 'time', 'state'}
    now = datetime.now().astimezone()
    old = serialize({'uuid': 'r1', 'host': 'h1', 'time': str(now), 'state': ClientState.RUNNING.value,
                     'site': 'A', 'roster': []})
    beat = Heartbeat.unpack_or_none(old)
    assert beat.site == 'A' and beat.roster == []
    assert beat.zone is None and beat.bounds is None and beat.clients is None


@mark.unit
def test_malformed_advertisement_reads_as_absent() -> None:
    """An advertisement field that is not valid decodes as unset rather than failing the beat."""
    now = datetime.now().astimezone()
    bad = serialize({'uuid': 'r1', 'host': 'h1', 'time': str(now), 'state': ClientState.RUNNING.value,
                     'site': 'A', 'zone': 5, 'bounds': {'min': 3, 'max': 1, 'init': 0}, 'clients': -1})
    beat = Heartbeat.unpack_or_none(bad)
    assert beat.site == 'A' and beat.zone is None and beat.bounds is None and beat.clients is None
    odd = serialize({'uuid': 'r1', 'host': 'h1', 'time': str(now), 'state': ClientState.RUNNING.value,
                     'site': 'A', 'instance': 7})
    assert Heartbeat.unpack_or_none(odd).instance is None
    worse = serialize({'uuid': 'r1', 'host': 'h1', 'time': str(now), 'state': ClientState.RUNNING.value,
                       'bounds': 'all', 'clients': True})
    beat = Heartbeat.unpack_or_none(worse)
    assert beat.bounds is None and beat.clients is None


@mark.unit
def test_topology_request_round_trips() -> None:
    """A relay's request names its site and identities; anything else is refused."""
    text = request_text('A', ['a-cpu', 'a-gpu'])
    assert json.loads(text) == {'protocol': TOPOLOGY_PROTOCOL, 'site': 'A', 'identities': ['a-cpu', 'a-gpu']}
    assert read_request(text) == ('A', ['a-cpu', 'a-gpu'])
    for bad in ('nope', '[]', json.dumps({'protocol': 99, 'site': 'A', 'identities': []}),
                json.dumps({'protocol': True, 'site': 'A', 'identities': []}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'identities': []}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'site': 'A', 'identities': [1]}),
                'x' * 20000, None):
        with pytest.raises(ValueError):
            read_request(bad)


@mark.unit
def test_topology_reply_null_entries_and_missing() -> None:
    """With nothing published the reply has a null version; with a topology, each asked identity's entry by zone,
    and a zone the topology does not name reads as assigned no clients."""
    null = reply_text(None, ['a-cpu'])
    assert json.loads(null) == {'protocol': TOPOLOGY_PROTOCOL, 'version': None}
    assert read_topology(null) is None
    topology = Topology('server:3', {'a-cpu': Entry('cpu', 2, 1), 'b-cpu': Entry('cpu', 1, 0)})
    text = reply_text(topology, ['a-cpu', 'a-gpu'])
    assert json.loads(text) == {'protocol': TOPOLOGY_PROTOCOL, 'version': 'server:3', 'checks': 0,
                                'zones': {'cpu': {'target': 2, 'seen': 1}}}
    reply = read_topology(text)
    assert reply == TopologyReply('server:3', {'cpu': Entry('cpu', 2, 1)}, checks=0)
    checked = Topology('server:3', topology.entries, checks=7)
    assert read_topology(reply_text(checked, ['a-cpu'])).checks == 7
    assert reply.assignment('cpu') == Entry('cpu', 2, 1)
    assert reply.assignment('gpu') == Entry('gpu', 0, 0)
    for bad in ('nope', json.dumps({'protocol': 99, 'version': None}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v'}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v', 'zones': {'cpu': {'target': 1}}}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v', 'zones': {'cpu': 1}}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v', 'zones': {}}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v', 'zones': {}, 'checks': -1}),
                json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': 'v', 'zones': {}, 'checks': True})):
        with pytest.raises(ValueError):
            read_topology(bad)


# --- The registry ----------------------------------------------------------------------------------

@mark.unit
def test_registry_overwrites_in_place_and_orders() -> None:
    """Each identity has one record, overwritten in place; identities keep their registration order and sites
    their first-sight rank, even after their identities leave."""
    registry = SiteRegistry()
    bounds = Bounds(min=0, max=3, init=0)
    registry.record('b-cpu', site='B', zone='cpu', bounds=bounds, clients=0, host='hb')
    registry.record('a-cpu', site='A', zone='cpu', bounds=bounds, clients=0, host='ha')
    registry.record('b-cpu', site='B', zone='cpu', bounds=Bounds(min=0, max=1, init=0), clients=2, host='hb')
    snapshot = registry.snapshot()
    assert [rec.identity for rec in snapshot] == ['b-cpu', 'a-cpu']
    assert [(rec.site, rec.rank, rec.registered) for rec in snapshot] == [('B', 0, 0), ('A', 1, 1)]
    assert snapshot[0].bounds.max == 1 and snapshot[0].clients == 2
    assert len(registry) == 2 and 'a-cpu' in registry
    assert registry.drop('b-cpu').identity == 'b-cpu'
    assert registry.drop('b-cpu') is None
    assert 'b-cpu' not in registry
    again = registry.record('b-new', site='B', zone='cpu', bounds=bounds, clients=0, host='hb')
    assert (again.rank, again.registered) == (0, 2)
    assert [rec.identity for rec in registry.snapshot()] == ['a-cpu', 'b-new']


@mark.unit
def test_registry_init_held_clears_once_reached() -> None:
    """An identity's init holds as a floor until it reports init clients, and does not come back after."""
    registry = SiteRegistry()
    bounds = Bounds(min=0, max=4, init=2)
    assert registry.record('a', site='A', zone='cpu', bounds=bounds, clients=0, host='h').init_held
    assert registry.record('a', site='A', zone='cpu', bounds=bounds, clients=1, host='h').init_held
    assert not registry.record('a', site='A', zone='cpu', bounds=bounds, clients=2, host='h').init_held
    assert not registry.record('a', site='A', zone='cpu', bounds=bounds, clients=0, host='h').init_held
    no_init = Bounds(min=0, max=4, init=0)
    assert not registry.record('b', site='A', zone='gpu', bounds=no_init, clients=0, host='h').init_held


# --- The scheduler's group -------------------------------------------------------------------------

@mark.integration
class TestSchedulerGroupEquivalence:
    """The publisher's group helper equals the autoscaler's own group rule, on a real SQLite file."""

    @staticmethod
    def assert_equal(reported: Optional[int], attempts: int = 1, eager: bool = False) -> int:
        """Both rules give the same group for `reported`; return it."""
        options = {'scheduler_group': (lambda: reported), 'attempts': attempts, 'eager': eager}
        scaler, _ = zoned_scaler(**options)
        group = scheduler_group(reported, attempts=attempts, eager=eager)
        assert group == scaler.current_group()
        return group

    def test_reported_group_wins(self, task_db: Engine) -> None:
        """A group the scheduler reports is the group, whatever the database holds."""
        add_tasks(2, group=1)
        assert self.assert_equal(3) == 3
        assert self.assert_equal(0) == 0

    def test_empty_database(self, task_db: Engine) -> None:
        """With no tasks, the start rule falls back to the default group."""
        self.assert_equal(None)

    def test_late_lower_group(self, task_db: Engine) -> None:
        """A late lower group under a completed higher one is where the scheduler starts."""
        Zone.ensure('cpu')
        Zone.ensure('gpu')
        scheduled(2, zone_id('gpu'), group=2, done=2)
        add_tasks(2, zone_id('gpu'), group=2)
        add_tasks(3, zone_id('cpu'), group=1)
        assert self.assert_equal(None) == 1

    def test_everything_complete(self, task_db: Engine) -> None:
        """With every task complete and nothing to retry, the most recently scheduled group stands."""
        scheduled(2, None, group=4, done=2)
        assert self.assert_equal(None) == 4

    @pytest.mark.parametrize('eager', [False, True])
    def test_failure_awaiting_retry(self, task_db: Engine, eager: bool) -> None:
        """A group holding a failure still to be retried is kept, under either retry order."""
        ids = scheduled(2, None, group=2, done=1)
        finish(ids[1], 1)
        add_tasks(1, group=5)
        self.assert_equal(None, attempts=2, eager=eager)
        self.assert_equal(None, attempts=1, eager=eager)
