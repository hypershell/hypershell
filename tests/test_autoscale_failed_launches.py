# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
An autoscaling cluster whose launches all fail stops after `autoscale.failed_launches` of them.

The autoscaler counts launched clients that exit non-zero in a row while no client is registered
with the server; a client exiting 0, a client connected, or a registration since the last check
ends the run. At the limit it launches no more and the cluster stops its server and raises. With
the limit at 0, and in every relay fleet, it relaunches without end, as before.

The new names are reached through their module, so each test here fails on its own (not the whole
module at import) on a tree without them.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List, Tuple, Final

# Standard libs
import os
import sys
import json
import re
import time
from pathlib import Path
from datetime import datetime
from subprocess import run, Popen, PIPE, TimeoutExpired

# External libs
import pytest
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace

# Internal libs
from tests.conftest import free_port
from tests.test_zone_model import task_db  # noqa: fixture
from tests.test_zone_autoscale import FleetDB, FakeProc, FakeLaunch, run_checks, DEFAULT, CPU
from tests.test_cluster_exit import run_child, kill_group
from hypershell.core.config import default, reload_env, ConfigurationError
from hypershell.core.logging import INSTANCE
from hypershell.data.model import Task, Client
from hypershell.cluster import remote
from hypershell.cluster.remote import AutoScaler, AutoScalerState, AutoScalingCluster
from hypershell.relay.site import site_cluster, zone_launch, zone_fleet, site_scaler, SiteScaler


ADDRESS: Final[str] = '127.0.0.1'
"""Bind address of every cluster here, so nothing depends on resolving this host's name."""

STOP_BOUND: Final[float] = 25
"""Seconds within which a cluster with a one-second period must have stopped after three failed launches."""

HS: Final[str] = os.path.join(os.path.dirname(sys.executable), 'hs')
"""This tree's `hs`."""


class ScriptedLaunch(FakeLaunch):
    """A launch whose clients exit at once with the next status of `statuses` (1 once they run out)."""

    def __init__(self, zone: str, started: List[FakeProc], statuses: List[int]) -> None:
        super().__init__(zone, started)
        self.statuses = statuses

    def start(self, **options) -> FakeProc:
        proc = super().start(**options)
        proc.status = self.statuses.pop(0) if self.statuses else 1
        return proc


def failing_scaler(statuses: List[int] = None, init_size: int = 1, **options: Any) -> Tuple[AutoScaler, List[FakeProc]]:
    """A fixed fleet of one in the default zone whose clients exit with `statuses`, started up to steady state."""
    started: List[FakeProc] = []
    launch = ScriptedLaunch('default', started, list(statuses or []))
    scaler = AutoScaler(launch, policy='fixed', period=3600, init_size=init_size, min_size=0, max_size=1,
                        **options)
    state = scaler.init()
    while state is AutoScalerState.SCALE:
        scaler.scale()
        state = scaler.init()
    return scaler, started


def fleet_db(monkeypatch: MonkeyPatch, remaining: Dict[int, Dict[str, int]]) -> FleetDB:
    """The stand-in database readings, including the own zone's count read by a fleet without discovery."""
    db = FleetDB(monkeypatch, remaining=remaining)
    monkeypatch.setattr(Task, 'count_remaining',
                        lambda group=None, zone=None: db.remaining.get(group or 0, {}).get(zone, 0))
    return db


def registrations_of(counts: List[Tuple[int, int]]) -> Callable[[], Tuple[int, int]]:
    """A registrations reader returning the last of `counts` (the list may be changed between checks)."""
    return lambda: counts[-1]


# --- The autoscaler's count --------------------------------------------------------------------------------


