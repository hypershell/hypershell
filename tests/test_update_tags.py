# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` changes tags by any key that is not a column, refuses a near-miss and a whole tag.

`tag=VALUE` is refused, pointing to `KEY=VALUE`, `KEY:VALUE` and `--remove-tag`. A `KEY=VALUE` (or
`hs update ID KEY VALUE`) whose key is not a task column is a tag change, the same as `KEY:VALUE`,
unless the key is one edit from a column (case aside): that is refused, naming the nearest column and
the `KEY:VALUE` way to set a tag by that name. `KEY:VALUE` is never checked against the columns. The
first separator in an argument splits it. Every tag is checked by the tag rules, exit 2 when one is
not a tag. The prompt lists each change as a column, a tag or a removal. The action options and the
single-task tag change are as before.
"""


# Type annotations
from __future__ import annotations
from typing import Final, List, Tuple
from pathlib import Path

# Standard libs
import re
import subprocess

# External libs
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from tests.input_checks import db_site  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.update_checks import (FORMS, hs, submit_three, snapshot, task_id, task_row, instant, unwritten, all_rows)
from tests.test_update_datetime import NINE


def tags(engine: Engine, n: int = 1) -> dict:
    """The tag of the task tagged n:`n`."""
    return task_row(engine, n)['tag']


def refused(rc: int, stdout: str, stderr: str, *words: str) -> None:
    """Exit status 2, each of `words` in the message, and nothing counted, asked or written."""
    assert rc == 2, stderr[-4000:]
    for word in words:
        assert word in stderr, (word, stderr[-4000:])
    unwritten(stdout, stderr)


# R11: tag=VALUE


@mark.integration
@mark.parametrize('value', ['x', 'none', 'a:1'])
def test_whole_tag_refused(db_site: Tuple[Path, Engine], value: str) -> None:
    """`tag=VALUE` exits 2, pointing to `KEY=VALUE`, `KEY:VALUE` and `--remove-tag`; nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, ['hs', 'update', f'tag={value}', '-t', 'n:1', '-f'])
    refused(rc, stdout, stderr, 'tag', 'KEY=VALUE', 'KEY:VALUE', '--remove-tag')
    assert snapshot(engine) == before


@mark.integration
def test_single_task_tag_merges(db_site: Tuple[Path, Engine]) -> None:
    """`hs update ID tag a:1` merges the tag `a` as before (guard)."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, ['hs', 'update', task_id(engine, 1), 'tag', 'a:1'])
    assert rc == 0, stderr[-4000:]
    assert tags(engine) == {'n': 1, 'a': 1}


# R13: the action options and KEY:VALUE, as before


ACTIONS: Final[List[Tuple[str, List[str]]]] = [
    ('cancel', ['--cancel']), ('revert', ['--revert']), ('delete', ['--delete']),
    ('remove-tag', ['--remove-tag', 'n']), ('key-value', ['color:red']),
]
"""Changes that behave as before, by test id."""


@mark.integration
@mark.parametrize('name, argv', ACTIONS, ids=[name for name, _ in ACTIONS])
def test_actions_unchanged(db_site: Tuple[Path, Engine], name: str, argv: List[str]) -> None:
    """The action options, `--remove-tag` and `KEY:VALUE` write as before (guard)."""
    site, engine = db_site
    submit_three(site)
    ident = task_id(engine, 1)
    rc, _, stderr = hs(site, ['hs', 'update', *argv, '-w', f'id == {ident}', '-f'])
    assert rc == 0, stderr[-4000:]
    if name == 'delete':
        assert sorted(str(record['id']) for record in all_rows(engine)) == sorted(task_id(engine, n) for n in (0, 2))
        return
    row = next(record for record in all_rows(engine) if str(record['id']) == ident)
    if name == 'cancel':
        assert row['exit_status'] == -1 and row['completion_time'] is not None
    elif name == 'revert':
        assert row['schedule_time'] is None and row['exit_status'] is None
    elif name == 'remove-tag':
        assert row['tag'] == {}
    else:
        assert row['tag'] == {'n': 1, 'color': 'red'}


# R16: any other key is a tag


@mark.integration
@mark.parametrize('form', FORMS)
def test_other_key_is_tag(db_site: Tuple[Path, Engine], form: str) -> None:
    """`color=blue` (or `ID color blue`) sets the tag `color`, as `color:blue` does."""
    site, engine = db_site
    submit_three(site)
    argv = (['hs', 'update', 'color=blue', '-t', 'n:1', '-f'] if form == 'filtered' else
            ['hs', 'update', task_id(engine, 1), 'color', 'blue'])
    rc, _, stderr = hs(site, argv)
    assert rc == 0, stderr[-4000:]
    assert tags(engine) == {'n': 1, 'color': 'blue'}
    assert tags(engine, 0) == {'n': 0} and tags(engine, 2) == {'n': 2}


@mark.integration
@mark.parametrize('form', FORMS)
def test_misspelling_two_edits_is_tag(db_site: Tuple[Path, Engine], form: str) -> None:
    """A column misspelled two edits away (`exti_stauts`) is a tag; the column is untouched."""
    site, engine = db_site
    submit_three(site)
    argv = (['hs', 'update', 'exti_stauts=0', '-t', 'n:1', '-f'] if form == 'filtered' else
            ['hs', 'update', task_id(engine, 1), 'exti_stauts', '0'])
    rc, _, stderr = hs(site, argv)
    assert rc == 0, stderr[-4000:]
    assert tags(engine) == {'n': 1, 'exti_stauts': 0}
    assert task_row(engine, 1)['exit_status'] is None


@mark.integration
@mark.parametrize('form', FORMS)
def test_invalid_tag_key_refused(db_site: Tuple[Path, Engine], form: str) -> None:
    """A key that is not a column and not a tag key exits 2 with the tag rules' message; nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    argv = (['hs', 'update', 'a b=1', '-t', 'n:1', '-f'] if form == 'filtered' else
            ['hs', 'update', task_id(engine, 1), 'a b', '1'])
    rc, stdout, stderr = hs(site, argv)
    refused(rc, stdout, stderr, 'Tag key must only contain', 'a b')
    assert snapshot(engine) == before


