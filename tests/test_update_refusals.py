# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` refuses what it would ignore: `--delete` with a change, and the single-task form
`hs update ID FIELD VALUE` with an action option or `--remove-tag` (exit status 2, nothing written).
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Tuple

# Standard libs
import os
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
