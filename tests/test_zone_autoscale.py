# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Zone-aware autoscaling: per-zone demand, the readings it decides on, and the autoscaler that applies it."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional

# Standard libs
from itertools import product

# External libs
from pytest import mark, MonkeyPatch
from sqlalchemy.engine import Engine

# Internal libs
from tests.test_zone_model import task_db, add_tasks, finish  # noqa: fixture
from hypershell.cluster import demand
from hypershell.cluster.demand import (AutoScalerPolicy, ZoneRef, ScalingKnobs, ZoneLoad,
                                       read_zone_loads, zone_demand, fleet_demand)
from hypershell.cluster.remote import AutoScaler, AutoScalerState
from hypershell.cluster.launch import client_launch, LaunchActuator
from hypershell.data.model import (Task, Client, Zone, TaskGroupInfo, zone_id, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME,
                                   CANCEL_STATUS)


DEFAULT = ZoneRef(DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME)
CPU = ZoneRef(zone_id('cpu'), 'cpu')
GPU = ZoneRef(zone_id('gpu'), 'gpu')


def knobs(policy: str = 'fixed', factor: float = 1.0, init_size: int = 0,
          min_size: int = 0, max_size: int = 2) -> ScalingKnobs:
    """Sizing knobs with small defaults."""
    return ScalingKnobs(AutoScalerPolicy.from_name(policy), factor, init_size, min_size, max_size)


def load(zone: ZoneRef = DEFAULT, remaining: int = 0, retryable: int = 0, pressure: Optional[float] = None) -> ZoneLoad:
    """One zone's reading."""
    return ZoneLoad(zone, remaining=remaining, retryable=retryable, pressure=pressure)


def scales_today(policy: str, launched: int, work: int, pressure: Optional[float],
                 min_size: int, max_size: int) -> bool:
    """The single-fleet decision of the autoscaler before zones: True to launch one client."""
    if launched < min_size:
        return True
    if policy == 'dynamic' and pressure is not None:
        return pressure > 1 and launched < max_size
    return launched == 0 and work > 0


@mark.unit
class TestZoneDemand:
    """The desired client count of one zone at one check."""

    @mark.parametrize('policy, launched, work, pressure, expected', [
        ('fixed', 0, 0, None, 0),
        ('fixed', 0, 5, None, 1),
        ('fixed', 1, 5, None, 1),
        ('fixed', 3, 5, None, 3),
        ('dynamic', 0, 0, None, 0),
        ('dynamic', 0, 5, None, 1),
        ('dynamic', 1, 5, None, 1),
        ('dynamic', 1, 5, 2.5, 2),
        ('dynamic', 1, 5, 0.5, 1),
        ('dynamic', 2, 5, 9.0, 2),
        ('dynamic', 4, 0, 9.0, 4),
    ])
    def test_table(self, policy: str, launched: int, work: int, pressure: Optional[float], expected: int) -> None:
        """Each row of the table, in the own zone with no minimum and a max-size of 2."""
        want = zone_demand(load(remaining=work, pressure=pressure), launched, knobs(policy, max_size=2),
                           own=True, initial=False)
        assert want == expected

    @mark.parametrize('policy', ['fixed', 'dynamic'])
    @mark.parametrize('min_size', [0, 1, 2])
    def test_own_zone_matches_single_fleet(self, policy: str, min_size: int) -> None:
        """In the own zone, demand scales exactly when the single-fleet autoscaler did, by one client."""
        pressures = [None, 0.5, 1.0, 2.5] if policy == 'dynamic' else [None]
        for launched, work, pressure, max_size in product([0, 1, 2, 3], [0, 1, 50], pressures, [1, 2, 4]):
            want = zone_demand(load(remaining=work, pressure=pressure), launched,
                               knobs(policy, min_size=min_size, max_size=max_size), own=True, initial=False)
            assert want in (launched, launched + 1)
            assert (want > launched) is scales_today(policy, launched, work, pressure, min_size, max_size)

    @mark.parametrize('policy', ['fixed', 'dynamic'])
    def test_other_zone_has_no_init_or_min(self, policy: str) -> None:
        """Init-size and min-size apply to the own zone only; another zone is sized from its work."""
        sizes = knobs(policy, init_size=3, min_size=2, max_size=4)
        assert zone_demand(load(CPU), 0, sizes, own=False, initial=True) == 0
        assert zone_demand(load(CPU), 0, sizes, own=False, initial=False) == 0
        assert zone_demand(load(CPU, remaining=4), 0, sizes, own=False, initial=False) == 1
        assert zone_demand(load(CPU, remaining=4), 1, sizes, own=False, initial=False) == 1
        assert zone_demand(load(), 0, sizes, own=True, initial=True) == 3
        assert zone_demand(load(), 4, sizes, own=True, initial=True) == 4
        assert zone_demand(load(), 1, sizes, own=True, initial=False) == 2

    def test_retryable_counts_as_work(self) -> None:
        """Failures awaiting a retry are work: a zone holding only those gets a client."""
        assert load(CPU, retryable=2).work == 2
        assert zone_demand(load(CPU, retryable=2), 0, knobs(), own=False, initial=False) == 1

    def test_never_below_launched(self) -> None:
        """Demand never asks for fewer clients than are launched; clients leave by idle exit."""
        for launched in range(5):
            for policy in ('fixed', 'dynamic'):
                for pressure in (None, 0.1, 5.0):
                    assert zone_demand(load(CPU, pressure=pressure), launched, knobs(policy, max_size=1),
                                       own=False, initial=False) >= launched


