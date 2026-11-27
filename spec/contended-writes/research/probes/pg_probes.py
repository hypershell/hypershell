# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probes on a local PostgreSQL server (with TimescaleDB, when installed) for the contended-writes plan.

Run (sandbox off, from the repository root):
    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
        uv run --extra postgres python spec/contended-writes/research/probes/pg_probes.py

Creates and drops its own databases (hs_probe_cw_pg, hs_probe_cw_ts). Two probes per database:

lock  Does a conditional UPDATE that waited on another writer's row lock, and whose re-check then
      failed (0 rows), keep holding that row's lock until its own transaction ends? This is the
      only way an in-server writer (all conditional on `completion_time IS NULL`) could still hold
      a failed row when the Scheduler marks it `retried`.
source  Two sessions both miss the reserved source row, then both insert it: a plain INSERT (today)
      and INSERT ... ON CONFLICT (id) DO NOTHING (the plan), each followed by a re-read.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from hypershell.data.model import Entity, Source, DIRECT_SOURCE_ID, RESERVED_SOURCE_PATHS

ADMIN = os.environ['HS_TEST_POSTGRES']
X = '018f0000-0000-7000-8000-000000000001'


def database(admin, name: str, timescale: bool):
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS {name}'))
        conn.execute(text(f'CREATE DATABASE {name}'))
    engine = create_engine(make_url(ADMIN).set(database=name))
    with engine.begin() as conn:
        if timescale:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
        conn.execute(text('CREATE TABLE t (id uuid NOT NULL, ts timestamptz NOT NULL, completion_time timestamptz, '
                          'exit_status int, client_id text, retried boolean NOT NULL DEFAULT false)'))
        if timescale:
            conn.execute(text("SELECT create_hypertable('t', by_range('ts'))"))
            version = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")).scalar()
            print(f'  timescaledb {version}, t is a hypertable')
        conn.execute(text('INSERT INTO t (id, ts) VALUES (:id, now())'), {'id': X})
    Entity.metadata.create_all(engine)
    return engine


def lock_probe(engine) -> None:
    receiver, confirm, scheduler = engine.connect(), engine.connect(), engine.connect()
    result = {}
    try:
        receiver.execute(text('UPDATE t SET completion_time = now(), exit_status = 1 WHERE id = :id'), {'id': X})

        def late_confirm():
            result['rows'] = confirm.execute(
                text("UPDATE t SET client_id = 'c' WHERE id = :id AND completion_time IS NULL"), {'id': X}).rowcount

        worker = threading.Thread(target=late_confirm)
        worker.start()
        time.sleep(0.5)  # the late confirm is now waiting on the result's row lock
        receiver.commit()
        worker.join()
        print(f'  late confirm after the result committed matched {result["rows"]} row(s); its transaction stays open')
        scheduler.execute(text("SET lock_timeout = '500ms'"))
        try:
            rows = scheduler.execute(text('UPDATE t SET retried = true WHERE id = :id AND retried IS FALSE'),
                                     {'id': X}).rowcount
            print(f'  retried mark while the late confirm is open: matched {rows} (no wait)')
        except OperationalError as error:
            print(f'  retried mark while the late confirm is open: blocked ({type(error.orig).__name__}) '
                  f'- the failed re-check kept the row lock')
            scheduler.rollback()
        confirm.commit()
        scheduler.execute(text("SET lock_timeout = '500ms'"))
        rows = scheduler.execute(text('UPDATE t SET retried = true WHERE id = :id AND retried IS FALSE'),
                                 {'id': X}).rowcount
        print(f'  retried mark once the late confirm committed: matched {rows}')
        scheduler.rollback()
        # Control: a conditional write that starts after the result committed never locks the row.
        rows = confirm.execute(text("UPDATE t SET client_id = 'c' WHERE id = :id AND completion_time IS NULL"),
                               {'id': X}).rowcount
        scheduler.execute(text("SET lock_timeout = '500ms'"))
        try:
            mark = scheduler.execute(text('UPDATE t SET retried = true WHERE id = :id AND retried IS FALSE'),
                                     {'id': X}).rowcount
            print(f'  control: a confirm begun after the result committed matched {rows}; '
                  f'the mark beside its open transaction matched {mark} (no wait)')
        except OperationalError as error:
            print(f'  control: the mark waited on a confirm begun after the result ({type(error.orig).__name__})')
        scheduler.rollback()
        confirm.rollback()
    finally:
        for conn in (receiver, confirm, scheduler):
            conn.close()


def source_probe(engine, conflict_safe: bool) -> None:
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM source'))
    table = Source.__table__
    values = {'id': DIRECT_SOURCE_ID, 'path': RESERVED_SOURCE_PATHS[DIRECT_SOURCE_ID],
              'created': datetime.now().astimezone()}
    statement = pg_insert(table).values(**values)
    if conflict_safe:
        statement = statement.on_conflict_do_nothing(index_elements=['id'])
    first, second = engine.connect(), engine.connect()
    outcome = {}
    try:
        for conn in (first, second):
            assert conn.execute(select(table.c.id).where(table.c.id == DIRECT_SOURCE_ID)).first() is None
        first.execute(statement)

        def other():
            try:
                outcome['rows'] = second.execute(statement).rowcount
                second.commit()
                outcome['found'] = second.execute(select(table.c.id).where(table.c.id == DIRECT_SOURCE_ID)).first()
            except IntegrityError as error:
                outcome['error'] = type(error.orig).__name__

        worker = threading.Thread(target=other)
        worker.start()
        time.sleep(0.5)  # the second insert waits on the first's uncommitted key
        first.commit()
        worker.join()
        label = 'ON CONFLICT DO NOTHING' if conflict_safe else 'plain INSERT'
        if 'error' in outcome:
            print(f'  source, {label}: second session failed with {outcome["error"]}')
        else:
            print(f'  source, {label}: second session inserted {outcome["rows"]} row(s), '
                  f're-read found the row: {outcome["found"] is not None}')
    finally:
        first.close()
        second.close()


def main() -> int:
    admin = create_engine(ADMIN, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        print('server:', conn.execute(text('SHOW server_version')).scalar())
        has_ts = conn.execute(text("SELECT count(*) FROM pg_available_extensions WHERE name = 'timescaledb'")).scalar()
    cases = [('hs_probe_cw_pg', False)] + ([('hs_probe_cw_ts', True)] if has_ts else [])
    for name, timescale in cases:
        print(f'{name}:')
        engine = database(admin, name, timescale)
        try:
            lock_probe(engine)
            source_probe(engine, conflict_safe=False)
            source_probe(engine, conflict_safe=True)
        finally:
            engine.dispose()
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS {name}'))
    admin.dispose()
    return 0


if __name__ == '__main__':
    sys.exit(main())
