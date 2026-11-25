# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` reads the whole-number, size, duration and decimal columns by their ranges.

`cores`, `memory` and `timeout` are read as `hs submit -c`, `-m` and `-W` read them; `exit_status`,
`attempt` and `part` as whole numbers; `waited` and `duration` as durations; `cores_max` and
`memory_max` as numbers; each inside its entry of the range table. Any other value exits with status
2, naming the column, the range and the value, before anything is counted, asked or written, and no
other change given with it lands. `id` is never written. A value past the advisory limit of `cores`
or `attempt` is written with one warning.

Three layers: each value read in process by `change_value`; each column driven through the command
line in both forms, on SQLite and, with `HS_TEST_POSTGRES`, on PostgreSQL; and the cases that cut
across columns. The bounds are read from the table, never retyped.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Tuple
from pathlib import Path

# Standard libs
import re

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.core.config_schema import shown
from hypershell.task import change_value
from tests.input_checks import db_site  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.update_checks import (FORMS, REFUSED, hs, submit_three, snapshot, task_id, task_row, update_argv,
                                 assert_refused, assert_refused_before_database, unwritten, drive, same_int,
                                 same_float)


SETTING: Final[Dict[str, str]] = {
    'cores': 'task.cores', 'memory': 'task.memory', 'timeout': 'task.timeout',
    'waited': 'task.timeout', 'duration': 'task.timeout',
    'exit_status': 'column.exit_status', 'attempt': 'server.attempts', 'part': 'option.part',
    'cores_max': 'column.cores_max', 'memory_max': 'column.memory_max',
}
"""Each ranged column and the entry of the range table it is read by."""

COLUMNS: Final[List[str]] = list(SETTING)
"""The ranged columns."""

KEEPS: Final[frozenset] = frozenset({'attempt', 'part'})
"""Ranged columns that may not be empty: `none` is refused."""

WHOLE: Final[frozenset] = frozenset({'cores', 'exit_status', 'attempt', 'part'})
"""Columns read as whole numbers."""

DURATIONS: Final[frozenset] = frozenset({'timeout', 'waited', 'duration'})
"""Columns read as durations, in whole seconds."""

PEAKS: Final[frozenset] = frozenset({'cores_max', 'memory_max'})
"""Columns read as numbers and stored as floats."""

