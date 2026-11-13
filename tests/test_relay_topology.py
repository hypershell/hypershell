# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A relay under a site topology: per-zone bounds, the advertisement, the topology read and its fleets.

Each zone a relay serves has client bounds: those it advertises upstream for a server autoscaling
with relay sites to plan with, and those of its fallback fleet when nothing is published. Every
zone identity's beat advertises them with the zone's launched clients and the relay's instance key,
and each beat is followed by a read of the published topology. Each fleet's `SiteScaler` applies a
version once, launching what the server asks for beyond what the zone has, and never ends a client;
with no topology it is the fixed fleet the relay always ran. Every launch names its zone.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

# Standard libs
import os
import re
import sys
import json
import time
import signal
import subprocess
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

# External libs
import pytest
from pytest import mark
from cmdkit.cli import ArgumentError

# Internal libs
from tests.conftest import free_port
from tests.test_zone_model import task_db, add_tasks  # noqa: fixture
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.reconnect import LinkDown
from hypershell.core.topology import Bounds, Entry, TopologyReply, reply_text, Topology
from hypershell.core.types import deserialize
from hypershell.cluster.launch import AUTH_ENV, ZONE_ENV
from hypershell.cluster.remote import AutoScaler, AutoScalerState, AutoScalerPhase
from hypershell.cluster.demand import ZoneRef
from hypershell.relay import RelayThread, RelayApp, zone_bounds
from hypershell.relay.site import (site_cluster, zone_launch, zone_fleet, fence_site, TopologyView, SiteScaler,
                                   launched_now)
from hypershell.relay.upstream import UpstreamHeartbeat, HeartbeatState, upstream_identity


# --- Bounds ----------------------------------------------------------------------------------------

def bounds(lo: int, hi: int, init: int) -> Bounds:
    """Bounds from (min, max, init)."""
    return Bounds(min=lo, max=hi, init=init)


BOUND_ROWS = [
    # (case, zone settings, relay-wide settings, advertised, fallback)
    ('nothing-set', {}, {'size': 1}, bounds(0, 1, 0), bounds(1, 1, 1)),
    ('size-only-is-the-fixed-fleet', {'size': 3}, {'size': 1}, bounds(0, 3, 0), bounds(3, 3, 3)),
    ('relay-size-only', {}, {'size': 4}, bounds(0, 4, 0), bounds(4, 4, 4)),
    ('min-zero-falls-back-to-one', {'min': 0, 'max': 3}, {'size': 1}, bounds(0, 3, 0), bounds(1, 3, 1)),
    ('all-three', {'min': 1, 'init': 2, 'max': 4}, {'size': 1}, bounds(1, 4, 2), bounds(1, 4, 2)),
    ('init-defaults-to-min', {'min': 2, 'max': 4}, {'size': 1}, bounds(2, 4, 2), bounds(2, 4, 2)),
    ('zone-wins-over-relay', {'max': 2}, {'size': 1, 'min': 1, 'max': 5}, bounds(1, 2, 1), bounds(1, 2, 1)),
    ('relay-wide-bounds', {}, {'size': 2, 'min': 1, 'max': 6, 'init': 3}, bounds(1, 6, 3), bounds(1, 6, 3)),
    ('max-below-size', {'size': 4, 'max': 2}, {'size': 1}, bounds(0, 2, 0), bounds(2, 2, 2)),
    ('unset-is-not-given', {'min': None}, {'size': 2, 'max': None}, bounds(0, 2, 0), bounds(2, 2, 2)),
]


@mark.unit
@mark.parametrize('case, settings, relay_wide, advertised, fallback', BOUND_ROWS, ids=[row[0] for row in BOUND_ROWS])
def test_zone_bounds(case, settings, relay_wide, advertised, fallback) -> None:
    """A zone advertises `min` (default 0), `init` (default `min`) and `max` (default the size); its
    fallback fleet is the size unless bounds are given, never below one client."""
    assert zone_bounds(settings, relay_wide) == (advertised, fallback)


@mark.unit
@mark.parametrize('settings', [{'min': 2, 'max': 1}, {'init': 3, 'max': 2}, {'max': 0}, {'min': -1},
                               {'max': True}, {'min': 1.5}, {'min': 3}, {'init': 2}, {'max': '3'}])
