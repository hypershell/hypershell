# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A task group is an integer, wherever it is given; input read in full is refused whole otherwise.

A tag value is an integer when `-g/--group` would accept the same text (`7`, `-3`, `+5`, `07`), and a
JSON value when it is a JSON integer, within PostgreSQL's 32-bit `INTEGER` range on every database.
A task file, a JSON source, arguments and `hs submit`'s standard input are refused whole with exit
status 5 and nothing inserted, naming the value and its line or record. `-t group:x` and an
out-of-range `-g` exit 2 before any input is read.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Tuple
from pathlib import Path

# Standard libs
import os
import json

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data.model import Task
from tests import create_taskfile, create_json_taskfile
from tests.input_checks import bounded, db_site, sqlite_engine, stored_groups, count, rows, submit, ADDRESS  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_cluster_exit import HS


REFUSED_TEXT: List[Tuple[str, str]] = [
    ('x', 'x'),
    ('empty', ''),
    ('float', '1.5'),
    ('exp', '1e3'),
    ('hex', '0x10'),
    ('true', 'true'),
    ('null', 'null'),
    ('nan', 'nan'),
    ('inf', 'inf'),
    ('huge', '99999999999999999999'),
]
"""Group tag text that is not an integer, or is out of range, by test id."""

ACCEPTED_TEXT: List[Tuple[str, str, int]] = [
    ('plain', '3', 3),
    ('zero-padded', '07', 7),
    ('plus', '+5', 5),
    ('minus', '-3', -3),
]
"""Group tag text that `-g` accepts, and the group it gives, by test id."""


# --- The rule ------------------------------------------------------------------------------------

@mark.unit
@mark.parametrize('value', [0, 7, -3, 2**31 - 1, -2**31])
def test_ensure_group_accepts_integer(value: int) -> None:
    """An integer in the 32-bit range is a group."""
    from hypershell.data.model import ensure_group
    assert ensure_group(value) == value


@mark.unit
@mark.parametrize('value', ['7', 'x', '', 1.5, 1000.0, float('nan'), float('inf'), True, False, None,
                            2**31, -2**31 - 1, 10**20])
def test_ensure_group_refuses(value: Any) -> None:
    """Anything else is refused: text, a float (integral or not), a boolean, null, and an integer out of range."""
    from hypershell.data.model import ensure_group, TaskGroupError
    with pytest.raises(TaskGroupError):
        ensure_group(value)


@mark.unit
@mark.parametrize('text, value', [('7', 7), ('07', 7), ('+5', 5), ('-3', -3), (' 7 ', 7), ('2147483647', 2**31 - 1)])
def test_parse_group_accepts(text: str, value: int) -> None:
    """The `-g` rule takes what `int()` takes, within range."""
    from hypershell.data.model import parse_group
    assert parse_group(text) == value


@mark.unit
@mark.parametrize('text', ['x', '', '1.5', '1e3', '0x10', 'true', 'nan', '2147483648', '3000000000'])
def test_parse_group_refuses(text: str) -> None:
    """The `-g` rule refuses anything `int()` refuses, and an integer out of range."""
    from hypershell.data.model import parse_group, TaskGroupError
    with pytest.raises(TaskGroupError):
        parse_group(text)


@mark.unit
@mark.parametrize('name, text', REFUSED_TEXT, ids=[name for name, _ in REFUSED_TEXT])
def test_split_argline_refuses_group(name: str, text: str) -> None:
    """An inline group that is not an integer is refused by the parser, naming the value as written."""
    from hypershell.data.model import TaskGroupError
    for line in (f'echo a # HYPERSHELL: group:{text}', f'# HYPERSHELL: group:{text}'):
        with pytest.raises(TaskGroupError) as caught:
            Task.split_argline(line)
        assert caught.value.value == text
        assert f'"{text}"' in str(caught.value)


