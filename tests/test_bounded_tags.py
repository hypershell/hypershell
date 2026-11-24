# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The task resource tags `cores`, `memory` and `timeout` take the ranges of `task.cores`, `task.memory` and `task.timeout`.

They are refused as a task group that is not an integer is refused: input read in full is refused
whole (exit 5, nothing inserted, the value and its line or record named); on a stream only the line
is refused, with an ERROR; `-t` exits 2. Past an advisory limit, one summary per submission. On
PostgreSQL the largest values of each column are stored.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Tuple

# Standard libs
import logging
from pathlib import Path
from queue import Queue

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.submit import Loader
from tests import create_taskfile, create_json_taskfile
from tests.input_checks import db_site, count, rows, submit, set_task, sqlite_engine, bounded  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)


@mark.integration
@mark.parametrize('tag, value', [('cores', '100000'), ('memory', '8388608TB'), ('timeout', '2147483648'),
                                 ('cores', 'x'), ('cores', '-1')],
                  ids=['cores-100000', 'memory-8388608TB', 'timeout-2147483648', 'cores-x', 'cores-minus1'])
def test_tag_option_refused(temp_site: Path, tag: str, value: str) -> None:
    """`-t` with a resource tag out of range, or not of its kind, exits 2 naming the value; nothing is stored."""
    site = Path(str(temp_site))
    rc, _, stderr = submit('echo', 'a', '-t', f'{tag}:{value}', cwd=site)
    assert rc == exit_status.bad_argument, stderr[-4000:]
    assert f'task.{tag}: expected ' in stderr and value in stderr, stderr[-4000:]
    assert 'Traceback' not in stderr, stderr[-4000:]


SINGLE_TASK: Final[List[List[str]]] = [['hs', 'run', '-n', '1'], ['hs', 'task', 'submit']]
"""Commands that submit one task with `-t` tags, before the tags and the task's arguments."""


@mark.integration
@mark.parametrize('command', SINGLE_TASK, ids=['run', 'task-submit'])
@mark.parametrize('tag, value', [('cores', '100000'), ('memory', '8388608TB'), ('timeout', '2147483648'),
                                 ('cores', 'x'), ('cores', '4.5')],
                  ids=['cores-100000', 'memory-8388608TB', 'timeout-2147483648', 'cores-x', 'cores-4.5'])
def test_single_task_tag_option_refused(db_site: Tuple[Path, Engine], command: List[str], tag: str, value: str) -> None:
    """`hs run -t` (and the one-task submit) with a resource tag out of range exits 2 naming its entry; nothing is stored."""
    site, engine = db_site
    rc, _, stderr = bounded([*command, '-t', f'{tag}:{value}', '--', 'echo', 'hi'], cwd=site)
    assert rc == exit_status.bad_argument, stderr[-4000:]
    lines = [line for line in stderr.splitlines() if f'task.{tag}: expected ' in line]
    assert len(lines) == 1 and value in lines[0], stderr[-4000:]
    assert 'Traceback' not in stderr and 'size: ' not in stderr, stderr[-4000:]
    assert count(engine, 'task') == 0


@mark.integration
def test_single_task_tag_option_stored(db_site: Tuple[Path, Engine]) -> None:
    """The one-task submit stores the largest resource tags given by `-t`, and a zero-fraction decimal as its integer."""
    site, engine = db_site
    rc, _, stderr = bounded(['hs', 'task', 'submit', '-t', 'cores:32767', 'memory:8388607TB', 'timeout:2147483647',
                             '--', 'echo', 'a'], cwd=site)
    assert rc == 0, stderr[-4000:]
    rc, _, stderr = bounded(['hs', 'task', 'submit', '-t', 'cores:4.0', 'timeout:90.5', '--', 'echo', 'b'], cwd=site)
    assert rc == 0, stderr[-4000:]
    assert sorted(rows(engine, 'SELECT args, cores, memory, timeout FROM task')) == [
        ('echo a', 32767, 8388607 * 1024**4, 2**31 - 1), ('echo b', 4, None, 91)]


ONE_TASK_ADVISED: Final[List[Tuple[str, List[str]]]] = [
    ('submit-tag', ['hs', 'submit', '-t', 'cores:1025', '--', 'echo', 'a']),
    ('submit-inline', ['hs', 'submit', '--', 'echo', 'a', '#', 'HYPERSHELL:', 'cores:1025']),
    ('task-submit-tag', ['hs', 'task', 'submit', '-t', 'cores:1025', '--', 'echo', 'a']),
]
"""A single task given as arguments with a resource tag past its advisory limit, by test id."""


