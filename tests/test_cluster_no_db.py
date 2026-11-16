# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
`hs cluster` start-up: `--no-db` leaves the configured database alone on every provider and in
every launch mode, and without it the cluster initializes or checks the database as before.

Unit tests enter `ClusterApp` in-process with the database steps replaced by recorders, so they
open no database and launch nothing. Integration tests run the real `hs cluster` in a throwaway
site and look at the configured SQLite file afterwards.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, List, Tuple

# Standard libs
import os
import sys
import sqlite3
from pathlib import Path
from subprocess import run, PIPE

# External libs
from pytest import mark, fixture
from cmdkit.app import exit_status

# Internal libs
import hypershell.cluster
from hypershell.cluster import ClusterApp
from tests import create_taskfile


DEADLINE: Final[float] = 120
"""Seconds any one `hs cluster` run may take."""

HS: Final[str] = str(Path(sys.executable).parent / 'hs')
"""The `hs` of the environment under test, given to launched clients as their executable."""


# --------------------------------------------------------------------------------------------------
# Unit: which database step a cluster start-up reaches


MODES: Final[Dict[str, List[str]]] = {
    'local': [],
    'launcher': ['--launcher', 'env', '-H', '127.0.0.1', '--remote-exe', HS],
    'ssh': ['--ssh', 'localhost'],
}
"""Each launch mode's arguments (nothing is launched: only start-up runs)."""

FLAGS: Final[Dict[str, List[str]]] = {
    'none': [],
    'initdb': ['--initdb'],
    'no-db': ['--no-db'],
}
"""Each database flag's arguments."""

EXPECTED_STEPS: Final[Dict[Tuple[str, str], List[str]]] = {
    ('sqlite', 'none'): ['initdb'],
    ('sqlite', 'initdb'): ['initdb'],
    ('sqlite', 'no-db'): [],
    ('postgres', 'none'): ['checkdb'],
    ('postgres', 'initdb'): ['initdb'],
    ('postgres', 'no-db'): [],
}
"""The database steps a start-up reaches, by dialect and flag, in every launch mode."""


@fixture
def taskfile(tmp_path: Path) -> Path:
    """A task file for the in-process start-up (it is opened, never read)."""
    return create_taskfile(tmp_path, ['echo a', 'echo b'])


def database_steps(monkeypatch, taskfile: Path, dialect: str, mode: str, flag: str) -> List[str]:
    """The database steps `ClusterApp` reaches on entering, under `dialect`, with `mode` and `flag`."""
    calls: List[str] = []
    for name in ('initdb', 'checkdb'):
        monkeypatch.setattr(hypershell.cluster, name, lambda *args, _name=name, **kwargs: calls.append(_name))
    monkeypatch.setattr(hypershell.cluster, 'DATABASE_DIALECT', dialect)
    app = ClusterApp.from_cmdline([str(taskfile), '--no-tls', *MODES[mode], *FLAGS[flag]])
    with app:
        pass
    return calls


@mark.unit
@mark.parametrize('mode', list(MODES))
def test_no_db_makes_no_database_call(monkeypatch, taskfile: Path, mode: str) -> None:
    """With --no-db under SQLite the cluster neither initializes nor checks the database, in any launch mode."""
    assert database_steps(monkeypatch, taskfile, 'sqlite', mode, 'no-db') == []


@mark.unit
@mark.parametrize('dialect, mode, flag', [
    (dialect, mode, flag) for dialect, flag in EXPECTED_STEPS for mode in MODES if (dialect, flag) != ('sqlite', 'no-db')
])
def test_database_step_kept(monkeypatch, taskfile: Path, dialect: str, mode: str, flag: str) -> None:
    """Without --no-db the cluster initializes SQLite (or any provider with --initdb) and checks the rest."""
    assert database_steps(monkeypatch, taskfile, dialect, mode, flag) == EXPECTED_STEPS[dialect, flag]


# --------------------------------------------------------------------------------------------------
# Integration: the real `hs cluster` and the configured SQLite file


def cluster(*args: str) -> Tuple[int, List[str], str]:
    """Run `hs cluster` with `args`; its exit status, its output lines, and its log."""
    proc = run(['hs', 'cluster', *args], stdout=PIPE, stderr=PIPE, env=os.environ, timeout=DEADLINE)
    return proc.returncode, proc.stdout.decode('utf-8').split(), proc.stderr.decode('utf-8')


def no_db_run(temp_site: Path, *args: str) -> Tuple[int, List[str], str]:
    """Run two tasks through `hs cluster --no-db` with one client and no TLS."""
    taskfile = create_taskfile(temp_site, ['a', 'b'])
    return cluster(str(taskfile), '--no-db', '-N1', '-t', 'echo {}', '--no-tls', *args)


def table_names(path: Path) -> List[str]:
    """Every table in the SQLite file at `path`."""
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return sorted(name for name, in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
    finally:
        conn.close()


@mark.integration
def test_no_db_creates_no_file(temp_site: Path) -> None:
    """With --no-db the cluster runs every task and never creates the configured SQLite file."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    rc, lines, log = no_db_run(temp_site)
    assert rc == exit_status.success, log
    assert sorted(lines) == ['a', 'b']
    assert not database.exists()


@mark.integration
def test_no_db_leaves_file_unchanged(temp_site: Path) -> None:
    """With --no-db an existing file at the configured path keeps its bytes, even one that is not a database."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    content = b'not a database\n' * 64
    database.write_bytes(content)
    rc, lines, log = no_db_run(temp_site)
    assert rc == exit_status.success, log
    assert sorted(lines) == ['a', 'b']
    assert database.read_bytes() == content


@mark.integration
def test_no_db_runs_with_unopenable_database(temp_site: Path, monkeypatch) -> None:
    """With --no-db a configured file under a missing directory neither stops the run nor gets created."""
    missing = Path(temp_site) / 'missing'
    monkeypatch.setenv('HYPERSHELL_DATABASE_FILE', str(missing / 'task.db'))
    rc, lines, log = no_db_run(temp_site)
    assert rc == exit_status.success, log
    assert sorted(lines) == ['a', 'b']
    assert not missing.exists()


@mark.integration
def test_no_db_runs_with_launcher(temp_site: Path) -> None:
    """With --no-db and launched clients the cluster runs every task and never creates the SQLite file."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    rc, lines, log = no_db_run(temp_site, '--launcher', 'env', '-H', '127.0.0.1', '--remote-exe', HS)
    assert rc == exit_status.success, log
    assert sorted(lines) == ['a', 'b']
    assert not database.exists()


@mark.integration
def test_without_no_db_initializes_kept(temp_site: Path) -> None:
    """Without --no-db the cluster initializes the configured SQLite file as before."""
    database = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    taskfile = create_taskfile(temp_site, ['a', 'b'])
    rc, lines, log = cluster(str(taskfile), '-N1', '-t', 'echo {}', '--no-tls')
    assert rc == exit_status.success, log
    assert sorted(lines) == ['a', 'b']
    assert {'client', 'source', 'task', 'zone'} <= set(table_names(database))