CHANGE_LINES: Final[List[str]] = [
    r'column\s+exit_status=0', r'tag\s+exti_stauts:0', r'tag\s+color:blue', r'remove\s+tag size',
]
"""The prompt's line for each change, in argument order."""


@mark.integration
def test_prompt_lists_changes(db_site: Tuple[Path, Engine]) -> None:
    """Before asking, the prompt lists each change as a column, a tag or a removal; "no" writes nothing."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, ['hs', 'update', 'exit_status=0', 'exti_stauts=0', 'color:blue',
                                   '--remove-tag', 'size', '-t', 'n:1'], stdin='no\n')
    assert rc == 0, stderr[-4000:]
    lines = [line.strip() for line in stdout.splitlines()]
    found = [next((i for i, line in enumerate(lines) if re.fullmatch(pattern, line)), None) for pattern in CHANGE_LINES]
    assert None not in found and found == sorted(found), stdout
    question = next(i for i, line in enumerate(lines) if 'continue?' in line)
    assert found[-1] < question, stdout
    assert snapshot(engine) == before


@mark.integration
def test_no_change_lines_without_prompt(db_site: Tuple[Path, Engine]) -> None:
    """Under `-f` nothing is listed, and the changes are written."""
    site, engine = db_site
    submit_three(site)
    rc, stdout, stderr = hs(site, ['hs', 'update', 'exit_status=0', 'exti_stauts=0', '-t', 'n:1', '-f'])
    assert rc == 0, stderr[-4000:]
    assert 'column' not in stdout and 'exti_stauts' not in stdout, stdout
    assert tags(engine) == {'n': 1, 'exti_stauts': 0} and task_row(engine, 1)['exit_status'] == 0


# R17: a key one edit from a column is refused


NEAR: Final[List[Tuple[str, str]]] = [
    ('exit_stauts', 'exit_status'),  # A swap of two adjacent characters
    ('exitstatus', 'exit_status'),   # A deletion
    ('exit_sstatus', 'exit_status'),  # An insertion
    ('exit_statue', 'exit_status'),  # A substitution
    ('Exit_Status', 'exit_status'),  # Letter case only
    ('Cores', 'cores'),
    ('tags', 'tag'), ('ip', 'id'), ('zones', 'zone'), ('groups', 'group'),  # Short names count
]
"""Keys one edit from a column, ignoring case, and the column each is near."""


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('key, column', NEAR, ids=[key for key, _ in NEAR])
def test_near_miss_refused(db_site: Tuple[Path, Engine], form: str, key: str, column: str) -> None:
    """A key one edit from a column exits 2 naming it, the nearest column and the `:` way out; nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    ident = task_id(engine, 1)
    if form == 'filtered':
        argv = ['hs', 'update', 'args=changed', f'{key}=0', '-t', 'n:1', '-f']
        way_out = f'write {key}:VALUE'
    else:
        argv = ['hs', 'update', ident, key, '0']
        way_out = f'write {ident} tag {key}:VALUE'
    rc, stdout, stderr = hs(site, argv)
    refused(rc, stdout, stderr, f'{key}: not a task column', f'did you mean {column}?',
            'To set a tag by that name', way_out)
    assert snapshot(engine) == before


