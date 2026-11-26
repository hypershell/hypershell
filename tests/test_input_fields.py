# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Task input sets the declarable fields with `KEY=VALUE` or `KEY:VALUE`, and refuses what `hs update` refuses.

The declarable fields are a task's group, zone, cores, memory and timeout. An inline `# HYPERSHELL:`
directive (on a task line or a tag-only line, in a task file, on standard input or among the
arguments), `-t` to `hs submit` and `-t` to `hs run` read each with either separator, by the kind and
range of its option (`cores:4.0` and `cores=4.0` are 4, by the maintainer's ruling). The most specific
source wins: the line's directive, then a tag-only line, then `-t`, then the option. A `KEY=VALUE`
key one edit from a column is refused, naming the column and the `KEY:VALUE` way to set a tag by that
name; a `KEY=VALUE` key that is a column but not a declarable field is refused as not set from task
input; with `:` both are tags. A refusal takes the form a refused group takes: input read in full
exits 5 naming its line (or the arguments) with nothing inserted, a stream refuses that line alone,
and `-t` exits 2 before any input is read. Every tag is checked by the tag rules, a tag with no value
ending nothing. `hs update` reads as before. Zones are in `tests/test_input_zones.py`.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Final, List, Optional, Tuple

# Standard libs
import os
import time
import logging
from pathlib import Path
from queue import Queue
from subprocess import Popen, PIPE, DEVNULL

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.submit import Loader
from tests import create_taskfile
from tests.input_checks import bounded, db_site, sqlite_engine, stored_tags, count, rows, submit, kill_group, BOUND  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.update_checks import hs as update, submit_three, task_row


# --- Helpers -------------------------------------------------------------------------------------

def run_until(argv: List[str], done: Callable[[], bool], cwd: Path, stdin: Optional[str] = None,
              env: Optional[Dict[str, str]] = None, bound: float = BOUND) -> Tuple[Optional[int], str, str]:
    """Run `argv` in its own session until it exits or `done()` holds; its exit status, stdout and stderr.

    The exit status is None when the command was still running once `done()` held; it is then
    stopped with everything in its session. A command still running at `bound` seconds with
    `done()` false is stopped and the test fails.
    """
    stamp = time.monotonic_ns()
    out_path, err_path = Path(cwd) / f'until-{stamp}.out', Path(cwd) / f'until-{stamp}.err'
    with open(out_path, mode='w') as out, open(err_path, mode='w') as err:
        proc = Popen(argv, cwd=cwd, env=env if env is not None else os.environ, text=True,
                     stdin=PIPE if stdin is not None else DEVNULL, stdout=out, stderr=err, start_new_session=True)
    try:
        if stdin is not None:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass  # The command exited before reading its input
        deadline = time.monotonic() + bound
        while proc.poll() is None and not done():
            if time.monotonic() > deadline:
                kill_group(proc)
                proc.wait()
                pytest.fail(f'{" ".join(argv)} still running after {bound} s:\n{err_path.read_text()[-4000:]}')
            time.sleep(0.2)
        status = proc.poll()
    finally:
        kill_group(proc)
        proc.wait()
    return status, out_path.read_text(), err_path.read_text()


def has_tasks(engine: Engine, n: int = 1) -> Callable[[], bool]:
    """A check that the database holds at least `n` tasks (false while it cannot be read)."""
    def check() -> bool:
        try:
            return count(engine, 'task') >= n
        except OperationalError:
            return False
    return check


def initdb(site: Path) -> None:
    """Initialize the site's database, so what a refused command left behind can be counted."""
    rc, _, stderr = bounded(['hs', 'initdb', '--yes'], cwd=site)
    assert rc == 0, stderr


def columns(engine: Engine) -> Dict[str, Tuple[Any, ...]]:
    """Stored group, cores, memory and timeout of every task, by its command."""
    return {args: tuple(values) for args, *values in rows(engine, 'SELECT args, "group", cores, memory, timeout FROM task')}


def load(source: List[str]) -> List[str]:
    """Run a Loader over the stream `source` to its end; the command of every task it queued.

    A stream that ends on an error, rather than refusing a line, fails the test.
    """
    queue: Queue = Queue()
    try:
        Loader(source=iter(source), queue=queue).run()
    except Exception as error:  # noqa: BLE001 (any error that ends the stream is the defect)
        pytest.fail(f'The stream ended on {type(error).__name__}: {error}')
    tasks = []
    while not queue.empty():
        task = queue.get()
        if task is not None:
            tasks.append(task.args)
    return tasks


def refusals(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Messages of the ERROR records that refused a line."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.ERROR and 'Refused line' in record.getMessage()]


def refusal_lines(stderr: str, words: List[str]) -> List[str]:
    """Lines of `stderr` holding every one of `words`."""
    return [line for line in stderr.splitlines() if all(word in line for word in words)]


# --- Both separators set every declarable field --------------------------------------------------

FIELDS: Final[Dict[str, str]] = {'group': '2', 'cores': '4.0', 'memory': '1G', 'timeout': '1:30:00'}
"""A value of each declarable field but the zone, as written; `cores` with a zero fraction (the ruling)."""

STORED: Final[Tuple[int, int, int, int]] = (2, 4, 1024**3, 5400)
"""The group, cores, memory and timeout those values store."""

SEPARATORS: Final[List[Any]] = [pytest.param(':', id='colon'), pytest.param('=', id='equals')]
"""Both separators, by test id."""


def field_tokens(sep: str) -> List[str]:
    """Every field of `FIELDS` and the tag `color`, each written with `sep`."""
    return [f'{key}{sep}{value}' for key, value in FIELDS.items()] + [f'color{sep}red']


INPUT_FORMS: Final[List[str]] = ['line', 'tagline', 'stdin', 'args', 'tag', 'run']
"""Task input: a task line's directive, a tag-only line, standard input, arguments, `hs submit -t`, `hs run -t`."""


def submit_form(site: Path, engine: Engine, form: str, tokens: List[str]) -> Tuple[Optional[int], str]:
    """Submit the task `echo a` with `tokens` in `form`; the exit status (None for `hs run`, left waiting) and stderr."""
    directive = '# HYPERSHELL: ' + ' '.join(tokens)
    if form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, [f'echo a {directive}'])), cwd=site)
    elif form == 'tagline':
        rc, _, stderr = submit(str(create_taskfile(site, [directive, 'echo a'])), cwd=site)
    elif form == 'stdin':
        rc, _, stderr = submit('-', stdin=f'echo a {directive}\n', cwd=site)
    elif form == 'args':
        rc, _, stderr = submit('--', 'echo', 'a', '#', 'HYPERSHELL:', *tokens, cwd=site)
    elif form == 'tag':
        rc, _, stderr = submit('-t', *tokens, '--', 'echo', 'a', cwd=site)
    else:
        rc, _, stderr = run_until(['hs', 'run', '-n', '1', '-t', *tokens, '--', 'echo', 'a'],
                                  has_tasks(engine), cwd=site)
    return rc, stderr