def test_zone_bounds_refused(settings) -> None:
    """Bounds that are not whole numbers with 0 <= min <= init <= max and max >= 1 are refused
    (after defaults, so `min = 3` alone is refused against the default size of one)."""
    with pytest.raises(ValueError):
        zone_bounds(settings, {'size': 1})


def relay_app(zones: List[str], tables: Dict[str, Dict[str, Any]], **relay_wide: Optional[int]) -> RelayApp:
    """A relay application as `[relay.zone.<name>]` tables (and `[relay]` bounds) would configure it."""
    app = RelayApp.from_cmdline([arg for zone in zones for arg in ('--zone', zone)])
    app.__dict__['zone_plan'] = (zones, tables)
    app.__dict__['relay_bounds'] = {'min': None, 'max': None, 'init': None, **relay_wide}
    return app


@mark.unit
def test_relay_app_checks_each_zone_bounds() -> None:
    """A zone table's bounds are kept when valid and refused, naming the zone, when not."""
    good = relay_app(['cpu', 'gpu'], {'cpu': {'max': 3}, 'gpu': {'min': 1, 'max': 2, 'init': 1}})
    assert good.zone_settings == {'cpu': {'max': 3}, 'gpu': {'min': 1, 'max': 2, 'init': 1}}
    good.check_bounds()
    with pytest.raises(ArgumentError, match='relay.zone.gpu.max must be a whole number'):
        relay_app(['gpu'], {'gpu': {'max': 1.5}}).zone_settings  # noqa: checked when first read
    with pytest.raises(ArgumentError, match='client bounds of zone gpu'):
        relay_app(['gpu'], {'gpu': {'min': 2}}).check_bounds()
    with pytest.raises(ArgumentError, match='client bounds of zone cpu'):
        relay_app(['cpu'], {'cpu': {}}, init=4).check_bounds()
    relay_app(['cpu'], {'cpu': {}}, max=4, init=4).check_bounds()


# --- The fleets' autoscaler --------------------------------------------------------------------------

class Proc:
    """A launched client that runs until told to exit."""

    def __init__(self) -> None:
        self.status: Optional[int] = None
        self.pid = id(self)

    def poll(self) -> Optional[int]:
        return self.status

    def exit(self) -> None:
        self.status = 0


class FakeLaunch:
    """A client launch that starts a `Proc` and records it."""

    def __init__(self) -> None:
        self.started: List[Proc] = []
        self.argv: List[str] = ['hs', 'client']
        self.env: Dict[str, str] = {}

    def start(self, **options: Any) -> Proc:  # noqa: unused options
        self.started.append(Proc())
        return self.started[-1]

    def describe(self) -> str:
        return 'hs client'


def scaler(view: Optional[TopologyView], size: int = 2, zone: str = 'cpu') -> Tuple[SiteScaler, FakeLaunch]:
    """A fleet's autoscaler for `zone`, fixed at `size` without a topology, on a recording launch."""
    launch = FakeLaunch()
    machine = SiteScaler(launch, view=view, policy='fixed', period=3600, init_size=size, min_size=size,
                         max_size=size, own_zone=ZoneRef.from_name(zone))
    return machine, launch


def view_of(version: str, **zones: Tuple[int, int]) -> TopologyView:
    """A view holding `version` with `zone=(target, seen)` entries."""
    return TopologyView(TopologyReply(version, {zone: Entry(zone, target, seen) for zone, (target, seen) in zones.items()}))


def apply_once(machine: SiteScaler) -> int:
    """One check of the machine (with its launches); return how many clients it started."""
    machine.clean()
    before = len(machine.clients)
    if machine.check_zones() is AutoScalerState.SCALE:
        assert machine.scale() is AutoScalerState.WAIT
    return len(machine.clients) - before


DELTA_ROWS = [
    # (case, target, seen, launched before, launched by this version)
    ('grow-from-none', 3, 0, 0, 3),
    ('grow-by-the-gap', 3, 2, 2, 1),
    ('an-exit-is-not-replaced', 3, 2, 1, 1),
    ('a-launch-not-yet-seen-counts', 2, 0, 1, 1),
    ('a-lowered-target-launches-none', 1, 0, 3, 0),
    ('zero-launches-none', 0, 0, 2, 0),
    ('met-target', 2, 2, 2, 0),
]


