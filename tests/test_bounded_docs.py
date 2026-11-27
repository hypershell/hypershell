# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The documentation states the range rule; the figures stay in the code.

Each numeric setting's range and advisory limit live in one table (`SETTINGS`), and a refusal or a
warning names them. The user documentation (the pages the website renders and the manual pages built
from them) gives no bound and no advisory threshold: a storage width written there would make it an
interface. It says a numeric value is held to its range, refused outside it with a message naming
the range, and taken with a warning past an advisory limit: each command's page marks the change, the
environment-variable page says a variable is held to its key's range, the API pages name the ranges,
and `AGENTS.md` and the review rubric name the table.
"""


# Type annotations
from __future__ import annotations
from typing import Final, List, Set

# Standard libs
import re
from pathlib import Path

# External libs
from pytest import mark

# Internal libs
from tests.test_client_host_default import MARKER


REPO: Final[Path] = Path(__file__).resolve().parent.parent
DOCS: Final[Path] = REPO / 'docs'
MAN: Final[Path] = REPO / 'share' / 'man' / 'man1'

PAGES: Final[List[str]] = ['cluster', 'server', 'client', 'relay', 'submit', 'task_search', 'task_update',
                           'task_wait', 'task_run']
"""The page of each command with a numeric option."""

RANGE_SENTENCE: Final[re.Pattern] = re.compile(r'\bRange:')
"""A range written out, as the documentation gave each one in 2.9.0a10."""

THRESHOLD: Final[re.Pattern] = re.compile(r'\b(?:[Aa]bove|[Bb]elow|[Pp]ast|[Uu]nder|[Oo]ver|[Bb]eyond|[Mm]ore than|[Ll]ess than|'
                                          r'[Ff]ewer than) \\?-?\d[\d,.]*[^.]*\bwarning')
"""An advisory threshold written out (`Above 1,024 it runs with a warning`, `More than 1,024 … warning`)."""

FIGURE: Final[re.Pattern] = re.compile(r'(?<![\w.])(?<!\d,)\\?-?(\d{1,3}(?:,\d{3})+|\d{4,})(?!\w|,\d)')
"""A whole number of four or more digits, with or without thousands separators (`65,535`, `65535`)."""


def flat(text: str) -> str:
    """`text` with every run of white space made one space (an RST paragraph wraps anywhere)."""
    return ' '.join(text.split())


def user_docs() -> List[Path]:
    """Every source page of the website and every manual page."""
    pages = [path for path in DOCS.rglob('*.rst') if '_build' not in path.parts]
    return sorted(pages + list(MAN.glob('*.1')))


def bound_figures() -> Set[str]:
    """Each whole bound of the table of four or more digits, without separators (`2147483647`)."""
    from hypershell.core.config_schema import SETTINGS
    figures = set()
    for entry in SETTINGS.values():
        for bound in (entry.low, entry.high):
            if abs(bound) >= 1000 and float(bound).is_integer():
                figures.add(str(int(abs(bound))))
    return figures


def found_figures(text: str, figures: Set[str]) -> List[str]:
    """Each figure in `text` that is a bound of the table, written with or without separators."""
    return [match.group(1) for match in FIGURE.finditer(text) if match.group(1).replace(',', '') in figures]


@mark.unit
def test_docs_give_no_range_sentence() -> None:
    """No page writes out a range or an advisory threshold."""
    found = []
    for path in user_docs():
        text = flat(path.read_text(encoding='utf-8'))
        found += [f'{path.relative_to(REPO)}: {match.group(0)}' for match in RANGE_SENTENCE.finditer(text)]
        found += [f'{path.relative_to(REPO)}: {match.group(0)}' for match in THRESHOLD.finditer(text)]
    assert not found, found


@mark.unit
def test_docs_give_no_bound_figure() -> None:
    """No page writes a bound of the table (a storage width) as a figure."""
    figures = bound_figures()
    assert {'2147483647', '32767', '65535'} <= figures
    found = []
    for path in user_docs():
        found += [f'{path.relative_to(REPO)}: {number}' for number in found_figures(path.read_text(encoding='utf-8'), figures)]
    assert not found, found


@mark.unit
@mark.parametrize('text', [
    'Range: 0 to 32,767.',
    'A group is an integer from -2147483648 to 2147483647, written as -g takes it.',
    'A port from 1 to 65535.',
    'A port from 1 to 65,535.',
    'More than 1,024 cores runs with a warning.',
    'Above 3,600 s it runs with a warning.',
    r'Range: \\-2,147,483,648 to 2,147,483,647.',
])
def test_guard_catches(text: str) -> None:
    """The guard's patterns catch a range, a threshold or a bound however its figures are written."""
    flagged = bool(RANGE_SENTENCE.search(text) or THRESHOLD.search(text) or found_figures(text, bound_figures()))
    assert flagged, text


@mark.unit
@mark.parametrize('page', PAGES)
def test_cli_page_help_marks_ranges(page: str) -> None:
    """The command's page marks the ranges with a `versionchanged:: 2.9.0` above its included help."""
    text = (DOCS / 'cli' / f'{page}.rst').read_text(encoding='utf-8')
    above, _ = text.split(f'.. include:: ../_include/{page}_help.rst')
    assert any('range' in body for body in MARKER.findall(above)), page


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
