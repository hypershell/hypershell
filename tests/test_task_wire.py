# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The task wire: what a task's fields are when they come off the JSON a server and client exchange.

A field is read as a time only when its entity declares that column a ``datetime``. Every other
field arrives exactly as it was sent, so a line or a returned command that looks like a date stays
text, while every timestamp still decodes as a time. The encoding itself is pinned against bytes
recorded with hypershell 2.9.0a8, so peers of that release read this one's wire unchanged.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List
from pathlib import Path

# Standard libs
import json
from datetime import datetime, timedelta, timezone

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.types import DateTime

# Internal libs
from hypershell.core.template import Template, DEFAULT_TEMPLATE
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import (Entity, Task, Client, Source, Zone, ResultOutcome,
                                   serialize_tasks, deserialize_tasks)


# Lines that datetime.fromisoformat accepts on every supported Python, each a date in November 2026.
DATE_LIKE = ['20261102', '2026-11-02', '2026W45', '2026-W45-1', '20261102T1200', '2026-11-02 12:00']

TEXT_FIELDS = ['args', 'command', 'outpath', 'errpath', 'csvpath', 'submit_host', 'server_host', 'client_host']
TIME_FIELDS = ['submit_time', 'schedule_time', 'start_time', 'completion_time']

WEST = timezone(timedelta(hours=-5))
TIMES = {
    'naive': datetime(2026, 11, 2, 9, 30, 0),
    'naive-usec': datetime(2026, 11, 2, 9, 30, 0, 123456),
    'aware': datetime(2026, 11, 2, 9, 30, 0, tzinfo=WEST),
    'aware-usec': datetime(2026, 11, 2, 14, 30, 0, 1, tzinfo=timezone.utc),
}


# The fixed task of tests/fixtures/make-task-wire-2.9.0a8.sh, field for field.
FIXED_TASK: Dict[str, Any] = {
    **{name: None for name in Task.columns},
    'id': '6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b',
    'group': 0,
    'args': '2026-11-02',
    'submit_id': '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d',
    'submit_time': datetime(2026, 11, 2, 9, 30, 0, 123456, tzinfo=WEST),
    'submit_host': 'submit-host',
    'cores': 1,
    'server_id': '1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e',
    'server_host': 'server-host',
    'schedule_time': datetime(2026, 11, 2, 14, 31, 0, tzinfo=timezone.utc),
    'attempt': 1,
    'retried': False,
    'source': '2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f',
    'fingerprint': '3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e',
    'part': 0,
    'tag': {'day': '2026-11-02'},
}
FIXED_RESULT: Dict[str, Any] = {
    **FIXED_TASK,
    'client_id': '4e5f6a7b-8c9d-4e0f-a1b2-c3d4e5f6a7b8',
    'client_host': 'client-host',
    'command': '2026-11-02',
    'start_time': datetime(2026, 11, 2, 9, 31, 1, 654321, tzinfo=WEST),
    'completion_time': datetime(2026, 11, 2, 9, 31, 3, 5),
    'waited': 61,
    'duration': 2,
    'exit_status': 0,
    'outpath': '2026-11-02.out',
    'errpath': '2026-11-02.err',
}

