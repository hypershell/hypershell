# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test client-side task execution helpers (executor slot)."""


# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark

# Internal libs
from hypershell.client import TaskExecutor, task_env
from hypershell.data.model import Task


@mark.unit
def test_task_executor_slot() -> None:
    """The executor slot is its 1-based id re-based to 0; slot_count is retained."""
    assert TaskExecutor(id=1, inbound=None, outbound=None, slot_count=4).slot == 0
    assert TaskExecutor(id=4, inbound=None, outbound=None, slot_count=4).slot == 3
    assert TaskExecutor(id=2, inbound=None, outbound=None, slot_count=4).slot_count == 4


@mark.unit
def test_task_env_slot(temp_site: Path) -> None:
    """task_env exposes TASK_SLOT and TASK_SLOT_COUNT for the executing slot."""
    env = task_env(Task.new('echo hello'), 2, 4)
    assert env['TASK_SLOT'] == '2'
    assert env['TASK_SLOT_COUNT'] == '4'


@mark.unit
def test_task_env_slot_defaults(temp_site: Path) -> None:
    """task_env defaults to slot 0 / count 1 so the variables are always defined."""
    env = task_env(Task.new('echo hello'))
    assert env['TASK_SLOT'] == '0'
    assert env['TASK_SLOT_COUNT'] == '1'
