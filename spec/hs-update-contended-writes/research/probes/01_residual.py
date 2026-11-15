# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probe: tell a set-based UPDATE/DELETE's missed row from a row this transaction wrote, on PostgreSQL.

Run from the repository root, sandbox off, with a local server:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
        uv run python spec/hs-update-contended-writes/research/probes/01_residual.py [postgres|timescale]

Creates database `hu01_<provider>` with `hs initdb` (via the CLI, env-configured), submits six tasks,
and installs a BEFORE ROW trigger that skips the UPDATE or DELETE of one chosen row (`RETURN NULL`):
a deterministic stand-in for TimescaleDB 2.29-2.31's contended 0-row match, which skips a row that
still qualifies. Then, in one transaction:

1. the set-based UPDATE's rowcount;
2. the residual: rows still matching the filter that this transaction did not write, by `xmin`;
3. a per-row re-run with the user filter as the condition, once the trigger lets the row through;

and the same for a set-based DELETE (residual: rows still matching the filter). Also checks that
`RETURNING` and `xmin` agree, and measures the statement count. Drops the database at the end.
"""

from __future__ import annotations

import os
import sys
import subprocess
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ADMIN = os.environ.get('HS_TEST_POSTGRES', 'postgresql+psycopg://postgres:postgres@localhost:5432/postgres')
PROVIDER = sys.argv[1] if len(sys.argv) > 1 else 'postgres'
NAME = f'hu01_{PROVIDER}'
SITE = os.path.join(os.environ.get('TMPDIR', '/tmp'), f'hu01/site-{PROVIDER}')

# The transaction's own xid (32-bit), as `xmin` shows it; works on PostgreSQL 13+ (txid_current is
# deprecated but present through 18, and pg_current_xact_id is 13+).
MINE = 'CAST(CAST(task.xmin AS text) AS bigint) <> txid_current() % 4294967296'
MINE_13 = 'task.xmin <> CAST(CAST(CAST(CAST(pg_current_xact_id() AS text) AS bigint) % 4294967296 AS text) AS xid)'  # alternative spelling

TRIGGER = """
CREATE TABLE skip_once (id uuid PRIMARY KEY, times int NOT NULL);
CREATE FUNCTION skip_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE skip_once SET times = times - 1 WHERE id = OLD.id AND times > 0;
  IF FOUND THEN RETURN NULL; END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER skip_row BEFORE UPDATE OR DELETE ON task FOR EACH ROW EXECUTE FUNCTION skip_row();