@mark.integration
@mark.parametrize('sep', SEPARATORS)
@mark.parametrize('form', INPUT_FORMS)
def test_fields_set_by_either_separator(db_site: Tuple[Path, Engine], form: str, sep: str) -> None:
    """Group, cores, memory and timeout are set from every form of task input with `=` as with `:`; other keys are tags."""
    site, engine = db_site
    rc, stderr = submit_form(site, engine, form, field_tokens(sep))
    assert rc in ((0, None) if form == 'run' else (0, )), stderr[-4000:]
    assert columns(engine) == {'echo a': STORED}, stderr[-4000:]
    assert stored_tags(engine) == {'echo a': {'color': 'red'}}


@mark.integration
@mark.parametrize('sep', SEPARATORS)
def test_fields_precedence(db_site: Tuple[Path, Engine], sep: str) -> None:
    """A line's directive wins over a tag-only line, which wins over `-t`, which wins over each field's option."""
    site, engine = db_site
    path = create_taskfile(site, [f'echo a # HYPERSHELL: group{sep}7 cores{sep}3 memory{sep}3K timeout{sep}30',
                                  'echo b',
                                  f'# HYPERSHELL: group{sep}8 cores{sep}4',
                                  'echo c'])
    rc, _, stderr = submit(str(path), '-g', '5', '-c', '1', '-m', '1K', '-W', '10',
                           '-t', f'group{sep}6', f'cores{sep}2', f'memory{sep}2K', f'timeout{sep}20', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert columns(engine) == {'echo a': (7, 3, 3072, 30), 'echo b': (6, 2, 2048, 20), 'echo c': (8, 4, 2048, 20)}


@mark.integration
def test_options_alone_set_fields(temp_site: Path) -> None:
    """With no field in its input, a task takes each field's option (guard)."""
    site = Path(str(temp_site))
    rc, _, stderr = submit('-g', '5', '-c', '1', '-m', '1K', '-W', '10', '--', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert columns(sqlite_engine()) == {'echo a': (5, 1, 1024, 10)}


# --- A near-miss with `=` is refused; with `:` it is a tag ---------------------------------------

NEAR: Final[List[Tuple[str, str]]] = [
    ('grop', 'group'),           # A deletion
    ('zones', 'zone'),           # An insertion
    ('Cores', 'cores'),          # Letter case only
    ('memroy', 'memory'),        # A swap of two adjacent characters
    ('timout', 'timeout'),       # A deletion
    ('exit_statux', 'exit_status'),  # A substitution, near a column not set from input
    ('tags', 'tag'),
]
"""Keys one edit from a column, ignoring case, and the column each is near."""


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
@mark.parametrize('key, column', NEAR, ids=[key for key, _ in NEAR])
def test_near_miss_refused(temp_site: Path, key: str, column: str, form: str) -> None:
    """`KEY=VALUE` one edit from a column is refused naming the column and the `KEY:VALUE` way out; nothing is stored."""
    site = Path(str(temp_site))
    initdb(site)
    words = [f'{key}: not a task column', f'did you mean {column}?', f'{key}:VALUE']
    if form == 'line':
        path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {key}=2', 'echo c'])
        rc, _, stderr = submit(str(path), cwd=site)
        assert rc == exit_status.runtime_error, stderr[-4000:]
        assert refusal_lines(stderr, [*words, 'line 2']), stderr[-4000:]
    else:
        rc, _, stderr = submit('-t', f'{key}=2', '--', 'echo', 'a', cwd=site)
        assert rc == exit_status.bad_argument, stderr[-4000:]
        assert refusal_lines(stderr, words), stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
def test_colon_key_is_tag(temp_site: Path, form: str) -> None:
    """`KEY:VALUE` is a tag when the key is near a column, or is a column not set from input; the columns are untouched (guard)."""
    site = Path(str(temp_site))
    tokens = ['grop:2', 'Cores:3', 'exit_status:0', 'attempt:4', 'exit_statux:5']
    if form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, ['echo a # HYPERSHELL: ' + ' '.join(tokens)])), cwd=site)
    else:
        rc, _, stderr = submit('-t', *tokens, '--', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    engine = sqlite_engine()
    assert stored_tags(engine) == {'echo a': {'grop': 2, 'Cores': 3, 'exit_status': 0, 'attempt': 4, 'exit_statux': 5}}
    assert rows(engine, 'SELECT "group", cores, exit_status, attempt FROM task') == [(0, None, None, 1)]


# --- A column not set from input, with `=`, is refused -------------------------------------------

COLUMNS: Final[List[Tuple[str, str]]] = [('exit_status', '0'), ('attempt', '2'), ('args', 'x'), ('client_host', 'h'),
                                         ('part', '1'), ('id', 'x')]
"""Task columns that task input does not set, and a value for each."""


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
@mark.parametrize('key, value', COLUMNS, ids=[key for key, _ in COLUMNS])
def test_column_not_set_from_input(temp_site: Path, key: str, value: str, form: str) -> None:
    """`KEY=VALUE` naming a column that is not a declarable field is refused as not set from task input."""
    site = Path(str(temp_site))
    initdb(site)
    words = [key, 'not set from task input', f'{key}:VALUE']
    if form == 'line':
        path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {key}={value}', 'echo c'])
        rc, _, stderr = submit(str(path), cwd=site)
        assert rc == exit_status.runtime_error, stderr[-4000:]
        assert refusal_lines(stderr, [*words, 'line 2']), stderr[-4000:]
    else:
        rc, _, stderr = submit('-t', f'{key}={value}', '--', 'echo', 'a', cwd=site)
        assert rc == exit_status.bad_argument, stderr[-4000:]
        assert refusal_lines(stderr, words), stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


# --- Every refusal takes the form of a refused group ---------------------------------------------

REFUSALS: Final[Dict[str, Tuple[List[str], List[str]]]] = {
    'near-miss': (['grop=2'], ['did you mean group?', 'grop:VALUE']),
    'column': (['exit_status=0'], ['not set from task input', 'exit_status:VALUE']),
    'group-kind': (['group=x'], ['must be an integer', '"x"']),
    'cores-kind': (['cores=x'], ['task.cores: expected', 'x']),
    'memory-range': (['memory=8388608TB'], ['task.memory: expected', '8388608TB']),
    'tag-rule': (['other=a/b'], ['Tag value must only contain', 'a/b']),
    'after-empty': (['empty', 'other:a/b'], ['Tag value must only contain', 'a/b']),
}
"""Refused task input, by test id: its tokens, and the words its refusal holds."""

REFUSAL_FORMS: Final[List[str]] = ['line', 'stdin', 'args', 'tag', 'run', 'stream']
"""Where the refused tokens are given: whole input (a file, standard input, arguments), `-t`, `hs run -t`, a stream."""


@mark.integration
@mark.parametrize('form', REFUSAL_FORMS)
@mark.parametrize('case', list(REFUSALS), ids=list(REFUSALS))
def test_refusal_forms(temp_site: Path, caplog: pytest.LogCaptureFixture, case: str, form: str) -> None:
    """Whole input exits 5 naming its line and inserting nothing; `-t` and `hs run -t` exit 2; a stream refuses one line."""
    site = Path(str(temp_site))
    tokens, words = REFUSALS[case]
    directive = '# HYPERSHELL: ' + ' '.join(tokens)
    if form == 'stream':
        caplog.set_level(logging.DEBUG, logger='hypershell')
        assert load(['echo a', f'echo b {directive}', 'echo c']) == ['echo a', 'echo c']
        messages = refusals(caplog)
        assert len(messages) == 1 and 'line 2' in messages[0], messages
        assert all(word in messages[0] for word in words), messages
        return
    initdb(site)
    engine = sqlite_engine()
    if form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, ['echo a', f'echo b {directive}', 'echo c'])), cwd=site)
        origin, status = 'line 2', exit_status.runtime_error
    elif form == 'stdin':
        rc, _, stderr = submit('-', stdin=f'echo a\necho b {directive}\necho c\n', cwd=site)
        origin, status = 'line 2', exit_status.runtime_error
    elif form == 'args':
        rc, _, stderr = submit('--', 'echo', 'b', '#', 'HYPERSHELL:', *tokens, cwd=site)
        origin, status = 'arguments', exit_status.runtime_error
    elif form == 'tag':
        rc, _, stderr = submit('-t', *tokens, '--', 'echo', 'b', cwd=site)
        origin, status = '', exit_status.bad_argument
    else:
        rc, _, stderr = run_until(['hs', 'run', '-n', '1', '-t', *tokens, '--', 'echo', 'b'],
                                  has_tasks(engine), cwd=site)
        origin, status = '', exit_status.bad_argument
    assert rc == status, stderr[-4000:]
    assert refusal_lines(stderr, [*words, origin]), stderr[-4000:]
    assert count(engine, 'task') == 0


