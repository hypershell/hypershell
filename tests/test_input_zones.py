# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A zone named in task input places the task in that zone, as `--zone` does, and is refused with no database.

`zone:NAME` and `zone=NAME` in an inline directive (a task line or a tag-only line, in a task file,
on standard input or among the arguments), in `-t` to `hs submit` and `hs run`, and a string `zone`
key in a `--from-json` record set the task's zone column and leave no `zone` tag. The name is read as
`--zone` reads it, from the text as written (`007` is a zone called 007, `none` a zone called none,
`default` the default zone, `a/b` is a name), and the zone is registered as `--zone` registers it,
also from a stream into `hs server`. The line's directive (or the record's key) wins over a tag-only
line, which wins over `-t`, which wins over `--zone`. A record's zone that is not a JSON string, and a
zone with no name, are refused as a bad group is. With no database (`hs submit --queue`,
`hs cluster --no-db`, `hs server --no-db`) a zone in input is refused: input read in full exits 5
naming its line or record, a stream refuses the line alone, `-t` exits 2. `hs cluster` logs one
WARNING for each zone in its input that its clients do not serve, and runs the rest.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Final, List, Optional, Tuple

# Standard libs
import os
import time
from pathlib import Path
from subprocess import Popen, PIPE

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data.model import zone_id
from tests import create_taskfile, create_json_taskfile
from tests.input_checks import bounded, db_site, sqlite_engine, stored_tags, count, rows, submit, kill_group, ADDRESS  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_cluster_exit import HS
from tests.test_env_config import start_server, stop_server, child_env, DIGIT_KEY
from tests.test_input_fields import run_until, has_tasks, initdb, refusal_lines, SEPARATORS


AUTH: Final[str] = 'Xk3v9QpL2mN7bR4tW8yZ'
"""The key of every server a test here starts, given by the environment, never on a command line."""


def zones(engine: Engine) -> Dict[str, Optional[str]]:
    """Stored zone id of every task (None for the default zone), by its command."""
    return {args: (None if zone is None else str(zone)) for args, zone in rows(engine, 'SELECT args, zone FROM task')}


def registered(engine: Engine) -> Dict[str, str]:
    """Name of every registered zone, by its id."""
    return {str(ident): name for ident, name in rows(engine, 'SELECT id, name FROM zone')}


def served_env() -> Dict[str, str]:
    """The test's environment with the server key set."""
    return {**os.environ, 'HYPERSHELL_SERVER_AUTH': AUTH}


def completed(engine: Engine, commands: List[str]) -> Callable[[], bool]:
    """A check that every task of `commands` has completed (false while the database cannot be read)."""
    def check() -> bool:
        try:
            done = rows(engine, 'SELECT args FROM task WHERE completion_time IS NOT NULL')
        except OperationalError:
            return False
        return set(commands) <= {args for args, in done}
    return check


def never() -> bool:
    """A check that never holds: run until the command exits."""
    return False


# --- A zone from input sets the zone column ------------------------------------------------------

ZONE_FORMS: Final[List[str]] = ['line', 'tagline', 'stdin', 'args', 'tag', 'run']
"""Task input naming a zone: a task line's directive, a tag-only line, standard input, arguments, `-t`, `hs run -t`."""


def submit_zone(site: Path, engine: Engine, form: str, token: str) -> Tuple[Optional[int], str]:
    """Submit the task `echo a` with `token` in `form`; the exit status (None for `hs run`, left waiting) and stderr."""
    directive = f'# HYPERSHELL: {token}'
    if form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, [f'echo a {directive}'])), cwd=site)
    elif form == 'tagline':
        rc, _, stderr = submit(str(create_taskfile(site, [directive, 'echo a'])), cwd=site)
    elif form == 'stdin':
        rc, _, stderr = submit('-', stdin=f'echo a {directive}\n', cwd=site)
    elif form == 'args':
        rc, _, stderr = submit('--', 'echo', 'a', '#', 'HYPERSHELL:', token, cwd=site)
    elif form == 'tag':
        rc, _, stderr = submit('-t', token, '--', 'echo', 'a', cwd=site)
    else:
        rc, _, stderr = run_until(['hs', 'run', '-n', '1', '-t', token, '--', 'echo', 'a'], has_tasks(engine), cwd=site)
    return rc, stderr