@mark.integration
@mark.parametrize('key', ['exit_stauts', 'exit_status', 'Cores', 'tags'])
def test_colon_always_sets_tag(db_site: Tuple[Path, Engine], key: str) -> None:
    """`KEY:VALUE` sets a tag even when the key is one edit from, or equal to, a column, which is untouched."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, ['hs', 'update', f'{key}:0', '-t', 'n:1', '-f'])
    assert rc == 0, stderr[-4000:]
    assert tags(engine) == {'n': 1, key: 0}
    assert task_row(engine, 1)['exit_status'] is None


@mark.integration
def test_single_task_colon_sets_tag(db_site: Tuple[Path, Engine]) -> None:
    """`hs update ID tag exit_stauts:0`, the single-task way out, sets the tag (guard)."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, ['hs', 'update', task_id(engine, 1), 'tag', 'exit_stauts:0'])
    assert rc == 0, stderr[-4000:]
    assert tags(engine) == {'n': 1, 'exit_stauts': 0}


# The tag rules on every tag path


@mark.integration
@mark.parametrize('form', ['filtered', 'single', 'after-naked'])
@mark.parametrize('tag, word', [('a b:1', 'Tag key must only contain'), ('memory:4X', "given '4X'")],
                  ids=['key', 'memory'])
def test_tag_rules_every_path(db_site: Tuple[Path, Engine], form: str, tag: str, word: str) -> None:
    """A tag that breaks the tag rules exits 2 in every form, even after a tag with no value; nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    if form == 'filtered':
        argv = ['hs', 'update', tag, '-t', 'n:1', '-f']
    elif form == 'after-naked':
        argv = ['hs', 'update', 'naked:', tag, '-t', 'n:1', '-f']
    else:
        argv = ['hs', 'update', task_id(engine, 1), 'tag', tag]
    rc, stdout, stderr = hs(site, argv)
    refused(rc, stdout, stderr, word)
    assert snapshot(engine) == before


# The first separator splits


@mark.integration
@mark.parametrize('argument, word', [('note:a=b', 'note:a=b'), ('time=12:30', 'time:12:30')],
                  ids=['colon-first', 'equals-first'])
def test_first_separator_splits(db_site: Tuple[Path, Engine], argument: str, word: str) -> None:
    """The earlier of `:` and `=` splits an argument: these are tags whose values the tag rules refuse, exit 2."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, ['hs', 'update', argument, '-t', 'n:1', '-f'])
    refused(rc, stdout, stderr, 'Tag value must only contain', word)
    assert snapshot(engine) == before


@mark.integration
def test_column_value_with_colon(db_site: Tuple[Path, Engine]) -> None:
    """A column's value may hold `:`, `start_time=2026-01-01T09:00` and `timeout=1:30:00` being columns."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, ['hs', 'update', 'start_time=2026-01-01T09:00', 'timeout=1:30:00', '-t', 'n:1', '-f'])
    assert rc == 0, stderr[-4000:]
    row = task_row(engine, 1)
    assert instant(row['start_time']) == NINE.timestamp() and row['timeout'] == 5400
    assert tags(engine) == {'n': 1}


# Completions


BASH_COMPLETION: Final[Path] = Path(__file__).parent.parent / 'share' / 'bash_completion.d' / 'hs'
ZSH_COMPLETION: Final[Path] = Path(__file__).parent.parent / 'share' / 'zsh' / 'site-functions' / '_hs'

BASH_PROBE: Final[str] = """
hs() { case "$*" in 'list --fields') echo id args cores tag zone ;; 'list --tag-keys') echo color ;; esac; }
source "$1"
COMP_WORDS=(hs update '') COMP_CWORD=2
_hs_task_update
printf '%s\\n' "${COMPREPLY[@]}"
"""
"""Complete the first argument of `hs update` with a stand-in for `hs list`."""


@mark.unit
def test_bash_completion_offers_no_whole_tag() -> None:
    """The bash completion offers `FIELD=` for each column but `id` and `tag`, and `KEY:` for each tag."""
    result = subprocess.run(['bash', '-c', BASH_PROBE, 'probe', str(BASH_COMPLETION)],
                            capture_output=True, text=True, timeout=30)
    offered = set(result.stdout.split())
    assert {'args=', 'cores=', 'zone=', 'color:'} <= offered, result.stdout + result.stderr
    assert 'tag=' not in offered and 'id=' not in offered, offered


@mark.unit
def test_zsh_completion_offers_no_whole_tag() -> None:
    """The zsh completion leaves out `tag=` as it leaves out `id=`."""
    text = ZSH_COMPLETION.read_text(encoding='utf-8')
    body = text[text.index('_hs_update_args() {'):]
    body = body[:body.index('\n}\n')]
    condition = re.search(r'for k in \$f; do (.*?) && assigns\+=', body)
    assert condition, body
    assert '!= id' in condition.group(1) and '!= tag' in condition.group(1), condition.group(1)
