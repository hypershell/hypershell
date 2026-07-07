# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test task cancellation is terminal and never re-scheduled by the server."""


# Type annotations
from __future__ import annotations

# Standard libs
import re
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, create_taskfile


def submit_pending(temp_site: Path, count: int) -> None:
    """Submit `count` uniquely-tagged echo tasks to the database as pending (not run)."""
    tasks = [f'echo RUN_{n}  # HYPERSHELL: n:{n}' for n in range(count)]
    taskfile = create_taskfile(temp_site, tasks)
    rc, _, _ = main(['hs', 'submit', str(taskfile)])
    assert rc == exit_status.success


@mark.integration
def test_cancel_is_terminal(temp_site: Path) -> None:
    """Cancelling a task marks it terminal: exit_status is the sentinel and completion_time is set.

    A cancelled task that only had `schedule_time`/`exit_status` set (but no `completion_time`)
    reads as "interrupted" to the scheduler and gets reverted/re-run - so completion_time
    must be populated for the cancellation to stick.
    """
    submit_pending(temp_site, 3)

    rc, _, _ = main(['hs', 'update', '--cancel', '-t', 'n:1', '--no-confirm'])
    assert rc == exit_status.success

    rc, out, _ = main_lines(['hs', 'list', 'exit_status', '-t', 'n:1'])
    assert out == ['-1']

    rc, out, _ = main_lines(['hs', 'list', 'completion_time', '-t', 'n:1'])
    assert out not in (['null'], [''])


@mark.integration
def test_cancelled_task_not_reverted_on_restart(temp_site: Path) -> None:
    """A cancelled task must not be reverted and re-run by `hs cluster --restart`.

    Regression: `--cancel` left `completion_time` NULL, so `revert_interrupted()` on restart
    treated the cancellation-set `schedule_time` as an interruption, reverted the task, and
    re-ran it (flipping exit_status from the cancel sentinel to 0).
    """
    submit_pending(temp_site, 4)

    rc, _, _ = main(['hs', 'update', '--cancel', '-t', 'n:2', '--no-confirm'])
    assert rc == exit_status.success

    rc, stdout, _ = main(['hs', 'cluster', '--restart'])
    assert rc == exit_status.success

    # The pending tasks run; the cancelled one does not.
    assert set(re.findall(r'RUN_\d', stdout)) == {'RUN_0', 'RUN_1', 'RUN_3'}
    assert 'RUN_2' not in stdout

    # It remains cancelled, not completed successfully.
    rc, out, _ = main_lines(['hs', 'list', 'exit_status', '-t', 'n:2'])
    assert out == ['-1']


@mark.integration
def test_cancelled_task_not_retried(temp_site: Path) -> None:
    """A cancelled task must not be picked up as a "failed" task for retry.

    Regression: the retry query (`select_failed`) matched any `exit_status != 0`, so with
    retries enabled the cancel sentinel (-1) looked like a failure and was re-scheduled.
    """
    submit_pending(temp_site, 3)

    rc, _, _ = main(['hs', 'update', '--cancel', '-t', 'n:2', '--no-confirm'])
    assert rc == exit_status.success

    # File-mode cluster (not --restart, so no revert path) with retries enabled: once the
    # pending tasks are exhausted the failed-task retry query runs - it must skip the cancelled task.
    extra = create_taskfile(temp_site, ['echo EXTRA  # HYPERSHELL: m:0'], filename='extra.in')
    rc, stdout, _ = main(['hs', 'cluster', str(extra), '-r2'])
    assert rc == exit_status.success

    assert 'RUN_2' not in stdout

    rc, out, _ = main_lines(['hs', 'list', 'exit_status', '-t', 'n:2'])
    assert out == ['-1']

    # No retry copy was created (a retry would add a second row with attempt=2).
    rc, out, _ = main_lines(['hs', 'list', 'attempt', '-t', 'n:2'])
    assert out == ['1']