@mark.unit
@mark.parametrize('case, target, seen, before, launched', DELTA_ROWS, ids=[row[0] for row in DELTA_ROWS])
def test_site_scaler_applies_each_version_once(case, target, seen, before, launched) -> None:
    """A version launches `target - max(seen, launched)` clients, at most once; nothing is ever ended."""
    machine, launch = scaler(view_of('s:1', cpu=(target, seen)))
    machine.phase = AutoScalerPhase.STEADY
    for _ in range(before):
        machine.actuator.launch_one()
    assert apply_once(machine) == launched
    assert apply_once(machine) == 0  # The same version again launches nothing.
    assert all(proc.status is None for proc in launch.started)


@mark.unit
def test_site_scaler_does_not_replace_exits_and_follows_new_versions() -> None:
    """A client that exits is replaced only when a later version asks for one."""
    view = view_of('s:1', cpu=(2, 0))
    machine, launch = scaler(view)
    machine.phase = AutoScalerPhase.STEADY
    assert apply_once(machine) == 2
    launch.started[0].exit()
    assert apply_once(machine) == 0 and len(machine.clients) == 1
    view.set(TopologyReply('s:2', {'cpu': Entry('cpu', 2, 2)}))  # Planned before the exit was seen.
    assert apply_once(machine) == 0
    view.set(TopologyReply('s:3', {'cpu': Entry('cpu', 2, 1)}))
    assert apply_once(machine) == 1 and len(machine.clients) == 2


@mark.unit
def test_site_scaler_applies_again_when_its_launches_left_uncounted() -> None:
    """Clients that left before the server counted them change no version, so after a grace of
    periods the same version is applied again; clients the server counted are never replaced."""
    view = view_of('s:1', cpu=(1, 0))
    machine, launch = scaler(view)
    machine.phase = AutoScalerPhase.STEADY
    assert apply_once(machine) == 1
    launch.started[0].exit()  # Before any beat counted it: the server still plans target 1, seen 0.
    assert apply_once(machine) == 0  # Within the grace.
    machine.applied_at -= machine.RETRY_PERIODS * machine.period
    assert apply_once(machine) == 1 and len(launch.started) == 2
    assert apply_once(machine) == 0  # Running again: nothing more.
    counted = view_of('s:2', cpu=(2, 2))
    machine.view = counted
    machine.clients = []
    for _ in range(2):
        machine.actuator.launch_one()
    assert apply_once(machine) == 0
    for proc in machine.clients:
        proc.exit()  # Counted by the server, then idle: never replaced.
    machine.applied_at -= machine.RETRY_PERIODS * machine.period
    assert apply_once(machine) == 0


@mark.unit
def test_site_scaler_missing_entry_is_zero() -> None:
    """A topology that names other zones only assigns this zone none."""
    machine, launch = scaler(view_of('s:1', gpu=(3, 0)))
    assert machine.assignment() == ('s:1', Entry('cpu', 0, 0))
    machine.phase = AutoScalerPhase.STEADY
    assert apply_once(machine) == 0 and launch.started == []


@mark.unit
def test_site_scaler_init_follows_the_topology() -> None:
    """With a topology at start the initial launch is the zone's entry, not the fixed fleet's size."""
    machine, launch = scaler(view_of('s:1', cpu=(1, 0)), size=4)
    assert machine.init() is AutoScalerState.SCALE
    assert machine.scale() is AutoScalerState.WAIT and machine.phase is AutoScalerPhase.STEADY
    assert len(launch.started) == 1
    zero, launch = scaler(view_of('s:1'), size=4)
    assert zero.init() is AutoScalerState.WAIT and launch.started == []


def run_init(machine: AutoScaler) -> int:
    """Run the initial launch to steady state; return the clients launched."""
    state = machine.init()
    for _ in range(100):
        if state is AutoScalerState.WAIT:
            return len(machine.clients)
        state = machine.scale() if state is AutoScalerState.SCALE else machine.init()
    raise AssertionError('initial launch did not settle')


