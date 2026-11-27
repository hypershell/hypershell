# Research 05 — list & search CLI apps and the `--part` filter

## Summary

There is **one** application class, `TaskSearchApp` (`src/hypershell/task.py:601`), that
serves **both** `hs list` (top-level, `__init__.py:130`) and `hs task search`
(`task.py:1235`). "list" and "search" are the same app — so `--part` is added **once**.
Its query machinery lives in the shared `SearchableMixin` (`task.py:447`), which is also
mixed into `TaskUpdateApp`. `part` is currently a tag entry only (`model.py:387`,
`tag = {**(tag or {}), **inline_tags, **{'part': 0,}}`) and is **not** in `Task.columns`
(`model.py:303-336`), which is why `hs list part` errors "Invalid field name". Promoting
`part` to a real column + adding it to `Task.columns` makes it selectable/displayable, and
a `--part N` flag mirroring the existing `-g/--group` filter injects `Task.part == N` into
the query. Auto-union composes correctly because the temp view is created *before* the query
runs and SQLite has no schema qualifier (`schema = None`).

## 1. The app + argument style (mirror `-g/--group`)

`TaskSearchApp` declares filters as class attrs bound to `interface.add_argument`. The
model to copy is the group filter — an int option feeding a `*_filter` dest:

```python
# task.py:646-647
group_filter: Optional[int] = None
interface.add_argument('-g', '--group', type=int, default=None, dest='group_filter')
```

Existing `-i/--ignore-partitions` (house style for the partition toggle):

```python
# task.py:672-673
ignore_partitions: bool = False
interface.add_argument('-i', '--ignore-partitions', action='store_true', dest='ignore_partitions')
```

`--fields` version string is `' '.join(Task.columns)` (`task.py:605`); the `field_names`
positional defaults to `ALL_FIELDS = list(Task.columns)` (`task.py:434`, `:607-608`).
`ALLOW_INTERSPERSE` is not set on any of these apps (cmdkit default). `-x/--extract` on
`TaskInfoApp` uses `choices=Task.columns` (`task.py:169`).

## 2. Where the WHERE clause is applied — shared, add once

`SearchableMixin.build_query` (`task.py:469-475`) is the single query builder; both
`TaskSearchApp` and `TaskUpdateApp` call it. Filters flow through `__build_filters`
(`task.py:513-538`), which appends **string** predicates to `self.where_clauses` and then
converts each via `WhereClause.from_cmdline`. The group filter is the exact template:

```python
# task.py:531-532
if self.group_filter is not None:
    self.where_clauses.append(f'group == {self.group_filter}')
```

`WhereClause.compile` (`task.py:1259-1262`) does `op_call(getattr(Task, self.field), value)`,
so `'part == N'` becomes `Task.part == N` — this **requires `part` to be a real mapped
column** (which the refactor provides). `from_cmdline` (`task.py:1264-1284`) coerces the RHS
via `smart_coerce` for non-`str` columns, so `part` (int) coerces cleanly.

**Recommended change (once):** add `part_filter: Optional[int] = None` to `SearchableMixin`
(beside `group_filter` at `task.py:465`); append `if self.part_filter is not None:
self.where_clauses.append(f'part == {self.part_filter}')` in `__build_filters`; and add
`interface.add_argument('--part', type=int, default=None, dest='part_filter')` to
`TaskSearchApp` (R9 scopes this to list/search). Adding the attr to the mixin is safe for
`TaskUpdateApp` — it defaults to `None`, so no filter unless that app also declares the flag.

## 3. Field selection / display — adding `part` to `columns` is sufficient

`check_field_names` (`task.py:545-549`) is the validator:

```python
for name in self.field_names:
    if name not in Task.columns:
        raise ArgumentError(f'Invalid field name "{name}"')
```

`fields` (`task.py:540-543`, and the color-aware override `:757-763`) does
`getattr(Task, name)`. So once `'part'` is in `Task.columns` **and** is a real attribute,
`hs list part` validates, selects, and displays. It also auto-appears in `--fields`,
`--list-columns` (`task.py:1227`), the default `ALL_FIELDS` view, and `-x/--extract`
choices. Order in `Task.columns` = column order in normal/default output (place it per the
model brief, e.g. right after `group`).

## 4. Auto-union composition — confirmed correct

`auto_union_sqlite` (`task.py:839-860`) attaches partition files and builds a temp view via
`SQLITE_UNION_PART` (`task.py:864-867`): `create temp view 'task' as select * from main.task
union all select * from part_N.task ...`. It runs in `run()` at `task.py:684-685`, **before**
`build_query()` at `:694`. Because SQLite resolves unqualified `task` to the temp schema
first, and `schema = config.pop('schema', None)` is `None` for SQLite (`data/core.py:199`),
SQLAlchemy emits `SELECT ... FROM task WHERE task.part = ?` against the **view**. `select *`
propagates each partition's own `part` value through the `union all`, so `WHERE part = N`
selects exactly partition N's rows. **No ordering issue** — the filter is part of the query
executed after the view exists. (Caveat for the migration brief: every partition file's
`task` table must carry the `part` column, or `union all`'s column counts mismatch.)

## 5. Type / coercion & "no filter"

Mirror `-g/--group`: `type=int, default=None`. Absent → `part_filter is None` → **no
filter** (this is why the guard must be `is not None`, not truthiness). `--part 0` is a
**legitimate** filter (part 0 = main partition) and works precisely because `0 is not None`.
Int limits use the same idiom (`-l/--limit` `type=int, default=None`, `task.py:629`).

## 6. Tests to mirror

`tests/test_list.py` is the pattern source. `test_signal_filter` (`:193-219`) and
`test_sighup_cancel_equivalence` (`:222-235`) are the closest analogs for a filter option:
submit via `create_taskfile`, mutate with `hs update ... --no-confirm`, then assert with
`main_lines(['hs', 'list', <field>, '--part', 'N'])`. Helpers: `main`/`main_lines`
(`tests/__init__.py:28,38`), `create_taskfile` (`:59`). Mark `@mark.integration`. Suggested
cases: `--part 0` returns main rows; `--part N` selects a rotated partition (needs an
`initdb --rotate` fixture — see the initdb brief); absent flag = unfiltered; `--part`
combines with `-t`/`-w`; `hs task search --part` behaves identically (same class).

## Open questions

- **`--part 0` semantics**: confirmed a valid filter (main partition). Keep the `is not
  None` guard so it is not swallowed as falsy.
- **`hs update --part`?** R9 names only list/search. The mixin change makes adding it to
  `TaskUpdateApp` trivial later, but out of scope now.
- **Short flag**: `-g` is taken by group; `-p` is free but unclaimed elsewhere — recommend
  long-only `--part` unless the plan wants a short alias.
- **Non-SQLite (Postgres)**: no auto-union; `--part` is a plain `WHERE part = N` and works
  identically. No partition semantics there, but the column filter is still valid.