@mark.unit
@mark.parametrize('name, text, value', ACCEPTED_TEXT, ids=[name for name, _, _ in ACCEPTED_TEXT])
def test_split_argline_accepts_group(name: str, text: str, value: int) -> None:
    """An inline group that `-g` would accept is taken as that integer."""
    assert Task.split_argline(f'echo a # HYPERSHELL: group:{text}') == ('echo a', {'group': value})


@mark.unit
def test_new_refuses_group_from_tags_only() -> None:
    """`Task.new` refuses a group from its tags; its `group` argument (retries, queue mode) is not checked."""
    from hypershell.data.model import TaskGroupError
    with pytest.raises(TaskGroupError):
        Task.new(args='echo a', tag={'group': 'x'})
    with pytest.raises(TaskGroupError):
        Task.new(args='echo a', tag={'group': True})
    assert Task.new(args='echo a', tag={'group': 4}).group == 4
    assert Task.new(args='echo a', group='x').group == 'x'
    assert Task.new(args='echo a', group=None).group is None


@mark.unit
def test_group_error_names_origin() -> None:
    """The error names the value as written and, once placed, where it came from."""
    from hypershell.data.model import TaskGroupError
    error = TaskGroupError('1e3').at('tasks.in, line 7')
    assert isinstance(error, RuntimeError)
    assert error.value == '1e3'
    assert '"1e3"' in str(error) and 'tasks.in, line 7' in str(error)


# --- hs submit, read in full -----------------------------------------------------------------------

def initdb(site: Path) -> None:
    """Initialize the site's database, so a refusal before the database is opened can be checked."""
    rc, _, stderr = bounded(['hs', 'initdb', '--yes'], cwd=site)
    assert rc == 0, stderr

def assert_refused(stderr: str, value: str, origin: str) -> None:
    """The error names the value as written and where it came from."""
    lines = [line for line in stderr.splitlines() if 'must be an integer' in line]
    assert lines, stderr[-4000:]
    assert any(f'"{value}"' in line and origin in line for line in lines), stderr[-4000:]


@mark.integration
@mark.parametrize('name, text', [*REFUSED_TEXT, ('tagline', 'x')], ids=[name for name, _ in REFUSED_TEXT] + ['tagline'])
def test_submit_file_refuses_group(db_site: Tuple[Path, Engine], name: str, text: str) -> None:
    """A task file holding a group that is not an integer inserts nothing and exits 5, naming value and line."""
    site, engine = db_site
    marked = f'# HYPERSHELL: group:{text}' if name == 'tagline' else f'echo b # HYPERSHELL: group:{text}'
    path = create_taskfile(site, ['echo a', marked, 'echo c'])
    rc, _, stderr = submit('-b', '1', '-w', '1', str(path), cwd=site)
    assert rc == 5, stderr[-4000:]
    assert_refused(stderr, text, 'line 2')
    assert count(engine, 'task') == 0
    assert count(engine, 'source') == 0


@mark.integration
@mark.parametrize('name, text, value', ACCEPTED_TEXT, ids=[name for name, _, _ in ACCEPTED_TEXT])
def test_submit_file_accepts_group(db_site: Tuple[Path, Engine], name: str, text: str, value: int) -> None:
    """A group that `-g` would accept is stored as that integer, from a task line and from a tag-only line."""
    site, engine = db_site
    path = create_taskfile(site, [f'echo a # HYPERSHELL: group:{text}', f'# HYPERSHELL: group:{text}', 'echo b'])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(engine) == {'echo a': value, 'echo b': value}


@mark.integration
def test_submit_stdin_refuses_group_whole(db_site: Tuple[Path, Engine]) -> None:
    """Standard input to `hs submit` with a database is read in full and refused whole: nothing inserted."""
    site, engine = db_site
    rc, _, stderr = submit('-b', '1', '-w', '1', stdin='echo a\necho b # HYPERSHELL: group:x\necho c\n', cwd=site)
    assert rc == 5, stderr[-4000:]
    assert_refused(stderr, 'x', 'line 2')
    assert count(engine, 'task') == 0
    assert count(engine, 'source') == 0


