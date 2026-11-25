# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` refuses what it would ignore: `--delete` with a change, and the single-task form
`hs update ID FIELD VALUE` with an action option, `--remove-tag` or a selection option (exit status 2,
nothing written). The filtered form's selection options select as they did.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Tuple

# Standard libs
import os
import json
import sys
import sqlite3
import subprocess
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status as cli_status

# Internal libs
from tests import main, main_lines, create_taskfile


def submit_three(temp_site: Path) -> None:
    """Three tasks tagged n:0, n:1 and n:2, all in group 0 and never run."""
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success


def rows() -> List[Tuple[Any, ...]]:
    """Every task's id, group, exit status, schedule and completion time and tag, read directly."""
    conn = sqlite3.connect(f'file:{os.environ["HYPERSHELL_DATABASE_FILE"]}?mode=ro', uri=True)
    try:
        return sorted(conn.execute('SELECT id, "group", exit_status, schedule_time, completion_time, tag '
                                   'FROM task').fetchall())
    finally:
        conn.close()


def task_id(n: int) -> str:
    """The id of the task tagged n:`n`."""
    rc, lines, _ = main_lines(['hs', 'list', 'id', '-t', f'n:{n}'])
    assert rc == cli_status.success and len(lines) == 1
    return lines[0]


DELETE_WITH_CHANGES: Dict[str, Tuple[List[str], List[str]]] = {
    # name: (argv after `hs update`, words the message must name)
    'field-limit': (['group=9', '--delete', '--limit', '1', '-f'], ['group=9']),
    'tag-limit': (['foo:bar', '--delete', '--limit', '1', '-f'], ['foo:bar']),
    'remove-tag-limit': (['--delete', '--limit', '1', '-f', '--remove-tag', 'foo'], ['--remove-tag', 'foo']),
    'field-where': (['group=9', '--delete', '-w', 'attempt == 1', '-f'], ['group=9']),
    'field-tag-filter': (['group=9', '--delete', '-t', 'n:1', '-f'], ['group=9']),
    'field-and-tag': (['group=9', 'foo:bar', '--delete', '-t', 'n:1', '-f'], ['group=9', 'foo:bar']),
    'no-filter': (['group=9', '--delete', '-f'], ['group=9']),
}


@mark.integration
@mark.parametrize('case', sorted(DELETE_WITH_CHANGES))
def test_delete_with_change_refused(temp_site: Path, case: str) -> None:
    """`--delete` with a change exits 2, naming `--delete` and the change, and deletes and writes nothing."""
    argv, named = DELETE_WITH_CHANGES[case]
    submit_three(temp_site)
    before = rows()
    rc, stdout, stderr = main(['hs', 'update', *argv])
    assert rc == cli_status.bad_argument, stderr
    assert '--delete' in stderr
    for word in named:
        assert word in stderr
    assert 'Searching database' not in stderr
    assert 'Deleted' not in stderr and 'Updated' not in stderr
    assert rows() == before


