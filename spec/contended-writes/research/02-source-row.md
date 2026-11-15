# 02 — The reserved source row: get-or-create under concurrent first use

Read and probed on `92af9c1`. Probes: [`probes/sqlite_source.py`](probes/sqlite_source.py),
[`probes/pg_probes.py`](probes/pg_probes.py) (its `source` part), and the R4 drive
[`../drive/concurrent_submit.sh`](../drive/concurrent_submit.sh).

## Today

`Source.reserved` (`model.py:1594-1606`): `from_id(caching=False)`; on `NotFound`, `cls.add(...)`
(`add_all`: add, commit, rollback and re-raise on error). Two sessions that both miss both insert;
the second fails on the primary key. Callers: `hs submit` (`submit.py:1480`, `:1487`, `:1511`) and
`hs cluster` (`cluster/__init__.py:511`, `:538`), each at setup, with nothing else pending in the
session.

## The table on every database

- Fresh: `Entity.metadata.create_all` → `source.id` is the primary key.
- Upgraded 2.8.3 (SQLite fixture `tests/fixtures/hypershell-2.8.3.db`, which has no `source` table):
  `upgrade_schema` creates it from `CreateTable(table, if_not_exists=True)` (`data/upgrade.py:_plan`),
  so it carries the same primary key; on PostgreSQL the same code path runs. Probe:
  `2.8.3 upgraded: source primary key ['id'], 0 row(s)`.
- TimescaleDB provider: only `task` becomes a hypertable (`data/timescale.py`); `source` is a plain
  table, so `ON CONFLICT (id)` needs no partition column.

## Probes (two connections both miss, then both insert; the second waits on the first's commit)

```
sqlite 3.53.1
fresh, plain INSERT: second session failed: IntegrityError (UNIQUE constraint failed: source.id)
fresh, ON CONFLICT DO NOTHING: second session inserted 0 row(s); re-read found the row: True
2.8.3 upgraded, plain INSERT: second session failed: IntegrityError (UNIQUE constraint failed: source.id)
2.8.3 upgraded, ON CONFLICT DO NOTHING: second session inserted 0 row(s); re-read found the row: True
hs_probe_cw_pg (PostgreSQL 18.7):
  source, plain INSERT: second session failed with UniqueViolation
  source, ON CONFLICT DO NOTHING: second session inserted -1 row(s), re-read found the row: True
hs_probe_cw_ts (TimescaleDB 2.32.0):
  (the same two lines)
```

The PostgreSQL `rowcount` of the conflict-skipping INSERT came back `-1`, so the design never reads
it: it always re-reads the row after the INSERT.

## The before, from the R4 drive (6 concurrent `hs submit -f -` per round, 5 tasks each)

```
concurrent_submit: sqlite, 20 rounds of 6 submits: 3 failed        (IntegrityError, 25 of 30 tasks)
concurrent_submit: sqlite-283, 10 rounds of 6 submits: 2 failed    (IntegrityError, 32 of 37 tasks)
concurrent_submit: postgres, 10 rounds of 6 submits: 7 failed      (UniqueViolation, 25 of 30 tasks)
```

(The seed measured 1/20 fresh SQLite and 2/10 upgraded.) PostgreSQL loses far more often: its
processes start their INSERTs closer together than SQLite's file lock lets them.

## Options weighed

- **`INSERT … ON CONFLICT (id) DO NOTHING`, then re-read** (chosen): one statement, no error path, no
  rollback of the caller's session; SQLAlchemy builds it per dialect
  (`sqlalchemy.dialects.sqlite.insert` / `sqlalchemy.dialects.postgresql.insert`,
  `.on_conflict_do_nothing(index_elements=['id'])`), floor `sqlalchemy>=2.0.50` has both. SQLite
  needs 3.24+ for the upsert clause (RHEL 8 ships 3.26; Python 3.11+ builds bundle newer).
- Plain INSERT, catch `IntegrityError`, roll back, re-read: dialect-neutral but rolls back the whole
  session (harmless at these call sites today, a trap for a later caller), and would also swallow an
  `IntegrityError` that is not the conflict.
- Advisory lock / `SELECT … FOR UPDATE`: no row to lock before it exists; heavier; rejected.
