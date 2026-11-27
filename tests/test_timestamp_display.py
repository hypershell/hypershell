# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs info`, `hs list` and `hs wait` show a task's times in the local zone, with the offset.

A time is shown in the reading process's local time zone (`TZ`), with its UTC offset, the same on
SQLite and PostgreSQL whatever the PostgreSQL session zone. SQLite stores the writer's local wall
time without an offset, which is read as local time. The wire format is not changed. The CLI tests
run on SQLite and, with `HS_TEST_POSTGRES`, on PostgreSQL.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, List, Tuple
from pathlib import Path

# Standard libs
import os
import re
import csv
import json
import time
from datetime import datetime, timezone, timedelta

# External libs
import yaml
from pytest import fixture, mark, MonkeyPatch
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.core.types import to_json_type
from hypershell.core.pretty_print import to_display_type, format_json
from tests import create_taskfile
from tests.input_checks import bounded, db_site, rows  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)


ZONE: str = 'America/New_York'
"""The reader's zone in these tests: five hours behind UTC on the day used."""

INSTANT: str = '2026-01-01T14:00:00+00:00'
"""The submit time each CLI test sets, given in UTC."""

LOCAL: str = '2026-01-01 09:00:00-05:00'
"""`INSTANT` as a reader in `ZONE` sees it."""

STAMP: re.Pattern = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}:\d{2}')
"""A shown time: ISO 8601, a space separator, an offset."""