@mark.unit
def test_stream_refused_tagline_sets_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """On a stream, a tag-only line with `=` sets its fields; a refused one sets nothing."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    queue: Queue = Queue()
    lines = ['# HYPERSHELL: group=3 cores=2', 'echo a', '# HYPERSHELL: grop=4', 'echo b', '# HYPERSHELL: group=5', 'echo c']
    try:
        Loader(source=iter(lines), queue=queue).run()
    except Exception as error:  # noqa: BLE001 (any error that ends the stream is the defect)
        pytest.fail(f'The stream ended on {type(error).__name__}: {error}')
    tasks = []
    while not queue.empty():
        task = queue.get()
        if task is not None:
            tasks.append((task.args, task.group, task.cores))
    assert tasks == [('echo a', 3, 2), ('echo b', 3, 2), ('echo c', 5, 2)]
    messages = refusals(caplog)
    assert len(messages) == 1 and 'line 3' in messages[0] and 'did you mean group?' in messages[0], messages


# --- A field with no value is refused ------------------------------------------------------------

NO_VALUE: Final[List[Tuple[str, str]]] = [
    ('cores-bare', 'cores'), ('cores-colon', 'cores:'), ('cores-equals', 'cores='),
    ('cores-none', 'cores:none'), ('cores-none-equals', 'cores=none'),
    ('memory-bare', 'memory'), ('memory-none', 'memory:none'),
    ('timeout-colon', 'timeout:'), ('timeout-none', 'timeout:none'),
    ('group-bare', 'group'), ('group-equals', 'group='),
]
"""A declarable field written with no value, or `none`, by test id."""


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
@mark.parametrize('case, token', NO_VALUE, ids=[case for case, _ in NO_VALUE])
def test_field_without_value_refused(temp_site: Path, case: str, token: str, form: str) -> None:
    """A field with no value, or `none`, is refused (it does not unset the option); nothing is stored."""
    site = Path(str(temp_site))
    initdb(site)
    field = token.replace('=', ':').partition(':')[0]
    if form == 'line':
        path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {token}', 'echo c'])
        rc, _, stderr = submit(str(path), '-c', '1', '-m', '1K', '-W', '10', cwd=site)
        assert rc == exit_status.runtime_error, stderr[-4000:]
        assert refusal_lines(stderr, [field, 'line 2']), stderr[-4000:]
    else:
        rc, _, stderr = submit('-c', '1', '-m', '1K', '-W', '10', '-t', token, '--', 'echo', 'a', cwd=site)
        assert rc == exit_status.bad_argument, stderr[-4000:]
        assert field in stderr and 'Tag key' not in stderr, stderr[-4000:]  # Refused as a field, not as a tag key
    assert count(sqlite_engine(), 'task') == 0


# --- Every tag is checked, a tag with no value ending nothing ------------------------------------

NAKED_ORDERS: Final[Dict[str, List[str]]] = {
    'first': ['naked', 'other:a/b'],
    'between': ['a:1', 'naked', 'other:a/b', 'b:2'],
}
"""A tag with no value before a tag the tag rules refuse, by test id."""


@mark.integration
@mark.parametrize('form', ['tag', 'line', 'stdin', 'args'])
@mark.parametrize('order', list(NAKED_ORDERS), ids=list(NAKED_ORDERS))
def test_naked_tag_ends_nothing(temp_site: Path, order: str, form: str) -> None:
    """A tag after a tag with no value is checked: `-t naked other:a/b` exits 2, and inline input is refused (exit 5)."""
    site = Path(str(temp_site))
    initdb(site)
    tokens = NAKED_ORDERS[order]
    directive = '# HYPERSHELL: ' + ' '.join(tokens)
    if form == 'tag':
        rc, _, stderr = submit('-t', *tokens, '--', 'echo', 'a', cwd=site)
        status = exit_status.bad_argument
    elif form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, ['echo a', f'echo b {directive}'])), cwd=site)
        status = exit_status.runtime_error
    elif form == 'stdin':
        rc, _, stderr = submit('-', stdin=f'echo a\necho b {directive}\n', cwd=site)
        status = exit_status.runtime_error
    else:
        rc, _, stderr = submit('--', 'echo', 'b', '#', 'HYPERSHELL:', *tokens, cwd=site)
        status = exit_status.runtime_error
    assert rc == status, stderr[-4000:]
    assert 'Tag value must only contain' in stderr and 'a/b' in stderr, stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


@mark.integration
def test_naked_tag_last_stored(temp_site: Path) -> None:
    """A tag with no value is stored as one, after other tags too (guard)."""
    site = Path(str(temp_site))
    rc, _, stderr = submit(str(create_taskfile(site, ['echo a # HYPERSHELL: a:1 naked'])), '-t', 'b:2', 'bare', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_tags(sqlite_engine()) == {'echo a': {'a': 1, 'naked': '', 'b': 2, 'bare': ''}}


# --- hs update reads as before -------------------------------------------------------------------

@mark.integration
def test_update_colon_group_stays_tag(temp_site: Path) -> None:
    """`hs update group:5 exit_status=0` sets the tag `group` and the column `exit_status`, as before (guard).

    Task input reads `group:` as the field and refuses `exit_status=`; `hs update` does neither.
    """
    site = Path(str(temp_site))
    submit_three(site)
    rc, _, stderr = update(site, ['hs', 'update', 'group:5', 'exit_status=0', '-t', 'n:1', '-f'])
    assert rc == 0, stderr[-4000:]
    row = task_row(sqlite_engine(), 1)
    assert row['tag'] == {'n': 1, 'group': 5}
    assert row['group'] == 0 and row['exit_status'] == 0


# --- The documentation -----------------------------------------------------------------------------

DOCS: Final[Path] = Path(__file__).resolve().parent.parent / 'docs'
"""The documentation sources."""


def flat_text(path: Path) -> str:
    """The text of `path` with every run of white space made one space (an RST paragraph wraps anywhere)."""
    return ' '.join(path.read_text(encoding='utf-8').split())


@mark.unit
def test_docs_describe_both_separators() -> None:
    """The `hs submit` description gives both separators, the five declarable fields, the typo rule and the zone."""
    text = flat_text(DOCS / '_include' / 'submit_desc.rst')
    assert '``KEY:VALUE`` or ``KEY=VALUE``' in text
    assert '``group``, ``zone``, ``cores``, ``memory`` and ``timeout``' in text
    assert 'likely typo' in text and '``grop:2``' in text
    assert '``exit_status=0``' in text and '``exit_status:0`` is a tag' in text
    assert 'Zones require the database' in text
    assert '.. versionchanged:: 2.9.0 A directive and ``-t`` take ``KEY=VALUE``' in text


@mark.unit
@mark.parametrize('page', ['submit_help', 'task_run_help'])
def test_docs_tag_option_takes_fields(page: str) -> None:
    """The `-t` entry on the `hs submit` and `hs run` help pages names both separators and the declarable fields."""
    text = flat_text(DOCS / '_include' / f'{page}.rst')
    assert '``KEY:VALUE``, ``KEY=VALUE`` or a bare ``KEY``' in text
    assert '(``group``, ``zone``, ``cores``, ``memory``, ``timeout``)' in text


@mark.unit
@mark.parametrize('page', ['task_run', 'cluster', 'server'])
def test_docs_mark_the_change(page: str) -> None:
    """The `hs run`, `hs cluster` and `hs server` pages mark the change, with no pull request number yet."""
    text = flat_text(DOCS / 'cli' / f'{page}.rst')
    assert '.. versionchanged:: 2.9.0 ``-t`` and an inline directive take ``KEY=VALUE``' in text or \
           '.. versionchanged:: 2.9.0 An inline directive takes ``KEY=VALUE``' in text