@mark.integration
@mark.parametrize('argv', [pytest.param(argv, id=case) for case, argv in ONE_TASK_ADVISED])
@mark.parametrize('db_site', ['sqlite'], indirect=True)
def test_one_task_tags_advised(db_site: Tuple[Path, Engine], argv: List[str]) -> None:
    """A single task from arguments past an advisory limit is stored as given, with the one summary of the other paths."""
    site, engine = db_site
    rc, _, stderr = bounded(argv, cwd=site)
    assert rc == 0, stderr[-4000:]
    assert rows(engine, 'SELECT args, cores FROM task') == [('echo a', 1025)]
    advice = [line for line in stderr.splitlines() if 'WARNING' in line and 'advised' in line]
    assert len(advice) == 1, stderr[-4000:]
    assert 'task.cores' in advice[0] and '1 task ' in advice[0] and 'the command line' in advice[0], advice[0]


WHOLE_DECIMALS: Final[List[Tuple[str, str, int]]] = [('cores', '4.0', 4), ('cores', '4.00', 4), ('cores', '+4.0', 4),
                                                     ('cores', '1_000.0', 1000), ('timeout', '90.5', 91),
                                                     ('timeout', '4.0', 4), ('memory', '4.0', 4)]
"""A resource tag written as a decimal, the value stored (a duration rounds a fraction of a second up)."""


