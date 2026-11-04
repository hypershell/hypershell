# 02 — Bind types on SQLite, PostgreSQL and TimescaleDB, and how CI proves them

## 1. The column types the 13 result columns carry (`data/model.py:95-102`)

- `client_id`: `UUID` = `Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')`.
- `start_time`, `completion_time` (and the `schedule_time` token): `DATETIME = DateTime(timezone=True)`.
- `exit_status`: `SMALL_INTEGER` (variant `SMALLINT` on PostgreSQL); `waited`, `duration`: `INTEGER`;
  `cores_max`, `memory_max`: `FLOAT`; the rest `TEXT`.

On `bc6547a`, `cls.id == value` and `.values({name: value})` both coerce the Python value into a bind
parameter typed with **the column's own type object**. A `bindparam(..., type_=t.c[name].type)` is
that same type object, so the bind processor (datetime formatting on SQLite, the UUID and
`timestamptz` handling on psycopg) is the same by construction, variant resolution included. The
plan requires taking every type from `Task.__table__.c`, never naming a type by hand.

## 2. What was observed on SQLite

A twin-row probe: three dispatched tasks finished with identical result values (timezone-aware
datetimes at +05:30 with microseconds, a UUID-string `client_id`, floats). One written by
`bc6547a`'s ORM statement, one by the typed prebuilt statement, one by an untyped prebuilt
statement. Read back raw with `text()` (no result processing): all three rows store the same
strings (`2026-11-04 09:01:02.345678`, the same `client_id`). Core gives an untyped bind the
column's type in a comparison and in the SET clause, so even the untyped form matched; explicit
types are kept anyway, because they make the intent checkable and do not rely on that coercion.

## 3. How the types are proven per database

- **SQLite (local and every `tests (3.x)` CI entry):** a reference-equivalence test. For each case
  (offsets east and west of UTC, microsecond 0 and 999999, `None` paths, a non-zero exit), apply one
  copy through `Task.apply_results` and its twin through a **reference statement written in the test
  exactly as `bc6547a` builds it** (the ORM `update(Task)…values({…})` form), then compare the two
  rows' raw stored values (`text()` select, no type processing) and their ORM-read values. The test
  also captures the UPDATE's SQL and asserts that the SET list is exactly `Task.result_columns` and
  the WHERE carries the three predicates. It passes on `bc6547a` (both sides are the same form) and
  must still pass on the fix.
- **PostgreSQL and TimescaleDB:** the same reference-equivalence test, appended to
  `tests/test_writeback_postgres.py` and run under that module's `pg_tasks` fixture (both session time
  zones). Raw values come back through psycopg's own loaders (`uuid.UUID`, aware `datetime`), which
  bypass SQLAlchemy's processing. That module already runs in all five entries of the CI
  `postgres` job (`.github/workflows/tests.yml:55-108`): **PostgreSQL (pg13)**, **PostgreSQL
  (pg18)**, **PostgreSQL (timescale)**, **PostgreSQL (timescale-oss)**, **PostgreSQL
  (timescale-floor)**. Its existing tests (`test_dispatched_echo_matches`, `test_stamped_echo_matches`,
  `test_stale_echo_after_redispatch`) already exercise the fence with timezone-aware tokens at any
  offset and microsecond, and stay unchanged.
- **A hypertable:** `tests/test_timescale.py::test_run_compress_list_vacuum` runs a real `hs cluster`
  with result writeback on the converted hypertable in the three TimescaleDB entries and checks
  that no task remains incomplete. The writeback tests themselves run on a plain table on those
  servers. Bind processing is client-side and per dialect, and a hypertable has the same column
  types, so the plan adds no hypertable-specific writeback test (a risk for the foreman to rule on).

PostgreSQL is not available locally; the local suite skips these. Their green comes from the CI run
on the draft PR, which the foreman opens, and is recorded in `EVIDENCE.md` (the `ci` block).
