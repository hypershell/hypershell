# 00 — Research digest (consolidated decisions)

Synthesis of briefs `01`–`04`. Each decision below is the single recommendation the design commits to;
where briefs disagreed, the resolution is called out.

## What `part` actually is (briefs 01, 02)

- Born as `0` on **every** task via `data/model.py:387` (`{'part': 0}` merged into the `tag` JSON dict).
- Set non-zero **only** by `rotatedb()` (`data/__init__.py:130-160`), invoked by **`hs initdb --rotate`**
  — a **manual, SQLite-only** maintenance command. There is **no config-driven / automatic** DB
  rotation (the `rotate` config key is for *log* files only, brief 03).
- `part_id` for a rotation = highest existing `main.N` partition **filename** suffix + 1
  (`next_rotate_path`, `:163-174`) — derived from the filesystem, never from a stored value.

## The R4 targets — exactly three expressions (brief 01, ground-truthed)

In `rotatedb()`:
- **write** `:139` — `.update({Task.tag: text("json_set(task.tag, '$.part', :v)") …})` → `.update({Task.part: part_id})`
- **read** `:147` — `Task.tag['part'] == type_coerce(part_id, JSON)` → `Task.part == part_id`
- **read** `:157` — `Task.tag['part'] != type_coerce(part_id, JSON)` → `Task.part != part_id`

After these, `type_coerce` / `JSON` become unused **in `data/__init__.py`** (grep-confirm before removing;
`text` stays — used by the `VACUUM` statements at `:146,:153,:159`). The `exit_status.isnot(None)` filter
(`:138`) is preserved verbatim — not part of this change.

## The auto-union is NOT a target (brief 01) — but it is the forward-only hazard (brief 03)

`auto_union_sqlite` (`task.py:839-859`) attaches partition files as `part_{i+1}` (a **positional
filesystem index, unrelated to the `part` value**) and does `SELECT * … UNION ALL SELECT *`. It never
reads `part`, so **no change needed**. But `SELECT *` is column-count-sensitive: a new-schema `main`
(with the `part` column) unioned against an **old** partition file (created before this change, lacking
the column) raises a column-count mismatch → `hs list/search/info` break until `--ignore-partitions`.
This is the concrete meaning of "forward-only / may require re-init" (already a GOAL non-goal). New
rotations are self-consistent: `VACUUM INTO` copies the current schema, so post-change `main.N` files
carry the column.

## Model change (briefs 02, 04, ground-truthed)

- **Insertion point (R1):** between `fingerprint` (`model.py:299`) and `tag` (`:301`, the current last
  column, `# kept last (export/print order)`).
- **Definition (R1, R3):** `part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)`.
  `INTEGER` (`model.py:96`) is already dialect-neutral (SQLite/PG `INTEGER`); no `with_variant`. `part`
  is not a SQL reserved word, so (unlike `group`) it needs no `quote=`.
- **`Task.new` (R2):** delete `**{'part': 0, }` from the `:387` merge → `tag = {**(tag or {}), **inline_tags}`.
  The `Task(...)` constructor (`:404-406`) needs no `part=` — the column `default=0` supplies it, and the
  retry builder (copies `tag`, not `part`) correctly yields `part=0` for new retry rows.
- **Fingerprint (R5):** delete `if key != 'part'` at `:423` (→ `'tags': dict(tags or {})`) and fix the
  docstring at `:414`. Byte-identical, since `part` no longer exists in the tag dict the function
  receives.

### Contradiction resolved — the `columns` dict (brief 02 vs brief 04)

Brief 02 said add `'part': int` to the parallel `columns` dict (`model.py:303-336`); brief 04 said **do
not**. **Resolution: DO NOT add it.** That dict is the allowlist driving `to_dict`/`to_json`/`from_dict`/
`repr`/`serialize_tasks` **and** CLI field validation / `--fields` / `hs list`. Adding `part` there would
expose it on the wire and in the CLI — violating **R5** ("not in user-facing output") and the **no-CLI-
surface non-goal**. So `part` is a real mapped column (→ real DB column, R1/R6) that is **deliberately
absent** from `columns` (→ internal-only). `rotatedb` reads it via the ORM attribute `Task.part`,
independent of that dict. This asymmetry is the one deliberate complexity — recorded in PLAN §3.

## Schema / forward-only (brief 03)

`initdb()` = `Entity.metadata.create_all(engine)` (`data/__init__.py:65`); adding the mapped column makes
it appear in a **fresh** DB with zero extra work (R6). `create_all(checkfirst=True)` never `ALTER`s, which
is the mechanical root of forward-only. `part` never enters via `submit.py` — only via `Task.new`.

## Tests & docs (brief 04)