@mark.unit
class TestFleetDemand:
    """The desired client count of every zone at one check."""

    def test_initial_launches_own_zone_only(self) -> None:
        """The initial launch sizes the own zone, even with no reading for it."""
        desired = fleet_demand({}, {}, knobs(init_size=2), own=DEFAULT, initial=True)
        assert desired == {DEFAULT: 2}
        desired = fleet_demand({CPU.id: load(CPU, remaining=5)}, {}, knobs(init_size=2), own=GPU, initial=True)
        assert desired == {GPU: 2, CPU: 0}

    def test_each_zone_decided_on_its_own(self) -> None:
        """Every zone is decided on its own load and fleet under the same knobs."""
        loads = {DEFAULT.id: load(DEFAULT, remaining=0),
                 CPU.id: load(CPU, remaining=3, pressure=4.0),
                 GPU.id: load(GPU, remaining=3, pressure=None)}
        desired = fleet_demand(loads, {DEFAULT.id: 1, CPU.id: 1}, knobs('dynamic', min_size=1, max_size=2),
                               own=DEFAULT, initial=False)
        assert desired == {DEFAULT: 1, CPU: 2, GPU: 1}

    def test_without_own_zone(self) -> None:
        """With no own zone, only the zones with a reading are decided, and none has a minimum."""
        loads = {CPU.id: load(CPU, remaining=1)}
        assert fleet_demand(loads, {}, knobs(min_size=3), own=None, initial=False) == {CPU: 1}
        assert fleet_demand(loads, {}, knobs(init_size=3), own=None, initial=True) == {CPU: 0}


def make_scaler(policy: str, clients: int = 0, **opts) -> AutoScaler:
    """New scaler with `clients` stand-ins for launched client processes."""
    launch = client_launch('hs', ('localhost', 0), 'x' * 32, num_threads=1, bundlesize=1, bundlewait=5,
                           template='{}', delay_start=0, task_signalwait=10)
    scaler = AutoScaler(launch=launch, policy=policy, **opts)
    scaler.clients = [None] * clients
    return scaler