@mark.integration
@mark.parametrize('sep', SEPARATORS)
@mark.parametrize('form', ZONE_FORMS)
def test_zone_set_by_input(db_site: Tuple[Path, Engine], form: str, sep: str) -> None:
    """`zone:gpu` and `zone=gpu` in every form of task input store the task in zone gpu, registered, with no `zone` tag."""
    site, engine = db_site
    rc, stderr = submit_zone(site, engine, form, f'zone{sep}gpu')
    assert rc in ((0, None) if form == 'run' else (0, )), stderr[-4000:]
    assert zones(engine) == {'echo a': zone_id('gpu')}, stderr[-4000:]
    assert stored_tags(engine) == {'echo a': {}}
    assert registered(engine).get(zone_id('gpu')) == 'gpu'


@mark.integration
def test_zone_set_by_json(db_site: Tuple[Path, Engine]) -> None:
    """A string `zone` key in a `--from-json` record stores its task in that zone, registered, with no `zone` tag."""
    site, engine = db_site
    path = create_json_taskfile(site, [{'args': 'echo a', 'zone': 'gpu'}, {'args': 'echo b'}])
    rc, _, stderr = submit('--from-json', str(path), cwd=site)
    assert rc == 0, stderr[-4000:]
    assert zones(engine) == {'echo a': zone_id('gpu'), 'echo b': None}
    assert stored_tags(engine) == {'echo a': {}, 'echo b': {}}
    assert registered(engine).get(zone_id('gpu')) == 'gpu'