@mark.unit
@mark.parametrize('view', [None, TopologyView()], ids=['no-view', 'nothing-published'])
def test_site_scaler_without_topology_is_the_fixed_fleet(view) -> None:
    """With no topology the fleet starts exactly as an autoscaler with the same knobs does."""
    machine, _ = scaler(view, size=3)
    plain = AutoScaler(FakeLaunch(), policy='fixed', period=3600, init_size=3, min_size=3, max_size=3,
                       own_zone=ZoneRef.from_name('cpu'))
    assert run_init(machine) == run_init(plain) == 3
    assert machine.phase is plain.phase is AutoScalerPhase.STEADY


@mark.integration
def test_site_scaler_without_topology_checks_as_the_fixed_fleet(task_db) -> None:
    """With no topology a check replaces an exited client exactly as the fixed fleet's does."""
    add_tasks(3, zone='cpu')
    machine, launch = scaler(TopologyView(), size=2, zone='cpu')
    plain = AutoScaler(FakeLaunch(), policy='fixed', period=3600, init_size=2, min_size=2, max_size=2,
                       own_zone=ZoneRef.from_name('cpu'))
    for fleet in (machine, plain):
        run_init(fleet)
        fleet.clients[0].exit()
        fleet.clean()
        assert fleet.check_zones() is AutoScalerState.SCALE
        fleet.scale()
    assert len(machine.clients) == len(plain.clients) == 2


@mark.unit
def test_site_scaler_checks_at_once_on_a_new_version() -> None:
    """A version read while waiting is applied at once, not at the end of the period."""
    view = TopologyView()
    machine, _ = scaler(view)
    machine.phase = AutoScalerPhase.STEADY
    machine.applied = None
    view.set(TopologyReply('s:1', {'cpu': Entry('cpu', 1, 0)}))
    assert machine.wait() is AutoScalerState.CHECK
    apply_once(machine)
    start = time.monotonic()
    assert machine.wait() is AutoScalerState.WAIT and time.monotonic() - start < 2


@mark.unit
def test_launched_now_counts_running_clients() -> None:
    """The advertised count is the clients still running, whether or not a check has cleaned the rest."""
    machine, launch = scaler(None)
    for _ in range(3):
        machine.actuator.launch_one()
    launch.started[1].exit()
    assert launched_now(machine) == 2 and len(machine.clients) == 3


# --- The advertisement and the topology read ----------------------------------------------------------

class Dispatcher:
    """A stand-in for the upstream dispatcher proxy; it answers each topology request with `reply`."""

    def __init__(self, reply: Optional[str]) -> None:
        self.reply = reply
        self.requests: List[str] = []

    def topology(self, text: str) -> Optional[str]:
        self.requests.append(text)
        return self.reply


class Link:
    """A link that can be taken down: calls then raise `LinkDown`."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.epoch = 1
        self.down = False
        self.down_for: set = set()

    def call(self, role, fn, deadline=None):  # noqa: unused
        if self.down:
            raise LinkDown('down')
        result = fn(self.client)
        return result

    def passed(self, stage, epoch=None) -> None:
        pass

    def wait_ready(self, after=(), timeout=1.0) -> bool:  # noqa: unused
        return not self.down


CPU_ID, GPU_ID = upstream_identity('cpu'), upstream_identity('gpu')
ADVERTISED = {'cpu': bounds(0, 3, 0), 'gpu': bounds(1, 2, 1)}


def relay_heartbeat(dispatcher: Any = None, launched: Optional[Dict[str, int]] = None,
                    view: Optional[TopologyView] = None) -> Tuple[UpstreamHeartbeat, Link]:
    """A relay's heartbeat for zones cpu and gpu at site A, on a stand-in upstream."""
    client = SimpleNamespace(heartbeat=Queue())
    if dispatcher is not None:
        client.dispatcher = dispatcher
    link = Link(client)
    counts = launched if launched is not None else {}
    machine = UpstreamHeartbeat(queue=link, identity=CPU_ID, heartrate=60, site='A', identities=[CPU_ID, GPU_ID],
                                zones={CPU_ID: 'cpu', GPU_ID: 'gpu'}, bounds=ADVERTISED,
                                launched=lambda zone: counts.get(zone, 0), instance=CPU_ID, view=view)
    return machine, link


