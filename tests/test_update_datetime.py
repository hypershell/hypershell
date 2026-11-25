# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` reads the datetime columns as ISO 8601, a value without an offset being local time.

`submit_time`, `schedule_time`, `start_time` and `completion_time` take a date, or a date and a time
joined by `T` or a space, with optional seconds and fraction (up to six digits) and an optional `Z`
or `±HH:MM` offset. A value without an offset is local time; a local time that does not exist (a
daylight-saving gap) is refused, and an ambiguous one is its first occurrence. The value is the same
instant on SQLite and PostgreSQL, shown the same by `hs info` and `hs list`. `now`, `today`, `epoch`
and every other form exit with status 2. Commands run with a local zone that is not UTC and, on
PostgreSQL, a session zone of UTC, so a value read in the wrong zone shows.

Every column of `Task` has its kind in `COLUMN_KINDS`.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Iterator, List, Tuple
from pathlib import Path

# Standard libs
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy.engine import Engine
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.task import change_value
from tests.input_checks import db_site  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_update_kinds import refused_in_process
from tests.update_checks import (FORMS, LOCAL, LOCAL_ZONE, REFUSED, hs, submit_three, task_id, task_row, instant,
                                 drive, same_instant, assert_refused_before_database, assert_refused, snapshot,
                                 update_argv)


COLUMNS: Final[List[str]] = ['submit_time', 'schedule_time', 'start_time', 'completion_time']
"""The datetime columns."""


def local(*parts: int) -> datetime:
    """A local time (the first of an ambiguous hour)."""
    return datetime(*parts, tzinfo=LOCAL)


NINE: Final[datetime] = local(2026, 1, 1, 9)
"""09:00 local time on 2026-01-01 (14:00 UTC)."""

ACCEPTED: Final[List[Tuple[str, datetime]]] = [
    ('2026-01-01', local(2026, 1, 1)),
    ('2026-01-01T09:00', NINE),
    ('2026-01-01 09:00', NINE),
    ('2026-01-01T09:00:30', local(2026, 1, 1, 9, 0, 30)),
    ('2026-01-01T09:00:30.5', local(2026, 1, 1, 9, 0, 30, 500_000)),
    ('2026-01-01T09:00:30.123456', local(2026, 1, 1, 9, 0, 30, 123_456)),
    ('2026-01-01T14:00Z', NINE),
    ('2026-01-01T19:00+05:00', NINE),
    ('2026-01-01 09:00:00-05:00', NINE),
    ('2026-07-01T09:00', local(2026, 7, 1, 9)),
    ('2026-11-01T01:30', local(2026, 11, 1, 1, 30)),
    ('2026-12-31T23:59:59', local(2026, 12, 31, 23, 59, 59)),
]
"""ISO 8601 values and the instant each is, read in the local zone."""

REFUSED_TEXT: Final[List[str]] = [
    'now', 'today', 'epoch', 'NOW', 'x', '5', '1h', '', '20260101', '20260101T0900', '2026-W01', '2026-01-01T09',
    '2026-01-01x09:00', '2026-01-01t09:00', '2026-01-01T24:00', '2026-01-01T09:00:00,5',
    '2026-01-01T09:00:00.1234567', '2026-01-01T09:00+05', '2026-01-01T09:00+0530', '2026-01-01T09:00 Z',
    '2026-02-30', '2026-01-01T09:60', '2026-1-1', '2026-03-08T02:30',
]
"""Values that are not ISO 8601 as the columns take it, or (the last) a local time that does not exist."""


def cases(column: str) -> List[Tuple[str, Any]]:
    """Every value of `column` and what each stores."""
    clear = [(word, REFUSED if column == 'submit_time' else None) for word in ('none', 'NULL')]
    return [*ACCEPTED, *[(text, REFUSED) for text in REFUSED_TEXT], *clear]