@mark.integration
def test_submit_args_refuse_group(db_site: Tuple[Path, Engine]) -> None:
    """A command given as arguments with a marker naming a group that is not an integer inserts nothing (exit 5)."""
    site, engine = db_site
    rc, _, stderr = submit('--', 'echo', 'a', '#', 'HYPERSHELL:', 'group:x', cwd=site)
    assert rc == 5, stderr[-4000:]
    assert 'must be an integer' in stderr and '"x"' in stderr
    assert count(engine, 'task') == 0


JSON_REFUSED = [('string', '7'), ('float', 1.5), ('bool', True), ('null', None)]
"""JSON group values that are not JSON integers, by test id."""


@mark.integration
@mark.parametrize('name, value', JSON_REFUSED, ids=[name for name, _ in JSON_REFUSED])
def test_submit_json_refuses_group(temp_site: Path, name: str, value: Any) -> None:
    """A `--from-json` record whose group is not a JSON integer inserts nothing and exits 5, naming the record."""
    site = Path(str(temp_site))
    initdb(site)
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', 'group': value}])
    rc, _, stderr = submit('-b', '1', '-w', '1', '--from-json', str(path), cwd=site)
    assert rc == 5, stderr[-4000:]
    lines = [line for line in stderr.splitlines() if 'must be an integer' in line]
    assert any('record 1' in line for line in lines), stderr[-4000:]
    engine = sqlite_engine()
    assert count(engine, 'task') == 0
    assert count(engine, 'source') == 0