@mark.unit
@mark.parametrize('policy', ['fixed', 'dynamic'])
def test_autoscaler_decides_through_demand(monkeypatch: MonkeyPatch, policy: str) -> None:
    """The autoscaler's check reads its own zone only, scoped to the current group, and scales as before."""
    asked: Dict[str, List] = {}
    reading = {'work': 0, 'pressure': None}

    def count_remaining(group: int = None, zone: str = None) -> int:
        asked['count_remaining'].append((group, zone))
        return reading['work'] if group == 3 else 0

    def task_pressure(factor: float, group: int = None, zone: str = None) -> Optional[float]:
        asked['task_pressure'].append((factor, group, zone))
        return reading['pressure']

    def unexpected(*args, **kwargs) -> None:
        raise AssertionError('discovery is off')

    monkeypatch.setattr(Task, 'current_group', lambda: TaskGroupInfo(3, 'testing'))
    monkeypatch.setattr(Task, 'count_remaining', count_remaining)
    monkeypatch.setattr(Task, 'task_pressure', task_pressure)
    monkeypatch.setattr(Task, 'count_remaining_by_zone', unexpected)
    monkeypatch.setattr(Client, 'count_connected', lambda: 0)
    for launched, work, pressure, min_size, max_size in product([0, 1, 2], [0, 7], [None, 0.5, 3.0],
                                                                [0, 1, 2], [1, 3]):
        asked.update(count_remaining=[], task_pressure=[])
        reading.update(work=work, pressure=pressure)
        scaler = make_scaler(policy, clients=launched, factor=2, min_size=min_size, max_size=max_size)
        state = scaler.check_fixed() if policy == 'fixed' else scaler.check_dynamic()
        expected = scales_today(policy, launched, work, pressure if policy == 'dynamic' else None,
                                min_size, max_size)
        assert state is (AutoScalerState.SCALE if expected else AutoScalerState.WAIT)
        assert asked['count_remaining'] == [(3, DEFAULT_ZONE_ID)]
        assert asked['task_pressure'] == ([(2, 3, DEFAULT_ZONE_ID)] if policy == 'dynamic' else [])
        assert scaler.pending == ({DEFAULT: launched + 1} if expected else {})


@mark.unit
def test_autoscaler_own_named_zone(monkeypatch: MonkeyPatch) -> None:
    """A named own zone is read by its id, and its initial size launches into it."""
    asked = []
    monkeypatch.setattr(Task, 'current_group', lambda: TaskGroupInfo(0, 'testing'))
    monkeypatch.setattr(Task, 'count_remaining', lambda group=None, zone=None: asked.append(zone) or 4)
    monkeypatch.setattr(Client, 'count_connected', lambda: 0)
    scaler = make_scaler('fixed', clients=0, own_zone=ZoneRef.from_name('gpu'))
    assert scaler.own_zone == GPU
    assert scaler.check_fixed() is AutoScalerState.SCALE
    assert asked == [GPU.id]
    assert scaler.pending == {GPU: 1}
    assert make_scaler('fixed', init_size=2).init() is AutoScalerState.SCALE
    assert make_scaler('fixed', clients=2, init_size=2).init() is AutoScalerState.WAIT


@mark.unit
def test_zone_ref_from_name() -> None:
    """None, '' and the reserved name give the default zone; other names keep their name."""
    assert ZoneRef.from_name(None) == ZoneRef.from_name('') == ZoneRef.from_name('default') == DEFAULT
    assert ZoneRef.from_name('gpu') == GPU
    assert DEFAULT.is_default and not GPU.is_default


