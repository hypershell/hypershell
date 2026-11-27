# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probe the Scheduler's retry path on develop's code, on a throwaway SQLite file.

Run: uv run python spec/contended-writes/research/probes/retry_path.py
(from the repository root; it never touches a configured site database).

Prints, for one failed row and attempts=2:
  1. the statements the retry path runs (INSERT/UPDATE order, one commit or two);
  2. what a contended mark does today (an injected 0-row match at the driver);
  3. what a stop between the retry insert and the mark leaves behind;
  4. whether the eager path hands the fresh retry row out twice (select_new re-reads it).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, text

from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task


class Killed(BaseException):
    """The process stopping mid-call (never caught by `except Exception`)."""


def fresh() -> object:
    path = os.path.join(tempfile.mkdtemp(prefix='hs-probe-'), 'tasks.db')
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    return engine


def failed_row(engine) -> str:
    task = Task.new(args='false')
    Task.add(task)
    [copy] = Task.next(limit=1)
    now = datetime.now().astimezone()
    with engine.begin() as conn:
        conn.execute(text('UPDATE task SET completion_time = :t, exit_status = 1 WHERE id = :id'),
                     {'t': now + timedelta(seconds=1), 'id': copy.id})
    return copy.id


def rows(engine) -> list:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            'SELECT substr(id,-8), attempt, retried, substr(coalesce(previous_id,\'-\'),-8), '
            'substr(coalesce(next_id,\'-\'),-8), schedule_time IS NOT NULL FROM task ORDER BY submit_time'))]


def probe_statements() -> None:
    engine = fresh()
    failed_row(engine)
    seen = []

    @event.listens_for(engine, 'before_cursor_execute')
    def record(conn, cursor, statement, params, context, executemany):
        seen.append(statement.split()[0] + (' (executemany)' if executemany else ''))

    @event.listens_for(engine, 'commit')
    def commits(conn):
        seen.append('COMMIT')

    tasks = Task.next(limit=5, attempts=2)
    print('1. statements:', ' -> '.join(seen))
    print('   returned', len(tasks), 'task(s); rows:', rows(engine))


def probe_contended_mark() -> None:
    engine = fresh()
    failed = failed_row(engine)

    @event.listens_for(engine, 'do_execute')
    def lose(cursor, statement, params, context):
        if statement.startswith('UPDATE task SET retried'):
            cursor.execute('UPDATE task SET id = id WHERE 0')  # matches 0 rows, as on a 2.29+ hypertable
            return True
        return False

    @event.listens_for(engine, 'do_executemany')
    def lose_many(cursor, statement, params, context):
        if statement.startswith('UPDATE task SET retried'):
            cursor.execute('UPDATE task SET id = id WHERE 0')
            return True
        return False

    try:
        Task.next(limit=5, attempts=2)
        print('2. contended mark: no error')
    except Exception as error:  # noqa: BLE001
        print(f'2. contended mark: {type(error).__name__}: {str(error)[:90]}')
    Session.rollback()
    event.remove(engine, 'do_execute', lose)
    event.remove(engine, 'do_executemany', lose_many)
    print('   rows after:', rows(engine))
    tasks = Task.next(limit=5, attempts=2)
    print('   next round retried it again:', [t.previous_id[-8:] for t in tasks if t.previous_id], 'failed', failed[-8:])
    print('   rows after next round:', rows(engine))


def probe_stop_between() -> None:
    engine = fresh()
    failed_row(engine)

    @event.listens_for(engine, 'before_cursor_execute')
    def stop(conn, cursor, statement, params, context, executemany):
        if statement.startswith('UPDATE task SET retried'):
            raise Killed()

    try:
        Task.next(limit=5, attempts=2)
    except Killed:
        pass
    Session.remove()  # the connection goes away, as with a killed process
    event.remove(engine, 'before_cursor_execute', stop)
    print('3. stop between insert and mark, rows left:', rows(engine))


def probe_eager_duplicate() -> None:
    engine = fresh()
    failed_row(engine)
    tasks = Task.next(limit=5, attempts=2, eager=True)
    ids = [t.id[-8:] for t in tasks]
    print('4. eager next returned', ids, '(duplicate)' if len(ids) != len(set(ids)) else '(no duplicate)')


if __name__ == '__main__':
    try:
        probe_statements()
        probe_contended_mark()
        probe_stop_between()
        probe_eager_duplicate()
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
    sys.exit(0)
