# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probe the reserved source row's get-or-create on SQLite: fresh, and a 2.8.3 database upgraded in place.

Run (from the repository root): uv run python spec/contended-writes/research/probes/sqlite_source.py

Two connections both miss the `<stdin>` row, then both insert it: a plain INSERT (today) and
INSERT ... ON CONFLICT (id) DO NOTHING (the plan), each followed by a re-read. The second insert
waits on SQLite's write lock (the driver's busy timeout) until the first commits.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from hypershell.data.model import Entity, Source, STDIN_SOURCE_ID, RESERVED_SOURCE_PATHS
from hypershell.data.upgrade import upgrade_schema

FIXTURE = os.path.join('tests', 'fixtures', 'hypershell-2.8.3.db')


def race(engine, conflict_safe: bool) -> str:
    table = Source.__table__
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM source'))
    values = {'id': STDIN_SOURCE_ID, 'path': RESERVED_SOURCE_PATHS[STDIN_SOURCE_ID],
              'created': datetime.now().astimezone()}
    statement = sqlite_insert(table).values(**values)
    if conflict_safe:
        statement = statement.on_conflict_do_nothing(index_elements=['id'])
    first, second = engine.connect(), engine.connect()
    outcome = {}
    try:
        for conn in (first, second):
            assert conn.execute(select(table.c.id).where(table.c.id == STDIN_SOURCE_ID)).first() is None
            conn.commit()
        first.execute(statement)

        def other():
            try:
                outcome['rows'] = second.execute(statement).rowcount
                second.commit()
                outcome['found'] = second.execute(select(table.c.id).where(table.c.id == STDIN_SOURCE_ID)).first()
            except IntegrityError as error:
                outcome['error'] = str(error.orig)

        worker = threading.Thread(target=other)
        worker.start()
        time.sleep(0.5)
        first.commit()
        worker.join()
    finally:
        first.close()
        second.close()
    if 'error' in outcome:
        return f'second session failed: IntegrityError ({outcome["error"]})'
    return f'second session inserted {outcome["rows"]} row(s); re-read found the row: {outcome["found"] is not None}'


def main() -> int:
    print('sqlite', sqlite3.sqlite_version)
    workdir = tempfile.mkdtemp(prefix='hs-probe-source-')
    fresh = os.path.join(workdir, 'fresh.db')
    upgraded = os.path.join(workdir, 'upgraded-2.8.3.db')
    shutil.copy(FIXTURE, upgraded)
    for label, path in (('fresh', fresh), ('2.8.3 upgraded', upgraded)):
        engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False})
        if label == 'fresh':
            Entity.metadata.create_all(engine)
        else:
            with engine.connect() as conn:
                before = conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'source'")).scalar()
            steps = upgrade_schema(engine)
            created = [step for step in steps if 'source' in step]
            print(f'{label}: source table before upgrade: {bool(before)}; upgrade steps naming source: {created}')
        with engine.connect() as conn:
            pk = [row[1] for row in conn.execute(text('PRAGMA table_info(source)')) if row[5]]
            rows = conn.execute(text('SELECT count(*) FROM source')).scalar()
        print(f'{label}: source primary key {pk}, {rows} row(s)')
        print(f'{label}, plain INSERT: {race(engine, False)}')
        print(f'{label}, ON CONFLICT DO NOTHING: {race(engine, True)}')
        engine.dispose()
    shutil.rmtree(workdir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
