# 02 — One change expression, one filter (research brief)

Probes under `probes/02_*` (base `fbc656e`; SQLite 3.53.1 runtime, PostgreSQL 18.7, TimescaleDB
2.32.0, SQLAlchemy 2.0.50). Every database created was dropped; raw outputs in scratch only.

## Q1 — the two non-contention defects (SQLite, `02_sqlite_defects.sh`): confirmed

```
--- A: hs update --limit 2 -f group=7 color:red      -> Updated 2 tasks; group 7,7,0,0; every tag {}
--- B: hs update -f group=7 color:blue -w group==0   -> Updated 4 tasks; group 7 x4; every tag {}
--- C: hs update -f -R --cancel color:green          -> Updated 4 tasks; exit_status -1 x4; every tag {}
```

## Q2 — the multi-key variant (`02_multikey.sh`, `02_pg_drive.sh`): reproduced on postgres and timescale

**Surprise first: the GOAL's command doesn't do what it says.** `-t/--with-tag` takes `nargs='+'`, so
in `hs update -f -t color:red color:blue size:big` all three words are tag *filters*. Collapsed into a dict
they become `{color: blue, size: big}`, which nothing matches, so you get "Update affects 0 tasks -
stopping" on every provider. The change has to come before `-t`, or after `--`, or the filter has to be
written as `--with-tag=color:red` (all three verified). R5's tests and the docs must write it that way.

Re-driven as `hs update color:blue size:big -f -t color:red`, after `hs update -f color:red` on 4 tasks:

| provider | result (all 4 rows) | log |
|---|---|---|
| sqlite | `{"color": "blue", "size": "big"}` | Updated 4 tasks |
| postgres (PG 18.7) | `{"color": "blue"}`: **size missed** | Updated 4 tasks |
| timescale (2.32, `task` a hypertable) | `{"color": "blue"}`: **size missed** | Updated 4 tasks |

`hs update -f --remove-tag color size -t color:red` (rows `{color: red, size: big}`): sqlite `{}`;
postgres and timescale `{"size": "big"}`: **size kept**, "Updated 4 tasks".

The cross-kind form misses **on every dialect, SQLite too**. A tag set takes rows out of the filter,
then `--remove-tag` misses them: `hs update color:blue -f --remove-tag size -t color:red` leaves
`size` on all 4 rows on sqlite, postgres and timescale ("Updated 4 tasks"). R5's "several tag keys or
`--remove-tag` names" therefore covers set followed by remove on SQLite as well. Field + set + remove
with a filter the field change leaves alone (`group=7 shape:sq --remove-tag size -t color:red`) lands
everywhere. `--limit 2 color:blue size:small` writes both keys on 2 rows on every provider, since only
one kind of change is involved.

## Q3 — one combined tag expression (`02_core_probe.py`): equals today's sequence in every case

These seven cases were run on sqlite, postgres and timescale, each comparing today's exact SQL
(copied from `task.py`, applied by id) with the combined expression:

- `types`: int, float, bool, str, None, and `''`
- `overwrite`: a set replacing an existing value
- `removes`: including a name that is missing
- `set+remove same`: a key that is both set and removed
- `set+remove mix`
- dotted, dashed and plus-sign keys, set
- dotted, dashed and plus-sign keys, removed

All 7 match on SQLite (`json_remove(json_set())`), and all 7 match for both PG forms.

```
sqlite  types  today [{"a":1,"b":1.5,"c":1,"d":0,"e":"x","f":null,"g":""}]  combined: same
pg      types  today [{"a": 1, "b": 1.5, "c": true, "d": false, "e": "x", "f": null, "g": ""}]  ||/-text[]: same; nested jsonb_set: same
set+remove same {a:1} + sets {a:5,b:6} - [a]  ->  {"b":6} today and combined (set, then remove)
```

Key quoting is today's behaviour and stays as it is; it is only reported here:

- **SQLite update paths are unquoted** (`'$.' + k`), so a dotted key nests. Setting `a.b:1`
  stores `{"a":{"b":1}}`, and `--remove-tag a.b` on a flat `"a.b"` key removes nothing.
- **Elsewhere the key is flat.** The SQLite filter quotes it (`$."name"`, and SQLAlchemy's
  `Task.tag[name]` does the same), and so do the ORM paths (`--limit`, legacy, submit) and PG
  (`'{a.b}'` is one path element). So on SQLite a dotted key set by the filtered form can't be
  found by `-t a.b`.
- Dashes and plus signs behave the same on both dialects.
- `--remove-tag` names are never validated.

Bool on SQLite: the filtered form stores `1`/`0`, while `--limit` (ORM JSON) stores `true`. The `-t c:true`
filter matches both (probe: `today=['c_true_orm','c_true_set']`). If `--limit` moves to the shared
expression, its SQLite bools become 0/1, which is a visible change in `hs list`/`hs info` output; using
`func.json('true')` instead would change the filtered form. Pick one deliberately.

