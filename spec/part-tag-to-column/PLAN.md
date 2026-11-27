# PLAN — Promote `part` from a bookkeeping tag to a first-class column

> **Status:** Draft for review (re-planned 2026-07-31 after GOAL reshape) · **Last updated:** 2026-07-31
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Add `part` as a real integer column on the `Task` model (between `fingerprint` and `tag`, mirroring the
dialect-neutral integer columns, `default=0`), add it to the `Task.columns` mapping so it is a visible,
selectable field like `group`, and stop stuffing it into the `tag` JSON dict. The SQLite `rotatedb()`
maintenance routine (behind `hs initdb --rotate`) is rewritten to read/write the column instead of
`json_set`/`json_extract` on the tag (R4). Finally, `hs list`/`hs search` — which are a **single app**
(`TaskSearchApp`) — gain a `--part N` filter to target a specific rotated partition (R9). Two vertical
slices: (P1) the data-layer conversion + visible column, (P2) the `--part` CLI filter with its
same-commit help/completions. This is a small, bounded change; research bought down the two unknowns
(the rotation mechanism and the CLI/§12 machinery).

> **Scope note:** the GOAL was reshaped on 2026-07-31 — the maintainer reversed the original "keep
> `part` internal" decision. `part` is now user-visible and gains a `--part` filter (R8, R9). This
> **reverses** the earlier design's one deviation (omitting `part` from `Task.columns`): it is now a
> normal column entry, so the deviation table is empty.

## 2. Design

### Data model — `src/hypershell/data/model.py`

- **New column (R1, R3):** insert between `fingerprint` (`:299`) and `tag` (`:301`, current last column):
  ```python
  part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)  # Partition index for SQLite database rotation.
  ```
  `INTEGER` (`model.py:96`) maps to `INTEGER` on both SQLite and PostgreSQL — dialect-neutral, no
  `with_variant`. `part` is not a reserved word (unlike `group`), so no `quote=`. Created automatically
  in a fresh DB by `metadata.create_all` (R6).
- **Visible field (R8):** add `'part': int` to the `Task.columns` dict (`:303-336`), placed next to the
  column's model position (after `fingerprint`, before `tag`). This flows `part` through
  `to_dict`/`to_json`/`from_dict`/`serialize_tasks` and CLI field validation (`task.py:545-549` checks
  `name in Task.columns`), so `hs list part`, `--fields`/`-x`, `hs list --all`, and `hs info` treat it
  like any other column — exactly as `group` behaves.
- **Stop hijacking the tag (R2):** in `Task.new`, change `:387`
  `tag = {**(tag or {}), **inline_tags, **{'part': 0, }}` → `tag = {**(tag or {}), **inline_tags}`. The
  `Task(...)` constructor (`:404-406`) needs no explicit `part=` — the column default supplies `0`; the
  retry builder copies `tag` but not `part`, so retry rows correctly start at `part=0`.
- **Fingerprint (R5):** in `compute_fingerprint`, drop the `if key != 'part'` filter at `:423`
  (→ `'tags': dict(tags or {})`) and update the docstring at `:414`. Byte-identical, because the tag dict
  it receives no longer contains `part`. (`part` being *visible* does not make it part of identity.)

### Rotation — `src/hypershell/data/__init__.py` (`rotatedb`, R4)

Replace the three JSON expressions with plain column access; behavior preserved:

| Line | Before | After |
|------|--------|-------|
| `:139` (write) | `.update({Task.tag: text("json_set(task.tag, '$.part', :v)")…})` | `.update({Task.part: part_id})` |
| `:147` (read)  | `Task.tag['part'] == type_coerce(part_id, JSON)` | `Task.part == part_id` |
| `:157` (read)  | `Task.tag['part'] != type_coerce(part_id, JSON)` | `Task.part != part_id` |

Then remove the now-unused `type_coerce`/`JSON` imports **after grep-confirming** they're unused
elsewhere in the file (`text` stays — used by the `VACUUM` statements). The `exit_status.isnot(None)`
filter (`:138`) and `next_rotate_path` (`:163-174`) are untouched.