def sent(link: Link) -> List[Dict[str, Any]]:
    """Every beat sent so far, as packed."""
    found = []
    while not link.client.heartbeat.empty():
        found.append(deserialize(link.client.heartbeat.get_nowait()))
    return found


@mark.unit
def test_every_beat_advertises_each_zone() -> None:
    """Each identity's beat carries its zone, bounds, launched count and the relay's instance key, every time."""
    counts = {'cpu': 2}
    machine, link = relay_heartbeat(launched=counts)
    assert machine.submit() is HeartbeatState.WAIT
    first = {beat['uuid']: beat for beat in sent(link)}
    assert set(first) == {CPU_ID, GPU_ID}
    assert first[CPU_ID]['zone'] == 'cpu' and first[CPU_ID]['bounds'] == {'min': 0, 'max': 3, 'init': 0}
    assert first[CPU_ID]['clients'] == 2 and first[GPU_ID]['clients'] == 0
    assert first[GPU_ID]['bounds'] == {'min': 1, 'max': 2, 'init': 1}
    assert first[CPU_ID]['instance'] == first[GPU_ID]['instance'] == CPU_ID
    counts['cpu'] = 3
    machine.submit()
    again = {beat['uuid']: Heartbeat.unpack_or_none(_pack(beat)) for beat in sent(link)}
    assert again[CPU_ID].clients == 3 and again[CPU_ID].bounds == ADVERTISED['cpu']


def _pack(beat: Dict[str, Any]) -> bytes:
    """A packed beat from its unpacked fields."""
    from hypershell.core.types import serialize
    return serialize(beat)


@mark.unit
def test_the_first_beat_after_a_reconnect_advertises() -> None:
    """A beat the link lost is sent again, with the advertisement, once the link is back."""
    machine, link = relay_heartbeat()
    link.down = True
    assert machine.submit() is HeartbeatState.WAIT_LINK
    link.down = False
    assert machine.wait_link() is HeartbeatState.SUBMIT
    assert machine.submit() is HeartbeatState.WAIT
    beats = sent(link)
    assert {beat['uuid'] for beat in beats} == {CPU_ID, GPU_ID}
    assert all({'zone', 'bounds', 'clients', 'instance'} <= set(beat) for beat in beats)


@mark.unit
def test_a_plain_relay_heartbeat_advertises_nothing() -> None:
    """A heartbeat given no zones advertises nothing and reads no topology."""
    link = Link(SimpleNamespace(heartbeat=Queue(), dispatcher=Dispatcher(None)))
    machine = UpstreamHeartbeat(queue=link, identity=CPU_ID, heartrate=60, site='A')
    machine.submit()
    beat, = sent(link)
    assert set(beat) == {'uuid', 'host', 'time', 'state', 'site'}
    assert link.client.dispatcher.requests == []


def published(*entries: Tuple[str, Entry], version: str = 's:1') -> str:
    """A server's reply naming `entries` (identity, entry)."""
    return reply_text(Topology(version, dict(entries)), [identity for identity, _ in entries])


@mark.unit
def test_fetch_sets_the_view(caplog) -> None:
    """The reply read after a beat is the view: entries by zone, a null version as none."""
    dispatcher = Dispatcher(published((CPU_ID, Entry('cpu', 2, 0))))
    view = TopologyView()
    machine, link = relay_heartbeat(dispatcher, view=view)
    with caplog.at_level('INFO', logger='hypershell.relay.upstream'):
        machine.submit()
        assert view.assignment('cpu') == ('s:1', Entry('cpu', 2, 0))
        assert view.assignment('gpu') == ('s:1', Entry('gpu', 0, 0))
        assert json.loads(dispatcher.requests[0]) == {'protocol': 1, 'site': 'A', 'identities': [CPU_ID, GPU_ID]}
        dispatcher.reply = json.dumps({'protocol': 1, 'version': None})
        machine.submit()
        machine.submit()
    assert view.reply is None
    lines = [record.getMessage() for record in caplog.records]
    assert sum('publishes no topology (not autoscaling with relay sites)' in line for line in lines) == 1
    assert sum('publishes a topology' in line for line in lines) == 1