FORMS_ACCEPTED: Final[Dict[str, List[Tuple[str, Any]]]] = {
    'cores': [('4', 4), ('+5', 5), ('07', 7), (' 8 ', 8), ('1_024', 1024)],
    'memory': [('4G', 4 * 1024**3), ('512MB', 512 * 1024**2), ('1.5GiB', 3 * 1024**3 // 2), ('1024', 1024),
               ('1K', 1024)],
    'timeout': [('90m', 5400), ('1h30m', 5400), ('1:30:00', 5400), ('1-12', 129_600), ('3600', 3600),
                ('45s', 45), ('1.5', 2)],
    'exit_status': [('0', 0), ('-1', -1), ('255', 255), ('-9', -9), ('-1001', -1001)],
    'attempt': [('2', 2), ('100', 100)],
    'part': [('3', 3)],
    'cores_max': [('1.5', 1.5), ('3', 3.0), ('-0.0', 0.0), ('.5', 0.5), ('1e3', 1000.0)],
    'memory_max': [('1073741824.5', 1073741824.5), ('4096', 4096.0)],
}
"""Forms each column takes beyond its bounds, and the value each stores."""
FORMS_ACCEPTED['waited'] = FORMS_ACCEPTED['duration'] = FORMS_ACCEPTED['timeout']

ALWAYS_REFUSED: Final[List[str]] = ['', 'x', 'true', 'nan', 'inf']
"""Values no ranged column takes."""

KIND_REFUSED: Final[Dict[str, List[str]]] = {
    'cores': ['1.5', '1e3', '0x10'], 'exit_status': ['1.5', '1e3'], 'attempt': ['1.5'], 'part': ['1.5'],
    'memory': ['1h', '1.5h'], 'timeout': ['4G', '1h30'], 'waited': ['4G'], 'duration': ['4G'],
    'cores_max': ['4G', '32767.5', '1_0.5x'], 'memory_max': ['4G', '1h'],
}
"""Values refused by the column's kind: a fraction for a whole number, a duration for a size, a size for a duration."""

CLEAR: Final[List[str]] = ['none', 'null', 'NONE', 'Null']
"""The words that clear a column that may be empty."""


def entry(setting: str) -> Any:
    """The range table's entry `setting` (imported when a test runs; a missing row fails the test)."""
    from hypershell.core.config_schema import SETTINGS
    assert setting in SETTINGS, f'the range table has no {setting} row'
    return SETTINGS[setting]


def bound(column: str, value: Any) -> Any:
    """What `column` stores for the bound `value`."""
    return float(value) if column in PEAKS else value


def cases(column: str) -> List[Tuple[str, Any]]:
    """Every value R14 lists for `column`, and what each stores (`REFUSED` when it is refused)."""
    rng = entry(SETTING[column])
    accepted = [(str(rng.low), bound(column, rng.low)), (str(rng.high), bound(column, rng.high)),
                *[(text, value) for text, value in FORMS_ACCEPTED[column]]]
    refused = [str(rng.low - 1), str(rng.high + 1), *ALWAYS_REFUSED, *KIND_REFUSED[column]]
    clear = [(word, REFUSED if column in KEEPS else None) for word in CLEAR]
    return [*accepted, *[(text, REFUSED) for text in refused], *clear]


def check_for(column: str) -> Any:
    """How a stored value of `column` is checked."""
    return same_float if column in PEAKS else same_int


# Layer 1: every value read in process


@mark.unit
@mark.parametrize('column', COLUMNS)
def test_change_value_accepted(column: str) -> None:
    """Each bound and each accepted form reads as the value stored, of the stored type."""
    for text, expected in cases(column):
        if expected is REFUSED:
            continue
        value = change_value(column, text)
        assert value == expected, (column, text, value)
        if expected is not None:
            assert type(value) is (float if column in PEAKS else int), (column, text, value)
            if column in PEAKS:
                assert str(value) != '-0.0', (column, text)


@mark.unit
@mark.parametrize('column', COLUMNS)
def test_change_value_refused(column: str) -> None:
    """One past each bound, a value of another kind, and `none` where it may not be empty, are refused naming the column."""
    rng = entry(SETTING[column])
    for text, expected in cases(column):
        if expected is not REFUSED:
            continue
        with pytest.raises(ArgumentError) as refused:
            change_value(column, text)
        message = str(refused.value)
        assert message.startswith(f'{column}: expected ') and f'given {shown(text)}' in message, message
        if text.strip().lower() not in ('none', 'null') or column in WHOLE:
            assert rng.bounds in message, message


@mark.unit
@mark.parametrize('column', [column for column in COLUMNS if column not in KEEPS])
def test_change_value_none_clears(column: str) -> None:
    """`none` and `null`, in any case, clear a column that may be empty."""
    for word in CLEAR:
        assert change_value(column, word) is None, (column, word)


@mark.unit
def test_change_value_group_unchanged() -> None:
    """The group is read as #124 reads it (guard)."""
    assert change_value('group', '07') == 7
    with pytest.raises(ArgumentError):
        change_value('group', 'x')


@mark.unit
def test_new_rows() -> None:
    """The range table holds the exit status and peak rows, which are not configuration keys."""
    from hypershell.core.config_schema import setting_for, Kind, INT16_MAX, INT64_MAX
    rows = {name: entry(f'column.{name}') for name in ('exit_status', 'cores_max', 'memory_max')}
    assert (rows['exit_status'].kind, rows['exit_status'].low, rows['exit_status'].high) == \
        (Kind.INT, -INT16_MAX - 1, INT16_MAX)
    assert (rows['cores_max'].kind, rows['cores_max'].low, rows['cores_max'].high) == (Kind.NUMBER, 0, INT16_MAX)
    assert (rows['memory_max'].kind, rows['memory_max'].low, rows['memory_max'].high) == (Kind.NUMBER, 0, INT64_MAX)
    for name, rng in rows.items():
        assert setting_for(f'column.{name}') is None, name
        assert rng.advise is None and rng.why, name
    assert rows['exit_status'].text == 'Range: -32,768 to 32,767.'
    assert rows['cores_max'].text == 'Range: 0 to 32,767.'
    assert rows['memory_max'].text == 'Range: 0 to 9,223,372,036,854,775,807 bytes.'


DESC: Final[Path] = Path(__file__).parent.parent / 'docs' / '_include' / 'task_update_desc.rst'
"""The description of `hs update`, shared by its help page and its manual."""


@mark.unit
def test_desc_lists_ranges() -> None:
    """The description lists each ranged column with its entry's range, as the documentation gives it."""
    lines = DESC.read_text(encoding='utf-8').splitlines()
    for column, setting in SETTING.items():
        text = entry(setting).text
        assert any(f'``{column}``' in line and text in line for line in lines), (column, text)


# Layer 2: each column through the command line, in both forms, on both backends


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('column', COLUMNS)
def test_column_values(db_site: Tuple[Path, Engine], column: str, form: str) -> None:
    """Every value of the column: stored as read when accepted, refused with exit 2 and nothing written otherwise."""
    site, engine = db_site
    submit_three(site)
    drive(site, engine, column, form, cases(column), check_for(column))


# Layer 3: the cases that cut across columns


@mark.integration
@mark.parametrize('form', FORMS)
def test_id_never_written(db_site: Tuple[Path, Engine], form: str) -> None:
    """`id` is refused with exit 2 in both forms, and nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    new = '01234567-89ab-7def-8123-456789abcdef'
    rc, stdout, stderr = hs(site, update_argv(engine, form, 'id', new, '-f'))
    assert rc == 2, stderr[-4000:]
    assert 'id' in stderr and new in stderr, stderr[-4000:]
    unwritten(stdout, stderr)
    assert snapshot(engine) == before


@mark.integration
@mark.parametrize('confirm', ['no-confirm', 'prompt'])
@mark.parametrize('form', FORMS)
def test_refusal_writes_no_other_change(db_site: Tuple[Path, Engine], form: str, confirm: str) -> None:
    """A refused change given with a good one and a tag writes neither, with `-f` or answered yes."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    if form == 'filtered':
        argv = ['hs', 'update', 'args=changed', 'cores=x', 'color:red', '-t', 'n:1']
    else:
        argv = ['hs', 'update', task_id(engine, 1), 'cores', 'x']
    argv += ['-f'] if confirm == 'no-confirm' else []
    rc, stdout, stderr = hs(site, argv, stdin='yes\n')
    assert_refused(rc, stdout, stderr, 'cores', 'x')
    assert snapshot(engine) == before


@mark.integration
def test_first_refusal_wins(db_site: Tuple[Path, Engine]) -> None:
    """Of two refused changes, the message names the first only."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, ['hs', 'update', 'memory=1h', 'cores=x', '-t', 'n:1', '-f'])
    assert_refused(rc, stdout, stderr, 'memory', '1h')
    assert 'cores' not in stderr, stderr[-4000:]
    assert snapshot(engine) == before


BEFORE_DATABASE: Final[Dict[str, str]] = {'cores': 'cores=x', 'memory': 'memory=4X', 'attempt': 'attempt=none'}
"""Refused ranged changes, by test id, each on a site with no database yet (the other kinds' are in their files)."""


@mark.integration
@mark.parametrize('change', list(BEFORE_DATABASE.values()), ids=list(BEFORE_DATABASE))
def test_refusal_before_database(temp_site: Path, change: str) -> None:
    """A refused change exits 2 before the database is opened: a fresh site gets no database file."""
    assert_refused_before_database(temp_site, change)


@mark.integration
@mark.parametrize('form', FORMS)
def test_cores_whole_decimal_as_submit(db_site: Tuple[Path, Engine], form: str) -> None:
    """`cores=4.0` is taken or refused as `hs submit -c 4.0` is; taken, it stores 4."""
    site, engine = db_site
    submit_three(site)
    rc_submit, _, stderr_submit = hs(site, ['hs', 'submit', '-c', '4.0', 'echo', 'four'])
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, update_argv(engine, form, 'cores', '4.0', '-f'))
    assert rc == rc_submit, f'submit exit {rc_submit}, update exit {rc}\n{stderr_submit[-2000:]}\n{stderr[-2000:]}'
    if rc_submit == 0:
        same_int(engine, 'cores', task_row(engine, 1)['cores'], 4)
    else:
        assert_refused(rc, stdout, stderr, 'cores', '4.0')
        assert snapshot(engine) == before


ADVISED: Final[List[Tuple[str, str, str]]] = [
    ('cores', '1025', '1,024'), ('attempt', '101', '100'),
]
"""Values just past the advisory limit of a reused entry, and the limit each warning names."""

AT_LIMIT: Final[List[Tuple[str, str]]] = [('cores', '1024'), ('attempt', '100')]
"""Values at the advisory limit: no warning."""


def advice(stderr: str) -> List[str]:
    """The advisory warnings in `stderr`."""
    return [line for line in stderr.splitlines() if ' is above the advised ' in line]


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('column, value, limit', ADVISED, ids=[column for column, _, _ in ADVISED])
def test_advised_past_limit(db_site: Tuple[Path, Engine], form: str, column: str, value: str, limit: str) -> None:
    """A value just past the advisory limit is written, exit 0, with one WARNING naming the column, value and limit."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, update_argv(engine, form, column, value, '-f'))
    assert rc == 0, stderr[-4000:]
    lines = advice(stderr)
    assert len(lines) == 1, stderr[-4000:]
    assert re.search(rf"{column}: {re.escape(shown(value))} is above the advised {re.escape(limit)}", lines[0]), lines
    assert 'WARNING' in lines[0], lines
    same_int(engine, column, task_row(engine, 1)[column], int(value))


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('column, value', AT_LIMIT, ids=[column for column, _ in AT_LIMIT])
def test_not_advised_at_limit(db_site: Tuple[Path, Engine], form: str, column: str, value: str) -> None:
    """A value at the advisory limit is written with no warning (guard)."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, update_argv(engine, form, column, value, '-f'))
    assert rc == 0, stderr[-4000:]
    assert advice(stderr) == [], stderr[-4000:]
    assert task_row(engine, 1)[column] == int(value)