@mark.integration
def test_submit_json_accepts_integer_group(temp_site: Path) -> None:
    """A `--from-json` record whose group is a JSON integer is stored in that group."""
    site = Path(str(temp_site))
    path = create_json_taskfile(site, [{'args': 'echo a', 'group': 4}, {'args': 'echo b', 'group': -2}])
    rc, _, stderr = submit('--from-json', str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(sqlite_engine()) == {'echo a': 4, 'echo b': -2}


# --- Options -------------------------------------------------------------------------------------

@mark.integration
@mark.parametrize('mode', ['database', 'queue'])
def test_submit_tag_option_refuses_group(temp_site: Path, mode: str) -> None:
    """`-t group:x` exits 2 before any input is read, with a database and with `--queue`."""
    site = Path(str(temp_site))
    initdb(site)
    options = ['--queue', '--no-tls', '-H', ADDRESS] if mode == 'queue' else []
    rc, _, stderr = submit(*options, '-t', 'group:x', '--', 'echo', 'a', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert '"x"' in stderr
    if mode == 'database':
        assert count(sqlite_engine(), 'task') == 0


@mark.integration
@mark.parametrize('text, value', [('07', 7), ('-3', -3)])
def test_submit_tag_option_accepts_group(temp_site: Path, text: str, value: int) -> None:
    """`-t group:N` with text `-g` would accept sets that group."""
    site = Path(str(temp_site))
    rc, _, stderr = submit('-t', f'group:{text}', '--', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(sqlite_engine()) == {'echo a': value}


@mark.integration
def test_submit_group_option_out_of_range(temp_site: Path) -> None:
    """`-g` outside PostgreSQL's 32-bit range exits 2, on SQLite too, and inserts nothing."""
    site = Path(str(temp_site))
    initdb(site)
    rc, _, stderr = submit('-g', '3000000000', '--', 'echo', 'a', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert count(sqlite_engine(), 'task') == 0


# --- hs cluster and hs server, read in full ------------------------------------------------------

BAD_FILE: List[str] = ['echo a', 'echo b # HYPERSHELL: group:x', 'echo c']
"""A task file whose second line holds a group that is not an integer."""


@mark.integration
@mark.parametrize('mode', ['database', 'no-db'])
def test_cluster_file_refuses_group(temp_site: Path, mode: str) -> None:
    """`hs cluster FILE` with a bad group runs and inserts nothing and exits 5, with a database and without one."""
    site = Path(str(temp_site))
    path = create_taskfile(site, BAD_FILE)
    options = ['--no-db'] if mode == 'no-db' else []
    rc, stdout, stderr = bounded([HS, 'cluster', str(path), '-N1', '-b', '1', '--no-tls', '-H', ADDRESS, *options],
                                 cwd=site, timeout=60)
    assert rc == 5, stderr[-4000:]
    assert_refused(stderr, 'x', 'line 2')
    assert stdout.split() == []
    if mode == 'database':
        engine = sqlite_engine()
        assert count(engine, 'task') == 0
        assert count(engine, 'source') == 0


@mark.integration
def test_server_file_refuses_group(temp_site: Path) -> None:
    """`hs server FILE` with a bad group inserts nothing and exits 5."""
    site = Path(str(temp_site))
    path = create_taskfile(site, BAD_FILE)
    rc, _, stderr = bounded([HS, 'server', str(path), '-b', '1', '--no-tls', '-H', ADDRESS], cwd=site,
                            env={**os.environ, 'HYPERSHELL_SERVER_AUTH': 'Xk3v9QpL2mN7bR4tW8yZ'})
    assert rc == 5, stderr[-4000:]
    assert_refused(stderr, 'x', 'line 2')
    assert count(sqlite_engine(), 'task') == 0


# --- Retries -------------------------------------------------------------------------------------

@mark.integration
@mark.parametrize('text', ['x', '3'], ids=['text', 'integer'])
def test_cluster_json_retry_keeps_command_and_group(temp_site: Path, text: str) -> None:
    """A retry keeps its failed task's command as stored and its group; the command's marker text is not re-read.

    A `--from-json` record's args are stored as written, `# HYPERSHELL:` text included. Its retry is
    the same command in the same group, whatever group that text names, and the run goes on.
    """
    site = Path(str(temp_site))
    command = f'false # HYPERSHELL: group:{text}'
    path = create_json_taskfile(site, [{'args': command}, {'args': 'echo fine'}])
    rc, stdout, stderr = bounded([HS, 'cluster', '--from-json', str(path), '--max-retries', '1', '-N1', '-b', '1',
                                  '--no-tls', '-H', ADDRESS], cwd=site, timeout=60)
    assert rc == 0, stderr[-4000:]
    assert 'fine' in stdout.split()
    engine = sqlite_engine()
    try:
        tasks = rows(engine, 'SELECT id, args, "group", attempt, previous_id, exit_status FROM task '
                             'WHERE args != :fine ORDER BY attempt', fine='echo fine')
    finally:
        engine.dispose()
    assert [(args, attempt) for _, args, _, attempt, _, _ in tasks] == [(command, 1), (command, 2)]
    (first_id, _, first_group, _, _, first_status), (_, _, retry_group, _, previous_id, retry_status) = tasks
    assert first_group == 0 and retry_group == first_group
    assert previous_id == first_id
    assert first_status == retry_status == 1


@mark.integration
def test_cluster_json_retry_keeps_tags(temp_site: Path) -> None:
    """A retry copies its failed task's tags as stored: a `--from-json` tag outside the inline tag grammar is kept."""
    site = Path(str(temp_site))
    path = create_json_taskfile(site, [{'args': 'false', 'input': 'data/a b.txt'}, {'args': 'echo fine'}])
    rc, stdout, stderr = bounded([HS, 'cluster', '--from-json', str(path), '--max-retries', '1', '-N1', '-b', '1',
                                  '--no-tls', '-H', ADDRESS], cwd=site, timeout=60)
    assert rc == 0, stderr[-4000:]
    assert 'fine' in stdout.split()
    engine = sqlite_engine()
    try:
        tasks = rows(engine, 'SELECT attempt, tag FROM task WHERE args = :args ORDER BY attempt', args='false')
    finally:
        engine.dispose()
    assert [(attempt, json.loads(tag) if isinstance(tag, str) else tag) for attempt, tag in tasks] == [
        (1, {'input': 'data/a b.txt'}), (2, {'input': 'data/a b.txt'})]
