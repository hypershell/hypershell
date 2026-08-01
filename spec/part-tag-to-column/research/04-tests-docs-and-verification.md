# Research 04 — Tests, Docs & Verification for `part` tag→column

## Summary

Promoting `part` from a `Task.tag` JSON entry (`{'part': 0}`, `src/hypershell/data/model.py:387`)
to a first-class nullable-default-0 column touches a small, well-bounded surface:

- **Model/source of truth**: `src/hypershell/data/model.py` (tag injection at :387, fingerprint
  exclusion at :414/:423), and the SQLite rotation/partition logic in
  `src/hypershell/data/__init__.py:133-159` (`rotatedb` uses `json_set(tag,'$.part',N)` + filters
  `Task.tag['part'] == N`). Auto-union in `src/hypershell/task.py:839-866` does **not** read `part`
  — it unions all partition *files* by numeric filename suffix, so it needs no change (verify only).
- **Design tension (flag to impl brief 03)**: `Task.columns` (`data/model.py:303-338`) drives
  `to_dict`/`to_json`/`serialize_tasks` (`data/model.py:128-157,237`), the CLI field validators
  (`task.py:434,548,1003,1160`), `--fields` (`task.py:605`), `-x/--extract` (`task.py:169`),
  `--order-by` (`task.py:946`), and default `hs list --all` output. To keep `part` **INTERNAL
  (no new CLI surface)**, add it as a `mapped_column` but **do NOT add it to the `columns` dict**;
  `rotatedb` reads it via the ORM attribute `Task.part`. Adding it to `columns` would leak it into
  `hs list --all`, `hs info`, `--fields`, and wire serialization.

## Tests to touch

**Will break / must update:**
- `tests/test_source.py:63-67` `test_fingerprint_excludes_part_tag` — passes `{'k':'1','part':N}`
  into `compute_fingerprint`. Behavior is preserved only if the `part`-exclusion filter stays in
  `compute_fingerprint` (`data/model.py:423`). Decide: keep filter as defensive dead-code (test
  passes unchanged) or drop filter + rewrite this test to assert the **column** doesn't affect
  identity (e.g. two `Task.new(...)` with different `part` attrs → same fingerprint).
- `tests/test_source.py:86` `test_new_stamps_source_and_falls_back_to_args_without_raw` — asserts
  `fallback.fingerprint == Task.compute_fingerprint('echo x', 0, {'part': 0})`. After removal, the
  natural RHS is `compute_fingerprint('echo x', 0, {})` (or `None`). Update the literal.

**Guards behavior we must preserve (verify still green, likely no edit):**
- `tests/test_source.py:99-116` `test_fresh_schema_creates_source_table_columns_and_indices` — uses
  subset `{'source','fingerprint'} <= task_columns`, so it won't break. **Best place to ADD** a new
  assertion `'part' in task_columns` to cover column existence.
- `tests/test_source.py:30-59,72-94` other fingerprint tests — order-independence, resource-knob
  exclusion, supplied-fingerprint — unaffected; re-run to confirm.
- `tests/test_initdb.py:67-82` `test_rotate` — **currently a stub**: it submits 4 tasks and stops;
  it never calls `hs initdb --rotate` nor asserts partition/union results. This is the natural home
  for **new column-based rotation coverage** (rotate → part column set on migrated rows, dropped from
  main, auto-union re-attaches). Extend it.
- `tests/data/test_model.py:20-45` `test_split_argline` — asserts exact `(args, tag)` tuples;
  `split_argline` never injects `part`, so unaffected (confirm).
- `tests/test_submit.py:221-238` inline/`tag=` resource-tag tests, `tests/test_list.py:91-92`
  tag-filter color test, `tests/test_update.py`, `tests/test_submit_json.py`, `tests/test_restart.py`
  — exercise tags/fingerprint but not `part`; re-run as regression.

**Helpers/fixtures (no change needed, use them):** `tests/__init__.py` — `main`/`main_lines`
(rc,stdout,stderr), `assert_output(pattern,output,count,groups)`, `create_taskfile_echo(temp_site,
count,tags=)`. `tests/conftest.py` — `temp_site` (sets `HYPERSHELL_SITE`,
`HYPERSHELL_DATABASE_FILE=<site>/local.db`, `LOGGING_LEVEL=DEBUG`), autouse `clean_env`.
Tag new tests `@mark.unit` / `@mark.integration` only (strict-markers).

## Docs to touch

- **`docs/_include/initdb_desc.rst:7-8`** — "applies a special purpose ``part:N`` **tag** to the new
  partition and remaining tasks." This is generated from the source string
  **`src/hypershell/data/__init__.py:190-191`** (`INITDB_HELP`). Per AGENTS.md, edit the source
  string AND regenerate/hand-sync the snippet in the **same commit**. Reword to describe the internal
  `part` column (or drop "tag").
- **`docs/_include/task_info_help.rst:34-38`** and **`docs/_include/task_search_help.rst:95-99`**
  (`--ignore-partitions` help; source = `task.py:148,596`) — these describe the "**numbering
  pattern**" of database *files*, not the part tag. Accurate as-is; **verify no change needed**.
- **`docs/manual.rst:104-108`** includes the initdb snippets (and `:25` the usage line) — rebuilds
  automatically once `_include/initdb_desc.rst` is fixed.
- **`docs/database.rst`** (+ `docs/_include/database.rst`) — contains **no** part/partition/rotate/tag
  prose today (grep clean); nothing to change.
- No other `docs/_include/*.rst` generated CLI help mentions `part` — confirmed. Because `part`
  stays internal (not added to `Task.columns`), `--fields`/`hs list` help need no change.