@mark.integration
@mark.parametrize('db_site', ['sqlite'], indirect=True)
def test_whole_decimal_tags_stored(db_site: Tuple[Path, Engine]) -> None:
    """A whole-number tag written with a zero fraction is that integer from a line, stdin, `-t` and a stream (maintainer ruling)."""
    site, engine = db_site
    lines = [f'echo {n} # HYPERSHELL: {tag}:{value}' for n, (tag, value, _) in enumerate(WHOLE_DECIMALS)]
    path = create_taskfile(site, lines)
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    stored = {args: dict(zip(('cores', 'memory', 'timeout'), values))
              for args, *values in rows(engine, 'SELECT args, cores, memory, timeout FROM task')}
    for n, (tag, value, want) in enumerate(WHOLE_DECIMALS):
        assert stored[f'echo {n}'][tag] == want, (tag, value, stored[f'echo {n}'])
    rc, _, stderr = submit('-b', '1', '-w', '1', stdin='echo s # HYPERSHELL: cores:8.0\n', cwd=site)
    assert rc == 0, stderr[-4000:]
    rc, _, stderr = submit('-t', 'cores:16.00', '--', 'echo', 't', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert dict(rows(engine, "SELECT args, cores FROM task WHERE args IN ('echo s', 'echo t')")) == {
        'echo s': 8, 'echo t': 16}
    assert load(['echo u # HYPERSHELL: cores:2.0', 'echo v # HYPERSHELL: cores:2.5']) == ['echo u']


@mark.integration
def test_whole_decimal_option_refused(temp_site: Path) -> None:
    """The ruling is the tags' alone: `-c 4.0` is refused, as before."""
    rc, _, stderr = submit('-c', '4.0', 'echo', 'a', cwd=Path(str(temp_site)))
    assert rc == exit_status.bad_argument, stderr[-4000:]
    assert "-c/--cores: expected a whole number" in stderr and "'4.0'" in stderr, stderr[-4000:]


REFUSED_LINES: Final[List[Tuple[str, str]]] = [('cores', '100000'), ('memory', '9223372036854775808'),
                                               ('timeout', '2147483648'), ('cores', 'x'), ('cores', '1.5'),
                                               ('memory', '8EB'), ('timeout', '-5')]
"""Resource tags a task line may not carry, as written."""


@mark.integration
@mark.parametrize('tag, value', REFUSED_LINES, ids=[f'{tag}-{value}' for tag, value in REFUSED_LINES])
def test_file_tag_refused_whole(db_site: Tuple[Path, Engine], tag: str, value: str) -> None:
    """A task file with a resource tag out of range inserts nothing and exits 5, naming the value and its line."""
    site, engine = db_site
    path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {tag}:{value}', 'echo c'])
    rc, _, stderr = submit('-b', '1', '-w', '1', str(path), cwd=site)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    lines = [line for line in stderr.splitlines() if f'task.{tag}: expected ' in line]
    assert len(lines) == 1 and value in lines[0] and 'line 2' in lines[0], stderr[-4000:]
    assert count(engine, 'task') == 0


@mark.integration
@mark.parametrize('db_site', ['sqlite'], indirect=True)
def test_stdin_tag_refused_whole(db_site: Tuple[Path, Engine]) -> None:
    """Standard input to `hs submit` with a database is read in full and refused whole."""
    site, engine = db_site
    rc, _, stderr = submit('-b', '1', '-w', '1', stdin='echo a\necho b # HYPERSHELL: cores:100000\n', cwd=site)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert 'line 2' in stderr and '100000' in stderr, stderr[-4000:]
    assert count(engine, 'task') == 0


JSON_REFUSED: Final[List[Tuple[str, str, Any]]] = [('cores-text', 'cores', 'x'), ('cores-100000', 'cores', 100000),
                                                  ('cores-true', 'cores', True), ('cores-float', 'cores', 1.5),
                                                  ('memory-2**63', 'memory', 2**63), ('timeout-text', 'timeout', 'x'),
                                                  ('timeout-1e20', 'timeout', 1e20)]
"""Resource tags a JSON record may not carry, by test id."""


@mark.integration
@mark.parametrize('tag, value', [pytest.param(tag, value, id=case) for case, tag, value in JSON_REFUSED])
@mark.parametrize('db_site', ['sqlite'], indirect=True)
def test_json_tag_refused_whole(db_site: Tuple[Path, Engine], tag: str, value: Any) -> None:
    """A `--from-json` record with a resource tag out of range, or not of its kind, inserts nothing and exits 5."""
    site, engine = db_site
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', tag: value}])
    rc, _, stderr = submit('-b', '1', '-w', '1', '--from-json', str(path), cwd=site)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    lines = [line for line in stderr.splitlines() if f'task.{tag}: expected ' in line]
    assert len(lines) == 1 and 'record 1' in lines[0], stderr[-4000:]
    assert count(engine, 'task') == 0


@mark.integration
@mark.parametrize('db_site', ['sqlite'], indirect=True)
def test_tags_in_range_stored(db_site: Tuple[Path, Engine]) -> None:
    """Resource tags inside their ranges are stored as today, from a line and from a JSON record."""
    site, engine = db_site
    path = create_taskfile(site, ['echo a # HYPERSHELL: cores:32767 memory:16G timeout:1h',
                                  'echo b # HYPERSHELL: cores:0 memory:0 timeout:0'])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    assert sorted(rows(engine, 'SELECT args, cores, memory, timeout FROM task')) == [
        ('echo a', 32767, 16 * 1024**3, 3600), ('echo b', 0, 0, 0)]


def load(source: List[str]) -> List[str]:
    """Run a Loader over the stream `source` to its end; the command of every task it queued."""
    queue: Queue = Queue()
    Loader(source=iter(source), queue=queue).run()
    tasks = []
    while not queue.empty():
        task = queue.get()
        if task is not None:
            tasks.append(task.args)
    return tasks


@mark.unit
def test_stream_tag_line_refused(caplog: pytest.LogCaptureFixture) -> None:
    """A stream refuses only the line whose resource tag is out of range, with an ERROR naming it and its line."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    assert load(['echo a', 'echo b # HYPERSHELL: cores:100000', 'echo c']) == ['echo a', 'echo c']
    errors = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1 and 'line 2' in errors[0] and '100000' in errors[0], errors


@mark.integration
def test_largest_values_stored(db_site: Tuple[Path, Engine]) -> None:
    """The largest cores, memory, timeout and attempt values are stored and read back (PostgreSQL's column types)."""
    site, engine = db_site
    rc, _, stderr = submit('-c', '32767', '-m', '9223372036854775807', '-W', '2147483647', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert rows(engine, 'SELECT cores, memory, timeout FROM task') == [(32767, 2**63 - 1, 2**31 - 1)]
    set_task(engine, 'echo a', attempt=32767)
    assert rows(engine, 'SELECT attempt FROM task') == [(32767, )]


@mark.integration
def test_tags_advised_once_per_submission(db_site: Tuple[Path, Engine]) -> None:
    """Tags past an advisory limit are taken and run as today, with one summary for the submission."""
    site, engine = db_site
    path = create_taskfile(site, ['echo a', 'echo b # HYPERSHELL: cores:1025', 'echo c # HYPERSHELL: cores:2000',
                                  'echo d # HYPERSHELL: cores:1024', 'echo e # HYPERSHELL: cores:5000'])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    assert count(engine, 'task') == 5
    advice = [line for line in stderr.splitlines() if 'WARNING' in line and 'advised' in line]
    assert len(advice) == 1, stderr[-4000:]
    assert 'task.cores' in advice[0] and '3 tasks' in advice[0] and 'line 2' in advice[0], advice[0]
    assert '1,024' in advice[0], advice[0]


@mark.unit
def test_tag_docs_give_ranges() -> None:
    """The task-input page gives each resource tag's range and advisory limit, and how a tag out of range is refused."""
    from hypershell.core.config_schema import SETTINGS
    from tests.test_bounded_docs import DOCS, flat
    text = flat((DOCS / '_include' / 'submit_desc.rst').read_text())
    for tag in ('cores', 'memory', 'timeout'):
        assert SETTINGS[f'task.{tag}'].text in text, tag
    assert SETTINGS['task.cores'].advice_text in text
    assert '``cores:4.0``' in text and '``timeout:90.5`` is 91 seconds' in text
