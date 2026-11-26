# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A task group is a non-negative integer: a group below 0 is refused wherever a group is read.

Groups run lowest first from the default, 0. A group below 0 is refused as a group above the 32-bit
maximum is: `-g/--group` and `-t group:…` on `hs submit`, `hs update` in both forms and the `-g`
filters exit 2 before anything is read, counted or written; a group in task input (an inline or
tag-only `group:` tag, a `--from-json` record) is refused whole with exit 5, naming where it came
from. The message names the range, from 0.
"""


# Type annotations
from __future__ import annotations
from typing import List, Tuple
from pathlib import Path

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data.model import DEFAULT_TASK_GROUP, TaskGroupError, ensure_group, parse_group
from tests import create_taskfile, create_json_taskfile
from tests.input_checks import bounded, db_site, sqlite_engine, stored_groups, count, submit  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_update_group import snapshot, submit_three, update_argv


BELOW: List[Tuple[str, str]] = [('minus-one', '-1'), ('int32-min', '-2147483648')]
"""Group text below 0, by test id."""


def initdb(site: Path) -> None:
    """Initialize the site's database, so a refusal before it is opened can be checked."""
    rc, _, stderr = bounded(['hs', 'initdb', '--yes'], cwd=site)
    assert rc == 0, stderr


def names_floor(stderr: str, value: str) -> bool:
    """True when a line of `stderr` names the group, the value and the range from 0."""
    return any('group' in line and value in line and 'from 0' in line for line in stderr.splitlines())


# --- The rule ------------------------------------------------------------------------------------

@mark.unit
@mark.parametrize('value', [-1, -2**31])
def test_ensure_group_refuses_below_zero(value: int) -> None:
    """`ensure_group` refuses an integer below 0 as out of range."""
    with pytest.raises(TaskGroupError) as caught:
        ensure_group(value)
    assert caught.value.out_of_range
    assert 'from 0 to' in str(caught.value)


@mark.unit
@mark.parametrize('name, text', BELOW, ids=[name for name, _ in BELOW])
def test_parse_group_refuses_below_zero(name: str, text: str) -> None:
    """`parse_group` refuses text naming a group below 0 as out of range."""
    with pytest.raises(TaskGroupError) as caught:
        parse_group(text)
    assert caught.value.out_of_range


@mark.unit
def test_zero_is_the_default_and_accepted() -> None:
    """Group 0 is the default and is accepted by both readers."""
    assert DEFAULT_TASK_GROUP == 0
    assert ensure_group(0) == 0
    assert parse_group('0') == 0


# --- hs submit -----------------------------------------------------------------------------------

@mark.integration
@mark.parametrize('name, text', BELOW, ids=[name for name, _ in BELOW])
def test_submit_group_option_refuses_below_zero(temp_site: Path, name: str, text: str) -> None:
    """`hs submit -g` below 0 exits 2, naming the range from 0, and inserts nothing."""
    site = Path(str(temp_site))
    initdb(site)
    rc, _, stderr = submit('-g', text, '--', 'echo', 'a', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert '-g/--group' in stderr and text in stderr and 'from 0' in stderr, stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


@mark.integration
def test_submit_tag_option_refuses_below_zero(temp_site: Path) -> None:
    """`hs submit -t group:-1` exits 2 before any input is read."""
    site = Path(str(temp_site))
    initdb(site)
    rc, _, stderr = submit('-t', 'group:-1', '--', 'echo', 'a', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert names_floor(stderr, '"-1"'), stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


@mark.integration
@mark.parametrize('tagline', [False, True], ids=['inline', 'tag-only'])
def test_submit_file_refuses_below_zero(db_site: Tuple[Path, Engine], tagline: bool) -> None:
    """A task file with a group below 0 inserts nothing and exits 5, naming the value, the range and the line."""
    site, engine = db_site
    marked = '# HYPERSHELL: group:-1' if tagline else 'echo b # HYPERSHELL: group:-1'
    path = create_taskfile(site, ['echo a', marked, 'echo c'])
    rc, _, stderr = submit('-b', '1', '-w', '1', str(path), cwd=site)
    assert rc == 5, stderr[-4000:]
    assert any('"-1"' in line and 'from 0' in line and 'line 2' in line for line in stderr.splitlines()), stderr[-4000:]
    assert count(engine, 'task') == 0


@mark.integration
def test_submit_json_refuses_below_zero(temp_site: Path) -> None:
    """A `--from-json` record with a group below 0 inserts nothing and exits 5, naming the record."""
    site = Path(str(temp_site))
    initdb(site)
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', 'group': -1}])
    rc, _, stderr = submit('-b', '1', '-w', '1', '--from-json', str(path), cwd=site)
    assert rc == 5, stderr[-4000:]
    assert any('from 0' in line and 'record 1' in line for line in stderr.splitlines()), stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


@mark.integration
def test_submit_group_zero_accepted(temp_site: Path) -> None:
    """`-g 0`, `-t group:0` and an inline `group:0` are taken, in group 0."""
    site = Path(str(temp_site))
    path = create_taskfile(site, ['echo c # HYPERSHELL: group:0'])
    for argv in (['-g', '0', '--', 'echo', 'a'], ['-t', 'group:0', '--', 'echo', 'b'], [str(path)]):
        rc, _, stderr = submit(*argv, cwd=site)
        assert rc == 0, stderr[-4000:]
    assert stored_groups(sqlite_engine()) == {'echo a': 0, 'echo b': 0, 'echo c': 0}


# --- hs update and the filters -------------------------------------------------------------------

@mark.integration
@mark.parametrize('form', ['filtered', 'single'])
@mark.parametrize('name, text', BELOW, ids=[name for name, _ in BELOW])
def test_update_refuses_below_zero(db_site: Tuple[Path, Engine], form: str, name: str, text: str) -> None:
    """`hs update group=-1` and `hs update ID group -1` exit 2 and change nothing."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = bounded(update_argv(engine, form, text, '-f'), cwd=site)
    assert rc == 2, stderr[-4000:]
    assert any('group' in line and text in line for line in stderr.splitlines()), stderr[-4000:]
    assert 'Updated' not in stdout + stderr
    assert snapshot(engine) == before


@mark.integration
@mark.parametrize('command', [['list'], ['update', 'group=1', '-f']], ids=['list', 'update'])
def test_group_filter_refuses_below_zero(temp_site: Path, command: List[str]) -> None:
    """The `-g` filter of `hs list` and `hs update` below 0 exits 2 naming the option and the range."""
    site = Path(str(temp_site))
    initdb(site)
    rc, _, stderr = bounded(['hs', *command, '-g', '-1'], cwd=site)
    assert rc == 2, stderr[-4000:]
    assert '-g/--group' in stderr and 'from 0' in stderr, stderr[-4000:]