Compiled SQL:
```
sqlite: UPDATE task SET tag=json_remove(json_set(task.tag, ?, ?, ?, ?), ?, ?) WHERE task.id = ?
pg:     UPDATE task SET tag=((task.tag || %(param_1)s::JSONB) - %(param_2)s::TEXT[]) WHERE task.id = %(id_1)s::UUID
nested: ...jsonb_set(jsonb_set(task.tag, CAST(%(param_1)s::VARCHAR AS TEXT[]), %(param_2)s::JSONB), ...) - %(param_5)s::VARCHAR) - ...
```
`jsonb || jsonb` needs PG 9.5 and `jsonb - text[]` needs PG 10, so both are fine on the 13 floor. Every
SQLite function used is JSON1 (3.9+); none of the 3.38 `->>` forms appear. 3.26 itself was not run, and
the `turso` provider (dialect `sqlite`) was not probed.

## Q4 — the filter as Core criteria

**(a) Bug: on SQLite, two name-only tags check only the last one.** Every `text('json_extract(task.tag,
:key) is not null')` shares one bind, `:key`, and the Query keeps one `.params()` dict:

```
CLI   hs list -t a b --count -> 2    (rows {a,b} and {b}; should be 1)
probe today -t a b SQL: json_extract(task.tag, ?) is not null AND json_extract(task.tag, ?) is not null | query params: {'key': '$."b"'}
      -t a b  today=['ab','b']  core_select=['ab']        -t b a  today=['a','ab']  core_select=['ab']
```

PG is correct (`today=['ab']`). The bug affects `hs update -t a b` and `hs list` alike. Fixing the shared
builder changes `hs list`, which is a GOAL non-goal, and so does leaving update and list on different
builders. The foreman should rule on this; it probably means a seed or a scope note.

**(b) Criteria** are self-contained and work unchanged in `select`, `update(Task.__table__)`,
`delete(Task.__table__)`, `Session.query(Task).filter(*crit)`, and the per-row `WHERE task.id = :id AND
…` lookup. On all 3 providers they matched today's rows for each of these filters, except the SQLite
`-t a b` bug above:

- `-w "args ~ ^sl"`: regexp; pysqlite registers REGEXP, PG uses `~`
- `-w group==3`
- `--signal TERM`
- `-g 3 --retries`
- `-R`
- `-C -t a`
- `-t c:true`
- `-t e:x n:1.5`
- `-t a`

The UPDATE and DELETE rowcounts equal the select counts. `WhereClause.compile()` needs no change. The
two alternatives for a name-only tag on SQLite both give `['ab']`:

- `func.json_extract(Task.tag, literal('$."a"')).isnot(None)`
- `text(...).bindparams(bindparam('key', '$."a"', unique=True))`, rendered as `:key_1`, `:key_2`

Also: `__build_filters` appends to `self.where_clauses`, so calling it twice duplicates the clauses
(`['exit_status == null','group == 3','exit_status == null','group == 3']`). Build the criteria once.

## Q5 — Query.update/delete versus limit and order_by

```
limit(None).update: ok, returned 6        limit(2).update / limit(2).delete / order_by.update:
text-param filter .update (sync auto): ok   InvalidRequestError: Can't call Query.update() or Query.delete() when limit()/order_by() has been called
Core UPDATE that changes nothing: rowcount=6 of 6 rows   (sqlite, postgres, timescale: rowcount = rows matched)
```

Today's code is safe only because `--order-by` requires `--limit` and the bulk path runs with `limit=None`.
Moving to Core: build `select(T.c.id).where(*crit).order_by(...).limit(n)` for `--limit`, and
`update(T).where(*crit)` for the bulk form. A Core statement on the Table skips session synchronisation,
so any `Task` objects already loaded (today's `query.all()`) go stale. `Query.update`'s return value (rows
matched) is what today ignores.

## Recommendation

- **Change, SQLite**: `func.json_remove(func.json_set(T.c.tag, literal('$.'+k, Text), literal(v), …),
  literal('$.'+r, Text), …)`, with each part omitted when it is empty. `literal()` gives unique anonymous
  binds and keeps 0/1 for bools, as the filtered form does today.
- **Change, PostgreSQL**: `T.c.tag.op('||', return_type=JSONB)(literal({k: to_json_type(v)}, JSONB))
  .op('-', return_type=JSONB)(literal(names, ARRAY(Text)))`. This is two binds whatever the number of
  keys, preferred over nested `jsonb_set` (2 binds per key, plus casts).
- **Whole change**: one `values(**field_updates, tag=<expr>)` on `update(T)`. Bulk form:
  `.where(*crit)`. `--limit` and re-runs: `.where(T.c.id == id, *crit)`, plus `select(T.c.id).where(T.c.id
  == id, *crit)` to tell "no longer matches" from a contended miss.
- **Filter**: a `filter_criteria()` on `SearchableMixin` that returns `[wc.compile() …] + tag criteria`, with
  the SQLite name-only tag as `func.json_extract(…, literal('$."name"')).isnot(None)`. Have `build_query`
  call `.filter(*criteria)` only if the foreman accepts the `hs list -t a b` fix. Otherwise keep today's
  text clause for list and decide what update does.
- **Multi-key verdict**: **reproduced** on postgres (PG 18.7) and timescale (2.32 hypertable): a
  second key is missed and a second `--remove-tag` name is kept, with "Updated 4 tasks" logged. The
  set-then-remove variant also misses on **SQLite**. The GOAL's example command line is mis-parsed;
  put the changes before `-t`.
