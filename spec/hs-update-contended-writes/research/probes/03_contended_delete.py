# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Research probe 03 (hs-update-contended-writes): does a DELETE (or a filtered, set-based UPDATE) that
waited on another writer's row lock count the contended row, on plain PostgreSQL and on a TimescaleDB
hypertable? Also: does a filtered statement that re-checked a row out of its filter keep a lock on it?

Run from the worktree root, sandbox off, against a local server:

    uv run python \
        spec/hs-update-contended-writes/research/probes/03_contended_delete.py

Creates the databases hu03_pg and hu03_ts, initializes each with `hs initdb` under the `postgres` and
`timescale` providers, and drops them at the end.
"""

from __future__ import annotations

import os
import sys
import time
import threading
import subprocess
from pathlib import Path

import psycopg

ADMIN = 'postgresql://postgres:postgres@localhost:5432/postgres'
SCRATCH = Path(os.environ.get('TMPDIR', '/tmp')) / 'hu03'
N = 4  # tasks per case


def url(name: str) -> str:
    return f'postgresql://postgres:postgres@localhost:5432/{name}'


def hs_env(name: str, provider: str) -> dict:
    env = dict(os.environ)
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    env.update({'HYPERSHELL_SITE': str(SCRATCH / 'site'), 'HYPERSHELL_DATABASE_PROVIDER': provider,
                'HYPERSHELL_DATABASE_HOST': 'localhost', 'HYPERSHELL_DATABASE_PORT': '5432',
                'HYPERSHELL_DATABASE_USER': 'postgres', 'HYPERSHELL_DATABASE_PASSWORD': 'postgres',
                'HYPERSHELL_DATABASE_DATABASE': name})
    return env


def hs(env: dict, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(['hs', *args], env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f'hs {" ".join(args)} failed ({proc.returncode}): {proc.stderr}')
    return proc


def create(name: str, provider: str) -> dict:
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {name}')
        conn.execute(f'CREATE DATABASE {name}')
    env = hs_env(name, provider)
    hs(env, 'initdb', '--yes')
    return env


def drop(name: str) -> None:
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def seed(env: dict, name: str, label: str) -> list[str]:
    """Empty task, submit N tasks through the CLI; return their ids, oldest first."""
    with psycopg.connect(url(name), autocommit=True) as conn:
        conn.execute('DELETE FROM task')
    path = SCRATCH / f'{label}.in'
    path.write_text(''.join(f'echo {label} {i}\n' for i in range(N)))
    hs(env, 'submit', '-f', str(path))
    with psycopg.connect(url(name), autocommit=True) as conn:
        return [str(r[0]) for r in conn.execute('SELECT id FROM task ORDER BY submit_time, args')]


def case(name: str, env: dict, label: str, holder_sql: str, worker_sql: str) -> dict:
    ids = seed(env, name, label)
    x = ids[0]
    result: dict = {'label': label}
    got = threading.Event()
    release = threading.Event()

    def work() -> None:
        with psycopg.connect(url(name)) as conn:
            result['worker_pid'] = conn.info.backend_pid
            cur = conn.execute(worker_sql, {'id': x})
            result['rowcount'] = cur.rowcount
            result['worker_xid'] = conn.execute('SELECT pg_current_xact_id()::text').fetchone()[0]
            got.set()
            release.wait(30)
            conn.commit()

    holder = psycopg.connect(url(name))
    try:
        hpid = holder.info.backend_pid
        assert holder.execute(holder_sql, {'id': x}).rowcount == 1
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        deadline = time.monotonic() + 30
        with psycopg.connect(url(name), autocommit=True) as obs:
            while time.monotonic() < deadline:
                rows = obs.execute('SELECT wait_event_type, query FROM pg_stat_activity WHERE datname = current_database() '
                                   'AND %s = ANY(pg_blocking_pids(pid))', (hpid,)).fetchall()
                if rows:
                    result['blocked'] = rows
                    break
                if got.is_set():
                    result['blocked'] = 'NOT BLOCKED'
                    break
                time.sleep(0.02)
            holder.commit()
            got.wait(30)
            # While the worker's transaction is still open: can a third session lock X at once?
            with psycopg.connect(url(name)) as third:
                third.execute("SET lock_timeout = '500ms'")
                try:
                    r = third.execute('SELECT id FROM task WHERE id = %s FOR UPDATE', (x,)).fetchall()
                    result['x_lockable_while_worker_open'] = f'yes ({len(r)} row)'
                except psycopg.errors.LockNotAvailable:
                    result['x_lockable_while_worker_open'] = 'no (lock_timeout)'
                third.rollback()
            # Who holds X? Its xmax names the worker's transaction if the worker locked (or deleted) it.
            result['x_xmax'] = [r[0] for r in obs.execute('SELECT xmax::text FROM task WHERE id = %s', (x,))]
            release.set()
            worker.join(30)
            remaining = obs.execute('SELECT count(*) FROM task').fetchone()[0]
            x_row = obs.execute('SELECT client_host, exit_status, "group" FROM task WHERE id = %s', (x,)).fetchall()
            result['after'] = {'rows_left': remaining, 'x_row': x_row}
    finally:
        holder.close()
    return result


CASES = [
    ('a_delete_by_id',
     "UPDATE task SET client_host = 'holder' WHERE id = %(id)s",
     'DELETE FROM task WHERE id = %(id)s'),
    ('b_delete_filtered',
     "UPDATE task SET client_host = 'holder' WHERE id = %(id)s",
     'DELETE FROM task WHERE exit_status IS NULL'),
    ('c_update_filtered',
     "UPDATE task SET client_host = 'holder' WHERE id = %(id)s",
     'UPDATE task SET "group" = 7 WHERE exit_status IS NULL'),
    ('d_delete_filtered_x_leaves',
     "UPDATE task SET client_host = 'holder', exit_status = 0 WHERE id = %(id)s",
     'DELETE FROM task WHERE exit_status IS NULL'),
    ('e_update_filtered_x_leaves',
     "UPDATE task SET client_host = 'holder', exit_status = 0 WHERE id = %(id)s",
     'UPDATE task SET "group" = 7 WHERE exit_status IS NULL'),
    ('f_delete_by_id_x_leaves',
     "UPDATE task SET client_host = 'holder', exit_status = 0 WHERE id = %(id)s",
     'DELETE FROM task WHERE id = %(id)s AND exit_status IS NULL'),
]


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for name, provider in [('hu03_pg', 'postgres'), ('hu03_ts', 'timescale')]:
        try:
            env = create(name, provider)
            with psycopg.connect(url(name), autocommit=True) as conn:
                ts = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchall()
                hyper = conn.execute("SELECT count(*) FROM pg_class WHERE relname = 'task'").fetchone()
                is_hyper = (conn.execute("SELECT count(*) FROM timescaledb_information.hypertables "
                                         "WHERE hypertable_name = 'task'").fetchone()[0] if ts else 0)
            print(f'=== {name} provider={provider} timescaledb={ts} task_hypertable={bool(is_hyper)}')
            for label, holder_sql, worker_sql in CASES:
                print(case(name, env, label, holder_sql, worker_sql))
        finally:
            drop(name)
    print('dropped hu03_pg, hu03_ts')


if __name__ == '__main__':
    main()