"""


def cli(*argv: str, stdin: str = None) -> str:
    url = make_url(ADMIN)
    env = {**os.environ, 'HYPERSHELL_SITE': SITE, 'HYPERSHELL_DATABASE_PROVIDER': PROVIDER,
           'HYPERSHELL_DATABASE_HOST': url.host, 'HYPERSHELL_DATABASE_PORT': str(url.port or 5432),
           'HYPERSHELL_DATABASE_USER': url.username, 'HYPERSHELL_DATABASE_PASSWORD': url.password or '',
           'HYPERSHELL_DATABASE_DATABASE': NAME, 'HYPERSHELL_LOGGING_LEVEL': 'INFO'}
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    proc = subprocess.run(['uv', 'run', 'hs', *argv], input=stdin, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f'hs {argv} failed: {proc.stderr}')
    return proc.stdout + proc.stderr


def main() -> None:
    os.makedirs(SITE, exist_ok=True)
    admin = create_engine(ADMIN, isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS {NAME}'))
        conn.execute(text(f'CREATE DATABASE {NAME}'))
    try:
        cli('initdb', '--yes')
        cli('submit', '-f', '-', stdin=''.join(f'echo {n}\n' for n in range(6)))
        engine = create_engine(make_url(ADMIN).set(database=NAME))
        with engine.begin() as conn:
            hyper = conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")).scalar()
            if hyper:
                hyper = conn.execute(text("SELECT count(*) FROM timescaledb_information.hypertables "
                                          "WHERE hypertable_name = 'task'")).scalar()
            print(f'provider={PROVIDER} hypertable={bool(hyper)} '
                  f'server={conn.execute(text("SHOW server_version")).scalar()}')
            conn.execute(text(TRIGGER))
            ids = [str(i) for i in conn.execute(text('SELECT id FROM task ORDER BY id')).scalars()]
            # Rows 0-1 get group 1, so the filter `"group" = 0` selects rows 2-5.
            conn.execute(text('UPDATE task SET "group" = 1 WHERE id IN (:a, :b)'), {'a': ids[0], 'b': ids[1]})
        lost = ids[3]

        # --- UPDATE that keeps written rows in the filter (a tag change on `"group" = 0`)
        with engine.connect() as conn:
            conn.execute(text('INSERT INTO skip_once VALUES (:id, 1)'), {'id': lost})
            n = conn.execute(text('UPDATE task SET client_host = \'w\' WHERE "group" = 0')).rowcount
            residual = [str(i) for i in conn.execute(text(f'SELECT id FROM task WHERE "group" = 0 AND {MINE}')).scalars()]
            residual13 = [str(i) for i in conn.execute(text(f'SELECT id FROM task WHERE "group" = 0 AND {MINE_13}')).scalars()]
            written = conn.execute(text(f'SELECT count(*) FROM task WHERE NOT ({MINE})')).scalar()
            rerun = conn.execute(text('UPDATE task SET client_host = \'w\' WHERE id = :id AND "group" = 0'),
                                 {'id': lost}).rowcount
            after = conn.execute(text(f'SELECT count(*) FROM task WHERE "group" = 0 AND {MINE}')).scalar()
            conn.rollback()
        print(f'UPDATE kept-in-filter: rowcount={n} (4 match, 1 skipped) residual={residual == [lost]} '
              f'residual(pg_current_xact_id form)={residual13 == [lost]} rows-written-by-xmin={written} '
              f'rerun={rerun} residual-after-rerun={after}')

        # --- UPDATE that takes written rows out of the filter (a cancel on `exit_status IS NULL`)
        with engine.connect() as conn:
            conn.execute(text('INSERT INTO skip_once VALUES (:id, 1)'), {'id': lost})
            n = conn.execute(text('UPDATE task SET exit_status = -1, schedule_time = now(), completion_time = now() '
                                  'WHERE exit_status IS NULL AND "group" = 0')).rowcount
            residual = [str(i) for i in conn.execute(
                text(f'SELECT id FROM task WHERE exit_status IS NULL AND "group" = 0 AND {MINE}')).scalars()]
            conn.rollback()
        print(f'UPDATE out-of-filter: rowcount={n} residual={residual == [lost]}')

        # --- RETURNING agrees with xmin
        with engine.connect() as conn:
            ret = sorted(str(i) for i in conn.execute(
                text('UPDATE task SET client_host = \'w\' WHERE "group" = 0 RETURNING id')).scalars())
            by_xmin = sorted(str(i) for i in conn.execute(text(f'SELECT id FROM task WHERE NOT ({MINE})')).scalars())
            conn.rollback()
        print(f'RETURNING == xmin-written: {ret == by_xmin} ({len(ret)} rows)')

        # --- DELETE: the residual needs no xmin, a deleted row is gone for this transaction
        with engine.connect() as conn:
            conn.execute(text('INSERT INTO skip_once VALUES (:id, 1)'), {'id': lost})
            n = conn.execute(text('DELETE FROM task WHERE "group" = 0')).rowcount
            residual = [str(i) for i in conn.execute(text('SELECT id FROM task WHERE "group" = 0')).scalars()]
            rerun = conn.execute(text('DELETE FROM task WHERE id = :id AND "group" = 0'), {'id': lost}).rowcount
            conn.rollback()
        print(f'DELETE: rowcount={n} residual={residual == [lost]} rerun={rerun}')
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {NAME} WITH (FORCE)'))
        admin.dispose()


if __name__ == '__main__':
    main()
