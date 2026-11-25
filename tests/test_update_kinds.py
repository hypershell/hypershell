# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs update` reads `retried`, the uuid columns, `zone` and the text columns by their kinds.

`retried` takes the configuration's boolean words in any case; `submit_id`, `server_id`, `client_id`,
`previous_id`, `next_id` and `source` take a uuid in the 8-4-4-4-12 form, stored in lower case;
`zone` takes a zone name as `hs submit --zone` reads it, `default` and `none` being the default zone
(NULL), and records a new zone only once the change is written; `args` takes any text but `none`;
the other text columns are as before. Any other value exits with status 2 naming the column and the
value, and nothing is written.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Tuple
from pathlib import Path

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.core.config_schema import shown
from hypershell.data.model import DIRECT_SOURCE_ID, STDIN_SOURCE_ID, zone_id
from hypershell.task import change_value
from tests.input_checks import db_site, rows  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.update_checks import (FORMS, REFUSED, hs, submit_three, snapshot, task_row, update_argv,
                                 assert_refused, drive, same_bool, same_text)


UPPER: Final[str] = '0198F3A2-1B2C-7D3E-8F40-0123456789AB'
"""A uuid in upper case."""

LOWER: Final[str] = UPPER.lower()
"""The same uuid as stored."""

UUID_COLUMNS: Final[List[str]] = ['submit_id', 'server_id', 'client_id', 'previous_id', 'next_id', 'source']
"""The columns that hold a uuid."""

TEXT_COLUMNS: Final[List[str]] = ['command', 'outpath', 'errpath', 'csvpath', 'submit_host', 'server_host',
                                  'client_host', 'fingerprint']
"""The text columns that may be empty."""

RETRIED: Final[List[Tuple[str, Any]]] = [
    ('TRUE', True), ('Yes', True), ('oN', True), ('1', True), ('true', True),
    ('OFF', False), ('no', False), ('0', False), ('False', False),
    ('none', REFUSED), ('NULL', REFUSED), ('x', REFUSED), ('2', REFUSED), ('', REFUSED), ('t', REFUSED),
]
"""Values of `retried` and what each stores."""


def uuid_cases(column: str) -> List[Tuple[str, Any]]:
    """Values of a uuid column and what each stores."""
    accepted = [(UPPER, LOWER), (LOWER, LOWER)]
    if column == 'source':
        accepted += [(DIRECT_SOURCE_ID, DIRECT_SOURCE_ID), (STDIN_SOURCE_ID, STDIN_SOURCE_ID)]
    refused = ['{' + LOWER + '}', 'urn:uuid:' + LOWER, LOWER.replace('-', ''), LOWER + '\n', LOWER[:-1],
               '<direct>', 'x', '']
    clear = [(word, REFUSED if column == 'submit_id' else None) for word in ('none', 'NULL')]
    return [*accepted, *[(text, REFUSED) for text in refused], *clear]


def zone_cases() -> List[Tuple[str, Any]]:
    """Values of `zone` and what each stores: the default zone is NULL, any other name its zone id."""
    return [('lab', zone_id('lab')), ('default', None), ('none', None), ('NULL', None), ('null', None),
            ('Default', zone_id('Default')), (' east ', zone_id('east')), ('', REFUSED), ('   ', REFUSED),
            ('a\tb', REFUSED), ('z' * 257, REFUSED)]


ARGS: Final[List[Tuple[str, Any]]] = [('echo changed', 'echo changed'), ('1', '1'), ('', ''),
                                      ('none', REFUSED), ('NULL', REFUSED)]
"""Values of `args` (text that may not be empty) and what each stores."""

TEXT: Final[List[Tuple[str, Any]]] = [('x y', 'x y'), ('1', '1'), ('', ''), ('none', None), ('NULL', None)]
"""Values of a text column that may be empty and what each stores (as before)."""


def refused_in_process(column: str, text: str) -> None:
    """`change_value` refuses `text` for `column`, naming both."""
    with pytest.raises(ArgumentError) as refused:
        change_value(column, text)
    message = str(refused.value)
    assert message.startswith(f'{column}: expected ') and f'given {shown(text)}' in message, message


def in_process(column: str, values: List[Tuple[str, Any]]) -> None:
    """Each value of `column` read in process: stored as expected, or refused."""
    for text, expected in values:
        if expected is REFUSED:
            refused_in_process(column, text)
        else:
            value = change_value(column, text)
            assert value == expected and type(value) is type(expected), (column, text, value)


# Layer 1: every value read in process


@mark.unit
def test_change_value_retried() -> None:
    """`retried` takes the boolean words in any case, and nothing else."""
    in_process('retried', RETRIED)


@mark.unit
@mark.parametrize('column', UUID_COLUMNS)
def test_change_value_uuid(column: str) -> None:
    """A uuid column takes the 8-4-4-4-12 form in any case, in lower case, and nothing else."""
    in_process(column, uuid_cases(column))