- **Will break, must update:** `tests/test_source.py:63-67,:86` hardcode `{'part': …}` into
  `compute_fingerprint` calls. `tests/test_source.py:99-116` (column subset check) is where to assert the
  real table has `part` **and** that `part` is absent from `Task.columns`.
- **Stub to fill:** `tests/test_initdb.py:67-82` `test_rotate` currently submits 4 tasks and never
  rotates — the natural home for real column-based rotation coverage (R4).
- **Docs (§12 same-commit):** `docs/_include/initdb_desc.rst:7-8` ("applies a special purpose `part:N`
  **tag**") is generated from `INITDB_HELP` (`data/__init__.py:~190`). Edit both together; `part` is no
  longer a tag. `--ignore-partitions` help and `docs/database.rst` reference file numbering, not the tag →
  no change. Completions don't carry the description → no `share/` completion change; regenerate the man
  page if it carries the initdb text.
- **Behavioral note (deliberate, document in PLAN):** removing the `:387` clobber means a user
  `-t part:5` would now persist as an ordinary user tag (previously it was silently overwritten to 0).
  This is consistent with "tags are user annotations again" — `part`-in-tag is now inert w.r.t. rotation,
  which reads the column. No special reservation added.

## Verification (brief 04)

Drive `hs` in a throwaway site via `.agents/factory/bin/temp_site.sh sh -c "…"` (run `sqlite3` inside the
same `sh -c` — the site is deleted on exit). Prove: (a) submitted task's `part` **column** = 0 and its
`tag` has no `part`; (b) `hs initdb --rotate` partitions by the column and `hs list` (auto-union) still
works; (c) fingerprint unchanged; and (after the reshape) (d) `hs list part` is a valid field and
`hs list --part N` selects partition N. Docs baseline: the 2 pre-existing toctree warnings
(`docs/cli/task_submit.rst`, `docs/manual.rst`).

## CLI surface — added after the 2026-07-31 GOAL reshape (briefs 05, 06)

The maintainer reversed the "keep internal" decision: `part` is now a **visible column** (in
`Task.columns`) and `hs list`/`hs search` gain a `--part` filter (R8, R9).

- **`hs list` and `hs search` are the SAME app** — `TaskSearchApp` (`task.py:601`), registered as `list`
  (`__init__.py:130`) and `search` (`task.py:1235`). `--part` is added **once**; one help source; one
  completion function per shell.
- **Visible field (R8):** field validation checks `name in Task.columns` (`task.py:545-549`); adding
  `'part'` to `Task.columns` (`model.py:303-336`) makes `hs list part` / `--fields` / `-x` /
  `hs list --all` treat `part` as a normal column. **This reverses the earlier omit-from-columns
  deviation** — `part` is now a normal entry, so PLAN's deviation table is empty.
- **`--part` filter (R9):** add `part_filter: Optional[int] = None` to `SearchableMixin` +
  `interface.add_argument('--part', type=int, default=None, dest='part_filter')` on `TaskSearchApp`; in
  `__build_filters` (`task.py:513-538`) append `f'part == {self.part_filter}'` when `part_filter is not
  None` — mirroring the `-g/--group` filter (`:465`, `:531-532`, `:646-647`). Works because `part` is a
  real column (`WhereClause.compile` → `getattr(Task,'part')`). `--part 0` is a legitimate filter (the
  main partition); absent flag = no filter (`is not None` guard).
- **Auto-union composition:** the union temp view is created in `run()` (`:684-685`) **before**
  `build_query()` (`:694`), so `WHERE part = N` over the view correctly selects partition N's rows. No
  ordering issue.
- **Docs (§12) — hand-maintained, NO generator:** help originates from `SEARCH_USAGE`/`SEARCH_HELP`
  constants (`task.py:554`/`:568`); the RST includes `docs/_include/task_search_{usage,desc,help}.rst`
  are hand-authored (there is **no** `list_*` include). Precedent: `961fb22 [feature] Add --all to task
  list` hand-edited `task_search_desc.rst` + `task_search_help.rst`. Edit by hand.
- **Completions (§12):** bash `_hs_task_search` (add `--part` to the `all_opts` string, ~`:377-380`, +
  optional `--part)` value branch); zsh `_hs_list` (add an `_arguments` line mirroring
  `--ignore-partitions`, ~`:422`). `hsx`/`hs cluster` completions are separate and unaffected.
- **Man pages / CI:** man pages are regenerated at **release time only** (`sphinx-build -b man`; the
  `--all` precedent did not touch `share/man/` in the feature commit) — **not** a same-commit
  obligation. The CI wheel-metadata assertion (`tests.yml:95-113`) checks a fixed path list; adding an
  option changes file *contents*, not the list, so it stays green.
- **Tests:** mirror `tests/test_list.py` invocation/assertion patterns for the `--part` cases.