# Printed by tests/fixtures/make-task-wire-2.9.0a8.sh with hypershell 2.9.0a8 (see tests/fixtures/README.md).
WIRE_2_9_0A8_DISPATCH = (
    b'{"id": "6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b", "group": 0, "args": "2026-11-02", "submit_id": "0'
    b'a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "submit_time": "2026-11-02 09:30:00.123456-05:00", "submit'
    b'_host": "submit-host", "cores": 1, "memory": null, "cores_max": null, "memory_max": null, "timeo'
    b'ut": null, "server_id": "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e", "server_host": "server-host", "s'
    b'chedule_time": "2026-11-02 14:31:00+00:00", "client_id": null, "client_host": null, "command": n'
    b'ull, "start_time": null, "completion_time": null, "exit_status": null, "outpath": null, "errpath'
    b'": null, "csvpath": null, "attempt": 1, "retried": false, "waited": null, "duration": null, "pre'
    b'vious_id": null, "next_id": null, "source": "2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f", "fingerprint'
    b'": "3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e", "zone": null, "part": 0, '
    b'"tag": {"day": "2026-11-02"}}'
)
WIRE_2_9_0A8_BUNDLE = (
    b'[{"id": "6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b", "group": 0, "args": "2026-11-02", "submit_id": "'
    b'0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "submit_time": "2026-11-02 09:30:00.123456-05:00", "submi'
    b't_host": "submit-host", "cores": 1, "memory": null, "cores_max": null, "memory_max": null, "time'
    b'out": null, "server_id": "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e", "server_host": "server-host", "'
    b'schedule_time": "2026-11-02 14:31:00+00:00", "client_id": null, "client_host": null, "command": '
    b'null, "start_time": null, "completion_time": null, "exit_status": null, "outpath": null, "errpat'
    b'h": null, "csvpath": null, "attempt": 1, "retried": false, "waited": null, "duration": null, "pr'
    b'evious_id": null, "next_id": null, "source": "2c3d4e5f-6a7b-4c8d-8e9f-1a2b3c4d5e6f", "fingerprin'
    b't": "3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e", "zone": null, "part": 0,'
    b' "tag": {"day": "2026-11-02"}}]'
)
WIRE_2_9_0A8_RESULT = (
    b'[{"id": "6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b", "group": 0, "args": "2026-11-02", "submit_id": "'
    b'0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "submit_time": "2026-11-02 09:30:00.123456-05:00", "submi'
    b't_host": "submit-host", "cores": 1, "memory": null, "cores_max": null, "memory_max": null, "time'
    b'out": null, "server_id": "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e", "server_host": "server-host", "'
    b'schedule_time": "2026-11-02 14:31:00+00:00", "client_id": "4e5f6a7b-8c9d-4e0f-a1b2-c3d4e5f6a7b8"'
    b', "client_host": "client-host", "command": "2026-11-02", "start_time": "2026-11-02 09:31:01.6543'
    b'21-05:00", "completion_time": "2026-11-02 09:31:03.000005", "exit_status": 0, "outpath": "2026-1'
    b'1-02.out", "errpath": "2026-11-02.err", "csvpath": null, "attempt": 1, "retried": false, "waited'
    b'": 61, "duration": 2, "previous_id": null, "next_id": null, "source": "2c3d4e5f-6a7b-4c8d-8e9f-1'
    b'a2b3c4d5e6f", "fingerprint": "3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e",'
    b' "zone": null, "part": 0, "tag": {"day": "2026-11-02"}}]'
)


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file, then restore it."""
    engine = create_engine(f'sqlite:///{tmp_path / "tasks.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


def through_bundle(task: Task) -> Task:
    """The task as the server's Receiver reads it: a result bundle, encoded and decoded."""
    return deserialize_tasks(serialize_tasks([task]))[0]


def through_dispatch(task: Task) -> Task:
    """The task as a client reads it: the dispatch text, loaded and decoded record by record."""
    return Task.from_json(json.loads(json.dumps(task.to_json())))


DECODERS = {'bundle': through_bundle, 'dispatch': through_dispatch}


def make_task(**fields: Any) -> Task:
    """A task with every column given, the rest from the fixed task."""
    return Task(**{**FIXED_TASK, **fields})


@mark.unit
@mark.parametrize('decoder', list(DECODERS))
@mark.parametrize('field', TEXT_FIELDS)
@mark.parametrize('form', DATE_LIKE)
def test_date_like_text_decodes_as_text(form: str, field: str, decoder: str) -> None:
    """Date-like text in any text column comes off the wire as the same text."""
    copy = DECODERS[decoder](make_task(**{field: form}))
    assert getattr(copy, field) == form
    assert isinstance(getattr(copy, field), str)


@mark.unit
@mark.parametrize('decoder', list(DECODERS))
@mark.parametrize('kind', list(TIMES))
@mark.parametrize('field', TIME_FIELDS)
def test_timestamps_decode_as_times(field: str, kind: str, decoder: str) -> None:
    """Each time column comes off the wire as an equal datetime with the same offset."""
    value = TIMES[kind]
    copy = DECODERS[decoder](make_task(**{field: value}))
    decoded = getattr(copy, field)
    assert isinstance(decoded, datetime)
    assert decoded == value
    assert decoded.utcoffset() == value.utcoffset()


@mark.unit
@mark.parametrize('decoder', list(DECODERS))
def test_tag_values_stay_as_sent(decoder: str) -> None:
    """A date-like tag value is left as text."""
    copy = DECODERS[decoder](make_task(tag={'day': '2026-11-02', 'week': '2026W45', 'n': 3}))
    assert copy.tag == {'day': '2026-11-02', 'week': '2026W45', 'n': 3}


@mark.unit
@mark.parametrize('value', [None, 12, 'not-a-time'], ids=['none', 'int', 'text'])
@mark.parametrize('field', TIME_FIELDS)
def test_odd_timestamp_values_pass_through(field: str, value: Any) -> None:
    """A time column holding something that is not a time is passed through as sent."""
    data = json.loads(json.dumps(make_task().to_json()))
    data[field] = value
    assert getattr(Task.from_json(data), field) == value


@mark.unit
def test_unknown_key_still_rejected() -> None:
    """A field the entity does not declare is refused, as before."""
    data = make_task().to_json()
    data['extra'] = '2026-11-02'
    with pytest.raises(TypeError):
        Task.from_json(data)