@fixture
def local_time() -> Iterator[None]:
    """This process in the local zone of the commands, restored after."""
    held = os.environ.get('TZ')
    os.environ['TZ'] = LOCAL_ZONE
    time.tzset()
    try:
        yield
    finally:
        if held is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = held
        time.tzset()


# Layer 1: every value read in process


@mark.unit
@mark.parametrize('column', COLUMNS)
def test_change_value_accepted(local_time: None, column: str) -> None:
    """Each ISO 8601 form reads as an aware local time, the instant expected."""
    for text, expected in ACCEPTED:
        value = change_value(column, text)
        assert isinstance(value, datetime) and value.tzinfo is not None, (column, text, value)
        assert value.timestamp() == expected.timestamp(), (column, text, value)
        assert value.utcoffset() == expected.utcoffset(), (column, text, value)


@mark.unit
@mark.parametrize('column', COLUMNS)
def test_change_value_refused(local_time: None, column: str) -> None:
    """Every other form, and a local time in a daylight-saving gap, is refused naming the column."""
    for text in REFUSED_TEXT:
        refused_in_process(column, text)


@mark.unit
@mark.parametrize('column', COLUMNS)
def test_change_value_none(local_time: None, column: str) -> None:
    """`none` clears every datetime column but `submit_time`, where it is refused."""
    for word in ('none', 'NULL'):
        if column == 'submit_time':
            refused_in_process(column, word)
        else:
            assert change_value(column, word) is None, (column, word)


@mark.unit
def test_column_kinds_complete() -> None:
    """Every column of `Task` has its kind, and nothing else does."""
    from hypershell.data.model import Task
    from hypershell.task import COLUMN_KINDS
    assert set(COLUMN_KINDS) == set(Task.columns)


# Layer 2: each column through the command line, in both forms, on both backends


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('column', COLUMNS)
def test_column_values(db_site: Tuple[Path, Engine], column: str, form: str) -> None:
    """Every value of the column: stored as the instant read when accepted, refused with exit 2 otherwise."""
    site, engine = db_site
    submit_three(site)
    drive(site, engine, column, form, cases(column), same_instant)


# Layer 3: the same instant on both backends, shown the same


SAME: Final[List[str]] = ['2026-01-01T09:00', '2026-01-01T14:00Z', '2026-01-01T19:00+05:00']
"""One instant written three ways: local time, UTC, and another offset."""


def shown_start(site: Path, ident: str) -> Tuple[str, str]:
    """How `hs info` and `hs list` show the start time of the task `ident`."""
    rc, info, stderr = hs(site, ['hs', 'info', ident])
    assert rc == 0, stderr[-4000:]
    started = [line.strip() for line in info.splitlines() if line.strip().startswith('started:')]
    assert len(started) == 1, info
    rc, listed, stderr = hs(site, ['hs', 'list', 'start_time', '-w', f'id == {ident}'])
    assert rc == 0, stderr[-4000:]
    return started[0], listed.strip()


@mark.integration
@mark.parametrize('form', FORMS)
def test_same_instant(db_site: Tuple[Path, Engine], form: str) -> None:
    """A naive value and two with offsets are one instant on both backends, shown the same by `hs info` and `hs list`."""
    site, engine = db_site
    submit_three(site)
    for n, text in enumerate(SAME):
        ident = task_id(engine, n)
        if form == 'filtered':
            argv = ['hs', 'update', f'start_time={text}', '-t', f'n:{n}', '-f']
        else:
            argv = ['hs', 'update', ident, 'start_time', text]
        rc, _, stderr = hs(site, argv)
        assert rc == 0, stderr[-4000:]
    assert [instant(task_row(engine, n)['start_time']) for n in range(3)] == [NINE.timestamp()] * 3
    shown = [shown_start(site, task_id(engine, n)) for n in range(3)]
    assert shown[0] == shown[1] == shown[2], shown
    if engine.dialect.name == 'sqlite':
        assert '2026-01-01 09:00:00' in shown[0][0] and shown[0][1].startswith('2026-01-01 09:00:00'), shown


