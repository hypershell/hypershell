# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Research probe 03 (hs-update-contended-writes): can an `hs update` subprocess be the write in a
`contend`-style real race? A holder keeps one task row locked; a thread runs `hs update ... -f` as a
subprocess (as `tests.main` does); the observer must see the subprocess's statement waiting on the
holder in `pg_stat_activity`, with the statement matched by the widened regex; then the holder commits
and the subprocess finishes.

    uv run python \
        spec/hs-update-contended-writes/research/probes/03_cli_delete_blocked.py [postgres|timescale]

Creates the database hu03_cli and drops it at the end.
"""

from __future__ import annotations

import re
import os
import sys
import time
import threading
import subprocess
from pathlib import Path

import psycopg

ADMIN = 'postgresql://postgres:postgres@localhost:5432/postgres'
NAME = 'hu03_cli'
URL = f'postgresql://postgres:postgres@localhost:5432/{NAME}'
SCRATCH = Path(os.environ.get('TMPDIR', '/tmp')) / 'hu03'

# The widened `contend` regex: a blocked UPDATE of task or DELETE FROM task, with or without a schema.
WRITE_TASK = re.compile(r'^\s*(UPDATE|DELETE\s+FROM)\s+(\S+\.)?"?task"?\s', re.I)
UPDATE_TASK = re.compile(r'^\s*UPDATE\s+(\S+\.)?"?task"?\s', re.I)  # today's

PROVIDER = sys.argv[1] if len(sys.argv) > 1 else 'timescale'
ENV = dict(os.environ)
ENV.pop('HYPERSHELL_DATABASE_FILE', None)
ENV.update({'HYPERSHELL_SITE': str(SCRATCH / 'site-cli'), 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG',
            'HYPERSHELL_DATABASE_PROVIDER': PROVIDER, 'HYPERSHELL_DATABASE_HOST': 'localhost',
            'HYPERSHELL_DATABASE_PORT': '5432', 'HYPERSHELL_DATABASE_USER': 'postgres',
            'HYPERSHELL_DATABASE_PASSWORD': 'postgres', 'HYPERSHELL_DATABASE_DATABASE': NAME})


def hs(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['hs', *args], env=ENV, capture_output=True, text=True)


def seed(n: int = 3) -> list[str]:
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute('DELETE FROM task')
    path = SCRATCH / 'cli.in'
    path.write_text(''.join(f'echo cli {i}\n' for i in range(n)))
    proc = hs("submit", "--repeat", "-f", str(path))
    assert proc.returncode == 0, proc.stderr[-1500:]
    with psycopg.connect(URL, autocommit=True) as conn:
        return [str(r[0]) for r in conn.execute('SELECT id FROM task ORDER BY submit_time, args')]


def race(label: str, argv: list[str]) -> None:
    x = seed()[0]
    out: dict = {}

    def work() -> None:
        proc = hs(*argv)
        out.update(rc=proc.returncode, info=[line for line in proc.stderr.splitlines()
                                             if 'Deleted' in line or 'Updated' in line or 'Traceback' in line
                                             or 'ERROR' in line or 'CRITICAL' in line])

    holder = psycopg.connect(URL)
    seen = None
    try:
        hpid = holder.info.backend_pid
        assert holder.execute("UPDATE task SET client_host = 'holder' WHERE id = %s", (x,)).rowcount == 1
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        deadline = time.monotonic() + 30
        with psycopg.connect(URL, autocommit=True) as obs:
            while time.monotonic() < deadline and seen is None and worker.is_alive():
                for wait_type, query in obs.execute(
                        'SELECT wait_event_type, query FROM pg_stat_activity WHERE datname = current_database() '
                        'AND %s = ANY(pg_blocking_pids(pid))', (hpid,)).fetchall():
                    if wait_type == 'Lock':
                        seen = (query, bool(WRITE_TASK.match(query or '')), bool(UPDATE_TASK.match(query or '')))
                time.sleep(0.02)
        holder.commit()
    finally:
        holder.close()
    worker.join(60)
    with psycopg.connect(URL, autocommit=True) as conn:
        left = conn.execute('SELECT count(*) FROM task').fetchone()[0]
        grouped = conn.execute('SELECT count(*) FROM task WHERE "group" = 7').fetchone()[0]
    print(f'--- {label}: hs {" ".join(argv)}')
    print(f'    blocked statement (query, widened regex, today regex): {seen}')
    print(f'    worker alive after join: {worker.is_alive()}; result: {out}; rows left: {left}; group=7: {grouped}')


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {NAME}')
        conn.execute(f'CREATE DATABASE {NAME}')
    try:
        assert hs('initdb', '--yes').returncode == 0
        with psycopg.connect(URL, autocommit=True) as conn:
            ts = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchall()
        print(f'=== provider={PROVIDER} timescaledb={ts}')
        race('filtered delete', ['update', '--delete', '-f', '-R'])
        race('--limit delete', ['update', '--delete', '-f', '-R', '--limit', '3'])
        race('filtered update', ['update', '-f', '-R', 'group=7'])
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS {NAME} WITH (FORCE)')
        print(f'dropped {NAME}')


if __name__ == '__main__':
    main()
