# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Functional tests for `--format=card` on `hs info` (and its `hs wait --info` parity).

These are unit-level and use a freshly-submitted (unscheduled) task, so the card honestly
renders `status: WAITING`. The completed-task path (an `OK` card, and `hs wait --info -f card`
which blocks until completion) is exercised end-to-end by the P5 integration test.
"""


# Type annotations
from __future__ import annotations

# Standard libs
import re
from pathlib import Path

# External libs
from pytest import mark, fixture
from cmdkit.app import exit_status as cli_status

# Internal libs
from hypershell.task import TaskInfoApp, TaskWaitApp
from tests import main, main_lines, create_taskfile


ANSI = re.compile(r'\x1b\[[0-9;]*m')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def strip_ansi(text: str) -> str:
    return ANSI.sub('', text)


@fixture
def pending_task(temp_site: Path) -> str:
    """Submit one task (left unscheduled → WAITING) and return its UUID."""
    taskfile = create_taskfile(temp_site, ['echo hello  # HYPERSHELL: n:0'])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    rc, lines, _ = main_lines(['hs', 'list', 'id'])
    assert rc == cli_status.success
    ids = [line for line in lines if UUID.fullmatch(line)]
    assert len(ids) == 1
    return ids[0]


@mark.unit
class TestCardInfo:
    """`hs info` renders the shared card, and `card` is a format for both info and wait."""

    def test_card_is_a_format_choice_for_info_and_wait(self) -> None:
        """R7/R8: `card` is a valid format for `hs info` and `hs wait --info` (parity)."""
        assert 'card' in TaskInfoApp.output_formats
        assert 'card' in TaskWaitApp.output_formats

    def test_info_renders_one_card(self, pending_task: str) -> None:
        """`hs info <id> --format=card` prints exactly one card with the full id and a badge."""
        rc, out, err = main(['hs', 'info', pending_task, '--format=card'])
        assert rc == cli_status.success
        clean = strip_ansi(out)
        assert clean.count('╭─ task') == 1        # a single card via the shared renderer
        assert pending_task in clean              # full id present
        assert 'status: WAITING' in clean         # a freshly-submitted task is unscheduled

    def test_normal_still_default_for_info(self, pending_task: str) -> None:
        """`hs info` without --format stays the plain `normal` block (no card border)."""
        rc, out, err = main(['hs', 'info', pending_task])
        assert rc == cli_status.success
        assert '╭─ task' not in strip_ansi(out)
