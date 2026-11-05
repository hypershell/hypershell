# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A task's zone is shown by name in `hs info` and `hs list`; the raw id never needs to be read."""


# Type annotations
from __future__ import annotations
from typing import Dict

# Standard libs
import re
import json
from pathlib import Path

# External libs
from pytest import mark, fixture
from cmdkit.app import exit_status

# Internal libs
from hypershell.data.model import Task, zone_id
from tests import main, main_lines


ANSI = re.compile(r'\x1b\[[0-9;]*m')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


@fixture
def tasks(temp_site: Path) -> Dict[str, str]:
    """One task in zone gpu and one without a zone: name -> task id."""
    ids = {}
    for name, options in (('gpu', ['--zone', 'gpu']), ('default', [])):
        rc, out, err = main(['hs', 'task', 'submit', *options, '--', 'echo', name])
        assert rc == exit_status.success, err
        ids[name] = out.strip()
    return ids


@mark.unit
def test_info_normal_view(tasks: Dict[str, str]) -> None:
    """The normal view names the zone, with its id for a named zone."""
    out = ANSI.sub('', main(['hs', 'info', tasks['gpu']])[1])
    assert re.search(rf'^\s+zone: gpu \({zone_id("gpu")}\)$', out, re.MULTILINE), out
    out = ANSI.sub('', main(['hs', 'info', tasks['default']])[1])
    assert re.search(r'^\s+zone: default$', out, re.MULTILINE), out


@mark.unit
def test_info_card_shows_zone(tasks: Dict[str, str]) -> None:
    """The card's submission region has a zone row."""
    out = ANSI.sub('', main(['hs', 'info', tasks['gpu'], '--format=card'])[1])
    assert re.search(r'zone\s+gpu', out), out


@mark.unit
def test_info_json_and_extract(tasks: Dict[str, str]) -> None:
    """`hs info --json` and `-x zone` give the name."""
    data = json.loads(main(['hs', 'info', tasks['gpu'], '--json'])[1])
    assert data['zone'] == 'gpu'
    data = json.loads(main(['hs', 'info', tasks['default'], '--json'])[1])
    assert data['zone'] == 'default'
    assert main_lines(['hs', 'info', tasks['gpu'], '-x', 'zone'])[1] == ['gpu']
    assert main_lines(['hs', 'info', tasks['default'], '-x', 'zone'])[1] == ['default']


@mark.unit
@mark.parametrize('fmt', ['plain', 'table', 'csv', 'json'])
def test_list_formats(tasks: Dict[str, str], fmt: str) -> None:
    """`hs list` shows zone names in every tabular format."""
    rc, out, err = main(['hs', 'list', 'args', 'zone', '--format', fmt])
    assert rc == exit_status.success, err
    out = ANSI.sub('', out)
    assert 'gpu' in out and 'default' in out
    assert zone_id('gpu') not in out
    if fmt == 'json':
        assert sorted((row['args'], row['zone']) for row in json.loads(out)) == [('echo default', 'default'),
                                                                                ('echo gpu', 'gpu')]


@mark.unit
def test_list_normal_view(tasks: Dict[str, str]) -> None:
    """`hs list` with every field (normal view) names each task's zone."""
    out = ANSI.sub('', main(['hs', 'list', '--all'])[1])
    assert re.search(r'^\s+zone: gpu \(', out, re.MULTILINE) and re.search(r'^\s+zone: default$', out, re.MULTILINE)


@mark.unit
def test_wire_format_unchanged(temp_site: Path) -> None:
    """`Task.to_json()` still carries the raw zone id (the wire format and task environment)."""
    task = Task.new(args='echo x', zone=zone_id('gpu'))
    assert task.to_json()['zone'] == zone_id('gpu')
    assert Task.new(args='echo y').to_json()['zone'] is None
