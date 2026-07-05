# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test autoscaler scaling decisions with respect to task groups."""


# Type annotations
from __future__ import annotations
from typing import Dict, Optional, Tuple

# Standard libs
import sys
from pathlib import Path

# External libs
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status

# Internal libs
from tests import main, create_taskfile
from hypershell.cluster.remote import AutoScaler, AutoScalerState
from hypershell.data.model import Task, Client, TaskGroupInfo


def make_scaler(policy: str, clients: int = 0, **opts) -> AutoScaler:
    """New scaler with `clients` stand-ins for launched client processes."""
    scaler = AutoScaler(launcher=['hs', 'client'], policy=policy, **opts)
    scaler.clients = [None] * clients  # Only len() is consulted by check methods
    return scaler


def patch_queries(monkeypatch: MonkeyPatch,
                  group: int = 0,
                  in_group: int = 0,
                  total: int = 0,
                  pressure: Optional[float] = None,
                  connected: int = 0) -> Dict[str, Tuple]:
    """Patch database-backed queries used by scaling checks, capturing `task_pressure` calls."""
    calls = {}
    counts = {None: total, group: in_group}

    def task_pressure(factor: float, group: int = None) -> Optional[float]:
        calls['task_pressure'] = (factor, group)
        return pressure

    monkeypatch.setattr(Task, 'current_group', lambda: TaskGroupInfo(group, 'testing'))
    monkeypatch.setattr(Task, 'count_remaining', lambda group=None: counts.get(group, 0))
    monkeypatch.setattr(Task, 'task_pressure', task_pressure)
    monkeypatch.setattr(Client, 'count_connected', lambda: connected)
    return calls


@mark.unit
class TestCheckDynamic:
    """Scaling decisions for the dynamic policy are relative to the active task group."""

    def test_pressure_computed_for_active_group(self, monkeypatch: MonkeyPatch) -> None:
        """Task pressure is requested for the active task group and high pressure scales."""
        calls = patch_queries(monkeypatch, group=4, in_group=100, total=100, pressure=2.5, connected=1)
        scaler = make_scaler('dynamic', clients=1, factor=2, max_size=4)
        assert scaler.check_dynamic() is AutoScalerState.SCALE
        assert calls['task_pressure'] == (2, 4)

    def test_low_pressure_waits_despite_backlog(self, monkeypatch: MonkeyPatch) -> None:
        """Low in-group pressure does not scale even with a large backlog in later groups."""
        patch_queries(monkeypatch, group=0, in_group=2, total=10_000, pressure=0.25, connected=1)
        scaler = make_scaler('dynamic', clients=1, max_size=8)
        assert scaler.check_dynamic() is AutoScalerState.WAIT

    def test_high_pressure_capped_by_max_size(self, monkeypatch: MonkeyPatch) -> None:
        """High pressure does not scale beyond max-size."""
        patch_queries(monkeypatch, group=0, in_group=500, total=500, pressure=9.0, connected=2)
        scaler = make_scaler('dynamic', clients=2, max_size=2)
        assert scaler.check_dynamic() is AutoScalerState.WAIT

    def test_min_size_scales(self, monkeypatch: MonkeyPatch) -> None:
        """Launched clients below min-size scales regardless of tasks."""
        patch_queries(monkeypatch, group=0, in_group=0, total=0)
        scaler = make_scaler('dynamic', clients=0, min_size=1)
        assert scaler.check_dynamic() is AutoScalerState.SCALE

    def test_unknown_pressure_starved_group_waits(self, monkeypatch: MonkeyPatch) -> None:
        """Do not launch clients for tasks the scheduler will not distribute (later groups)."""
        patch_queries(monkeypatch, group=1, in_group=0, total=10_000)
        scaler = make_scaler('dynamic', clients=0)
        assert scaler.check_dynamic() is AutoScalerState.WAIT

    def test_unknown_pressure_bootstraps_active_group(self, monkeypatch: MonkeyPatch) -> None:
        """Launch first client when the active group has tasks but pressure is unknown."""
        patch_queries(monkeypatch, group=1, in_group=50, total=10_000)
        scaler = make_scaler('dynamic', clients=0)
        assert scaler.check_dynamic() is AutoScalerState.SCALE

    def test_unknown_pressure_with_clients_waits(self, monkeypatch: MonkeyPatch) -> None:
        """Wait on running clients to complete initial tasks before scaling further."""
        patch_queries(monkeypatch, group=1, in_group=50, total=50, connected=1)
        scaler = make_scaler('dynamic', clients=1)
        assert scaler.check_dynamic() is AutoScalerState.WAIT


@mark.unit
class TestCheckFixed:
    """Scaling decisions for the fixed policy are relative to the active task group."""

    def test_min_size_scales(self, monkeypatch: MonkeyPatch) -> None:
        """Launched clients below min-size scales regardless of tasks."""
        patch_queries(monkeypatch, group=0, in_group=0, total=0)
        scaler = make_scaler('fixed', clients=1, min_size=2)
        assert scaler.check_fixed() is AutoScalerState.SCALE

    def test_steady_state_waits(self, monkeypatch: MonkeyPatch) -> None:
        """Launched clients at min-size waits."""
        patch_queries(monkeypatch, group=0, in_group=10, total=10, connected=2)
        scaler = make_scaler('fixed', clients=2, min_size=2)
        assert scaler.check_fixed() is AutoScalerState.WAIT

    def test_starved_group_waits(self, monkeypatch: MonkeyPatch) -> None:
        """Do not launch a client for tasks the scheduler will not distribute (later groups)."""
        patch_queries(monkeypatch, group=1, in_group=0, total=500)
        scaler = make_scaler('fixed', clients=0, min_size=0)
        assert scaler.check_fixed() is AutoScalerState.WAIT

    def test_bootstraps_active_group(self, monkeypatch: MonkeyPatch) -> None:
        """Launch first client when the active group has remaining tasks."""
        patch_queries(monkeypatch, group=1, in_group=3, total=500)
        scaler = make_scaler('fixed', clients=0, min_size=0)
        assert scaler.check_fixed() is AutoScalerState.SCALE


@mark.integration
def test_count_remaining_by_group(temp_site: Path) -> None:
    """Group-scoped counts only include unfinished tasks within the given group."""

    tasks = [
        'echo 0  #HYPERSHELL: group:1',
        'echo 1  #HYPERSHELL: group:1',
        'echo 2  #HYPERSHELL: group:1',
        'echo 3  #HYPERSHELL: group:2',
        'echo 4  #HYPERSHELL: group:2',
    ]

    taskfile = create_taskfile(temp_site, tasks)
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.success, stderr

    # NOTE: query in a subprocess so the engine binds to the temp site environment
    rc, stdout, stderr = main([sys.executable, '-c', (
        'from hypershell.data.model import Task\n'
        'print(Task.count_remaining(),\n'
        '      Task.count_remaining(group=1),\n'
        '      Task.count_remaining(group=2),\n'
        '      Task.count_remaining(group=9),\n'
        '      Task.current_group().value)\n'
    )])
    assert rc == 0, stderr
    assert stdout == '5 3 2 0 1'