@mark.unit
class TestFailedLaunches:
    """The run of failed launches at each check, and where it ends."""

    def test_three_failures_stop(self, monkeypatch: MonkeyPatch) -> None:
        """Three launched clients in a row exiting non-zero with none registered end the machine; no fourth launch."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        scaler, started = failing_scaler(failed_launches=3, registrations=lambda: (0, 0))
        states = run_checks(scaler, 10)
        assert states == [AutoScalerState.SCALE, AutoScalerState.SCALE, AutoScalerState.FINAL]
        assert scaler.exhausted and len(started) == 3
        assert scaler.failures == [(proc.pid, 'default', 1) for proc in started]
        assert scaler.actions[AutoScalerState.FINAL]() is AutoScalerState.HALT

    def test_an_exit_zero_ends_the_run(self, monkeypatch: MonkeyPatch) -> None:
        """Fail, fail, 0, fail, fail does not stop; one more failure does."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        scaler, started = failing_scaler([1, 1, 0, 1, 1, 1], failed_launches=3, registrations=lambda: (0, 0))
        states = run_checks(scaler, 5)
        assert AutoScalerState.FINAL not in states and not scaler.exhausted
        assert [status for _, _, status in scaler.failures] == [1, 1]
        assert run_checks(scaler, 1) == [AutoScalerState.FINAL]
        assert len(started) == 6

    def test_signal_and_status_are_both_failures(self, monkeypatch: MonkeyPatch) -> None:
        """A client ended by a signal counts as a failed launch, as a non-zero status does."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        scaler, _ = failing_scaler([-9, 2, 255], failed_launches=3, registrations=lambda: (0, 0))
        assert run_checks(scaler, 5)[-1] is AutoScalerState.FINAL
        assert [status for _, _, status in scaler.failures] == [-9, 2, 255]

    def test_a_registration_since_the_last_check_ends_the_run(self, monkeypatch: MonkeyPatch) -> None:
        """A client that registered and left between two checks resets the run; that check's exits count for none."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        counts = [(0, 0)]
        scaler, _ = failing_scaler(failed_launches=3, registrations=registrations_of(counts))
        run_checks(scaler, 2)
        assert len(scaler.failures) == 2
        counts.append((1, 0))  # registered and gone again
        run_checks(scaler, 1)
        assert scaler.failures == [] and scaler.seen == 1
        assert AutoScalerState.FINAL not in run_checks(scaler, 2)
        assert run_checks(scaler, 1) == [AutoScalerState.FINAL]

    def test_a_connected_client_never_lets_it_stop(self, monkeypatch: MonkeyPatch) -> None:
        """While a client is connected (another zone's, say), failed launches are relaunched without end."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        scaler, started = failing_scaler(failed_launches=3, registrations=lambda: (1, 1))
        states = run_checks(scaler, 10)
        assert set(states) == {AutoScalerState.SCALE} and not scaler.exhausted
        assert len(started) == 11

    def test_zero_never_stops_and_never_reads_registrations(self, monkeypatch: MonkeyPatch) -> None:
        """With the limit at 0 nothing is counted and the registrations are never read."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})

        def unread() -> Tuple[int, int]:
            raise AssertionError('registrations read with the limit off')

        scaler, started = failing_scaler(failed_launches=0, registrations=unread)
        states = run_checks(scaler, 10)
        assert set(states) <= {AutoScalerState.SCALE, AutoScalerState.WAIT}
        assert not scaler.exhausted and scaler.failures == [] and len(started) == 11

    def test_off_by_default(self, monkeypatch: MonkeyPatch) -> None:
        """An autoscaler built without the setting relaunches failed clients without end."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4}})
        scaler, started = failing_scaler()
        assert scaler.failed_launches == 0 and scaler.registrations is None
        assert AutoScalerState.FINAL not in run_checks(scaler, 10) and len(started) == 11

    def test_every_zone_counts_together(self, monkeypatch: MonkeyPatch) -> None:
        """Failures in different zones form one run, named by pid and zone, in launch order."""
        fleet_db(monkeypatch, {0: {DEFAULT.id: 4, CPU.id: 4}})
        started: List[FakeProc] = []
        scaler = AutoScaler(ScriptedLaunch('default', started, []), policy='fixed', period=3600,
                            init_size=1, min_size=0, max_size=1,
                            launch_for=lambda zone: ScriptedLaunch(zone.name, started, []),
                            failed_launches=3, registrations=lambda: (0, 0))
        while scaler.init() is AutoScalerState.SCALE:
            scaler.scale()
        assert run_checks(scaler, 5)[-1] is AutoScalerState.FINAL
        assert [zone for _, zone, _ in scaler.failures] == ['default', 'default', 'cpu']
        assert [pid for pid, _, _ in scaler.failures] == [proc.pid for proc in started[:3]]


@mark.unit
def test_each_failed_launch_is_warned_about_by_pid_and_zone(monkeypatch: MonkeyPatch, caplog) -> None:
    """The warning at each non-zero exit names the client's process id and zone, and its status."""
    fleet_db(monkeypatch, {0: {CPU.id: 4}})
    started: List[FakeProc] = []
    scaler = AutoScaler(ScriptedLaunch('cpu', started, [3]), policy='fixed', period=3600, init_size=1,
                        min_size=0, max_size=1, own_zone=CPU)
    while scaler.init() is AutoScalerState.SCALE:
        scaler.scale()
    with caplog.at_level('WARNING', logger='hypershell'):
        run_checks(scaler, 1)
    warned = [record.getMessage() for record in caplog.records if 'exited with status' in record.getMessage()]
    assert warned == [f'Autoscale client ({started[0].pid}, zone: cpu) exited with status 3']


