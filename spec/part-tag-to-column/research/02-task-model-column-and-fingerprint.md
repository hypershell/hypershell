# Research 02 — `Task` model: `part` column, placement, and fingerprint

**Scope:** `src/hypershell/data/model.py` (Task ORM, `Task.new`, fingerprint) + `data/core.py`
type/dialect handling. Covers R1, R2, R3, R5. Read-only; no files edited.

## Summary

`part` today is injected into every task's `tag` dict as `{'part': 0}` in `Task.new`
(`model.py:387`) and filtered back out of the identity fingerprint (`model.py:423`). Promoting
it to a column is mechanically small and low-risk **inside the model**: add one integer column
between `fingerprint` and `tag`, add one entry to the `columns` dict, drop the `{'part': 0}`
merge, and drop the `key != 'part'` filter (which then becomes a no-op). Serialization,
`to_dict`/`to_json`/`from_dict`, and `repr` all key off the `columns` dict, so they flow through
automatically once that dict is updated. The fingerprint value is unchanged. The heavy
downstream coupling is **not** in the model — it is the SQLite rotation logic in
`data/__init__.py` (`rotatedb`, R4), which is a separate research item.

## Ordered `Task` column list + insertion point

Columns are declared `model.py:259-301`, in this order:
`id, group, args, submit_id, submit_time, submit_host, cores, memory, cores_max, memory_max,
timeout, server_id, server_host, schedule_time, client_id, client_host, command, start_time,
completion_time, exit_status, outpath, errpath, csvpath, attempt, retried, waited, duration,
previous_id, next_id, source, fingerprint, tag`.

The **current last column is `tag`** (`model.py:301`):

```python
tag: Mapped[dict] = mapped_column(JSON, nullable=False, default={})  # kept last (export/print order)
```

The column immediately before it is `fingerprint` (`model.py:299`). **Insertion point: a new
`part` line between `fingerprint` (`:299`) and `tag` (`:301`)** — i.e. `part` becomes the last
column *before* `tag`, satisfying R1.

The parallel `columns` dict (`model.py:303-336`) mirrors this order and ends
`... 'source': str, 'fingerprint': str, 'tag': dict`. A `'part': int` entry must be inserted
between `'fingerprint'` and `'tag'` — this is the one **manual** list that does not update itself.

## Dialect-neutral type + recommended `part` column

Pre-defined types (`model.py:93-101`):

```python
INTEGER = Integer()
SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')
JSON = JSON_TEXT().with_variant(JSON_BYTES(), 'postgresql')   # the tag column's type
```

`tag` uses `JSON` (`Column(JSON)`), which is `sqlalchemy JSON` on SQLite and `JSONB` on
PostgreSQL via `with_variant`. `with_variant` is the repo's dialect-neutrality pattern, but for a
plain integer no variant is even needed: `INTEGER = Integer()` already maps to SQLite `INTEGER`
and PostgreSQL `INTEGER` — fully dialect-neutral, no SQLite-only construct (R3 met trivially).

The closest analogue is `group` (`model.py:260`), the other partition/bookkeeping index, which is
`INTEGER, nullable=False, default=DEFAULT_TASK_GROUP`. (`group` additionally carries
`quote=True, name='group'` **only** because `group` is a SQL reserved word — `part` is not
reserved in SQLite or PostgreSQL, so no quoting is needed.) `attempt`/`exit_status`/`cores` use
`SMALL_INTEGER`; either type is dialect-neutral.

**Recommended definition** (matches `group`, immutable default, non-null):

```python
part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)  # kept last before `tag`
```

Optional polish consistent with `DEFAULT_TASK_GROUP` (`model.py:46`): add
`DEFAULT_TASK_PART: Final[int] = 0` and use it as the default, avoiding a magic literal. A
Python-side `default=` (not `server_default=`) matches every other column and is sufficient —
`Task.new` always sets `part`, and the schema is forward-only (R6; no migration).

## `Task.new` / tag-assembly change (`model.py:357-406`)

Current merge (`model.py:387`):

```python
tag = {**(tag or {}), **inline_tags, **{'part': 0, }}
```

