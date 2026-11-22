# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A `#` in a command is part of the command; a line that begins with `#` is a comment.

The `# HYPERSHELL:` marker still sets tags, after a command or alone on a line, and is found
anywhere on a line, in quotes too. Every other `#` after a line's first non-blank character is
stored and run as written, for file, stream and argument input to `hs submit`, `hs cluster` and
`hs server`, with a database and without one, and under `--template`.
"""


# Type annotations
from __future__ import annotations
from typing import List, Tuple
from pathlib import Path

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data.model import Task
from tests import create_taskfile
from tests.input_checks import (bounded, db_site, sqlite_engine, stored_args, stored_groups, stored_tags,  # noqa: F401
                                count, submit, ADDRESS)
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_cluster_exit import run_cli, cluster_command
from tests.test_env_config import start_server, stop_server, run_bounded, child_env, DIGIT_KEY


HASH_LINES: List[str] = [
    "echo 'issue #42'",
    "curl 'http://h/p#frag'",
    'echo $#',
    'echo a#b',
    "awk '{print $1}' # trailing",
]
"""Every line of the reported problem, which earlier releases cut at the first `#`."""

RUN_LINES: List[str] = ["echo 'issue #42'", 'echo a#b', 'echo kept # trailing note']
"""Lines whose output shows whether the command ran as written."""

RUN_OUTPUT: List[str] = ['a#b', 'issue #42', 'kept']
"""Sorted output of `RUN_LINES` run as written (the shell ignores the trailing note)."""


# --- The parser ---------------------------------------------------------------------------------

@mark.unit
@mark.parametrize('line', HASH_LINES)
def test_split_argline_keeps_hash(line: str) -> None:
    """A `#` after the first non-blank character stays in the command."""
    assert Task.split_argline(line) == (line, {})
    assert Task.split_argline(f'  {line}  ') == (line, {})


@mark.unit
@mark.parametrize('line', ['# a comment', '#comment', '   # indented comment', '#', '## HYPERSHELL: x:1',
                           '# c # HYPERSHELL: x:1', '#!/bin/sh'])
def test_split_argline_comment_line(line: str) -> None:
    """A line whose first non-blank character is `#`, and which does not start with the marker, is a comment."""
    assert Task.split_argline(line) == ('', {})


@mark.unit
def test_split_argline_marker_beside_hash() -> None:
    """The marker still sets tags after a command that holds a `#`, and on a tag-only line."""
    assert Task.split_argline('echo a#b # HYPERSHELL: x:1') == ('echo a#b', {'x': 1})
    assert Task.split_argline("echo 'issue #42' #HYPERSHELL: y:2 z") == ("echo 'issue #42'", {'y': 2, 'z': ''})
    assert Task.split_argline('# HYPERSHELL: x:1') == ('', {'x': 1})
    assert Task.split_argline('#HYPERSHELL x:1') == ('', {'x': 1})


@mark.unit
def test_split_argline_quote_escape() -> None:
    """Shell quoting that breaks the marker text keeps it out of the parser: `echo '#'' HYPERSHELL:'`."""
    line = "echo '#'' HYPERSHELL:'"
    assert Task.split_argline(line) == (line, {})


@mark.unit
def test_split_argline_quoted_marker_still_parsed() -> None:
    """The marker is found in quotes too, as earlier releases did: its tail is parsed (and refused here)."""
    with pytest.raises(RuntimeError):
        Task.split_argline("echo '# HYPERSHELL: group:3'")


# --- hs submit -----------------------------------------------------------------------------------

@mark.integration
def test_submit_file_keeps_hash(db_site: Tuple[Path, Engine]) -> None:
    """A task file's commands are stored as written; its comment line is not a task."""
    site, engine = db_site
    path = create_taskfile(site, ['# a comment line', *HASH_LINES])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr
    assert f'Found {len(HASH_LINES)} tasks' in stderr
    assert stored_args(engine) == HASH_LINES


@mark.integration
def test_submit_args_keep_hash(db_site: Tuple[Path, Engine]) -> None:
    """A command given as arguments keeps its `#`."""
    site, engine = db_site
    rc, _, stderr = submit('--', 'echo', 'direct #7', cwd=site)
    assert rc == 0, stderr
    assert stored_args(engine) == ['echo "direct #7"']


@mark.integration
def test_submit_stdin_keeps_hash(db_site: Tuple[Path, Engine]) -> None:
    """Commands read from standard input keep their `#`."""
    site, engine = db_site
    rc, _, stderr = submit('-f', '-', stdin='\n'.join(['# a comment line', *HASH_LINES]) + '\n', cwd=site)
    assert rc == 0, stderr
    assert stored_args(engine) == HASH_LINES