## Verification recipe (copy-paste; seed per-phase `verify:` gates)

Wrapper: `.agents/factory/bin/temp_site.sh` mirrors the pytest `temp_site` isolation and sets
`HYPERSHELL_DATABASE_FILE=$site/task.db`. **The site is deleted on exit**, so all `sqlite3`
inspection MUST run inside the same `sh -c "..."`. `sqlite3` is present (`/usr/bin/sqlite3`, 3.51).

**Baseline (current, pre-change) — for contrast:** 32-col task table with NO `part` column; a
submitted row has `tag = {"part": 0}`; `hs info <id>` prints `tags: part:0`; `hs list tag -f json`
shows `{"tag": {"part": 0}}`; `hs list part` → `CRITICAL Invalid field name "part"`.

**(a) `part` is a real COLUMN, default 0, set on submit:**
```
.agents/factory/bin/temp_site.sh sh -c 'printf "echo one\necho two\n" > t.in; uv run hs submit t.in >/dev/null 2>&1;
  sqlite3 "$HYPERSHELL_DATABASE_FILE" "PRAGMA table_info(task);" | grep -iw part;
  sqlite3 "$HYPERSHELL_DATABASE_FILE" "SELECT DISTINCT part FROM task;"'
```
PASS: `table_info` shows a `part|INTEGER` row (with default 0); `SELECT DISTINCT part` prints `0`.

**(b) `part` is GONE from the tag dict / tag output:**
```
.agents/factory/bin/temp_site.sh sh -c 'printf "echo one\n" > t.in; uv run hs submit t.in >/dev/null 2>&1;
  echo "col:"; sqlite3 "$HYPERSHELL_DATABASE_FILE" "SELECT tag FROM task LIMIT 1;";
  id=$(uv run hs list id -f plain | head -1);
  echo "info:"; uv run hs info "$id" 2>/dev/null | grep -i "tag";
  echo "json:"; uv run hs list tag -f json 2>/dev/null | tr -d " \n"'
```
PASS: DB `tag` = `{}`; `hs info` tags line is empty (`tags:`), no `part:0`; JSON shows `{"tag":{}}`.
(Add a user tag to prove real tags still flow: `printf "echo x  # HYPERSHELL: color:red\n"` → tag
`{"color":"red"}`, still no `part`.)

**(b2) `part` stays INTERNAL (no CLI surface):**
```
.agents/factory/bin/temp_site.sh sh -c 'uv run hs list --fields 2>&1 | tr " " "\n" | grep -w part && echo LEAK || echo internal-ok'
```
PASS: prints `internal-ok` (no `part` in `--fields`); `uv run hs list part` still errors
`Invalid field name "part"`. (If `part` appears, it was wrongly added to `Task.columns`.)

**(c) SQLite rotate + auto-union still works, driven by the column:**
```
.agents/factory/bin/temp_site.sh sh -c 'set -e; printf "echo a\necho b\necho c\n" > t.in;
  uv run hs submit t.in >/dev/null 2>&1;
  uv run hs update exit_status=0 completion_time=now --completed --no-confirm >/dev/null 2>&1 || \
    sqlite3 "$HYPERSHELL_DATABASE_FILE" "UPDATE task SET exit_status=0, completion_time=CURRENT_TIMESTAMP;";
  uv run hs initdb --rotate --yes 2>&1 | tail -2;
  echo "partition files:"; ls "$(dirname "$HYPERSHELL_DATABASE_FILE")" | grep -E "task\.[0-9]+$";
  echo "part col in partition:"; sqlite3 "$(dirname "$HYPERSHELL_DATABASE_FILE")/task.1" "SELECT DISTINCT part FROM task;";
  echo "union count:"; uv run hs list --count -f plain'
```
PASS: a `task.1` partition file exists; migrated rows in it carry `part = 1`; `hs list --count`
(auto-union across main + `task.1`) equals the full history count. NOTE: the completed-task setup
step above (marking `completion_time`/`exit_status`) is what makes `rotatedb` migrate rows — confirm
the exact "mark completed" idiom against `rotatedb` (`data/__init__.py:133-138`) during build.

**(d) Fingerprint/identity unchanged (unit-level, fastest signal):**
```
uv run pytest -v tests/test_source.py -k "fingerprint or part or schema"
uv run pytest -v tests/test_initdb.py -k rotate
uv run pytest -v -m unit
uv run pytest -v            # full suite (integration needs `uv sync` first)
```
PASS: all green. Spot-identity check across the change: `compute_fingerprint('echo a',0,{})` before
and after must be byte-identical (the md5 payload must not gain/lose a `part` key).

**Docs build gate:**
```
uv run sphinx-build -q docs docs/_build
```
PASS: **exactly the pre-existing baseline** and no new warnings — one `pkg_resources`
DeprecationWarning (environmental) + two "document isn't included in any toctree" warnings for
**`docs/cli/task_submit.rst`** and **`docs/manual.rst`**. Any new warning (esp. from
`_include/initdb_desc.rst`) is a regression.

## Inspecting the DB column directly

Because the temp site is torn down on exit, inspect inside the same `sh -c`:
- `sqlite3 "$HYPERSHELL_DATABASE_FILE" "PRAGMA table_info(task);"` — confirms `part` is a real
  column (name/type/default); baseline task table is 32 cols, post-change 33.
- `sqlite3 "$HYPERSHELL_DATABASE_FILE" "SELECT part, tag FROM task;"` — column populated, tag clean.
- Partition file: `sqlite3 "<dir>/task.1" "SELECT DISTINCT part FROM task;"` after `--rotate`.
