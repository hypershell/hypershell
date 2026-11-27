# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for `Task.status_label` lifecycle derivation."""


# Type annotations
from __future__ import annotations

# Standard libs
from datetime import datetime

# External libs
from pytest import mark

# Internal libs
from hypershell.data.model import Task, CANCEL_STATUS


# A fixed timestamp for the schedule/completion columns; the value is irrelevant to the
# derivation (only NULL vs. set matters), so one constant serves every case.
NOW = datetime(2026, 1, 1, 12, 0, 0)


def make(*, scheduled: bool = True, completed: bool = True, exit_status: int = None) -> Task:
    """Construct a transient Task with only the lifecycle-relevant columns set."""
    return Task(
        schedule_time=(NOW if scheduled else None),
        completion_time=(NOW if completed else None),
        exit_status=exit_status,
    )


@mark.unit
class TestStatusLabel:
    """The lifecycle -> status-label ladder (order-critical; honors exit_status ranges)."""

    def test_waiting_when_unscheduled(self) -> None:
        """schedule_time NULL dominates every later column."""
        assert make(scheduled=False, completed=False).status_label == 'WAITING'
        assert make(scheduled=False, completed=True, exit_status=0).status_label == 'WAITING'

    def test_running_when_scheduled_but_incomplete(self) -> None:
        """Scheduled with no completion_time is in-flight (or interrupted, pre-revert)."""
        assert make(scheduled=True, completed=False).status_label == 'RUNNING'
        assert make(scheduled=True, completed=False, exit_status=0).status_label == 'RUNNING'

    def test_ok_on_zero_exit(self) -> None:
        assert make(exit_status=0).status_label == 'OK'

    def test_cancelled_on_cancel_status(self) -> None:
        """CANCEL_STATUS (-1) must be classified before generic signal deaths."""
        assert make(exit_status=CANCEL_STATUS).status_label == 'CANCELLED'
        assert make(exit_status=-1).status_label == 'CANCELLED'

    def test_error_on_never_ran_sentinel(self) -> None:
        """The documented <= -1000 range (TASK_TEMPLATE_ERROR / TASK_RESOURCE_ERROR, and future)."""
        assert make(exit_status=-1001).status_label == 'ERROR'
        assert make(exit_status=-1002).status_label == 'ERROR'
        assert make(exit_status=-5000).status_label == 'ERROR'

    def test_killed_on_signal_death(self) -> None:
        """Signal deaths (-2..-64); -1 is excluded (that is CANCEL_STATUS)."""
        assert make(exit_status=-2).status_label == 'KILLED'
        assert make(exit_status=-9).status_label == 'KILLED'
        assert make(exit_status=-64).status_label == 'KILLED'

    def test_failed_on_positive_exit(self) -> None:
        assert make(exit_status=1).status_label == 'FAILED'
        assert make(exit_status=127).status_label == 'FAILED'

    def test_unknown_when_completed_without_exit_status(self) -> None:
        """Defensive: completed but exit_status NULL, and the None-before-numeric ordering
        must not raise on `None < 0`."""
        assert make(completed=True, exit_status=None).status_label == 'UNKNOWN'
