# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test `hs update` filtering, including the --cancelled and --signal aliases (parity with list), and
combined changes: every change given lands on every task counted as updated."""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Tuple

# Standard libs
import os
import json
import sqlite3
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status as cli_status

# Internal libs
from tests import main, main_lines, create_taskfile


def submit_with_statuses(temp_site: Path, statuses: dict) -> None:
    """Submit one task per tag in `statuses` and set each task's exit_status."""
    taskfile = create_taskfile(temp_site, [f'echo {tag}  # HYPERSHELL: n:{tag}' for tag in statuses])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    for tag, status in statuses.items():
        assert main(['hs', 'update', f'exit_status={status}', '-t', f'n:{tag}', '--no-confirm'])[0] == \
            cli_status.success


@mark.integration
def test_update_cancelled_filter(temp_site: Path) -> None:
    """`hs update --cancelled` matches only cancelled tasks (exit_status == -1)."""
    submit_with_statuses(temp_site, {0: -1, 1: -15, 2: 0})

    # Revert only the cancelled task.
    assert main(['hs', 'update', '--revert', '--cancelled', '--no-confirm'])[0] == cli_status.success

    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['null']   # reverted
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:1'])[1] == ['-15']    # untouched
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:2'])[1] == ['0']      # untouched


@mark.integration
def test_update_signal_filter(temp_site: Path) -> None:
    """`hs update --signal NAME` matches only tasks killed by that signal (exit_status == -N)."""
    submit_with_statuses(temp_site, {0: -15, 1: -9})   # SIGTERM, SIGKILL

    # Revert only the SIGTERM task.
    assert main(['hs', 'update', '--revert', '--signal', 'TERM', '--no-confirm'])[0] == cli_status.success

    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['null']   # reverted
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:1'])[1] == ['-9']     # SIGKILL untouched


@mark.integration
def test_update_signal_unknown_errors(temp_site: Path) -> None:
    """An unknown --signal name is a usage error (before any change is applied)."""
    submit_with_statuses(temp_site, {0: -15})
    rc, _, _ = main(['hs', 'update', '--revert', '--signal', 'BOGUS', '--no-confirm'])
    assert rc != cli_status.success
    # The task was not touched.
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['-15']


def submit_tagged(temp_site: Path, tags: List[str]) -> None:
    """Submit one task per inline tag list (`KEY:VALUE` words, or a bare name)."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: {tag} n:{n}' for n, tag in enumerate(tags)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success


def stored() -> Dict[int, Tuple[int, Any, Dict[str, Any]]]:
    """Every task's group, exit status and tag (without `n`), by its `n` tag, read from the database file."""
    conn = sqlite3.connect(f'file:{os.environ["HYPERSHELL_DATABASE_FILE"]}?mode=ro', uri=True)
    try:
        found = conn.execute('SELECT "group", exit_status, tag FROM task').fetchall()
    finally:
        conn.close()
    result = {}
    for group, status, tag in found:
        tag = json.loads(tag)
        result[tag.pop('n')] = (group, status, tag)
    return result


def updated(stderr: str) -> List[str]:
    """The "Updated N tasks" lines."""
    return [line for line in stderr.splitlines() if 'Updated' in line and 'tasks' in line]


@mark.integration
def test_update_limit_applies_every_change(temp_site: Path) -> None:
    """`--limit` with a field and a tag change applies both to every task it updates."""
    submit_tagged(temp_site, [''] * 4)

    rc, _, stderr = main(['hs', 'update', '--limit', '2', '-f', 'group=7', 'color:red'])

    assert rc == cli_status.success
    after = stored()
    written = sorted(n for n, (group, _, _) in after.items() if group == 7)
    assert len(written) == 2, after
    assert {n: after[n] for n in written} == {n: (7, None, {'color': 'red'}) for n in written}
    assert {n: data for n, data in after.items() if n not in written} == \
        {n: (0, None, {}) for n in after if n not in written}
    assert any('Updated 2 tasks' in line for line in updated(stderr)), stderr


@mark.integration
def test_update_tag_reaches_rows_moved_out_of_filter(temp_site: Path) -> None:
    """A tag change lands on tasks the field change takes out of the filter."""
    submit_tagged(temp_site, [''] * 4)

    rc, _, stderr = main(['hs', 'update', '-f', 'group=7', 'color:blue', '-w', 'group==0'])

    assert rc == cli_status.success
    assert stored() == {n: (7, None, {'color': 'blue'}) for n in range(4)}
    assert any('Updated 4 tasks' in line for line in updated(stderr)), stderr


@mark.integration
def test_update_cancel_and_tag_together(temp_site: Path) -> None:
    """`--cancel` with a tag change cancels and tags every remaining task."""
    submit_tagged(temp_site, [''] * 4)

    rc, _, stderr = main(['hs', 'update', '-f', '-R', '--cancel', 'color:green'])

    assert rc == cli_status.success
    assert stored() == {n: (0, -1, {'color': 'green'}) for n in range(4)}
    assert any('Updated 4 tasks' in line for line in updated(stderr)), stderr


# Several tag keys, several removals, and a set with a removal, each under a tag filter the change
# takes the tasks out of: (inline tags of the four matching tasks, the update, the tag every one of
# them is left with). A fifth task, tagged color:green, never matches.
TAG_CASES = {
    'keys': ('color:red', ['color:blue', 'size:big', '-f', '-t', 'color:red'], {'color': 'blue', 'size': 'big'}),
    'removals': ('color:red size:big', ['-f', '--remove-tag', 'color', 'size', '-t', 'color:red'], {}),
    'set_and_remove': ('color:red size:big', ['color:blue', '--remove-tag', 'size', '-f', '-t', 'color:red'],
                       {'color': 'blue'}),
}


@mark.integration
@mark.parametrize('case', list(TAG_CASES))
def test_update_tag_keys_and_removals(temp_site: Path, case: str) -> None:
    """Every tag key set and every tag name removed lands on every task counted as updated."""
    tags, argv, expected = TAG_CASES[case]
    submit_tagged(temp_site, [tags] * 4 + ['color:green'])

    rc, _, stderr = main(['hs', 'update', *argv])

    assert rc == cli_status.success
    assert stored() == {**{n: (0, None, expected) for n in range(4)}, 4: (0, None, {'color': 'green'})}
    assert any('Updated 4 tasks' in line for line in updated(stderr)), stderr


@mark.integration
def test_update_name_only_tags_check_every_tag(temp_site: Path) -> None:
    """Two name-only tag filters both apply: only the task with both tags is updated."""
    submit_tagged(temp_site, ['a b', 'b', 'a'])

    rc, _, stderr = main(['hs', 'update', '-f', 'group=3', '-t', 'a', 'b'])

    assert rc == cli_status.success
    assert {n: group for n, (group, _, _) in stored().items()} == {0: 3, 1: 0, 2: 0}
    assert any('Updated 1 tasks' in line for line in updated(stderr)), stderr