@mark.unit
def test_fetch_against_an_old_server_is_silent_and_falls_back(caplog) -> None:
    """An upstream server with no topology method clears the view, logged once at INFO and nothing louder."""
    view = TopologyView(TopologyReply('s:1', {}))
    machine, link = relay_heartbeat(dispatcher=SimpleNamespace(), view=view)
    with caplog.at_level('DEBUG', logger='hypershell.relay.upstream'):
        for _ in range(3):
            assert machine.submit() is HeartbeatState.WAIT
    assert view.reply is None
    assert sum('an earlier release' in record.getMessage() for record in caplog.records) == 1
    assert not [record for record in caplog.records if record.levelname in ('WARNING', 'ERROR', 'CRITICAL')]


@mark.unit
def test_fetch_failure_keeps_the_view(caplog) -> None:
    """A read lost with the link, or a reply that cannot be read, leaves the last topology in place."""
    dispatcher = Dispatcher(published((CPU_ID, Entry('cpu', 2, 0))))
    view = TopologyView()
    machine, link = relay_heartbeat(dispatcher, view=view)
    machine.submit()
    kept = view.reply
    link.down = True
    machine.fetch()
    assert view.reply is kept
    link.down = False
    dispatcher.reply = 'not json'
    with caplog.at_level('WARNING', logger='hypershell.relay.upstream'):
        machine.fetch()
    assert view.reply is kept
    assert any('Topology not read' in record.getMessage() for record in caplog.records)


# --- The relay's fleets and launches -------------------------------------------------------------------

KEY = 'topology-upstream-key-0123456789'


@mark.unit
def test_every_launch_exposes_its_zone_and_is_fenced() -> None:
    """Every fleet's launch names its zone to the launcher (word and variable) and to the client; each
    fleet follows the relay's one topology view with its fallback bounds."""
    relay = RelayThread(address=('127.0.0.1', free_port()), launcher='srun --partition={zone}',
                        zones=['cpu', 'gpu', None],
                        zone_settings={'cpu': {'max': 3}, 'gpu': {'size': 2, 'min': 1}})
    machines = [relay.cluster.autoscaler.machine, *(fleet.machine for fleet in relay.fleets)]
    assert all(isinstance(machine, SiteScaler) and machine.view is relay.view for machine in machines)
    assert relay.cluster.autoscaler.machine.launch is relay.cluster.launch
    for machine, word, zone in zip(machines, ['cpu', 'gpu', 'default'], [['cpu'], ['gpu'], []]):
        argv = machine.launch.argv
        assert argv[:2] == ['srun', f'--partition={word}']
        assert [argv[i + 1] for i, arg in enumerate(argv) if arg == '--zone'] == zone
        assert machine.launch.env == {AUTH_ENV: relay.site_key, ZONE_ENV: word}
    assert relay.advertised == {'cpu': bounds(0, 3, 0), 'gpu': bounds(1, 2, 1), None: bounds(0, 1, 0)}
    assert [(m.init_size, m.min_size, m.max_size) for m in machines] == [(1, 1, 3), (2, 1, 2), (1, 1, 1)]
    fence_site(relay.cluster, KEY, environ={}, exports={}, launches=[fleet.machine.launch for fleet in relay.fleets])


@mark.unit
def test_site_cluster_and_zone_fleet_keep_their_size_shorthand() -> None:
    """Given only a size, a fleet is fixed at that size, as before bounds."""
    cluster = site_cluster(address=('127.0.0.1', free_port()), size=3, zone='cpu')
    machine = cluster.autoscaler.machine
    assert (machine.init_size, machine.min_size, machine.max_size) == (3, 3, 3) and machine.view is None
    fleet = zone_fleet(zone_launch(cluster, 'gpu'), 'gpu', size=2, init_size=1, max_size=4)
    assert (fleet.machine.init_size, fleet.machine.min_size, fleet.machine.max_size) == (1, 2, 4)
    assert fleet.machine.own_zone.name == 'gpu' and fleet.name == 'hypershell-autoscaler-gpu'


# --- A relay under a server autoscaling with relay sites -------------------------------------------------

SERVER_KEY = 'topology-relay-server-key-0123456789'
DEADLINE = 120

