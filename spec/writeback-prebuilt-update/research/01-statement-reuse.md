# 01 — Statement reuse: where the cost is, what replaces it, what a guard can observe

Read against `develop` `bc6547a`, SQLAlchemy 2.0.50 (the lock and the floor). Probes ran in-process
on throwaway SQLite sites, importing `hypershell` from an extracted tree; none touched the repo.

## 1. The per-result path today

`Receiver.update_tasks` (`server.py:459`) calls `Task.apply_results(self.tasks)` once per bundle,
which calls `Task.__apply_result` per result (`data/model.py:837-860`) and commits once. Per result
it builds an ORM-enabled `update(cls).where(id, schedule_time, completion_time IS NULL)
.values({13 columns: values}).execution_options(synchronize_session=False)` and executes it. Only
when that matches nothing does it build and run the classifying `select(cls.id)`.

Probe over 16 APPLIED results on `bc6547a`: **16 distinct UPDATE statement objects**; the compiled
cache reports **one `CACHE_MISS` and then `CACHE_HIT`** for every later UPDATE. So compilation is
already cached. The extra CPU is building the statement, coercing the 13 literal values into bind
parameters, and generating its cache key on every execute, as the GOAL's profile says.

## 2. The replacement

Build the conditional UPDATE once per process against `Task.__table__`, with typed `bindparam`s:

```
t = Task.__table__
update(t).where(t.c.id == bindparam('r_id', type_=t.c.id.type),
                t.c.schedule_time == bindparam('r_schedule_time', type_=t.c.schedule_time.type),
                t.c.completion_time.is_(None))
         .values({name: bindparam(f'r_{name}', type_=t.c[name].type) for name in Task.result_columns})
```

and execute it with **one parameter dict per result** (`Session.execute(stmt, {...}).rowcount`).

- **SQL text:** captured with `before_cursor_execute`, the prebuilt UPDATE's SQL on SQLite is
  byte-identical to `bc6547a`'s (same SET order, same WHERE). The ORM path and the Core path both
  render `UPDATE task SET … WHERE task.id = ? AND task.schedule_time = ? AND task.completion_time IS NULL`.
- **Bind names:** a `bindparam` named exactly like a column in the SET clause is reserved by
  SQLAlchemy for the automatic VALUES/SET parameters; prefix every name (`r_…`).
- **One dict, never a list:** a list makes it an executemany, and `rowcount` per row is then not
  available; classification needs it per result.
- **Types come from the table columns**, so the dialect variants (`UUID`, `SMALL_INTEGER`, `JSON`)
  resolve per dialect at compile time exactly as the ORM path's column-typed binds do. See 02.
- **Schema:** `Task.__table__` carries the configured schema (`data/core.py`), the same table object
  the ORM statement targets. There is no `schema_translate_map` anywhere in the code.
- **Threads and engines:** a Core statement is immutable; executing it with parameters does not
  mutate it. The compiled cache is per engine and dialect, so tests that rebind `Session` to another
  engine (SQLite file, PostgreSQL) still compile per dialect. The scoped session is per thread.
- **Session behaviour:** the result copies are transient objects never added to the session, so
  `synchronize_session=False` had nothing to synchronise; a Core statement through
  `Session.execute` runs in the same transaction and keeps the single commit and rollback in
  `apply_results`. No `Task` column has `onupdate`.
- **The classifying SELECT** may be prebuilt the same way (`select(t.c.id).where(id, schedule_time,
  completion_time IS NOT NULL)`). The GOAL leaves it optional. It is a few lines, it keeps a storm of
  STALE results after an eviction off the slow path, and the guard can then cover both statements.

Prototype (a patched copy of `bc6547a`'s tree in scratch, UPDATE prebuilt only, through
`Receiver.update_tasks`, 5,000 one-result bundles, four recorded interleaved rounds, Python 3.14.6):

| build | mean CPU ms/result |
|---|---|
| 2.8 (`b819de6`) | 0.414 |
| develop (`bc6547a`) | 0.534 |
| prototype | 0.329 |

## 3. What the R4 guard observes

| Candidate | `bc6547a` (N=16) | prototype | Usable? |
|---|---|---|---|
| Distinct UPDATE statement objects the writeback executes | 16 | 1 | **yes** |
| Compiled-cache misses across N results | 1 | 1 | **no**, flat on both |

The compiled-cache candidate cannot fail against `bc6547a`, because the per-result statement already
hits the cache; only cache-key generation and coercion repeat. **Statement identity is the guard.**
Observe it by wrapping `Session.execute` (the scoped session the model imports) for the duration of
the test and keeping every statement object it receives, so `id()` reuse after garbage collection
cannot fake a match. Drive at least 8 results through `Receiver.update_tasks` with mixed outcomes
(APPLIED, DUPLICATE, STALE) and assert at most one distinct UPDATE object and at most one distinct
SELECT object. On `bc6547a` it fails by assertion (N distinct UPDATEs), using only symbols that
exist there, so it can be committed red with `xfail(strict=True)` first.