@mark.integration
def test_delete_with_change_refused_before_prompt(temp_site: Path) -> None:
    """Without `-f`, the refusal comes before the count and the prompt, so a "yes" deletes nothing."""
    submit_three(temp_site)
    before = rows()
    proc = subprocess.run(['hs', 'update', 'group=9', '--delete', '-t', 'n:1'], input=b'yes\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ, timeout=60)
    stdout, stderr = proc.stdout.decode(), proc.stderr.decode()
    assert proc.returncode == cli_status.bad_argument, stderr
    assert 'continue?' not in stdout and 'continue?' not in stderr
    assert 'affects' not in stdout and 'affects' not in stderr
    assert rows() == before


@mark.integration
def test_delete_with_change_refused_before_database(temp_site: Path) -> None:
    """The refusal comes before the database is opened: a fresh site gets no database file."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    assert not database.exists()
    rc, _, stderr = main(['hs', 'update', 'group=9', '--delete', '-f'])
    assert rc == cli_status.bad_argument, stderr
    assert not database.exists()


@mark.integration
@mark.parametrize('limit', [[], ['--limit', '1']])
def test_delete_with_filters_only_still_deletes(temp_site: Path, limit: List[str]) -> None:
    """`--delete` with filters only deletes exactly the matching task, as before (guard)."""
    submit_three(temp_site)
    gone = task_id(1)
    rc, _, stderr = main(['hs', 'update', '--delete', '-t', 'n:1', *limit, '-f'])
    assert rc == cli_status.success, stderr
    assert [row[0] for row in rows()] == sorted(task_id(n) for n in (0, 2))
    assert gone not in [row[0] for row in rows()]


LEGACY_OPTIONS: Dict[str, List[str]] = {
    'cancel': ['--cancel'],
    'revert': ['--revert'],
    'delete': ['--delete'],
    'remove-tag': ['--remove-tag', 'n'],
}


@mark.integration
@mark.parametrize('option', sorted(LEGACY_OPTIONS))
def test_legacy_form_with_option_refused(temp_site: Path, option: str) -> None:
    """`hs update ID FIELD VALUE` with an action option or `--remove-tag` exits 2, naming it; nothing written."""
    submit_three(temp_site)
    before = rows()
    words = LEGACY_OPTIONS[option]
    rc, _, stderr = main(['hs', 'update', task_id(1), 'group', '7', '-f', *words])
    assert rc == cli_status.bad_argument, stderr
    assert words[0] in stderr
    assert rows() == before


@mark.integration
def test_legacy_form_still_writes(temp_site: Path) -> None:
    """The single-task form without those options writes the field, as before (guard)."""
    submit_three(temp_site)
    target = task_id(1)
    rc, _, stderr = main(['hs', 'update', target, 'group', '7'])
    assert rc == cli_status.success, stderr
    assert {row[0]: row[1] for row in rows()}[target] == 7


SELECTION: Dict[str, Tuple[List[str], List[str], str]] = {
    # name: (the option as given to the single-task form, as given to the filtered form, the long name)
    'where-short': (['-w', 'exit_status == 7'], ['-w', 'exit_status == 7'], '--where'),
    'where-long': (['--where', 'exit_status == 7'], ['--where', 'exit_status == 7'], '--where'),
    'with-tag-short': (['-t', 'n:1'], ['-t', 'n:1'], '--with-tag'),
    'with-tag-long': (['--with-tag', 'n:1'], ['--with-tag', 'n:1'], '--with-tag'),
    'group-short': (['-g', '0'], ['-g', '0'], '--group'),
    'group-long': (['--group', '1'], ['--group', '1'], '--group'),
    'limit-short': (['-l', '0'], ['-l', '2'], '--limit'),  # the filtered form refuses -l 0 (bounded-inputs)
    'limit-long': (['--limit', '1'], ['--limit', '1'], '--limit'),
    'order-by-short': (['-s', 'args'], ['-s', 'args', '-l', '2'], '--order-by'),
    'order-by-long': (['--order-by', 'args'], ['--order-by', 'args', '-l', '2'], '--order-by'),
    'desc': (['--desc'], ['-s', 'args', '-l', '2', '--desc'], '--desc'),
    'failed-short': (['-F'], ['-F'], '--failed'),
    'failed-long': (['--failed'], ['--failed'], '--failed'),
    'succeeded-short': (['-S'], ['-S'], '--succeeded'),
    'succeeded-long': (['--succeeded'], ['--succeeded'], '--succeeded'),
    'completed-short': (['-C'], ['-C'], '--completed'),
    'completed-long': (['--completed'], ['--completed'], '--completed'),
    'remaining-short': (['-R'], ['-R'], '--remaining'),
    'remaining-long': (['--remaining'], ['--remaining'], '--remaining'),
    'cancelled-short': (['-X'], ['-X'], '--cancelled'),
    'cancelled-long': (['--cancelled'], ['--cancelled'], '--cancelled'),
    'retries': (['--retries'], ['--retries'], '--retries'),
    'signal': (['--signal', 'KILL'], ['--signal', 'KILL'], '--signal'),
}
"""Every selection option, by short and long name where it has both, and how each is given."""

SELECTED: Dict[str, Any] = {
    # name: the tasks (by n) the filtered form writes on the five tasks of `submit_five`; an int is a count
    'where-short': {2}, 'where-long': {2},
    'with-tag-short': {1}, 'with-tag-long': {1},
    'group-short': {0, 1, 2, 3}, 'group-long': {4},
    'limit-short': 2, 'limit-long': 1,
    'order-by-short': {0, 1}, 'order-by-long': {0, 1}, 'desc': {3, 4},
    'failed-short': {2, 3, 4}, 'failed-long': {2, 3, 4},
    'succeeded-short': {1}, 'succeeded-long': {1},
    'completed-short': {1, 2, 3, 4}, 'completed-long': {1, 2, 3, 4},
    'remaining-short': {0}, 'remaining-long': {0},
    'cancelled-short': {3}, 'cancelled-long': {3},
    'retries': {2},
    'signal': {4},
}
"""What the filtered form writes for each selection option, as it does today."""


@mark.integration
@mark.parametrize('confirm', [[], ['-f']], ids=['prompt', 'no-confirm'])
@mark.parametrize('option', sorted(SELECTION))
def test_legacy_form_with_selection_refused(temp_site: Path, option: str, confirm: List[str]) -> None:
    """`hs update ID FIELD VALUE` with a selection option exits 2 naming it, with or without `-f`; nothing written."""
    submit_three(temp_site)
    before = rows()
    words, _, name = SELECTION[option]
    rc, _, stderr = main(['hs', 'update', task_id(1), 'group', '7', *words, *confirm])
    assert rc == cli_status.bad_argument, stderr
    assert name in stderr
    assert rows() == before


@mark.integration
def test_legacy_form_names_every_option(temp_site: Path) -> None:
    """One message names every selection option given, and shows the filtered form for a guarded write."""
    submit_three(temp_site)
    before = rows()
    rc, _, stderr = main(['hs', 'update', task_id(1), 'group', '7',
                          '-w', 'exit_status == 7', '-t', 'n:1', '-s', 'args', '-l', '1', '--desc'])
    assert rc == cli_status.bad_argument, stderr
    for name in ('--where', '--with-tag', '--order-by', '--limit', '--desc'):
        assert name in stderr
    assert '-w "id == ID"' in stderr
    assert rows() == before


@mark.integration
def test_legacy_form_names_action_and_selection(temp_site: Path) -> None:
    """An action option and a selection option given together are both named."""
    submit_three(temp_site)
    before = rows()
    rc, _, stderr = main(['hs', 'update', task_id(1), 'group', '7', '--cancel', '-F'])
    assert rc == cli_status.bad_argument, stderr
    assert '--cancel' in stderr and '--failed' in stderr
    assert rows() == before


def submit_five(temp_site: Path) -> None:
    """Five tasks tagged n:0 to n:4, set by `hs update` to cover every selection option.

    n:0 never ran; n:1 exited 0; n:2 exited 7 on its second attempt; n:3 was cancelled (exit -1);
    n:4 was killed by SIGKILL (exit -9) and is in group 1. All others are in group 0.
    """
    taskfile = create_taskfile(temp_site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(5)])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    for n, changes in ((1, ['exit_status=0']), (2, ['exit_status=7', 'attempt=2']), (3, ['exit_status=-1']),
                       (4, ['exit_status=-9', 'group=1'])):
        rc, _, stderr = main(['hs', 'update', *changes, '-t', f'n:{n}', '-f'])
        assert rc == cli_status.success, stderr


def written() -> List[int]:
    """The tasks (by n) whose tag holds hit:yes."""
    hits = [json.loads(tag) for *_, tag in rows()]
    return sorted(tag['n'] for tag in hits if tag.get('hit') == 'yes')


@mark.integration
@mark.parametrize('option', sorted(SELECTION))
def test_filtered_form_selection_unchanged(temp_site: Path, option: str) -> None:
    """The filtered form writes exactly the tasks each selection option matches, as before (guard)."""
    submit_five(temp_site)
    _, words, _ = SELECTION[option]
    rc, _, stderr = main(['hs', 'update', 'hit:yes', *words, '-f'])
    assert rc == cli_status.success, stderr
    expected = SELECTED[option]
    if isinstance(expected, int):
        assert len(written()) == expected
    else:
        assert written() == sorted(expected)


@mark.integration
def test_filtered_form_selection_with_prompt(temp_site: Path) -> None:
    """The filtered form with a selection option and no `-f` asks, and a "yes" writes the match (guard)."""
    submit_three(temp_site)
    proc = subprocess.run(['hs', 'update', 'hit:yes', '-t', 'n:1'], input=b'yes\n',
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ, timeout=60)
    assert proc.returncode == cli_status.success, proc.stderr.decode()
    assert 'continue?' in proc.stdout.decode()
    assert written() == [1]


@mark.integration
@mark.parametrize('confirm', [[], ['-f']], ids=['prompt', 'no-confirm'])
def test_legacy_form_alone(temp_site: Path, confirm: List[str]) -> None:
    """The single-task form with no selection option writes the task, with or without `-f` (guard)."""
    submit_three(temp_site)
    target = task_id(1)
    rc, _, stderr = main(['hs', 'update', target, 'group', '7', *confirm])
    assert rc == cli_status.success, stderr
    assert {row[0]: row[1] for row in rows()} == {**{task_id(n): 0 for n in (0, 2)}, target: 7}