@fixture
def local_zone(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """This process reads times in `ZONE`, restored afterwards."""
    monkeypatch.setenv('TZ', ZONE)
    time.tzset()
    try:
        yield
    finally:
        monkeypatch.undo()
        time.tzset()


@mark.unit
def test_aware_time_shown_in_local_zone(local_zone: None) -> None:
    """An aware time from another zone is converted to the local zone, with its offset."""
    assert to_display_type(datetime(2026, 1, 1, 14, tzinfo=timezone.utc)) == LOCAL
    assert to_display_type(datetime(2026, 7, 1, 14, tzinfo=timezone.utc)) == '2026-07-01 10:00:00-04:00'


@mark.unit
def test_naive_time_read_as_local_wall_time(local_zone: None) -> None:
    """A time without an offset (as SQLite returns it) is local wall time and gains the local offset."""
    assert to_display_type(datetime(2026, 1, 1, 9)) == LOCAL
    assert to_display_type(datetime(2026, 1, 1, 9, 0, 0, 123456)) == '2026-01-01 09:00:00.123456-05:00'


@mark.unit
def test_other_values_as_before(local_zone: None) -> None:
    """Anything but a time, an empty one included, is shown as before."""
    for value in [None, 'text', 3, 1.5, True, {'a': 1}, ['x']]:
        assert to_display_type(value) == to_json_type(value)
    assert format_json(None) == 'null'


@mark.unit
def test_time_past_calendar_edge_shown_as_stored(local_zone: None) -> None:
    """An instant the local zone cannot hold is shown as stored, never raised."""
    value = datetime(1, 1, 1, tzinfo=timezone.utc)
    assert to_display_type(value) == '0001-01-01 00:00:00+00:00'


@mark.unit
def test_wire_format_unchanged(local_zone: None) -> None:
    """`to_json_type` (the wire format) keeps the value's own zone, or none."""
    other = timezone(timedelta(hours=5, minutes=30))
    assert to_json_type(datetime(2026, 1, 1, 19, 30, tzinfo=other)) == '2026-01-01 19:30:00+05:30'
    assert to_json_type(datetime(2026, 1, 1, 9)) == '2026-01-01 09:00:00'


def reader_env(zone: str) -> Dict[str, str]:
    """Environment for a reader in `zone`, with the PostgreSQL session zone forced to UTC."""
    return {**os.environ, 'TZ': zone, 'PGTZ': 'UTC', 'COLUMNS': '200'}


def hs(site: Path, *args: str, zone: str = ZONE) -> Tuple[str, str]:
    """Run `hs` in `zone`; return stdout and stderr, failing on a non-zero exit."""
    rc, stdout, stderr = bounded(['hs', *args], cwd=site, env=reader_env(zone))
    assert rc == 0, stderr
    return stdout, stderr


@fixture
def one_task(db_site: Tuple[Path, Engine]) -> Iterator[Tuple[Path, Engine, str]]:
    """A site holding one task, never run, whose submit time is `INSTANT` (set by a reader in `ZONE`)."""
    site, engine = db_site
    hs(site, 'submit', str(create_taskfile(site, ['echo 1'])))
    (task_id, ), = rows(engine, 'SELECT id FROM task')
    hs(site, 'update', str(task_id), 'submit_time', INSTANT, '-f')
    yield site, engine, str(task_id)


@mark.integration
def test_info_every_format(one_task: Tuple[Path, Engine, str]) -> None:
    """`hs info` shows the local time with its offset in each format; an empty time as before."""
    site, _, task_id = one_task
    assert hs(site, 'info', task_id, '-x', 'submit_time')[0].strip() == LOCAL
    assert hs(site, 'info', task_id, '-x', 'schedule_time')[0].strip() == 'null'
    data = json.loads(hs(site, 'info', task_id, '-f', 'json')[0])
    assert data['submit_time'] == LOCAL and data['schedule_time'] is None
    data = yaml.safe_load(hs(site, 'info', task_id, '-f', 'yaml')[0])
    assert data['submit_time'] == LOCAL and data['schedule_time'] is None
    assert f'submitted: {LOCAL}' in hs(site, 'info', task_id)[0]
    assert LOCAL in hs(site, 'info', task_id, '-f', 'card')[0]


@mark.integration
def test_list_every_format(one_task: Tuple[Path, Engine, str]) -> None:
    """`hs list` shows the local time with its offset in each format; an empty time as before."""
    site, _, task_id = one_task
    fields = ['id', 'submit_time', 'schedule_time']
    data = json.loads(hs(site, 'list', *fields, '-f', 'json')[0])
    assert data == [{'id': task_id, 'submit_time': LOCAL, 'schedule_time': None}]
    lines = list(csv.reader(hs(site, 'list', *fields, '-f', 'csv')[0].splitlines()))
    assert lines == [fields, [task_id, LOCAL, 'null']]
    assert hs(site, 'list', *fields, '-f', 'plain')[0].split() == [task_id, LOCAL.split()[0], LOCAL.split()[1], 'null']
    assert LOCAL in hs(site, 'list', *fields, '-f', 'table')[0]
    assert f'submitted: {LOCAL}' in hs(site, 'list', '-f', 'normal')[0]
    assert LOCAL in hs(site, 'list', '-f', 'card')[0]


@mark.integration
def test_every_time_carries_an_offset(one_task: Tuple[Path, Engine, str]) -> None:
    """Every time `hs list` shows, set by a client or not, has the ISO 8601 form with an offset."""
    site, _, task_id = one_task
    hs(site, 'update', '--cancel', '-w', f'id=={task_id}', '--no-confirm')
    data, = json.loads(hs(site, 'list', '-f', 'json')[0])
    shown = [data[name] for name in ('submit_time', 'schedule_time', 'completion_time')]
    assert all(STAMP.fullmatch(value) for value in shown), shown


@mark.integration
def test_reader_zone_follows_tz(one_task: Tuple[Path, Engine, str], request) -> None:
    """A reader in UTC sees the same instant in UTC on PostgreSQL; SQLite's stored wall time as UTC."""
    site, _, task_id = one_task
    shown = hs(site, 'info', task_id, '-x', 'submit_time', zone='UTC')[0].strip()
    if request.node.callspec.params['db_site'] == 'postgres':
        assert shown == '2026-01-01 14:00:00+00:00'
    else:
        assert shown == '2026-01-01 09:00:00+00:00'


@mark.integration
def test_wait_logs_local_completion_time(one_task: Tuple[Path, Engine, str]) -> None:
    """`hs wait` logs the completion time in the local zone, with its offset."""
    site, _, task_id = one_task
    hs(site, 'update', task_id, 'exit_status', '0', '-f')
    hs(site, 'update', task_id, 'completion_time', INSTANT, '-f')
    _, stderr = hs(site, 'wait', task_id)
    assert f'Task completed ({LOCAL})' in stderr


DOCS: Path = Path(__file__).parent.parent / 'docs' / 'cli'
"""The command pages."""

MARKER: str = r'\.\. versionchanged:: 2\.9\.0\n(?:   :pr:`\d+`: )?'
"""A 2.9.0 change marker, with or without the pull request link added at release."""


@mark.unit
@mark.parametrize('page', ['task_info.rst', 'task_search.rst'])
def test_docs_state_rule(page: str) -> None:
    """Each command page states the rule under a 2.9.0 change marker."""
    text = (DOCS / page).read_text(encoding='utf-8')
    assert re.search(MARKER + r'\s*Times \(``submit_time``.*?local time zone \(``TZ``\) with their UTC offset',
                     text, re.DOTALL)