# --- Relay fleets never stop -------------------------------------------------------------------------------


@mark.unit
def test_relay_fleets_are_off() -> None:
    """A relay's site cluster and each further zone's fleet never count failed launches."""
    cluster = site_cluster(address=(ADDRESS, free_port()))
    assert isinstance(cluster.autoscaler.machine, SiteScaler)
    assert cluster.autoscaler.machine.failed_launches == 0
    fleet = zone_fleet(zone_launch(cluster, 'gpu'), 'gpu', size=2, period=7)
    assert fleet.machine.failed_launches == 0


@mark.unit
def test_site_scaler_on_a_failing_launch_never_stops(monkeypatch: MonkeyPatch) -> None:
    """A fixed site fleet whose every client fails keeps relaunching, even made from a counting autoscaler."""
    fleet_db(monkeypatch, {0: {CPU.id: 4}})
    started: List[FakeProc] = []
    launch = ScriptedLaunch('cpu', started, [])
    counting = AutoScaler(launch, policy='fixed', period=3600, init_size=1, min_size=1, max_size=1, own_zone=CPU,
                          failed_launches=3, registrations=lambda: (0, 0))
    machine = site_scaler(counting, launch, view=None)
    assert machine.failed_launches == 0
    while machine.init() is AutoScalerState.SCALE:
        machine.scale()
    states = run_checks(machine, 10)
    assert AutoScalerState.FINAL not in states and not machine.exhausted
    assert len(started) == 11


# --- The cluster's defaults and its setting ----------------------------------------------------------------


@mark.unit
def test_cluster_counts_three_by_default() -> None:
    """An autoscaling cluster stops at the default of 3, reading registrations from the database; 0 is passed on."""
    machine = AutoScalingCluster(launcher='false', bind=(ADDRESS, free_port())).autoscaler.machine
    assert machine.failed_launches == remote.DEFAULT_AUTOSCALE_FAILED_LAUNCHES == 3
    assert machine.registrations is remote.client_registrations
    off = AutoScalingCluster(launcher='false', bind=(ADDRESS, free_port()), failed_launches=0).autoscaler.machine
    assert off.failed_launches == 0


@mark.integration
def test_client_registrations_count_this_server_only(task_db) -> None:
    """Every row this process's server stamped counts once; those not disconnected count as now; others never."""
    assert remote.client_registrations() == (0, 0)
    Client.add_all([
        Client.new(),                                              # connected now
        Client.new(disconnected_at=datetime.now()),                # came and went
        Client.new(server_id='00000000-0000-0000-0000-000000000000', server_host='earlier'),  # an earlier server
    ])
    assert remote.client_registrations() == (2, 1)
    assert INSTANCE != '00000000-0000-0000-0000-000000000000'


