# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Research probe 03 (hs-update-contended-writes): can a transaction count the task rows it updated by
their `xmin`, through a TimescaleDB hypertable as on a plain table?

    uv run python \
        spec/hs-update-contended-writes/research/probes/03_xmin.py

Creates hu03_xpg (`postgres` provider) and hu03_xts (`timescale`), drops both at the end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg

ADMIN = 'postgresql://postgres:postgres@localhost:5432/postgres'
SCRATCH = Path(os.environ.get('TMPDIR', '/tmp')) / 'hu03'

MINE = {
    'xid8->text->xid': "xmin = CAST(CAST(pg_current_xact_id() AS text) AS xid)",
    'xid8::xid': "xmin = pg_current_xact_id()::xid",
    'txid mod 2^32 (PG13)': "xmin::text = (txid_current() % 4294967296)::text",
}


def url(name: str) -> str:
    return f'postgresql://postgres:postgres@localhost:5432/{name}'


def counts(conn: psycopg.Connection) -> dict:
    out = {}
    for label, cond in MINE.items():
        try:
            with conn.transaction():  # a savepoint, so a failing form does not abort the probe
                out[label] = conn.execute(f'SELECT count(*) FROM task WHERE {cond}').fetchone()[0]
        except psycopg.Error as error:
            out[label] = f'{type(error).__name__}: {str(error).splitlines()[0]}'
    return out


def probe(name: str, provider: str) -> None:
    env = dict(os.environ)
    env.pop('HYPERSHELL_DATABASE_FILE', None)
    env.update({'HYPERSHELL_SITE': str(SCRATCH / 'site-xmin'), 'HYPERSHELL_DATABASE_PROVIDER': provider,
                'HYPERSHELL_DATABASE_HOST': 'localhost', 'HYPERSHELL_DATABASE_PORT': '5432',
                'HYPERSHELL_DATABASE_USER': 'postgres', 'HYPERSHELL_DATABASE_PASSWORD': 'postgres',
                'HYPERSHELL_DATABASE_DATABASE': name})
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS {name}')
        conn.execute(f'CREATE DATABASE {name}')
    try:
        assert subprocess.run(['hs', 'initdb', '--yes'], env=env, capture_output=True).returncode == 0
        path = SCRATCH / f'{name}.in'
        path.write_text(''.join(f'echo xmin {i}\n' for i in range(5)))
        assert subprocess.run(['hs', 'submit', '-f', str(path)], env=env, capture_output=True).returncode == 0
        with psycopg.connect(url(name)) as conn:
            hyper = conn.execute("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'").fetchone()[0] and \
                conn.execute("SELECT count(*) FROM timescaledb_information.hypertables "
                             "WHERE hypertable_name = 'task'").fetchone()[0]
            print(f'=== {name} provider={provider} hypertable={bool(hyper)}')
            ids = [r[0] for r in conn.execute('SELECT id FROM task ORDER BY args')]
            print('    SELECT xmin FROM task (3 rows):', conn.execute('SELECT xmin::text FROM task LIMIT 3').fetchall())
            print('    before any write in this transaction:', counts(conn))
            n = conn.execute('UPDATE task SET "group" = 7 WHERE id = ANY(%s)', (ids[:2],)).rowcount
            print(f'    after UPDATE of {n} rows:', counts(conn))
            with conn.transaction():  # a savepoint: rows it updates get the subtransaction's xid
                n2 = conn.execute('UPDATE task SET "group" = 8 WHERE id = %s', (ids[2],)).rowcount
                print(f'    inside a savepoint after UPDATE of {n2} more row:', counts(conn))
            print('    after the savepoint released:', counts(conn))
            conn.execute('SELECT id FROM task WHERE id = %s FOR UPDATE', (ids[3],))
            print('    after SELECT FOR UPDATE of a 4th row (lock only):', counts(conn))
            conn.rollback()
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    probe('hu03_xpg', 'postgres')
    probe('hu03_xts', 'timescale')
    print('dropped hu03_xpg, hu03_xts')


if __name__ == '__main__':
    main()
