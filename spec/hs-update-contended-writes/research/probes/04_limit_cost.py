# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probe: the cost of `hs update --limit N` as one transaction of per-row UPDATEs, against batches of 100.

Run from the repository root (sandbox off for PostgreSQL):

    uv run python spec/hs-update-contended-writes/research/probes/04_limit_cost.py sqlite
    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
        uv run python spec/hs-update-contended-writes/research/probes/04_limit_cost.py postgres

Builds the `task` table from the model (`create_all`, plain table, no hypertable), inserts N rows, and
times (a) today's shape, `bulk_update_mappings` per batch of 100 with a commit per batch, and (b) the
planned shape, one `UPDATE task SET ... WHERE id = :id AND exit_status IS NULL` per row with one
commit, which holds every written row's lock (SQLite: the database write lock) until that commit.
On SQLite it also measures how long another connection's write waits meanwhile.
"""

from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime

from sqlalchemy import create_engine, text, update, bindparam
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as OrmSession

from hypershell.data.model import Entity, Task

KIND = sys.argv[1] if len(sys.argv) > 1 else 'sqlite'
SIZES = [1_000, 10_000, 100_000] if KIND == 'sqlite' else [1_000, 10_000]


def make_engine():
    if KIND == 'sqlite':
        path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'hu01/limit_cost.db')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.replace(path, path + '.old')
        return create_engine(f'sqlite:///{path}')
    admin = create_engine(os.environ['HS_TEST_POSTGRES'], isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS hu01_cost'))
        conn.execute(text('CREATE DATABASE hu01_cost'))
    admin.dispose()
    return create_engine(make_url(os.environ['HS_TEST_POSTGRES']).set(database='hu01_cost'))


def fill(engine, n: int) -> list:
    with engine.begin() as conn:
        conn.execute(Task.__table__.delete())
    with OrmSession(engine) as session:
        tasks = [Task.new(args=f'echo {i}') for i in range(n)]
        session.add_all(tasks)
        session.commit()
        return [t.id for t in tasks]


def main() -> None:
    engine = make_engine()
    Entity.metadata.create_all(engine)
    table = Task.__table__
    per_row = (update(table).where(table.c.id == bindparam('r_id', type_=table.c.id.type),
                                   table.c.exit_status.is_(None))
               .values(group=bindparam('r_group', type_=table.c.group.type)))
    try:
        for n in SIZES:
            ids = fill(engine, n)
            with OrmSession(engine) as session:
                start = time.perf_counter()
                for i in range(0, n, 100):
                    session.bulk_update_mappings(Task, [{'id': id, 'group': 7} for id in ids[i:i + 100]])
                    session.commit()
                batches = time.perf_counter() - start
            ids = fill(engine, n)
            waited = []

            def other_writer(started: threading.Event) -> None:
                started.wait()
                time.sleep(0.05)
                begin = time.perf_counter()
                with engine.begin() as conn:
                    conn.execute(text('UPDATE task SET client_host = :h WHERE id = :id'), {'h': 'x', 'id': ids[0]})
                waited.append(time.perf_counter() - begin)

            started = threading.Event()
            thread = threading.Thread(target=other_writer, args=(started, ))
            thread.start()
            error = None
            with OrmSession(engine) as session:
                start = time.perf_counter()
                landed = 0
                for id in ids:
                    landed += session.execute(per_row, {'r_id': id, 'r_group': 8}).rowcount
                    if landed == 1:
                        started.set()
                session.commit()
                one_txn = time.perf_counter() - start
            thread.join(60)
            print(f'{KIND} N={n}: batches of 100 {batches:.2f}s; one transaction per-row {one_txn:.2f}s '
                  f'({landed} landed; {1e6 * one_txn / n:.0f} us/row); another writer waited '
                  f'{waited[0] if waited else float("nan"):.2f}s {error or ""}')
    finally:
        engine.dispose()
        if KIND != 'sqlite':
            admin = create_engine(os.environ['HS_TEST_POSTGRES'], isolation_level='AUTOCOMMIT')
            with admin.connect() as conn:
                conn.execute(text('DROP DATABASE IF EXISTS hu01_cost WITH (FORCE)'))
            admin.dispose()


if __name__ == '__main__':
    main()
