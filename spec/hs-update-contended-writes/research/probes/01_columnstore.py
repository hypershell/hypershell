# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Probe: does the `xmin` residual read work on columnstore (compressed) chunks of the task hypertable?

Run from the repository root, sandbox off, with a local TimescaleDB (Community) server:

    HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres \
        uv run python spec/hs-update-contended-writes/research/probes/01_columnstore.py

Creates `hu01_cs` under the `timescale` provider with `hs initdb`, submits six tasks, converts every
chunk to the columnstore (`compress_chunk`), then runs, each in its own rolled-back transaction:
the `xmin` residual read alone; a set-based UPDATE followed by it; the same with `RETURNING id`; and
a set-based DELETE followed by the plain residual read. Drops the database at the end.
"""

from __future__ import annotations

import os
import subprocess
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ADMIN = os.environ.get('HS_TEST_POSTGRES', 'postgresql+psycopg://postgres:postgres@localhost:5432/postgres')
NAME = 'hu01_cs'
SITE = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'hu01/site-cs')
MINE = 'CAST(CAST(task.xmin AS text) AS bigint) <> txid_current() % 4294967296'


def cli(*argv: str, stdin: str = None) -> None:
    url = make_url(ADMIN)
    env = {**os.environ, 'HYPERSHELL_SITE': SITE, 'HYPERSHELL_DATABASE_PROVIDER': 'timescale',
           'HYPERSHELL_DATABASE_HOST': url.host, 'HYPERSHELL_DATABASE_PORT': str(url.port or 5432),
           'HYPERSHELL_DATABASE_USER': url.username, 'HYPERSHELL_DATABASE_PASSWORD': url.password or '',
           'HYPERSHELL_DATABASE_DATABASE': NAME, 'HYPERSHELL_LOGGING_LEVEL': 'WARNING'}
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    proc = subprocess.run(['uv', 'run', 'hs', *argv], input=stdin, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise SystemExit(f'hs {argv} failed: {proc.stderr}')


def attempt(engine, label: str, *statements: str) -> None:
    with engine.connect() as conn:
        try:
            out = []
            for sql in statements:
                result = conn.execute(text(sql))
                out.append(result.rowcount if not result.returns_rows else len(result.all()))
            print(f'{label}: ok {out}')
        except Exception as error:  # noqa: BLE001 (a probe reports whatever the server says)
            print(f'{label}: {type(error.orig).__name__ if hasattr(error, "orig") else type(error).__name__}: '
                  f'{str(getattr(error, "orig", error)).splitlines()[0]}')
        finally:
            conn.rollback()


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
            print('version', conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")).scalar())
            chunks = conn.execute(text("SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('task') c")).all()
            print('compressed chunks', len(chunks), conn.execute(text(
                "SELECT count(*) FROM timescaledb_information.chunks WHERE hypertable_name='task' AND is_compressed")).scalar())
        attempt(engine, 'residual read alone', f'SELECT id FROM task WHERE exit_status IS NULL AND {MINE}')
        attempt(engine, 'update + residual', 'UPDATE task SET "group" = 7 WHERE exit_status IS NULL',
                f'SELECT id FROM task WHERE exit_status IS NULL AND {MINE}')
        attempt(engine, 'update RETURNING id', 'UPDATE task SET "group" = 7 WHERE exit_status IS NULL RETURNING id')
        attempt(engine, 'xmin with tableoid', 'SELECT tableoid::regclass, xmin FROM task')
        attempt(engine, 'delete + residual', 'DELETE FROM task WHERE exit_status IS NULL',
                'SELECT id FROM task WHERE exit_status IS NULL')
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS {NAME} WITH (FORCE)'))
        admin.dispose()


if __name__ == '__main__':
    main()