@mark.unit
def test_change_value_zone() -> None:
    """`zone` takes a zone name, the default zone being NULL."""
    in_process('zone', zone_cases())


@mark.unit
def test_change_value_args() -> None:
    """`args` takes any text but `none` or `null`."""
    in_process('args', ARGS)


@mark.unit
@mark.parametrize('column', TEXT_COLUMNS)
def test_change_value_text(column: str) -> None:
    """A text column takes the text as given, `none` clearing it, as before (guard)."""
    in_process(column, TEXT)


# Layer 2: each column through the command line, in both forms, on both backends


def same_zone(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """The zone id expected, or NULL for the default zone."""
    same_text(engine, column, stored, expected)


KIND_COLUMNS: Final[List[str]] = ['retried', *UUID_COLUMNS, 'zone', 'args']
"""The columns of this file driven through the command line."""


def column_cases(column: str) -> Tuple[List[Tuple[str, Any]], Any]:
    """The values of `column` and how each stored value is checked."""
    if column == 'retried':
        return RETRIED, same_bool
    if column == 'zone':
        return zone_cases(), same_zone
    if column == 'args':
        return ARGS, same_text
    return uuid_cases(column), same_text


@mark.integration
@mark.parametrize('form', FORMS)
@mark.parametrize('column', KIND_COLUMNS)
def test_column_values(db_site: Tuple[Path, Engine], column: str, form: str) -> None:
    """Every value of the column: stored as read when accepted, refused with exit 2 and nothing written otherwise."""
    site, engine = db_site
    submit_three(site)
    values, check = column_cases(column)
    drive(site, engine, column, form, values, check)


@mark.integration
@mark.parametrize('form', FORMS)
def test_text_columns_unchanged(db_site: Tuple[Path, Engine], form: str) -> None:
    """Each text column takes the text as given and `none` clears it, as before (guard)."""
    site, engine = db_site
    submit_three(site)
    for column in TEXT_COLUMNS:
        drive(site, engine, column, form, TEXT, same_text)


# Layer 3: the zone is recorded only once the change is written


def zones_named(engine: Engine, name: str) -> int:
    """The number of recorded zones named `name`."""
    return rows(engine, 'SELECT count(*) FROM zone WHERE name = :name', name=name)[0][0]


@mark.integration
@mark.parametrize('form', FORMS)
def test_zone_recorded(db_site: Tuple[Path, Engine], form: str) -> None:
    """A new zone name is recorded, as `hs submit --zone` records it, and `hs list` shows the name."""
    site, engine = db_site
    submit_three(site)
    assert zones_named(engine, 'lab') == 0
    rc, _, stderr = hs(site, update_argv(engine, form, 'zone', 'lab', '-f'))
    assert rc == 0, stderr[-4000:]
    assert str(task_row(engine, 1)['zone']) == zone_id('lab')
    assert zones_named(engine, 'lab') == 1
    rc, stdout, stderr = hs(site, ['hs', 'list', 'zone', '-t', 'n:1'])
    assert rc == 0, stderr[-4000:]
    assert stdout.strip() == 'lab', stdout


@mark.integration
@mark.parametrize('form', FORMS)
def test_zone_default_is_null(db_site: Tuple[Path, Engine], form: str) -> None:
    """`zone=default` puts the task back in the default zone: NULL, so the default zone's filter matches it."""
    site, engine = db_site
    submit_three(site)
    rc, _, stderr = hs(site, update_argv(engine, form, 'zone', 'lab', '-f'))
    assert rc == 0, stderr[-4000:]
    rc, _, stderr = hs(site, update_argv(engine, form, 'zone', 'default', '-f'))
    assert rc == 0, stderr[-4000:]
    assert task_row(engine, 1)['zone'] is None
    rc, stdout, stderr = hs(site, ['hs', 'list', '-c', '-w', 'zone == none'])
    assert rc == 0, stderr[-4000:]
    assert stdout.strip() == '3', stdout


@mark.integration
def test_zone_not_recorded_on_refusal(db_site: Tuple[Path, Engine]) -> None:
    """A zone given with a refused change is not recorded, and nothing is written."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, stdout, stderr = hs(site, ['hs', 'update', 'zone=lab', 'cores=x', '-t', 'n:1', '-f'])
    assert_refused(rc, stdout, stderr, 'cores', 'x')
    assert snapshot(engine) == before
    assert zones_named(engine, 'lab') == 0


@mark.integration
def test_zone_not_recorded_when_declined(db_site: Tuple[Path, Engine]) -> None:
    """A zone change declined at the prompt records no zone and writes nothing (guard)."""
    site, engine = db_site
    submit_three(site)
    before = snapshot(engine)
    rc, _, stderr = hs(site, ['hs', 'update', 'zone=lab', '-t', 'n:1'], stdin='no\n')
    assert rc == 0, stderr[-4000:]
    assert snapshot(engine) == before
    assert zones_named(engine, 'lab') == 0
