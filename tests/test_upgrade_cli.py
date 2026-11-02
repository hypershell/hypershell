# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Open a database created by hypershell 2.8.3 through the command line."""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Tuple

# Standard libs
import os
import re
import time
import socket
import sqlite3
import secrets
import threading
from pathlib import Path
from subprocess import Popen, PIPE, TimeoutExpired, run

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status

# Internal libs
from tests import (main, main_lines, NO_OUTPUT, FIXTURE_283, ORIGINAL_TASK_COLUMNS, copy_fixture,
                   make_half_upgraded, snapshot, task_columns, sqlite_schema)

FAIL_ARGS = "sh -c 'exit 3'"
NEW_TASK_COLUMNS = ('source', 'fingerprint', 'part')
NEW_INDEXES = ('index_tasks_source', 'index_tasks_previous_id', 'index_tasks_next_id', 'index_source_lookup')
UPGRADE_LINE = re.compile(r'^INFO \[hypershell\.data[\w.]*\] (Added column|Created index|Created table|Dropped) ')


@fixture(params=['untouched', 'half-upgraded'])
def legacy_db(request, temp_site: Path) -> Path:
    """A copy of the 2.8.3 database at the site path, optionally left half-upgraded by 2.9.0a1."""
    path = copy_fixture(temp_site)
    if request.param == 'half-upgraded':
        make_half_upgraded(path)
    return path


def task_ids(path: Path) -> Dict[Tuple[str, int], str]:
    """Map (args, attempt) to task id, read raw."""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return {(args, attempt): id for id, args, attempt in conn.execute('SELECT id, args, attempt FROM task')}
    finally:
        conn.close()


def assert_schema_current(path: Path) -> None:
    """The task table has every new column and the database has every new table and index."""
    columns = task_columns(path)
    for name in NEW_TASK_COLUMNS:
        assert name in columns, f'task.{name} missing'
    names = {name for _, name, _, _ in sqlite_schema(path)}
    assert 'source' in names
    for name in NEW_INDEXES:
        assert name in names, f'index {name} missing'


def upgrade_lines(stderr: List[str]) -> List[str]:
    """Lines logged by the upgrade step."""
    return [line for line in stderr if UPGRADE_LINE.match(line)]


@mark.integration
def test_end_to_end(temp_site: Path) -> None:
    """A 2.8.3 database takes new tasks, completes pending ones with a retry, and lists them all."""
    path = copy_fixture(temp_site)
    before = snapshot(path)['task']
    ids = task_ids(path)

    rc, _, _ = main(['hs', 'submit', '-t', 'kind:new', '--', 'echo', 'new-0'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['8'])

    rc, stdout, stderr = main(['hs', 'cluster', '--restart', '-r', '2', '-N2', '-b1', '-w1', '--no-tls'])
    assert rc == exit_status.success
    assert sorted(stdout.split()) == ['new-0', 'pending-0', 'pending-1']
    assert len(re.findall(r'Non-zero exit status \(3\)', stderr)) == 1
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['9'])
    assert main_lines(['hs', 'list', '--remaining', '--count'])[:2] == (exit_status.success, ['0'])

    after = snapshot(path)['task']
    second = ids[(FAIL_ARGS, 2)]
    retries = [row for id, row in after.items() if id not in before and row[2] == FAIL_ARGS]
    assert len(retries) == 1
    retry = dict(zip(ORIGINAL_TASK_COLUMNS, retries[0]))
    assert retry['attempt'] == 3
    assert retry['previous_id'] == second

    kept = ('id', 'group', 'args', 'submit_id', 'submit_time', 'submit_host', 'attempt', 'retried',
            'previous_id', 'next_id', 'tag')
    for id, old in before.items():
        new = after[id]
        old_row, new_row = dict(zip(ORIGINAL_TASK_COLUMNS, old)), dict(zip(ORIGINAL_TASK_COLUMNS, new))
        if id == second:
            assert new_row['retried'] == 1
            assert new_row['next_id'] == retry['id']
            assert {k: v for k, v in new_row.items() if k not in ('retried', 'next_id')} == \
                   {k: v for k, v in old_row.items() if k not in ('retried', 'next_id')}
        elif old_row['args'].startswith('echo pending-'):
            assert {k: new_row[k] for k in kept} == {k: old_row[k] for k in kept}
            assert new_row['exit_status'] == 0
            assert new_row['completion_time'] is not None
        else:
            assert new == old, f'row {id} ({old_row["args"]}) changed'

    # Pre-2.9 SQLite databases keep the retry-chain UNIQUE constraints, and the retry still worked.
    task_sql = [sql for type, name, _, sql in sqlite_schema(path) if type == 'table' and name == 'task'][0]
    assert 'UNIQUE (previous_id)' in task_sql
    assert 'UNIQUE (next_id)' in task_sql