Change to drop the injection:

```python
tag = {**(tag or {}), **inline_tags}
```

and set `part` at construction (`model.py:404-406`), either via a new `part: int = 0` kwarg or by
relying on the column default. The constructor becomes `Task(id=uuid(), ..., tag=tag,
part=part, **other)`. Because `part` has a column default of `0`, simply omitting it also yields
`0`; an explicit `part=0` kwarg is clearer and lets the retry path forward it.

**Callers of `Task.new`** (all pass `tag=`, none pass `part`): `submit.py:351, 382, 1363`;
`task.py:101, 425`; and the retry builder `__schedule_next_failed_tasks` (`model.py:620-627`),
which copies `tag=task.tag, ...` but not part. With the injection removed, `task.tag` no longer
carries part, so the retry needs no part forwarding — a retried (failed, uncompleted) row in the
main DB always has `part=0` (rotation drops rotated rows out of main), so `default=0` preserves
today's behavior. No signature change is forced on callers.

## Fingerprint change (`model.py:408-426`)

The exclusion lives in `compute_fingerprint` (`model.py:420-424`):

```python
payload = json.dumps(
    {'args': raw_command,
     'group': group,
     'tags': {key: value for key, value in (tags or {}).items() if key != 'part'}},
    sort_keys=True, separators=(',', ':'), ensure_ascii=False,
)
```

Once `part` is no longer merged into `tag`, the `tags` dict passed here never contains `part`, so
`{... if key != 'part'}` is equivalent to `{... for ...}`. **Removing the filter yields byte-
identical payloads and hashes** (R5 preserved). `compute_fingerprint`'s signature stays the same
— `part` must **not** become a parameter (it deliberately does not participate in identity).

## Serialization / dict / schema-reflecting touchpoints

`to_tuple`/`to_dict`/`to_json`/`__repr__` (`model.py:119-134`) all iterate `self.columns`;
`from_dict`/`from_json` (`:137-144`) call `cls(**data)`; `serialize_tasks`/`deserialize_tasks`
(`:237-245`) go through `to_json`/`from_json`. **All of these pick up `part` automatically once
it is added to the `columns` dict** — no per-method edits. `part` will then appear in the JSON
wire payload sent to clients, which is harmless (a column, not a user tag). The only manual
serialization edit is the `columns` dict entry.

## Lifecycle orthogonality (§1 invariant)

`part` is independent of the state predicates (`schedule_time`, `completion_time`, `exit_status`)
and appears in **no** state/selection query in `model.py` — only in rotation. It does not belong
in `select_new`/`select_failed`/`next`/`revert_*`/`increment_group` etc. Confirmed orthogonal.

## Open questions / cross-references (outside this brief's scope)

- **R4 (separate item):** `data/__init__.py rotatedb` is the real consumer — it *writes* part
  into tag JSON via `json_set(task.tag, '$.part', part_id)` (`data/__init__.py:139`) and *reads*
  it via `Task.tag['part'] == type_coerce(part_id, JSON)` (`:147`) and `!=` (`:157`). These must
  move to the `part` **column** (plain `Task.part == part_id`), dropping the JSON functions.
- **R5 user-facing output:** the tag search/list paths in `task.py` (`:487-495`, `:720-733`)
  read arbitrary tag keys via `json_extract`/`Task.tag[name]`. They need **no change** — with
  part gone from `tag`, they simply stop matching/listing `part`, which is the desired outcome.
- **R7 tests:** `tests/test_source.py:64-66` and `:86` pass `{'part': 0}`/`{'part': 7}` into
  `compute_fingerprint` to assert part-in-tag doesn't shift identity. After the filter is
  removed, passing `part` in the tag dict *would* alter the hash — those tests must be rewritten
  to reflect that part no longer lives in `tag` (and new coverage: part-column set on submit,
  absent from `tag`, excluded from fingerprint).
- Naming: consider `DEFAULT_TASK_PART` constant for parity with `DEFAULT_TASK_GROUP`. `part` is
  not a reserved word, so no `quote=True`/`name=` needed (unlike `group`).