def with_config(monkeypatch: MonkeyPatch, **sources: Namespace) -> None:
    """Read the setting from a configuration of the defaults and `sources` (env from the environment)."""
    monkeypatch.setattr(remote, 'config', Configuration(default=default, **sources, env=reload_env()))


@mark.unit
class TestSetting:
    """`autoscale.failed_launches`, from a file or its environment form, and its refusals."""

    def test_default(self, monkeypatch: MonkeyPatch) -> None:
        with_config(monkeypatch)
        assert remote.configured_failed_launches() == 3

    def test_file(self, monkeypatch: MonkeyPatch) -> None:
        with_config(monkeypatch, user=Namespace({'autoscale': {'failed_launches': 0}}))
        assert remote.configured_failed_launches() == 0

    def test_environment(self, monkeypatch: MonkeyPatch) -> None:
        """The environment form arrives split at every underscore, and takes precedence over a file."""
        monkeypatch.setenv('HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES', '5')
        with_config(monkeypatch, user=Namespace({'autoscale': {'failed_launches': 1}}))
        assert remote.configured_failed_launches() == 5

    @mark.parametrize('value', ['-1', 'true', 'abc', '1.5'])
    def test_refused_from_environment(self, monkeypatch: MonkeyPatch, value: str) -> None:
        monkeypatch.setenv('HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES', value)
        with_config(monkeypatch)
        with pytest.raises(ConfigurationError, match='from: HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES'):
            remote.configured_failed_launches()

    @mark.parametrize('value', [-2, True, 'three'])
    def test_refused_from_a_file(self, monkeypatch: MonkeyPatch, value: Any) -> None:
        with_config(monkeypatch, user=Namespace({'autoscale': {'failed_launches': value}}))
        with pytest.raises(ConfigurationError, match='autoscale.failed_launches'):
            remote.configured_failed_launches()