### CLI filter — `src/hypershell/task.py` (`--part`, R9)

`hs list` and `hs search` are the **same app**, `TaskSearchApp` (`task.py:601`; registered `list` at
`__init__.py:130`, `search` at `task.py:1235`), so `--part` is added **once**:

- Add `part_filter: Optional[int] = None` to the `SearchableMixin` and
  `interface.add_argument('--part', type=int, default=None, dest='part_filter')` to `TaskSearchApp`,
  mirroring the `-g/--group` option (`:465`).
- In `__build_filters` (`:513-538`), append `f'part == {self.part_filter}'` when `part_filter is not
  None` (mirroring the group filter at `:531-532`, `:646-647`). This routes through `WhereClause`, which
  resolves `getattr(Task, 'part')` — valid now that `part` is a real column. `--part 0` is a legitimate
  filter (main partition); absent flag ⇒ no filter (`is not None` guard, so `0` is not treated as
  "unset").
- **Auto-union composition:** `auto_union_sqlite` builds the temp view in `run()` (`:684-685`) before
  `build_query()` (`:694`), so `WHERE part = N` over the union correctly returns partition N's rows.

### Docs / help / completions (R7, R9, §12 same-commit)

- **`hs initdb --rotate` help:** `part` is no longer a tag — update `INITDB_HELP` (`data/__init__.py`)
  and regenerate/hand-edit `docs/_include/initdb_desc.rst`.
- **`--part` help (hand-maintained, no generator):** update `SEARCH_USAGE`/`SEARCH_HELP` constants
  (`task.py:554`/`:568`) and the hand-authored `docs/_include/task_search_help.rst` (and
  `task_search_usage.rst` iff the synopsis line changes) — following the `961fb22 [feature] Add --all`
  precedent.
- **Completions:** bash `_hs_task_search` (add `--part` to `all_opts`, ~`:377-380`, + optional value
  branch); zsh `_hs_list` (add an `_arguments` line mirroring `--ignore-partitions`, ~`:422`).
  `hsx`/`hs cluster` completions are separate and unaffected.
- **Man pages / CI:** man pages regenerate at release time only (not same-commit; matches the `--all`
  precedent). The CI wheel-metadata assertion checks a fixed path list — unaffected by content changes.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | New `part` `mapped_column(INTEGER, nullable=False, default=0)` between `fingerprint` and `tag`. |
| R2 | `Task.new:387` drops the `{'part': 0}` merge; creation sets the column (default), `tag` = user annotations only. |
| R3 | `INTEGER` — dialect-neutral (SQLite/PG); no SQLite-only construct. |
| R4 | `rotatedb` `:139/:147/:157` rewritten to `Task.part`; JSON funcs & dead imports removed; outcome preserved. |
| R5 | Fingerprint `part`-exclusion removed (byte-identical); `part` is a column, not a user tag. |
| R6 | Mapped column → `metadata.create_all` includes it in fresh `hs initdb` schema. Forward-only. |
| R7 | Update fingerprint/rotate tests, add `--part` tests; update help snippets + completions; full suite + docs build green. |
| R8 | `'part'` added to `Task.columns` → selectable/displayable via `--fields`, `hs list --all`, `hs info`, like `group`. |
| R9 | `--part N` option on `TaskSearchApp` (`hs list`/`hs search`) → `part == N` filter over the (possibly unioned) query; help + completions updated same-commit. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before research and again after this (re-planned) design.

- **§1 Task lifecycle (`data/model.py`, highest blast radius)** — `part` is orthogonal to the
  nullable-column state predicates; it is not added to any state query, and the `--part` filter is an
  ordinary `WHERE` on a non-state column that composes with (does not alter) existing predicates.
  Task-state logic stays in `Task` classmethods; identity semantics preserved (fingerprint change is
  byte-identical). **Honored.**
- **§10 Config / data layer** — schema via `metadata.create_all` at `hs initdb`; `create_all` never
  `ALTER`s → pre-existing DBs out of scope (forward-only, GOAL non-goal). No config-singleton change.
  **Honored.**
