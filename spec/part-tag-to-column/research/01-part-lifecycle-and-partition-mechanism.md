# `part` lifecycle & the SQLite partition / rotation mechanism

## Summary

`part` is a **bookkeeping integer** that tracks which "database partition" a completed
task belongs to. It is born as `0` on every task and only becomes non-zero during the
**manual, SQLite-only** `hs initdb --rotate` operation, which splits completed tasks out
into a sibling file (`main.db` → `main.1`, `main.2`, …). The `hs list` / `hs search`
commands then transparently re-union those partition files at read time
(`auto_union_sqlite`). Today `part` lives *inside* the `Task.tag` JSON dict, so both the
write (`json_set`) and the two reads (`Task.tag['part']` → SQLite `json_extract`) go
through SQLite JSON functions. Converting `part` to a real column collapses all of that
to plain column reads/writes. **`part` has exactly one writer of non-zero values
(`rotatedb`) and exactly two readers (both in `rotatedb`); the auto-union does NOT read
`part` at all.** On PostgreSQL `part` is pure dead weight — written but never read.

---

## Write sites

1. **Birth (always `0`)** — `data/model.py:387`, in `Task.new`:
   ```python
   tag = {**(tag or {}), **inline_tags, **{'part': 0, }}
   ```
   Every task created gets `part: 0` merged into its tag dict. This is the *only* place
   `part` enters normal task creation. It is never set to anything but `0` here.

2. **Rotation stamp (non-zero)** — `data/__init__.py:139`, in `rotatedb()`:
   ```python
   (Session.query(Task)
        .filter(Task.exit_status.isnot(None))
        .update({Task.tag: text('json_set(task.tag, :k, :v)').params({'k': '$.part', 'v': part_id})}))
   ```
   All **completed** tasks (`exit_status IS NOT NULL`) are stamped with the next
   partition id `part_id` (an integer from `next_rotate_path`, `data/__init__.py:130`,
   `:163-174`). This is the *only* incrementer — `part_id` is derived from the highest
   existing `main.N` file suffix + 1, **not** from any stored `part` value.

No other write sites exist (verified across `server.py`, `submit.py`, `client.py`, `task.py`).

## Read sites

Both live inside `rotatedb()` (`data/__init__.py`) and both go through SQLite JSON indexing:

- `data/__init__.py:147`
  ```python
  count_deleted = Session.query(Task).filter(Task.tag['part'] == type_coerce(part_id, JSON)).delete()
  ```
  Drops the just-stamped completed tasks from the **main** DB.

- `data/__init__.py:157` (on the freshly-cloned partition file, via a separate engine/session)
  ```python
  count_deleted = external_session.query(Task).filter(Task.tag['part'] != type_coerce(part_id, JSON)).delete()
  ```
  Drops everything that *doesn't* belong to this partition from the **new** file.

`Task.tag['part']` compiles, on SQLite, to a `json_extract(task.tag, '$.part')`
expression (SQLAlchemy also wraps tag-key lookups in `json_quote`, per the explicit NOTE
at `task.py:487-489`). This is the exact "complicated SQLite JSON syntax" the change targets.

**Fingerprint exclusions (read of the key name, not a query):** `data/model.py:414` and
`:423` deliberately drop `'part'` from the tag dict before hashing:
```python
'tags': {key: value for key, value in (tags or {}).items() if key != 'part'}
```
Once `part` is a column and no longer injected into `tag`, this `!= 'part'` guard becomes
dead (harmless) code — a candidate cleanup, not required by R4.

## The partition / rotation mechanism explained

There are **two halves**, both SQLite-only, with **no automatic driver** (no config knob;
the only `rotate` config key, `core/config.py:109`, is the *logging* file-rotation policy — unrelated).

**(A) Split — `rotatedb()` (`data/__init__.py:124-160`), invoked by `hs initdb --rotate`**
(`InitDBApp.run`, `data/__init__.py:239-242`; guarded to SQLite at `:127-128` and `:260-261`):
1. `next_rotate_path()` (`:163-174`) enumerates the DB directory and picks the next integer
   suffix: `main.db` → `main.1`, `main.2`, … The returned `n` **is** the new `part_id`.
2. Stamp all completed tasks with `part = n` (write site above).
3. `VACUUM INTO :path` (`:146`) clones the *entire* DB to `main.n`.
4. Delete the stamped rows from main (`:147`); `VACUUM` main (`:153`).
5. Open `main.n` with a fresh engine (`:156`) and delete every row whose `part != n`
   (`:157`), then `VACUUM` it. Net result: `main.n` holds exactly the completed tasks with
   `part == n`; main keeps everything else (all still `part == 0`).

So **each partition file contains rows that all share one `part` value equal to the file's
numeric suffix.** The `part` column is what makes the two delete steps precise (avoiding a
race where tasks complete between the clone and the drop — see the comment at `:133-135`).