@mark.integration
@mark.parametrize('sep', SEPARATORS)
def test_zone_precedence(db_site: Tuple[Path, Engine], sep: str) -> None:
    """A line's zone wins over a tag-only line's, which wins over `-t`, which wins over `--zone`; each is registered."""
    site, engine = db_site
    path = create_taskfile(site, [f'echo a # HYPERSHELL: zone{sep}inline', 'echo b',
                                  f'# HYPERSHELL: zone{sep}running', 'echo c'])
    rc, _, stderr = submit(str(path), '--zone', 'opt', '-t', f'zone{sep}tagged', cwd=site)
    assert rc == 0, stderr[-4000:]
    rc, _, stderr = submit('--zone', 'opt', '--', 'echo', 'd', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert zones(engine) == {'echo a': zone_id('inline'), 'echo b': zone_id('tagged'),
                             'echo c': zone_id('running'), 'echo d': zone_id('opt')}
    assert {'inline', 'tagged', 'running', 'opt'} <= set(registered(engine).values())
    assert all('zone' not in tags for tags in stored_tags(engine).values())


@mark.integration
def test_zone_precedence_json(db_site: Tuple[Path, Engine]) -> None:
    """A record's `zone` key wins over `-t`, which wins over `--zone`."""
    site, engine = db_site
    path = create_json_taskfile(site, [{'args': 'echo a', 'zone': 'record'}, {'args': 'echo b'}])
    rc, _, stderr = submit('--from-json', str(path), '--zone', 'opt', '-t', 'zone:tagged', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert zones(engine) == {'echo a': zone_id('record'), 'echo b': zone_id('tagged')}
    assert {'record', 'tagged'} <= set(registered(engine).values())


@mark.integration
def test_zone_option_alone(temp_site: Path) -> None:
    """With no zone in its input, a task takes `--zone`, registered (guard)."""
    site = Path(str(temp_site))
    rc, _, stderr = submit('--zone', 'opt', '--', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    engine = sqlite_engine()
    assert zones(engine) == {'echo a': zone_id('opt')}
    assert registered(engine).get(zone_id('opt')) == 'opt'


# --- The name as written -------------------------------------------------------------------------

NAMES: Final[List[Tuple[str, str]]] = [('slash', 'a/b'), ('none', 'none'), ('padded', '007'), ('exponent', '1e3')]
"""Zone names a tag could not be, or that a tag's typing would alter, by test id."""


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
@mark.parametrize('sep', SEPARATORS)
@mark.parametrize('case, name', NAMES, ids=[case for case, _ in NAMES])
def test_zone_name_as_written(temp_site: Path, case: str, name: str, sep: str, form: str) -> None:
    """A zone name is the text as written, as `--zone` reads it: `a/b`, `none`, `007` and `1e3` are zones by those names."""
    site = Path(str(temp_site))
    token = f'zone{sep}{name}'
    if form == 'line':
        rc, _, stderr = submit(str(create_taskfile(site, [f'echo a # HYPERSHELL: {token}'])), cwd=site)
    else:
        rc, _, stderr = submit('-t', token, '--', 'echo', 'a', cwd=site)
    assert rc == 0, stderr[-4000:]
    engine = sqlite_engine()
    assert zones(engine) == {'echo a': zone_id(name)}
    assert registered(engine).get(zone_id(name)) == name
    assert stored_tags(engine) == {'echo a': {}}


@mark.integration
@mark.parametrize('sep', SEPARATORS)
def test_zone_default_from_input(temp_site: Path, sep: str) -> None:
    """`zone:default` is the default zone, as `--zone default` is: the column is NULL and no `zone` tag is stored."""
    site = Path(str(temp_site))
    rc, _, stderr = submit(str(create_taskfile(site, [f'echo a # HYPERSHELL: zone{sep}default'])), '--zone', 'opt', cwd=site)
    assert rc == 0, stderr[-4000:]
    engine = sqlite_engine()
    assert zones(engine) == {'echo a': None}
    assert stored_tags(engine) == {'echo a': {}}


NO_NAME: Final[List[Tuple[str, str]]] = [('bare', 'zone'), ('colon', 'zone:'), ('equals', 'zone=')]
"""A zone written with no name, by test id."""


@mark.integration
@mark.parametrize('form', ['line', 'tag'])
@mark.parametrize('case, token', NO_NAME, ids=[case for case, _ in NO_NAME])
def test_zone_without_name_refused(temp_site: Path, case: str, token: str, form: str) -> None:
    """A zone with no name is refused as `--zone ''` is: a file exits 5 naming its line, `-t` exits 2; nothing is stored."""
    site = Path(str(temp_site))
    initdb(site)
    if form == 'line':
        path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {token}', 'echo c'])
        rc, _, stderr = submit(str(path), cwd=site)
        assert rc == exit_status.runtime_error, stderr[-4000:]
        assert [line for line in refusal_lines(stderr, ['line 2']) if 'zone' in line.lower()], stderr[-4000:]
    else:
        rc, _, stderr = submit('-t', token, '--', 'echo', 'a', cwd=site)
        assert rc == exit_status.bad_argument, stderr[-4000:]
        assert 'zone' in stderr.lower() and 'Tag key' not in stderr, stderr[-4000:]  # Refused as a zone, not a tag key
    assert count(sqlite_engine(), 'task') == 0


JSON_REFUSED: Final[List[Tuple[str, Any]]] = [('integer', 4), ('bool', True), ('null', None), ('float', 1.5),
                                              ('empty', '')]
"""A record's `zone` that is not a JSON string, or names no zone, by test id."""


@mark.integration
@mark.parametrize('case, value', JSON_REFUSED, ids=[case for case, _ in JSON_REFUSED])
def test_zone_json_refused(temp_site: Path, case: str, value: Any) -> None:
    """A `--from-json` record whose zone is not a JSON string naming a zone inserts nothing and exits 5, naming the record."""
    site = Path(str(temp_site))
    initdb(site)
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', 'zone': value}])
    rc, _, stderr = submit('--from-json', str(path), cwd=site)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert [line for line in refusal_lines(stderr, ['record 1']) if 'zone' in line.lower()], stderr[-4000:]
    engine = sqlite_engine()
    assert count(engine, 'task') == 0
    assert count(engine, 'source') == 0


# --- A stream registers its zones ----------------------------------------------------------------

@mark.integration
def test_server_stream_registers_zones(db_site: Tuple[Path, Engine]) -> None:
    """A stream into `hs server` stores each line in the zone it names and registers that zone."""
    site, engine = db_site
    lines = 'echo a # HYPERSHELL: zone:gpu\necho b # HYPERSHELL: zone=cpu\necho c\n'
    _, _, stderr = run_until(['hs', 'server', '-b', '1', '-w', '1', '--no-tls', '-H', ADDRESS], has_tasks(engine, 3),
                             cwd=site, stdin=lines, env=served_env())
    assert zones(engine) == {'echo a': zone_id('gpu'), 'echo b': zone_id('cpu'), 'echo c': None}, stderr[-4000:]
    assert {'gpu', 'cpu'} <= set(registered(engine).values())
    assert all('zone' not in tags for tags in stored_tags(engine).values())


# --- No database: a zone in input is refused -----------------------------------------------------

QUEUE: Final[List[str]] = ['--queue', '--no-tls', '-H', ADDRESS]
"""`hs submit` options sending to a server's queue, with no database."""


@mark.integration
@mark.parametrize('sep', SEPARATORS)
@mark.parametrize('form', ['line', 'args', 'tag'])
def test_queue_refuses_zone(temp_site: Path, form: str, sep: str) -> None:
    """`hs submit --queue` refuses a zone in its input, as `--zone` with `--queue`: whole input exits 5, `-t` exits 2."""
    site = Path(str(temp_site))
    token = f'zone{sep}gpu'
    if form == 'line':
        path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: {token}', 'echo c'])
        rc, _, stderr = submit(*QUEUE, str(path), cwd=site)
        status, origin = exit_status.runtime_error, 'line 2'
    elif form == 'args':
        rc, _, stderr = submit(*QUEUE, '--', 'echo', 'b', '#', 'HYPERSHELL:', token, cwd=site)
        status, origin = exit_status.runtime_error, 'arguments'
    else:
        rc, _, stderr = submit(*QUEUE, '-t', token, '--', 'echo', 'b', cwd=site)
        status, origin = exit_status.bad_argument, ''
    assert rc == status, stderr[-4000:]
    assert refusal_lines(stderr, ['gpu', origin]), stderr[-4000:]
    assert 'Connection' not in stderr and 'Tag key' not in stderr, stderr[-4000:]  # Refused for the zone alone


@mark.integration
def test_queue_refuses_json_zone(temp_site: Path) -> None:
    """`hs submit --queue --from-json` refuses a record's zone (exit 5, naming the record) before it connects."""
    site = Path(str(temp_site))
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', 'zone': 'gpu'}])
    rc, _, stderr = submit(*QUEUE, '--from-json', str(path), cwd=site)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert refusal_lines(stderr, ['gpu', 'record 1']), stderr[-4000:]
    assert 'Connection' not in stderr, stderr[-4000:]


def assert_line_refused(log: str, line: int) -> None:
    """The log holds one ERROR refusing `line`, naming the zone gpu."""
    errors = [text for text in log.splitlines() if 'ERROR' in text and 'Refused line' in text]
    assert len(errors) == 1, log[-4000:]
    assert f'line {line}' in errors[0] and 'gpu' in errors[0], log[-4000:]


STREAM: Final[str] = 'echo a\necho b # HYPERSHELL: zone:gpu\necho c\n'
"""A stream whose second line names a zone."""


def serve_stream(site: Path, *args: str) -> Tuple[Popen, Path]:
    """Start `hs server` reading `STREAM` in its own session, logging to a file in `site`; wait until it listens."""
    log_path = site / 'server.log'
    with open(log_path, mode='w') as stream:
        server = Popen(['hs', 'server', *args, '--no-tls', '-H', ADDRESS], cwd=site, env=served_env(), text=True,
                       stdin=PIPE, stdout=stream, stderr=stream, start_new_session=True)
    server.stdin.write(STREAM)
    server.stdin.close()
    deadline = time.monotonic() + 30
    while 'Server listening' not in log_path.read_text() and server.poll() is None:
        if time.monotonic() > deadline:
            kill_group(server)
            pytest.fail(f'Server not listening within 30 s: {log_path.read_text()[-4000:]}')
        time.sleep(0.1)
    return server, log_path


def run_client(site: Path, env: Dict[str, str]) -> Tuple[int, str, str]:
    """Run one client against the test's server until it has had no task for five seconds."""
    return bounded(['hs', 'client', '-N', '1', '-T', '5', '--no-tls', '-H', ADDRESS], cwd=site, env=env, timeout=60)


@mark.integration
def test_queue_stream_refuses_zone_line(temp_site: Path) -> None:
    """`hs submit --queue` refuses the line of its stream that names a zone, submits the rest, and exits 0."""
    site = Path(str(temp_site))
    env = child_env(site, HYPERSHELL_SERVER_AUTH=DIGIT_KEY, HYPERSHELL_SERVER_QUEUESIZE='100')
    server = start_server(site, env, '--no-db', '--no-tls', '-H', ADDRESS)
    try:
        submitted = bounded(['hs', 'submit', *QUEUE, '-w', '1'], stdin=STREAM, cwd=site, env=env)
        status, stdout, stderr = run_client(site, env)
    finally:
        stop_server(server)
    assert submitted[0] == 0, submitted[2][-4000:]
    assert_line_refused(submitted[2], 2)
    assert status == 0, stderr[-4000:]
    assert sorted(stdout.split()) == ['a', 'c']


@mark.integration
@mark.parametrize('sep', SEPARATORS)
def test_cluster_nodb_file_refuses_zone(temp_site: Path, sep: str) -> None:
    """`hs cluster --no-db FILE` naming a zone runs nothing and exits 5, naming the line."""
    site = Path(str(temp_site))
    path = create_taskfile(site, ['echo a', f'echo b # HYPERSHELL: zone{sep}gpu', 'echo c'])
    rc, stdout, stderr = bounded([HS, 'cluster', str(path), '--no-db', '-N1', '--no-tls', '-H', ADDRESS],
                                 cwd=site, timeout=60)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert refusal_lines(stderr, ['gpu', 'line 2']), stderr[-4000:]
    assert stdout.split() == []


@mark.integration
def test_cluster_nodb_json_refuses_zone(temp_site: Path) -> None:
    """`hs cluster --no-db --from-json` with a record's zone runs nothing and exits 5, naming the record."""
    site = Path(str(temp_site))
    path = create_json_taskfile(site, [{'args': 'echo a'}, {'args': 'echo b', 'zone': 'gpu'}])
    rc, stdout, stderr = bounded([HS, 'cluster', '--from-json', str(path), '--no-db', '-N1', '--no-tls', '-H', ADDRESS],
                                 cwd=site, timeout=60)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert refusal_lines(stderr, ['gpu', 'record 1']), stderr[-4000:]
    assert stdout.split() == []


@mark.integration
def test_cluster_nodb_stream_refuses_zone_line(temp_site: Path) -> None:
    """A stream into `hs cluster --no-db` refuses the line naming a zone, runs the rest, and exits 0."""
    site = Path(str(temp_site))
    rc, stdout, stderr = bounded([HS, 'cluster', '--no-db', '-N1', '--no-tls', '-H', ADDRESS], stdin=STREAM,
                                 cwd=site, timeout=60)
    assert rc == 0, stderr[-4000:]
    assert_line_refused(stderr, 2)
    assert sorted(stdout.split()) == ['a', 'c']


@mark.integration
def test_server_nodb_file_refuses_zone(temp_site: Path) -> None:
    """`hs server --no-db FILE` naming a zone exits 5 before it serves, naming the line."""
    site = Path(str(temp_site))
    path = create_taskfile(site, ['echo a', 'echo b # HYPERSHELL: zone:gpu', 'echo c'])
    rc, _, stderr = run_until(['hs', 'server', str(path), '--no-db', '--no-tls', '-H', ADDRESS], never, cwd=site,
                              env=served_env(), bound=15)
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert refusal_lines(stderr, ['gpu', 'line 2']), stderr[-4000:]


@mark.integration
def test_server_nodb_stream_refuses_zone_line(temp_site: Path) -> None:
    """A stream into `hs server --no-db` refuses the line naming a zone and serves the rest."""
    site = Path(str(temp_site))
    server, log_path = serve_stream(site, '--no-db')
    try:
        status, stdout, stderr = run_client(site, served_env())
    finally:
        stop_server(server)
    assert status == 0, stderr[-4000:]
    assert_line_refused(log_path.read_text(), 2)
    assert sorted(stdout.split()) == ['a', 'c']


# --- hs cluster warns of a zone its clients do not serve -----------------------------------------

CLUSTER_LINES: Final[List[str]] = ['echo a', 'echo b # HYPERSHELL: zone:served', 'echo c # HYPERSHELL: zone:far-gpu',
                                   'echo d # HYPERSHELL: zone:far-gpu', 'echo e # HYPERSHELL: zone:far-cpu']
"""Input to a cluster serving zone `served`: two tasks it serves, three in zones it does not."""


@mark.integration
@mark.parametrize('form', ['file', 'stream'])
def test_cluster_warns_once_per_unserved_zone(temp_site: Path, form: str) -> None:
    """`hs cluster --zone served` logs one WARNING for each zone of its input it does not serve, and runs the rest."""
    site = Path(str(temp_site))
    initdb(site)
    engine = sqlite_engine()
    argv = [HS, 'cluster', '--zone', 'served', '-N1', '-b', '1', '--no-tls', '-H', ADDRESS]
    if form == 'file':
        argv.insert(2, str(create_taskfile(site, CLUSTER_LINES)))
        stdin = None
    else:
        stdin = '\n'.join(CLUSTER_LINES) + '\n'
    _, _, stderr = run_until(argv, completed(engine, ['echo a', 'echo b']), cwd=site, stdin=stdin, bound=60)
    warnings = [line for line in stderr.splitlines() if 'WARNING' in line]
    assert len([line for line in warnings if 'far-gpu' in line]) == 1, stderr[-4000:]
    assert len([line for line in warnings if 'far-cpu' in line]) == 1, stderr[-4000:]
    assert not [line for line in warnings if 'served' in line and 'far-' not in line], stderr[-4000:]
    assert rows(engine, "SELECT args, exit_status FROM task WHERE args IN ('echo a', 'echo b') ORDER BY args") == [
        ('echo a', 0), ('echo b', 0)]
    assert zones(engine) == {'echo a': zone_id('served'), 'echo b': zone_id('served'), 'echo c': zone_id('far-gpu'),
                             'echo d': zone_id('far-gpu'), 'echo e': zone_id('far-cpu')}
    assert {'served', 'far-gpu', 'far-cpu'} <= set(registered(engine).values())