- **§12 Project conventions (same-commit)** — the new `--part` option updates the `task_search_*` help
  snippets **and** the bash+zsh completions in the same commit; `hs initdb --rotate` help snippet
  updated with its behavior change; new tests `@mark.unit`; no hardcoded version; no dependency-floor or
  CI-metadata changes. **Honored.**
- Sections **§2–§9, §11** — not touched (pure data-model + one query-site + one leaf CLI app; no
  server/client/queue/FSM/signal/cluster involvement). The wire now carries `part` (it is in `columns`),
  exactly as it carries `group` today — inert for execution, round-trips like any column.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| — (none) | — | — |

*(The prior draft's one deviation — omitting `part` from `Task.columns` to keep it internal — is removed
by the GOAL reshape; `part` is now a normal column entry.)*

## 4. Rabbit holes (resolved)

- **What is "database rotation" and where does `part` flow?** → `rotatedb()` behind the manual,
  SQLite-only `hs initdb --rotate`; exactly three JSON expressions to convert
  ([`research/01`](research/01-part-lifecycle-and-partition-mechanism.md)).
- **Auto-union & `part`** → the `part_{i+1}` attach alias is a filesystem index unrelated to the value;
  the union is `SELECT *` (no change), but old partition files lacking the column are the forward-only
  hazard ([`research/01`](research/01-part-lifecycle-and-partition-mechanism.md),
  [`research/03`](research/03-initdb-submit-and-config.md)).
- **Where does `--part` hook in, and is it one app or two?** → one app (`TaskSearchApp`); mirror
  `-g/--group`; the filter composes over the union view ([`research/05`](research/05-list-search-apps-and-part-filter.md)).
- **§12 machinery** → help includes are hand-maintained `task_search_*` (no generator); one bash + one
  zsh completion function; man pages release-time only
  ([`research/06`](research/06-help-snippets-and-completions.md)).

## 5. Risks & open questions

- **Forward-only breakage (documented, accepted):** new code against an old DB → INSERT/SELECT fail on
  the missing column, and auto-union of old partition files raises a column-count mismatch until
  `--ignore-partitions`. GOAL non-goal; worth a release-note line.
- **`part` now on the wire:** adding `part` to `columns` serializes it in task bundles (like `group`).
  Inert for execution and round-trips like any column; noted for awareness, not a concern in a
  single-version cluster.
- **User `-t part:5` now persists as a plain user tag** (previously clobbered to 0). Now that `part` is
  also a prominent column + filter, a same-named user tag is inert but *potentially confusing*. Default:
  no key reservation (tags are the user's namespace; the column/`--part` are separate). Flag for the
  human — trivial to reserve later if undesired.
- **`--part` on `hs update`?** Out of R9 scope (R9 says list/search). Not added.

## 6. Verification strategy

Seeds the phase `verify:`. Prefer driving the real CLI in a throwaway site.

- **Column + tag-clean (P1):** submit a task, then
  `sqlite3 "$HYPERSHELL_DATABASE_FILE" "select part from task; select count(*) from task where json_extract(tag,'\$.part') is not null"`
  → `part = 0`, count `0`. `hs info <id>` shows no `part:` tag; `hs list part` is now a **valid** field
  (shows `0`).
- **Rotation (P1):** `hs initdb --rotate` after completing tasks → completed rows carry the right `part`
  and land in the partition file; `hs list` (auto-union) still works.
- **`--part` filter (P2):**
  `.agents/factory/bin/temp_site.sh sh -c '…submit + complete + hs initdb --rotate…; uv run hs list --part 1'`
  returns only partition-1 rows; `hs list --part 0` returns the main partition; absent ⇒ all.
- **Tests:** `tests/test_source.py` (fingerprint stable; `part` in `Task.columns` **and** in the real
  table), `tests/test_initdb.py::test_rotate` (real column-based rotation), `tests/test_list.py` (`--part`
  filter cases), full `uv run pytest -q`.
- **Docs/completions:** `uv run sphinx-build -E -b html docs docs/_build` → only the 2 baseline toctree
  warnings; the updated `task_search_help.rst` renders; `--part` present in bash/zsh completions.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md) (briefs `01`–`06`).*
