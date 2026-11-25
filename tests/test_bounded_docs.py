# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The documentation gives each numeric setting's range and advisory limit, as the table renders them.

Each command's help page gives the range (and any advisory limit) of each numeric option, and the
command's page marks the change; the parameter reference gives each numeric key's range beside it;
the environment-variable page says a variable is held to its key's range; the API pages name the
ranges; `AGENTS.md` and the review rubric name the table. The rendered text comes from the table
(`Range.text`, `Range.advice_text`), so the docs cannot drift from it.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, List, Tuple

# Standard libs
import re
from pathlib import Path

# External libs
import pytest
from pytest import mark

# Internal libs
from hypershell.core.config_schema import KEYS, ZONE_KEYS, Kind
from tests.test_bounded_table import OPTIONS, NUMERIC
from tests.test_client_host_default import MARKER
from tests.test_config_schema import parse_reference


REPO: Final[Path] = Path(__file__).resolve().parent.parent
DOCS: Final[Path] = REPO / 'docs'

PAGES: Final[Dict[str, str]] = {
    'cluster': 'cluster', 'server': 'server', 'client': 'client', 'relay': 'relay', 'submit': 'submit',
    'list': 'task_search', 'update': 'task_update', 'wait': 'task_wait', 'run': 'task_run',
}
"""Each command with a numeric option, and the name of its help snippet and page."""


def flat(text: str) -> str:
    """`text` with every run of white space made one space (an RST paragraph wraps anywhere)."""
    return ' '.join(text.split())


def entries() -> Any:
    """The table's entries (imported when a test runs, not at collection)."""
    from hypershell.core.config_schema import SETTINGS
    return SETTINGS


def snippet_entry(page: str, flag: str) -> str:
    """The entry of `flag` (its first flag) on the help snippet `page`: its term line and the body under it."""
    lines = (DOCS / '_include' / f'{page}_help.rst').read_text(encoding='utf-8').splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f'``{flag}``')]
    assert len(starts) == 1, f'{page}_help.rst has {len(starts)} entries for {flag}'
    entry = [lines[starts[0]]]
    for line in lines[starts[0] + 1:]:
        if line and not line.startswith(' '):
            break
        entry.append(line)
    return '\n'.join(entry)


def first_flag(name: str) -> str:
    """The first flag of an option's name (`-N` of `-N/--num-threads`)."""
    return name.split('/')[0]


@mark.unit
@mark.parametrize('command, dest', sorted(OPTIONS), ids=[f'{command}-{dest}' for command, dest in sorted(OPTIONS)])
def test_help_snippet_gives_range(command: str, dest: str) -> None:
    """The option's entry on its command's help page gives its range and any advisory limit."""
    setting, name = OPTIONS[(command, dest)]
    entry = flat(snippet_entry(PAGES[command], first_flag(name)))
    range_ = entries()[setting]
    assert range_.text in entry, (range_.text, entry)
    if range_.advise is not None:
        assert range_.advice_text in entry, (range_.advice_text, entry)


@mark.unit
@mark.parametrize('command', sorted(PAGES))
def test_cli_page_help_marks_ranges(command: str) -> None:
    """The command's page marks the ranges with a `versionchanged:: 2.9.0` above its included help."""
    page = PAGES[command]
    text = (DOCS / 'cli' / f'{page}.rst').read_text(encoding='utf-8')
    above, _ = text.split(f'.. include:: ../_include/{page}_help.rst')
    assert any('range' in body for body in MARKER.findall(above)), page


def numeric_keys() -> List[str]:
    """Every numeric key of `KEYS`."""
    return sorted(path for path, kind in KEYS.items() if kind in NUMERIC)


@mark.unit
@mark.parametrize('path', numeric_keys())
def test_param_reference_gives_range(path: str) -> None:
    """The key's entry in the parameter reference gives its range and any advisory limit, marked 2.9.0."""
    body = parse_reference()[path][1]
    range_ = entries()[path]
    assert range_.text in flat(body), (range_.text, flat(body))
    if range_.advise is not None:
        assert range_.advice_text in flat(body), (range_.advice_text, flat(body))
    assert 'versionchanged:: 2.9.0' in body


@mark.unit
def test_param_reference_zone_keys() -> None:
    """The `relay.zone` entry gives the range of each numeric zone key."""
    body = flat(parse_reference()['relay.zone'][1])
    for name, kind in ZONE_KEYS.items():
        if kind in NUMERIC:
            assert entries()[f'relay.zone.*.{name}'].text in body, name


@mark.unit
@mark.parametrize('page', ['config_intro.rst', 'config_intro_alt.rst'])
def test_env_page_names_ranges(page: str) -> None:
    """The environment-variable page says a variable is held to its key's range, at load, for every command."""
    text = flat((DOCS / '_include' / page).read_text(encoding='utf-8'))
    assert "held to its key's range" in text, page
    assert 'checked by the command that reads it' not in text, page


@mark.unit
def test_config_set_page_names_ranges() -> None:
    """The `hs config set` page says a numeric value is held to its key's range, an untyped integer to 64 bits, marked."""
    text = flat((DOCS / '_include' / 'config_set_desc.rst').read_text(encoding='utf-8'))
    assert "held to the key's range" in text and 'outside 64 bits' in text and 'advisory limit' in text
    assert 'checked by the command that reads it' not in text
    page = (DOCS / 'cli' / 'config_set.rst').read_text(encoding='utf-8')
    assert any("held to the key's range" in flat(body) for body in MARKER.findall(page))


@mark.unit
@mark.parametrize('page', ['client', 'cluster', 'server', 'submit'])
def test_api_pages_name_ranges(page: str) -> None:
    """Each API page says its configuration defaults are held to the table's ranges, and explicit arguments are not."""
    text = (DOCS / 'api' / f'{page}.rst').read_text(encoding='utf-8')
    bodies = [flat(body) for body in MARKER.findall(text)]
    assert any('range' in body and 'explicit' in body for body in bodies), page


@mark.unit
def test_agents_and_rubric_name_table() -> None:
    """`AGENTS.md` § Configuration names the table and the advisory tier; the rubric points at it."""
    agents = (REPO / 'AGENTS.md').read_text(encoding='utf-8')
    section = agents.split('## Configuration', 1)[1].split('\n## ', 1)[0]
    assert '`SETTINGS`' in section and 'advisory' in section
    rubric = (REPO / '.agents' / 'factory' / 'review-rubric.md').read_text(encoding='utf-8')
    assert 'before `bounded-inputs` lands' not in rubric
    assert '`SETTINGS`' in rubric