@mark.integration
class TestReadZoneLoads:
    """Readings of the database, scoped to the current group, on a real SQLite file."""

    @staticmethod
    def setup_zones() -> None:
        """Record the named zones, as a submit does."""
        Zone.ensure('cpu')
        Zone.ensure('gpu')

    def test_discovery(self, task_db: Engine) -> None:
        """Every zone with work in the group is read; a zone with work only in a later group is not."""
        self.setup_zones()
        add_tasks(2, group=0)
        add_tasks(3, CPU.id, group=0)
        add_tasks(4, GPU.id, group=1)
        loads = read_zone_loads(0, knobs(), own=DEFAULT, discover=True, attempts=1, eager=False)
        assert loads == {DEFAULT_ZONE_ID: load(DEFAULT, remaining=2), CPU.id: load(CPU, remaining=3)}
        loads = read_zone_loads(1, knobs(), own=DEFAULT, discover=True, attempts=1, eager=False)
        assert loads == {DEFAULT_ZONE_ID: load(DEFAULT), GPU.id: load(GPU, remaining=4)}

    def test_own_zone_only_without_discovery(self, task_db: Engine) -> None:
        """Without discovery only the own zone is read, scoped to its zone and the group."""
        self.setup_zones()
        add_tasks(2, group=0)
        add_tasks(3, CPU.id, group=0)
        loads = read_zone_loads(0, knobs(), own=CPU, discover=False, attempts=1, eager=False)
        assert loads == {CPU.id: load(CPU, remaining=3)}
        loads = read_zone_loads(0, knobs(), own=DEFAULT, discover=False, attempts=1, eager=False)
        assert loads == {DEFAULT_ZONE_ID: load(DEFAULT, remaining=2)}

    def test_default_zone_is_keyed_by_its_id(self, task_db: Engine) -> None:
        """The default zone is found from NULL rows and keyed by its reserved id, with or without an own zone."""
        add_tasks(2, group=0)
        loads = read_zone_loads(0, knobs(), own=None, discover=True, attempts=1, eager=False)
        assert loads == {DEFAULT_ZONE_ID: load(DEFAULT, remaining=2)}

    @mark.parametrize('attempts, eager, counted', [(1, False, False), (2, False, True), (2, True, True),
                                                       (1, True, False)])
    def test_retry_edge(self, task_db: Engine, attempts: int, eager: bool, counted: bool) -> None:
        """A named zone whose only work is a failure awaiting a retry is found when the server would retry it."""
        self.setup_zones()
        cpu_id, = add_tasks(1, CPU.id, group=0)
        default_id, = add_tasks(1, group=0)
        Task.next(limit=10, group=0, attempts=attempts, eager=eager)
        finish(cpu_id, 1)
        finish(default_id, 1)
        loads = read_zone_loads(0, knobs(), own=DEFAULT, discover=True, attempts=attempts, eager=eager)
        expected = {DEFAULT_ZONE_ID: load(DEFAULT)}
        if counted:
            expected[CPU.id] = load(CPU, retryable=1)
        assert loads == expected

    def test_cancelled_is_not_work(self, task_db: Engine) -> None:
        """A cancelled task is terminal and is never counted as work awaiting a retry."""
        self.setup_zones()
        cpu_id, = add_tasks(1, CPU.id, group=0)
        finish(cpu_id, CANCEL_STATUS)
        assert read_zone_loads(0, knobs(), own=None, discover=True, attempts=3, eager=True) == {}

    def test_unnamed_zone_is_skipped(self, task_db: Engine, monkeypatch: MonkeyPatch) -> None:
        """A zone id with no recorded name cannot be launched into, so it is skipped with a warning."""
        warned = []
        monkeypatch.setattr(demand.log, 'warning', warned.append)
        Zone.ensure('cpu')
        add_tasks(1, CPU.id, group=0)
        add_tasks(1, GPU.id, group=0)
        loads = read_zone_loads(0, knobs(), own=DEFAULT, discover=True, attempts=1, eager=False)
        assert loads == {DEFAULT_ZONE_ID: load(DEFAULT), CPU.id: load(CPU, remaining=1)}
        assert len(warned) == 1 and GPU.id in warned[0]

    def test_pressure_under_dynamic_only(self, task_db: Engine, monkeypatch: MonkeyPatch) -> None:
        """Pressure is read per zone and group under the dynamic policy, and never under the fixed one."""
        asked = []
        monkeypatch.setattr(Task, 'task_pressure', lambda factor, group=None, zone=None: asked.append(zone) or 1.5)
        self.setup_zones()
        add_tasks(1, CPU.id, group=0)
        loads = read_zone_loads(0, knobs('fixed'), own=DEFAULT, discover=True, attempts=1, eager=False)
        assert asked == [] and {zone.pressure for zone in loads.values()} == {None}
        loads = read_zone_loads(0, knobs('dynamic', factor=2), own=DEFAULT, discover=True, attempts=1, eager=False)
        assert sorted(asked) == sorted([DEFAULT_ZONE_ID, CPU.id])
        assert loads[CPU.id].pressure == 1.5


class FakeProc:
    """A stand-in for a launched client process."""

    def __init__(self, zone: str, pid: int) -> None:
        self.zone, self.pid, self.status = zone, pid, None

    def poll(self) -> Optional[int]:
        return self.status