@mark.integration
def test_refusal_before_database(temp_site: Path) -> None:
    """A refused datetime exits 2 before the database is opened: a fresh site gets no database file."""
    assert_refused_before_database(temp_site, 'start_time=now')


# The ends of the calendar, on both sides of UTC


ZONES: Final[Dict[str, str]] = {'west': LOCAL_ZONE, 'east': 'Asia/Tokyo'}
"""A local zone behind UTC and one ahead of it, so a time past either end of the calendar is reached both ways."""

EDGE_REFUSED: Final[List[str]] = [
    '9999-12-31T23:59', '9999-12-31T23:59:59.999999', '9999-12-31T23:59-05:00', '9999-12-31T00:00Z',
    '0001-01-01', '0001-01-01T00:00', '0001-01-01T00:00+05:00', '0001-01-01T00:00Z', '0001-01-01T23:59Z',
]
"""Instants within a day of either end of the calendar, which a reader in some zone cannot hold: refused in every zone."""

EDGE_EITHER: Final[List[str]] = ['9999-12-31', '9999-12-30', '0001-01-02', '0001-01-02T12:00', '9999-12-30T12:00Z',
                                 '0001-01-02T12:00Z']
"""Times near either end: taken or refused (exit 2) by the local zone, never a traceback; one taken is read
back in another zone."""


@contextmanager
def zone_of(name: str) -> Iterator[None]:
    """This process in the local zone `name`, restored after."""
    held = os.environ.get('TZ')
    os.environ['TZ'] = name
    time.tzset()
    try:
        yield
    finally:
        if held is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = held
        time.tzset()


@mark.unit
@mark.parametrize('side', list(ZONES))
@mark.parametrize('column', COLUMNS)
def test_change_value_calendar_ends(column: str, side: str) -> None:
    """A time past either end of the calendar is refused naming the column; one near an end is taken or refused, never raised."""
    with zone_of(ZONES[side]):
        for text in EDGE_REFUSED:
            refused_in_process(column, text)
        for text in EDGE_EITHER:
            try:
                value = change_value(column, text)
            except ArgumentError:  # The column's refusal, and no other error
                refused_in_process(column, text)
            else:
                assert value.tzinfo is not None and value.astimezone(timezone.utc), (column, text, value)


@mark.integration
@mark.parametrize('side', list(ZONES))
@mark.parametrize('form', FORMS)
def test_calendar_ends(db_site: Tuple[Path, Engine], form: str, side: str) -> None:
    """At either end of the calendar, in a zone on each side of UTC: exit 2 naming the column, or written and shown; never exit 5."""
    site, engine = db_site
    submit_three(site)
    ident = task_id(engine, 1)
    zone = {'TZ': ZONES[side]}
    for text in EDGE_REFUSED:
        before = snapshot(engine)
        rc, stdout, stderr = hs(site, update_argv(engine, form, 'start_time', text, '-f'), **zone)
        assert_refused(rc, stdout, stderr, 'start_time', text)
        assert snapshot(engine) == before, text
    for text in EDGE_EITHER:
        before = snapshot(engine)
        rc, stdout, stderr = hs(site, update_argv(engine, form, 'start_time', text, '-f'), **zone)
        if rc == 2:
            assert_refused(rc, stdout, stderr, 'start_time', text)
            assert snapshot(engine) == before, text
            continue
        assert rc == 0 and 'Traceback' not in stderr, f'{text}: exit {rc}\n{stderr[-4000:]}'
        for argv in (['hs', 'info', ident], ['hs', 'list', 'start_time', '-w', f'id == {ident}']):
            rc, stdout, stderr = hs(site, argv, **zone)
            assert rc == 0 and 'Traceback' not in stderr, f'{text}: {argv} exit {rc}\n{stderr[-4000:]}'