@mark.integration
def test_submit_template_keeps_hash(db_site: Tuple[Path, Engine]) -> None:
    """Under `--template`, a `#` in the line and a `#` in the template both stay in the command."""
    site, engine = db_site
    path = create_taskfile(site, ['a#b', "'issue #42'"])
    rc, _, stderr = submit('--template', 'echo {} #tail', str(path), cwd=site)
    assert rc == 0, stderr
    assert stored_args(engine) == ['echo a#b #tail', "echo 'issue #42' #tail"]


@mark.integration
def test_template_comment_line_is_not_a_task(db_site: Tuple[Path, Engine]) -> None:
    """Under `--template`, comment, blank and tag-only lines are not tasks, and the tag-only line sets the group."""
    site, engine = db_site
    path = create_taskfile(site, ['a', '# a comment', '', '# HYPERSHELL: group:2', 'b'])
    rc, _, stderr = submit('--template', 'echo {}', str(path), cwd=site)
    assert rc == 0, stderr
    assert 'Found 2 tasks' in stderr
    assert stored_args(engine) == ['echo a', 'echo b']
    assert stored_groups(engine)['echo b'] == 2


@mark.integration
def test_submit_comment_and_marker_count(db_site: Tuple[Path, Engine]) -> None:
    """Comment lines and the marker keep their meaning: the count, the commands and the tags are as before."""
    site, engine = db_site
    path = create_taskfile(site, ['# comment', 'echo a # HYPERSHELL: x:1', '# HYPERSHELL: y:2', '', 'echo b'])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 0, stderr
    assert 'Found 2 tasks' in stderr
    assert stored_args(engine) == ['echo a', 'echo b']
    tags = stored_tags(engine)
    assert tags['echo a'] == {'x': 1}
    assert tags['echo b'] == {'y': 2}


@mark.integration
def test_quoted_marker_still_refused(temp_site: Path) -> None:
    """The marker in quotes is still parsed, so a line holding it is refused whole, as before (exit 5)."""
    site = Path(str(temp_site))
    path = create_taskfile(site, ['echo a', "echo '# HYPERSHELL: group:3'"])
    rc, _, stderr = submit(str(path), cwd=site)
    assert rc == 5, stderr
    assert count(sqlite_engine(), 'task') == 0


# --- hs cluster ----------------------------------------------------------------------------------

def cluster_output(site: Path) -> List[str]:
    """Sorted lines the cluster's tasks printed."""
    return sorted((site / 'out.txt').read_text().splitlines())


@mark.integration
def test_cluster_file_runs_hash_as_written(temp_site: Path) -> None:
    """`hs cluster FILE` runs each command with its `#`."""
    site = Path(str(temp_site))
    create_taskfile(site, RUN_LINES, filename='tasks.in')
    status, _, log = run_cli(site, f'{cluster_command("tasks.in", "-N1")} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert cluster_output(site) == RUN_OUTPUT


@mark.integration
def test_cluster_stream_runs_hash_as_written(temp_site: Path) -> None:
    """A stream into `hs cluster` runs each command with its `#`."""
    site = Path(str(temp_site))
    create_taskfile(site, RUN_LINES, filename='tasks.in')
    status, _, log = run_cli(site, f'cat tasks.in | {cluster_command("-N1")} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert cluster_output(site) == RUN_OUTPUT


@mark.integration
@mark.parametrize('mode', ['file', 'stream'])
def test_cluster_tag_only_line_sets_group(temp_site: Path, mode: str) -> None:
    """A tag-only line given to `hs cluster` sets the group of the lines after it."""
    site = Path(str(temp_site))
    create_taskfile(site, ['echo a', '# HYPERSHELL: group:2', 'echo b'], filename='tasks.in')
    command = cluster_command('tasks.in', '-N1') if mode == 'file' else f'cat tasks.in | {cluster_command("-N1")}'
    status, _, log = run_cli(site, f'{command} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert cluster_output(site) == ['a', 'b']
    assert stored_groups(sqlite_engine()) == {'echo a': 0, 'echo b': 2}


# --- hs submit --queue ---------------------------------------------------------------------------

@mark.integration
def test_queue_submit_keeps_hash(temp_site: Path) -> None:
    """Commands submitted with `--queue` to a server without a database run with their `#`."""
    site = Path(str(temp_site))
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY, HYPERSHELL_SERVER_QUEUESIZE='100')
    server = start_server(site, env, '--no-db', '--no-tls', '-H', ADDRESS)
    try:
        submitted = bounded(['hs', 'submit', '--queue', '--no-tls', '-H', ADDRESS, '-w', '1'],
                            stdin='\n'.join(RUN_LINES) + '\n', cwd=site, env=env)
        status, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1', '-T', '5', '--no-tls',
                                                    '-H', ADDRESS], env)
    finally:
        server_status = stop_server(server)
    assert submitted[0] == 0, submitted[2]
    assert status == 0, stderr
    assert server_status == 0, (site / 'server.log').read_text()[-4000:]
    assert sorted(stdout.splitlines()) == RUN_OUTPUT