class FakeLaunch:
    """A stand-in launch of one zone that records the processes it starts."""

    def __init__(self, zone: str, started: List[FakeProc]) -> None:
        self.zone, self.started = zone, started

    def start(self, **options) -> FakeProc:
        proc = FakeProc(self.zone, len(self.started) + 1)
        self.started.append(proc)
        return proc

    def describe(self) -> str:
        return f'shim {self.zone}'


def make_actuator(own: ZoneRef = DEFAULT) -> tuple:
    """An actuator over fake launches, with the started processes and the zones built."""
    started, built = [], []

    def launch_for(zone: ZoneRef) -> FakeLaunch:
        built.append(zone.name)
        return FakeLaunch(zone.name, started)

    return LaunchActuator(FakeLaunch(own.name, started), own=own, launch_for=launch_for), started, built


@mark.unit
class TestLaunchActuator:
    """Starting clients zone by zone, and counting them per zone."""

    def test_apply_launches_the_shortfall(self) -> None:
        """Each zone gets the clients it lacks to reach its desired count, and no more."""
        actuator, started, built = make_actuator()
        assert actuator.apply({DEFAULT: 2, CPU: 1}) == 3
        assert [proc.zone for proc in started] == ['default', 'default', 'cpu']
        assert actuator.launched() == {DEFAULT.id: 2, CPU.id: 1}
        assert actuator.apply({DEFAULT: 1, CPU: 2, GPU: 0}) == 1
        assert actuator.launched() == {DEFAULT.id: 2, CPU.id: 2}
        assert built == ['cpu']
        assert actuator.clients == started

    def test_launch_of_a_zone_is_built_once(self) -> None:
        """A zone's launch is built at its first client and kept; the own zone's is the one given."""
        actuator, started, built = make_actuator(own=GPU)
        for _ in range(3):
            actuator.launch_one(CPU)
            actuator.launch_one()
        assert built == ['cpu']
        assert [proc.zone for proc in started] == ['cpu', 'gpu'] * 3
        assert actuator.launched() == {CPU.id: 3, GPU.id: 3}

    def test_clean_drops_exited_and_never_terminates(self) -> None:
        """Exited clients are dropped from their zone's count; nothing is ever stopped."""
        actuator, started, _ = make_actuator()
        actuator.apply({DEFAULT: 1, CPU: 2})
        started[1].status = 0
        started[2].status = 3
        assert actuator.clean() == started[1:]
        assert actuator.clients == started[:1]
        assert actuator.launched() == {DEFAULT.id: 1}
        assert actuator.apply({CPU: 1}) == 1

    def test_assigned_clients_count_as_own(self) -> None:
        """A process the actuator did not start counts in the own zone; its own keep their zone."""
        actuator, started, _ = make_actuator(own=CPU)
        actuator.apply({GPU: 1})
        actuator.clients = [*actuator.clients, None, None]
        assert actuator.launched() == {GPU.id: 1, CPU.id: 2}
        actuator.clients = [None]
        assert actuator.launched() == {CPU.id: 1}

    def test_without_launch_for_every_zone_uses_the_one_launch(self) -> None:
        """With no builder, every client is started with the one launch given."""
        started = []
        actuator = LaunchActuator(FakeLaunch('default', started))
        actuator.apply({CPU: 1})
        assert [proc.zone for proc in started] == ['default']
        assert actuator.launched() == {CPU.id: 1}


@mark.unit
def test_autoscaler_scales_through_its_actuator() -> None:
    """The autoscaler's launch and clients are its actuator's; a scale launches what its check left pending."""
    started = []
    scaler = AutoScaler(FakeLaunch('default', started), policy='fixed',
                        launch_for=lambda zone: FakeLaunch(zone.name, started))
    assert scaler.launch is scaler.actuator.launch
    assert scaler.clients is scaler.actuator.clients
    scaler.scale()
    assert [proc.zone for proc in started] == ['default']
    scaler.pending = {CPU: 1, DEFAULT: 2}
    scaler.scale()
    assert [proc.zone for proc in started] == ['default', 'cpu', 'default']
    assert scaler.pending == {}
    assert scaler.launched() == {DEFAULT.id: 2, CPU.id: 1}
    scaler.clients = []
    assert scaler.launched() == {}