**(B) Re-union at read time — `auto_union_sqlite()` (`task.py:839-867`)**, called by
`hs list` (`TaskListApp`, `task.py:191-192`) and `hs search` (`task.py:684-685`) unless
`-i/--ignore-partitions` is passed (`task.py:148,178-179`; `:596,672-673`):
```python
parts = sorted([... for filename in os.listdir(dirname)
                if re.match(f'^{basename}.[0-9]+$', filename)],
               key=(lambda fn: int(fn.split('.')[-1])))
...
Session.execute(text(f"attach database '{path}' as 'part_{i+1}'"))
...
SQLITE_UNION_PART + '\n'.join([f'union all\nselect * from part_{i+1}.task' ...])
```
where `SQLITE_UNION_PART` (`task.py:864-867`) is `create temp view 'task' as select * from main.task`.

**Key finding — the attach alias `part_{i+1}` is unrelated to the `part` tag/column value.**
Partitions are discovered purely by **filesystem** globbing (`main.N` regex), sorted by
suffix; `part_{i+1}` is just a positional ATTACH alias (1-based enumeration index), *not*
the stored `part` number, and the union does **not filter or reference `part` at all** —
it blindly `union all`s every partition's `task` table into a temp view. Because
`select *` is used, adding a `part` column is transparent: all files share the schema, so
the union keeps working unchanged after the migration.

## Exact simplification opportunities for R4

R4: read/compare `part` from the **column**, drop SQLite JSON functions for `part`,
preserve behavior. Three concrete edits:

1. **`data/model.py:387`** — stop injecting into tag; set the column instead. The
   `{'part': 0}` merge is removed and `part` becomes a `mapped_column(..., default=0,
   nullable=False)` placed **after `fingerprint` (`:299`) and before `tag` (`:301`)**, and
   added to the `columns` dict **before `'tag'`** (`:335`) so serialization/print order is
   preserved (`to_dict`/`to_json` iterate `columns`, `data/model.py:130-134`).

2. **`data/__init__.py:139`** (write) —
   `... .update({Task.tag: text('json_set(task.tag, :k, :v)')...})`
   becomes plainly `... .update({Task.part: part_id})`. No `json_set`.

3. **`data/__init__.py:147` and `:157`** (reads) —
   `Task.tag['part'] == type_coerce(part_id, JSON)` → `Task.part == part_id`
   `Task.tag['part'] != type_coerce(part_id, JSON)` → `Task.part != part_id`
   No `json_extract`/`json_quote`, no `type_coerce`/`JSON` import needed for these lines.

`auto_union_sqlite` (`task.py:839+`) needs **no change** — it neither reads nor writes
`part` (it relies on `select *` + filesystem discovery). Same for `--ignore-partitions`.

## Dialect notes

- `tag` is `JSON = JSON_TEXT().with_variant(JSON_BYTES(), 'postgresql')` (`data/model.py:101`)
  — i.e. **TEXT-backed JSON on SQLite, JSONB on PostgreSQL**. That is why the current reads
  need `json_extract` (SQLite) vs the `Task.tag[name]` operator SQLAlchemy would emit for
  JSONB — see the parallel dialect split at `task.py:486-495`, `:709-717`, `:724-733`,
  `:1113-1128`.
- **The entire rotation/partition feature is SQLite-only.** `rotatedb` raises for non-SQLite
  (`data/__init__.py:127-128`), `InitDBApp.check_arguments` rejects `--rotate` on non-SQLite
  (`:260-261`), and `auto_union_sqlite` early-returns unless SQLite (`task.py:841-842`).
- Consequence: on **PostgreSQL, `part` is written (`part:0` in JSONB) but never read** — it
  is dead payload today. Promoting it to a column is a small storage/clarity win there and a
  correctness/simplicity win on SQLite; a real `SMALL_INTEGER`/`INTEGER` column works
  identically on both dialects.

## Open questions / risks

- **Column type & nullability:** current values are only ever `0` or a small positive
  suffix — `SMALL_INTEGER` (as used for `attempt`/`exit_status`, `data/model.py:283,289`)
  with `default=0, nullable=False` matches the existing "always present" semantics. Confirm
  `default=0` covers rows created by `serialize_tasks`/queue round-trips (JSON-sourced tasks
  go through `Task.new`, so they get the default).
- **Migration of existing DBs:** existing SQLite files (main + any `main.N` partitions) have
  `part` only inside `tag` JSON and **no `part` column**. `initdb` uses
  `create_all` (`data/__init__.py:65`) which does **not** ALTER existing tables — there is no
  migration framework here. After the change, an old partition file attached by
  `auto_union_sqlite` would lack the `part` column, so `select * ... union all` would fail on
  a column-count mismatch. This is the main behavioral risk and likely needs either an
  ADD COLUMN step or an accepted "fresh DB only" boundary — flag for the plan.
- **`type_coerce(..., JSON)` import:** if `:147/:157` are the last JSON uses in
  `data/__init__.py`, the `type_coerce`/`JSON` imports (`data/__init__.py:20,31`) may become
  removable (verify against `vacuumdb`/others — they don't use them).
- **Print/export order:** R-goal says "immediately before `tag`". Verify the man pages,
  `docs/_include/*.rst`, and shell completions under `share/` that enumerate task fields are
  updated in lockstep (AGENTS.md same-commit rule).