@mark.integration
def test_cluster_refuses_a_bad_setting(temp_site: Path) -> None:
    """`hs cluster --autoscaling` refuses a bad setting before it starts anything, naming its source."""
    (Path(temp_site) / 't.in').write_text('echo 1\n')
    env = {**os.environ, 'HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES': '-1'}
    done = run([HS, 'cluster', 't.in', '--no-tls', '--autoscaling', 'fixed', '-H', ADDRESS, '--launcher', 'false'],
               cwd=temp_site, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == exit_status.bad_config, done.stderr
    assert 'HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES' in done.stderr


# --- The cluster stops ------------------------------------------------------------------------------------


# Builds an autoscaling cluster whose launcher always fails, from the JSON options in argv[1], and
# joins it for at most `wait` seconds (then stops it). Prints what it saw as JSON.
SCALE_SCRIPT: Final[str] = """\
import sys, json, time, logging
records = []
class Capture(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())
logging.getLogger('hypershell').addHandler(Capture(level=logging.WARNING))
from hypershell.data import initdb
from hypershell.data.model import Task
from hypershell.cluster.remote import AutoScalingCluster
options = json.loads(sys.argv[1])
wait = options.pop('wait')
initdb()
cluster = AutoScalingCluster(source=[f'echo {n}' for n in range(4)], tls=None, launcher='false', policy='fixed',
                             period=1, bind=tuple(options.pop('bind')), **options)
started = time.monotonic()
cluster.start()
error, waiting = None, False
try:
    cluster.join(timeout=wait)
    waiting = cluster.is_alive()
    if waiting:
        cluster.stop(wait=True)
    cluster.join()
except Exception as exc:
    error = {'type': type(exc).__name__, 'message': str(exc)}
print(json.dumps({
    'error': error, 'waiting': waiting, 'elapsed': time.monotonic() - started,
    'server_alive': cluster.server.is_alive(), 'remaining': Task.count_remaining(),
    'warnings': records,
}))
"""


def run_scaling(workdir: Path, **options: Any) -> Dict[str, Any]:
    """Run an autoscaling cluster with a failing launcher in a child process; return what it saw."""
    port = free_port()
    options = {'bind': [ADDRESS, port], **options}
    stdout = run_child([sys.executable, '-c', SCALE_SCRIPT, json.dumps(options)], workdir)
    return {**json.loads(stdout.strip().splitlines()[-1]), 'port': port}


@mark.integration
def test_cluster_stops_after_three_failed_launches(temp_site: Path) -> None:
    """The cluster stops its server and raises, naming the three launches, their statuses and the address."""
    seen = run_scaling(Path(temp_site), wait=STOP_BOUND)
    assert not seen['waiting'] and not seen['server_alive'], seen
    assert seen['error'] is not None and seen['error']['type'] == 'ClusterLaunchError', seen
    message = seen['error']['message']
    assert message.startswith('3 launched clients in a row exited non-zero with no client registered ('), message
    assert message.endswith(f'; clients were told to dial {ADDRESS}:{seen["port"]}'), message
    warned = [re.fullmatch(r'Autoscale client \((\d+), zone: default\) exited with status 1', text)
              for text in seen['warnings'] if 'exited with status' in text]
    assert len(warned) == 3 and all(warned), seen['warnings']
    for match in warned:
        assert f'{match.group(1)} (zone: default) status 1' in message
    assert seen['remaining'] == 4


@mark.integration
def test_cluster_with_the_limit_off_keeps_relaunching(temp_site: Path) -> None:
    """With the limit at 0 the cluster is still relaunching at the same bound, with no error."""
    seen = run_scaling(Path(temp_site), wait=12, failed_launches=0)
    assert seen['waiting'] and seen['error'] is None, seen
    assert sum('exited with status 1' in text for text in seen['warnings']) >= 4, seen['warnings']


OPEN_INPUT_TIMEOUT: Final[float] = 30
"""Seconds an autoscaling cluster reading input that stays open is given before the test fails."""

OPEN_INPUT_BOUND: Final[float] = 15
"""Seconds from the start within which that cluster must have exited: three launches, two seconds apart, fail."""


@mark.integration
def test_cluster_reading_open_input_stops_after_three_failed_launches(temp_site: Path) -> None:
    """Tasks on standard input from a producer that stays open: the cluster still exits 5, keeping the committed task."""
    site = Path(temp_site)
    argv = [HS, 'cluster', '--no-tls', '-H', ADDRESS, '-p', str(free_port()),
            '--autoscaling', 'fixed', '-P', '2s', '--launcher', 'false']
    stderr_path = site / 'open-input.stderr'
    with open(stderr_path, mode='w') as stderr, open(os.devnull, mode='w') as stdout:
        started = time.monotonic()
        proc = Popen(argv, cwd=site, stdin=PIPE, stdout=stdout, stderr=stderr, start_new_session=True, text=True)
        try:
            proc.stdin.write('echo 1\n')
            proc.stdin.flush()
            try:
                proc.wait(timeout=OPEN_INPUT_TIMEOUT)
            except TimeoutExpired:
                pytest.fail(f'Cluster did not exit within {OPEN_INPUT_TIMEOUT}s:\n' + stderr_path.read_text()[-4000:])
            elapsed = time.monotonic() - started
        finally:
            kill_group(proc)
            proc.stdin.close()
    log_text = stderr_path.read_text()
    assert proc.returncode == exit_status.runtime_error, log_text[-4000:]
    assert elapsed <= OPEN_INPUT_BOUND, log_text[-4000:]
    assert '3 launched clients in a row exited non-zero' in log_text
    assert 'Fatal Python error' not in log_text
    counted = run([HS, 'list', '--count'], cwd=site, capture_output=True, text=True, timeout=60)
    assert counted.returncode == 0 and counted.stdout.strip() == '1', counted.stdout + counted.stderr