@mark.unit
@mark.parametrize('entity', [Task, Client, Source, Zone], ids=lambda entity: entity.__name__)
def test_datetime_columns_are_declared(entity: type) -> None:
    """Every DateTime column is declared a datetime, so it decodes as a time off the wire."""
    table = entity.__table__
    assert list(entity.columns) == [column.name for column in table.columns]
    for column in table.columns:
        if isinstance(column.type, DateTime):
            assert entity.columns[column.name] is datetime, column.name
        else:
            assert entity.columns[column.name] is not datetime, column.name


@mark.unit
def test_encoding_matches_2_9_0a8() -> None:
    """The dispatch text, a bundle and a result bundle encode byte for byte as 2.9.0a8 encodes them."""
    assert json.dumps(make_task().to_json()).encode('utf-8') == WIRE_2_9_0A8_DISPATCH
    assert serialize_tasks([make_task()]) == WIRE_2_9_0A8_BUNDLE
    assert serialize_tasks([Task(**FIXED_RESULT)]) == WIRE_2_9_0A8_RESULT


def assert_fields(task: Task, expected: Dict[str, Any]) -> None:
    """Every column of `task` equals `expected`, times with their offsets."""
    for name, value in expected.items():
        assert getattr(task, name) == value, name
        if isinstance(value, datetime):
            assert getattr(task, name).utcoffset() == value.utcoffset(), name


@mark.unit
def test_2_9_0a8_dispatch_decodes() -> None:
    """A dispatch recorded from 2.9.0a8 decodes with its line as text and its times intact."""
    assert_fields(Task.from_json(json.loads(WIRE_2_9_0A8_DISPATCH)), FIXED_TASK)
    assert_fields(deserialize_tasks(WIRE_2_9_0A8_BUNDLE)[0], FIXED_TASK)


@mark.unit
def test_2_9_0a8_result_decodes() -> None:
    """A result recorded from 2.9.0a8 decodes with its command as text and its times intact."""
    assert_fields(deserialize_tasks(WIRE_2_9_0A8_RESULT)[0], FIXED_RESULT)


@mark.unit
@mark.parametrize('template', ['echo {}', DEFAULT_TEMPLATE])
@mark.parametrize('form', DATE_LIKE)
def test_template_expands_date_like_args(form: str, template: str) -> None:
    """A date-like line received by a client expands to the exact text under a template."""
    copy = through_dispatch(make_task(args=form))
    assert Template(template).expand(copy.args) == template.replace('{}', form)


def dispatched_copies(*args: str) -> List[Task]:
    """Submit one task per line and return the copies a client receives, through the wire."""
    for line in args:
        Task.add(Task.new(args=line))
    return [through_dispatch(task) for task in Task.next(limit=len(args))]


def finish(copy: Task) -> Task:
    """Stamp a copy's result columns as a client does under the default template."""
    start_time = datetime.now().astimezone()
    copy.client_id, copy.client_host = FIXED_RESULT['client_id'], '2026-11-02'
    copy.command = copy.args
    copy.start_time = start_time
    copy.completion_time = start_time + timedelta(seconds=2)
    copy.waited = 1
    copy.duration = 2
    copy.exit_status = 127
    copy.outpath = '2026-11-02.out'
    copy.errpath = '2026-11-02 12:00'
    copy.csvpath = '20261102'
    return copy


@mark.unit
def test_apply_results_stores_date_like_command_verbatim(task_db: Engine) -> None:
    """A result whose text fields look like dates is stored as sent, onto its own dispatch only."""
    copy, = dispatched_copies('2026-11-02')
    finish(copy)
    shifted = through_bundle(copy)
    shifted.schedule_time = shifted.schedule_time + timedelta(microseconds=1)
    assert Task.apply_results([shifted]) == [ResultOutcome.STALE]
    assert Task.apply_results([through_bundle(copy)]) == [ResultOutcome.APPLIED]
    row = Task.from_id(copy.id, caching=False)
    assert (row.args, row.command, row.client_host) == ('2026-11-02', '2026-11-02', '2026-11-02')
    assert (row.outpath, row.errpath, row.csvpath) == ('2026-11-02.out', '2026-11-02 12:00', '20261102')
    assert row.exit_status == 127
    assert Task.apply_results([through_bundle(copy)]) == [ResultOutcome.DUPLICATE]


@mark.unit
def test_apply_results_takes_2_9_0a8_result(task_db: Engine) -> None:
    """A result recorded from 2.9.0a8 is applied to the dispatch it names, its command stored as sent."""
    Task.add(Task.from_json(json.loads(WIRE_2_9_0A8_DISPATCH)))
    assert Task.apply_results(deserialize_tasks(WIRE_2_9_0A8_RESULT)) == [ResultOutcome.APPLIED]
    row = Task.from_id(FIXED_TASK['id'], caching=False)
    assert (row.args, row.command, row.exit_status) == ('2026-11-02', '2026-11-02', 0)
