# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A stream refuses only the line whose group is not an integer.

Standard input, a pipe, a FIFO or a process substitution given to `hs cluster` or `hs server`, or
to `hs submit --queue`, is scheduled as it arrives, so it cannot be refused whole. The line at fault
is not inserted or run; an ERROR names its value and line number; every other line is handled as if
it were absent (a refused tag-only line sets nothing); the run's exit status is unchanged; and no
value hangs the run. Each stream here carries an accepted line: a stream with none, on a fresh
database, meets the empty-input wait that every empty stream meets.
"""


# Type annotations
from __future__ import annotations
from typing import Iterable, List, Tuple
from pathlib import Path

# Standard libs
import logging
from queue import Queue

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.submit import Loader
from tests import create_taskfile
from tests.input_checks import bounded, db_site, sqlite_engine, stored_args, stored_groups, ADDRESS  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_cluster_exit import run_cli, cluster_command
from tests.test_env_config import start_server, stop_server, run_bounded, child_env, DIGIT_KEY


# --- The Loader's policy -------------------------------------------------------------------------

def load(source: Iterable[str]) -> List[Tuple[str, int]]:
    """Run a Loader over `source` to its end; return the (command, group) of every task it queued."""
    queue: Queue = Queue()
    Loader(source=source, queue=queue).run()
    tasks = []
    while not queue.empty():
        task = queue.get()
        tasks.append((task.args, task.group))
    return tasks


def refusals(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Messages of the ERROR records that refused a line."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.ERROR and 'Refused line' in record.getMessage()]


@mark.unit
def test_loader_stream_refuses_one_line(caplog: pytest.LogCaptureFixture) -> None:
    """A stream (an iterator) refuses the bad line, names its value and line number, and queues the rest."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    lines = ['# a comment', '', 'echo a', 'echo b # HYPERSHELL: group:x', 'echo c']
    assert load(iter(lines)) == [('echo a', 0), ('echo c', 0)]
    messages = refusals(caplog)
    assert len(messages) == 1
    assert 'line 4' in messages[0] and '"x"' in messages[0]


@mark.unit
@mark.parametrize('text', ['nan', 'null', '', '1.5', '99999999999999999999'])
def test_loader_stream_refuses_each_value(caplog: pytest.LogCaptureFixture, text: str) -> None:
    """No refused value reaches a task, so none can stop the committer."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    assert load(iter(['echo a', f'echo b # HYPERSHELL: group:{text}'])) == [('echo a', 0)]
    assert len(refusals(caplog)) == 1


@mark.unit
def test_loader_stream_refused_tag_only_line_sets_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A refused tag-only line sets nothing: the lines after it keep the group they had."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    lines = ['# HYPERSHELL: group:3', 'echo a', '# HYPERSHELL: group:x', 'echo b', '# HYPERSHELL: group:4', 'echo c']
    assert load(iter(lines)) == [('echo a', 3), ('echo b', 3), ('echo c', 4)]
    messages = refusals(caplog)
    assert len(messages) == 1 and 'line 3' in messages[0]


@mark.unit
def test_loader_whole_input_raises() -> None:
    """Input read in full (a list) is refused before the Loader runs; the Loader raises rather than skip a line."""
    from hypershell.data.model import TaskGroupError
    with pytest.raises(TaskGroupError):
        load(['echo a', 'echo b # HYPERSHELL: group:x', 'echo c'])


@mark.unit
def test_loader_other_tag_errors_still_end_the_stream() -> None:
    """Only a refused group is skipped: any other malformed tag still ends the stream, as before."""
    with pytest.raises(RuntimeError) as caught:
        load(iter(['echo a', 'echo b # HYPERSHELL: bad@key:1', 'echo c']))
    assert 'must be an integer' not in str(caught.value)


# --- hs cluster ----------------------------------------------------------------------------------

def stream_lines(value: str) -> List[str]:
    """Three task lines; the second names `value` as its group."""
    return ['echo a', f'echo b # HYPERSHELL: group:{value}', 'echo c']


def assert_line_refused(log: str, value: str, line: int) -> None:
    """The log holds one ERROR refusing `line`, naming `value`."""
    errors = [text for text in log.splitlines() if 'ERROR' in text and 'Refused line' in text]
    assert len(errors) == 1, log[-4000:]
    assert f'line {line}' in errors[0] and f'"{value}"' in errors[0], log[-4000:]


@mark.integration
@mark.parametrize('value', ['x', 'nan'])
def test_cluster_stream_refuses_one_line(db_site: Tuple[Path, Engine], value: str) -> None:
    """A stream into `hs cluster` refuses the bad line, runs and stores the rest, and exits 0."""
    site, engine = db_site
    create_taskfile(site, stream_lines(value), filename='tasks.in')
    status, _, log = run_cli(site, f'cat tasks.in | {cluster_command("-N1")} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert_line_refused(log, value, 2)
    assert sorted((site / 'out.txt').read_text().split()) == ['a', 'c']
    assert sorted(stored_args(engine)) == ['echo a', 'echo c']


@mark.integration
def test_cluster_nodb_stream_refuses_one_line(temp_site: Path) -> None:
    """Without a database, a stream into `hs cluster` refuses the bad line and runs the rest."""
    site = Path(str(temp_site))
    create_taskfile(site, stream_lines('nan'), filename='tasks.in')
    status, _, log = run_cli(site, f'cat tasks.in | {cluster_command("-N1", "--no-db")} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert_line_refused(log, 'nan', 2)
    assert sorted((site / 'out.txt').read_text().split()) == ['a', 'c']


@mark.integration
def test_stream_refused_tag_only_line_sets_nothing(temp_site: Path) -> None:
    """A refused tag-only line on a stream sets nothing: the lines after it keep their group."""
    site = Path(str(temp_site))
    create_taskfile(site, ['# HYPERSHELL: group:3', 'echo a', '# HYPERSHELL: group:x', 'echo b'], filename='tasks.in')
    status, _, log = run_cli(site, f'cat tasks.in | {cluster_command("-N1")} > out.txt', timeout=60)
    assert status == 0, log[-4000:]
    assert_line_refused(log, 'x', 3)
    assert stored_groups(sqlite_engine()) == {'echo a': 3, 'echo b': 3}


# --- hs submit --queue ---------------------------------------------------------------------------

@mark.integration
def test_queue_stream_refuses_one_line(temp_site: Path) -> None:
    """`hs submit --queue` refuses the bad line of its stream, submits the rest, and exits 0."""
    site = Path(str(temp_site))
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY, HYPERSHELL_SERVER_QUEUESIZE='100')
    server = start_server(site, env, '--no-db', '--no-tls', '-H', ADDRESS)
    try:
        submitted = bounded(['hs', 'submit', '--queue', '--no-tls', '-H', ADDRESS, '-w', '1'],
                            stdin='\n'.join(stream_lines('x')) + '\n', cwd=site, env=env)
        status, stdout, stderr = run_bounded(site, ['hs', 'client', '-N', '1', '-T', '5', '--no-tls',
                                                    '-H', ADDRESS], env)
    finally:
        server_status = stop_server(server)
    assert submitted[0] == 0, submitted[2][-4000:]
    assert_line_refused(submitted[2], 'x', 2)
    assert status == 0, stderr
    assert server_status == 0, (site / 'server.log').read_text()[-4000:]
    assert sorted(stdout.split()) == ['a', 'c']