R1_COMMANDS: Dict[str, Tuple[List[str], str]] = {
    # name: (argv, expected `hs list --count` afterwards)
    'submit': (['hs', 'submit', 'echo r1'], '8'),
    'list': (['hs', 'list', '--all'], '7'),
    'info': (['hs', 'info', '{done}'], '7'),
    'search': (['hs', 'task', 'search', 'args'], '7'),
    'update': (['hs', 'update', 'exit_status=0', '-t', 'n:99', '--no-confirm'], '7'),
    'wait': (['hs', 'wait', '{done}', '-n', '1'], '7'),
    'initdb': (['hs', 'initdb', '--yes'], '7'),
}


@mark.integration
@mark.parametrize('command', list(R1_COMMANDS))
def test_first_command(legacy_db: Path, command: str) -> None:
    """Each database-backed command brings the schema current on first use and succeeds."""
    done = task_ids(legacy_db)[('echo done-0', 1)]
    argv, count = R1_COMMANDS[command]
    rc, _, stderr = main([arg.format(done=done) for arg in argv])
    assert rc == exit_status.success, stderr
    assert_schema_current(legacy_db)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, [count])


def wait_for_port(port: int, server: Popen, timeout: float = 30) -> None:
    """Block until the server listens on `port`; fail if it exits first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            _, stderr = server.communicate()
            assert False, f'server exited ({server.returncode}) before listening: {stderr.decode()}'
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(('localhost', port)) == 0:
                return
        time.sleep(0.1)
    pytest.fail(f'server did not listen on port {port} within {timeout}s')


@mark.integration
def test_first_command_server(legacy_db: Path) -> None:
    """A standalone server opens the database, completes pending tasks, and retries the failure."""
    key = secrets.token_hex(16)
    port = int(os.environ['HYPERSHELL_SERVER_PORT'])
    server = Popen(['hs', 'server', '--restart', '-r', '2', '-b', '1', '-w', '1', '--no-tls', '-k', key],
                   stdout=PIPE, stderr=PIPE, env=os.environ)
    try:
        wait_for_port(port, server)
        client = run(['hs', 'client', '--no-tls', '-k', key, '-N', '2', '-T', '10'],
                     stdout=PIPE, stderr=PIPE, env=os.environ, timeout=120)
        _, server_stderr = server.communicate(timeout=120)
    except TimeoutExpired:
        server.kill()
        server.communicate()
        assert False, 'server and client did not finish'
    finally:
        if server.poll() is None:
            server.kill()
            server.communicate()
    assert server.returncode == exit_status.success, server_stderr.decode()
    assert client.returncode == exit_status.success, client.stderr.decode()
    assert sorted(client.stdout.decode().split()) == ['pending-0', 'pending-1']
    assert_schema_current(legacy_db)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['8'])
    assert main_lines(['hs', 'list', '--remaining', '--count'])[:2] == (exit_status.success, ['0'])


@mark.integration
def test_rows_preserved(legacy_db: Path) -> None:
    """The upgrade keeps every row as stored; new columns read NULL, NULL, and 0."""
    before = snapshot(legacy_db)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    assert snapshot(legacy_db) == before
    conn = sqlite3.connect(f'file:{legacy_db}?mode=ro', uri=True)
    try:
        values = conn.execute('SELECT source, fingerprint, part FROM task').fetchall()
    finally:
        conn.close()
    assert values == [(None, None, 0)] * 7


@mark.integration
def test_upgrade_logged_once(request, legacy_db: Path) -> None:
    """The first command logs each change at INFO naming the table; the second is silent and changes nothing."""
    rc, stdout, stderr = main_lines(['hs', 'list', '--count'])
    assert (rc, stdout) == (exit_status.success, ['7'])
    lines = upgrade_lines(stderr)
    for message in ('Added column task.source', 'Added column task.fingerprint', 'Added column task.part',
                    'Created index index_tasks_source on task'):
        assert len([line for line in lines if line.endswith(message)]) == 1, stderr
    created_source = [line for line in lines if line.endswith('Created table source')]
    assert len(created_source) == (1 if request.node.callspec.params['legacy_db'] == 'untouched' else 0), stderr

    schema = sqlite_schema(legacy_db)
    assert main_lines(['hs', 'list', '--count']) == (exit_status.success, ['7'], NO_OUTPUT)
    assert sqlite_schema(legacy_db) == schema


@mark.integration
@mark.parametrize('journal', ['delete', 'wal'])
def test_concurrent_first_use(temp_site: Path, journal: str) -> None:
    """Four processes opening one 2.8 database at once all succeed; exactly one upgrades it."""
    path = copy_fixture(temp_site)
    conn = sqlite3.connect(str(path))
    try:
        mode, = conn.execute(f'PRAGMA journal_mode = {journal}').fetchone()
    finally:
        conn.close()
    if mode != journal:
        pytest.fail(f'Could not set journal_mode={journal} (got {mode})')

    count = 4
    barrier = threading.Barrier(count)
    results: List[Tuple[int, str, str]] = [None] * count

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = main(['hs', 'list', '--count'])

    threads = [threading.Thread(target=worker, args=(i, )) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    if any(result is None for result in results):
        pytest.fail('a worker did not finish')

    assert [rc for rc, _, _ in results] == [exit_status.success] * count, results
    assert [stdout for _, stdout, _ in results] == ['7'] * count
    upgraded = [stderr for _, _, stderr in results if 'Added column task.source' in stderr]
    assert len(upgraded) == 1, results
    assert sorted(stderr for _, _, stderr in results)[:count - 1] == [''] * (count - 1), results
    assert_schema_current(path)


@mark.integration
def test_sqlite_keeps_chain_constraints(legacy_db: Path) -> None:
    """The upgrade leaves the SQLite retry-chain UNIQUE constraints in place (no table rebuild)."""
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    task_sql = [sql for type, name, _, sql in sqlite_schema(legacy_db) if type == 'table' and name == 'task'][0]
    assert 'UNIQUE (previous_id)' in task_sql
    assert 'UNIQUE (next_id)' in task_sql


@mark.integration
def test_initdb_truncate(legacy_db: Path) -> None:
    """`hs initdb --truncate` works on a 2.8 database and leaves a fresh schema."""
    rc, _, stderr = main(['hs', 'initdb', '--truncate', '--yes'])
    assert rc == exit_status.success, stderr
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['0'])
    assert_schema_current(legacy_db)
    task_sql = [sql for type, name, _, sql in sqlite_schema(legacy_db) if type == 'table' and name == 'task'][0]
    assert 'UNIQUE' not in task_sql


@mark.integration
def test_initdb_rotate(temp_site: Path, legacy_db: Path) -> None:
    """`hs initdb --rotate` works on a 2.8 database; completed rows move to the partition."""
    rc, _, stderr = main(['hs', 'initdb', '--rotate', '--yes'])
    assert rc == exit_status.success, stderr
    assert (Path(temp_site) / 'local.1').is_file()
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    assert sorted(main_lines(['hs', 'list', 'args', '--part', '1'])[1]) == sorted(
        ['echo done-0', 'echo done-1', 'echo done-2', FAIL_ARGS, FAIL_ARGS])
    assert sorted(main_lines(['hs', 'list', 'args', '--part', '0'])[1]) == ['echo pending-0', 'echo pending-1']


@mark.integration
def test_partition_from_283(temp_site: Path) -> None:
    """A 2.8.3 partition lists beside an upgraded main database, columns aligned, and is never written."""
    copy_fixture(temp_site)
    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['7'])
    part = copy_fixture(temp_site, 'local.1')
    schema = sqlite_schema(part)

    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['14'])
    assert sorted(main_lines(['hs', 'list', 'args', '-t', 'kind:done'])[1]) == sorted(
        ['echo done-0', 'echo done-1', 'echo done-2'] * 2)
    assert sorted(main_lines(['hs', 'list', 'args', '--part', '0', '-t', 'kind:pending'])[1]) == sorted(
        ['echo pending-0', 'echo pending-1'] * 2)
    assert sqlite_schema(part) == schema
    assert part.read_bytes() == FIXTURE_283.read_bytes()


@mark.integration
def test_partition_column_order(temp_site: Path) -> None:
    """A freshly created partition and an upgraded main database (columns in another order) align by name."""
    rc, _, _ = main(['hs', 'submit', '-t', 'kind:fresh', '--', 'echo', 'fresh-0'])
    if rc != exit_status.success:
        pytest.fail('Could not create fresh partition')
    main_path = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    os.replace(main_path, Path(temp_site) / 'local.1')
    copy_fixture(temp_site)

    assert main_lines(['hs', 'list', '--count'])[:2] == (exit_status.success, ['8'])
    assert main_lines(['hs', 'list', 'args', '-t', 'kind:fresh'])[:2] == (exit_status.success, ['echo fresh-0'])
    assert sorted(main_lines(['hs', 'list', 'args', '-t', 'kind:done'])[1]) == [
        'echo done-0', 'echo done-1', 'echo done-2']