RECORDER = '''\
#!/bin/sh
log=$1
shift
printf '%s %s\\n' "${HYPERSHELL_ZONE:--}" "$$" >> "$log"
exec sleep 60
'''


def wait_for(predicate, what: str, deadline: float = DEADLINE) -> None:
    """Wait until `predicate()` holds; fail naming `what` after `deadline` seconds."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.2)
    pytest.fail(f'Timed out waiting for {what}')


def stop(proc: subprocess.Popen) -> None:
    """Interrupt a process group; kill it if it does not end."""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGINT)
            proc.wait(30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(30)
        except ProcessLookupError:
            pass


@mark.integration
def test_relay_follows_a_server_autoscaling_with_sites(temp_site: Path, tmp_path: Path) -> None:
    """A relay serving two zones registers both upstream; work in one zone launches clients only there,
    as many as that zone's advertised `init`, and nothing is ended."""
    port = free_port()
    env = {**os.environ, 'HYPERSHELL_SERVER_AUTH': SERVER_KEY, 'HYPERSHELL_SERVER_EVICT': '30'}
    subprocess.run(['hs', 'submit', '--zone', 'cpu', '-'], input=b'echo 1\necho 2\necho 3\n', env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server_log = tmp_path / 'server.log'
    with open(server_log, 'w') as stream:
        server = subprocess.Popen(['hs', 'server', '--autoscaling=fixed', '-P', '1', '--no-tls',
                                   '-H', '127.0.0.1', '-p', str(port)], env=env, stdout=subprocess.DEVNULL,
                                  stderr=stream, start_new_session=True)
    relay = None
    try:
        launches = tmp_path / 'launches.txt'
        recorder = tmp_path / 'record.sh'
        recorder.write_text(RECORDER)
        config = tmp_path / 'relay.toml'
        config.write_text(f'[relay]\nsite = "A"\nlauncher = "sh {recorder} {launches}"\n'
                          f'[relay.zone.cpu]\ninit = 2\nmax = 3\n[relay.zone.gpu]\nmax = 2\n')
        (tmp_path / 'relay-site').mkdir()
        relay_env = {**{k: v for k, v in os.environ.items() if k != 'HYPERSHELL_DATABASE_FILE'},
                     'HYPERSHELL_SITE': str(tmp_path / 'relay-site'),
                     'HYPERSHELL_CONFIG_FILE': str(config),
                     'HYPERSHELL_RELAY_DATABASE': str(tmp_path / 'relay.db'),
                     'HYPERSHELL_RELAY_UPSTREAM_HOST': '127.0.0.1',
                     'HYPERSHELL_RELAY_UPSTREAM_PORT': str(port),
                     'HYPERSHELL_RELAY_UPSTREAM_AUTH': SERVER_KEY,
                     'HYPERSHELL_RELAY_UPSTREAM_TLS_ENABLED': 'false',
                     'HYPERSHELL_RELAY_UPSTREAM_HEARTRATE': '1'}
        relay_log = tmp_path / 'relay.log'
        with open(relay_log, 'w') as stream:
            relay = subprocess.Popen(['hs', 'relay', '--zone', 'cpu', '--zone', 'gpu',
                                      '--bind', '127.0.0.1', '--port', str(free_port())],
                                     env=relay_env, stdout=subprocess.DEVNULL, stderr=stream, start_new_session=True)
        registered = re.compile(r'Relay site registered \(site: A, zone: (\w+), min: 0, init: [02], max: [23],')
        wait_for(lambda: set(registered.findall(server_log.read_text())) == {'cpu', 'gpu'}, 'both zones registered')
        wait_for(lambda: launches.exists() and len(launches.read_text().splitlines()) >= 2, 'two cpu launches')
        time.sleep(4)  # Later versions launch no more: the fixed policy holds the zone at its init.
        zones = [line.split()[0] for line in launches.read_text().splitlines()]
        assert zones == ['cpu', 'cpu'], zones
        text = relay_log.read_text()
        assert 'Upstream server publishes a topology' in text
        assert re.search(r'Topology \S+ \(zone: cpu, target: 2, seen: 0, launched: 0\) - launching 2', text)
        assert 'Terminating site client' not in text
        assert relay.poll() is None and server.poll() is None
    finally:
        if relay is not None:
            stop(relay)
        stop(server)
