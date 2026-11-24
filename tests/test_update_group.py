# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` writes a task group only when it is an integer by the rule of `hs submit -g`.

A group change, `group=VALUE` in the filtered form or `ID group VALUE` in the single-task form, is
taken as `hs submit -g/--group` takes a group: an integer in the 32-bit range, stored as that integer.
Anything else exits with status 2, naming the value, before anything is counted, asked or written,
and no other change given with it lands. Each test runs on SQLite and, with `HS_TEST_POSTGRES`, on
PostgreSQL.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from pathlib import Path

# Standard libs
import json

# External libs
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from tests import create_taskfile
from tests.input_checks import bounded, db_site, rows  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)


REFUSED: List[Tuple[str, str]] = [
    ('x', 'x'),
    ('empty', ''),
    ('float', '1.5'),
    ('exp', '1e3'),
    ('hex', '0x10'),
    ('true', 'true'),
    ('none', 'none'),
    ('null', 'null'),
    ('nan', 'nan'),
    ('above', '2147483648'),
    ('below', '-2147483649'),
]
"""Group change values that are not a task group, by test id."""

ACCEPTED: List[Tuple[str, str, int]] = [
    ('zero-padded', '07', 7),
    ('plus', '+5', 5),
    ('plain', '3', 3),
    ('min', '-2147483648', -2**31),
    ('max', '2147483647', 2**31 - 1),
]
"""Group change values that `hs submit -g` accepts, and the group each gives, by test id."""

FORMS: List[str] = ['filtered', 'single']
"""`group=VALUE -t n:1 -f` (filtered) and `ID group VALUE` (single-task), on the task tagged n:1."""


def submit_three(site: Path) -> None:
    """Three tasks tagged n:0, n:1 and n:2, all in group 0 and never run."""
    taskfile = create_taskfile(site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    rc, _, stderr = bounded(['hs', 'submit', str(taskfile)], cwd=site)
    assert rc == 0, stderr


def tag_of(tag: Any) -> Dict[str, Any]:
    """A stored tag as a dictionary (text on SQLite, JSON on PostgreSQL)."""
    return (json.loads(tag) if isinstance(tag, str) else tag) or {}


def snapshot(engine: Engine) -> List[Tuple[Any, ...]]:
    """Every task's id, group, command, exit status, attempt and tag, read through the private engine."""
    return sorted((str(task_id), group, args, status, attempt, json.dumps(tag_of(tag), sort_keys=True))
                  for task_id, group, args, status, attempt, tag
                  in rows(engine, 'SELECT id, "group", args, exit_status, attempt, tag FROM task'))


def task_id(engine: Engine, n: int) -> str:
    """The id of the task tagged n:`n`, as text."""
    found = [str(task_id) for task_id, tag in rows(engine, 'SELECT id, tag FROM task') if tag_of(tag).get('n') == n]
    assert len(found) == 1, found
    return found[0]


def update_argv(engine: Engine, form: str, value: str, *extra: str) -> List[str]:
    """The `hs update` command for a group change to `value` on the task tagged n:1, in `form`."""
    if form == 'filtered':
        return ['hs', 'update', f'group={value}', *extra, '-t', 'n:1']
    return ['hs', 'update', task_id(engine, 1), 'group', value, *extra]


def assert_refused(rc: int, stdout: str, stderr: str, value: str) -> None:
    """Exit status 2, a line naming the group and the value, and nothing counted, asked or written."""
    assert rc == 2, stderr[-4000:]
    assert any('group' in line and value in line for line in stderr.splitlines()), stderr[-4000:]
    for output in (stdout, stderr):
        assert 'Searching database' not in output
        assert 'affects' not in output and 'continue?' not in output
        assert 'Updated' not in output


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('name, value', REFUSED, ids=[name for name, _ in REFUSED])
def test_group_change_refused(db_site: Tuple[Path, Engine], form: str, name: str, value: str) -> None:
    """A group change that is not a task group exits 2 naming the value; every row is unchanged."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = bounded(update_argv(engine, form, value, '-f'), cwd=site)
    assert_refused(rc, stdout, stderr, value)
    assert snapshot(engine) == before


@mark.integration
@mark.parametrize('form', ['filtered-prompt', 'single-no-confirm'])
@mark.parametrize('value', ['x', 'none', '2147483648'])
def test_group_change_refused_f_and_prompt(db_site: Tuple[Path, Engine], form: str, value: str) -> None:
    """Without `-f`, a "yes" at the prompt writes nothing, since nothing is counted or asked; with `-f`, the same."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    if form == 'filtered-prompt':
        argv = update_argv(engine, 'filtered', value)
    else:
        argv = update_argv(engine, 'single', value, '-f')
    rc, stdout, stderr = bounded(argv, stdin='yes\n', cwd=site)
    assert_refused(rc, stdout, stderr, value)
    assert snapshot(engine) == before


@mark.integration
def test_group_refusal_writes_no_other_change(db_site: Tuple[Path, Engine]) -> None:
    """A refused group given with another field change and a tag change writes neither."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = bounded(['hs', 'update', 'group=x', 'args=changed', 'color:red', '-t', 'n:1', '-f'],
                                 cwd=site)
    assert_refused(rc, stdout, stderr, 'x')
    assert snapshot(engine) == before


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('name, value, group', ACCEPTED, ids=[name for name, _, _ in ACCEPTED])
def test_group_change_accepted(db_site: Tuple[Path, Engine], form: str, name: str, value: str, group: int) -> None:
    """A group `hs submit -g` accepts is written as that integer, to the task asked for only (guard)."""
    site, engine = db_site
    submit_three(site)
    target = task_id(engine, 1)
    rc, _, stderr = bounded(update_argv(engine, form, value, '-f'), cwd=site)
    assert rc == 0, stderr[-4000:]
    stored = {str(task_id): stored for task_id, stored in rows(engine, 'SELECT id, "group" FROM task')}
    assert stored == {**dict.fromkeys(stored, 0), target: group}
    assert type(stored[target]) is int
    if engine.dialect.name == 'sqlite':
        assert rows(engine, 'SELECT typeof("group") FROM task WHERE id = :id', id=target) == [('integer', )]
