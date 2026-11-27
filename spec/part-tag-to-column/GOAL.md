# GOAL — Promote `part` from a bookkeeping tag to a first-class column

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** part-tag-to-column
- **kind:** refactor
- **appetite:** small

## Problem

`part` is an internal bookkeeping number used by the SQLite **database-partition** ("auto-union")
mechanism to record which partition/database-file a task belongs to. It predates the dedicated
`group` column, and — probably because `group` didn't exist yet — it was implemented as an entry in
the `Task` **`tag` dictionary** rather than as a real column: task creation injects `{'part': 0}`
into `tag` (`data/model.py:387`), and because that value is not a user annotation it then has to be
special-cased back out again — excluded from the task fingerprint (`data/model.py:414-423`) and
extracted with SQLite JSON functions (`json_extract(task.tag, …)`) in the partition/rotation queries
(`data/__init__.py`).

This conflates two different things in one field. The `tag` dict is meant for **user-supplied
metadata**; hijacking it to carry an internal partition index makes the user's annotations "dirty,"
forces the fingerprint and query code to route around the `part` key, and ties `part` to SQLite's
JSON syntax — which is more complicated than it needs to be and keeps `part` de-facto SQLite-only even
though a plain column would work under PostgreSQL too. With a new **`zone`** concept coming, we want
partition/bookkeeping state to be first-class schema and the user's `tag` annotations to stay clean,
rather than growing a second piece of management state hidden inside `tag`. In short: making `part` a
tag was a reasonable-at-the-time shortcut that has aged into a footgun — this is the cleanup.

## Outcome / vision

`part` is a first-class column on the `Task` model — placed as the **last column, immediately before
the `tag` dictionary field** — and is no longer stored inside `tag`. User annotations in `tag` contain
only user-supplied metadata again. The database partition/rotation logic reads `part` from the column
instead of extracting it from tag JSON, which simplifies those queries (no SQLite JSON functions for
`part`). The column is defined dialect-neutrally so it is usable under both SQLite and PostgreSQL, even
though the active partition mechanism stays SQLite-only for now. `part` is a **first-class, visible
field** (present in the model's column mapping, so it displays and selects like any other column), and
`hs list` / `hs search` gain a **`--part` option** to filter results to a specific partition — useful
after a rotate for targeting the tasks that landed in a given database-partition file. The change
applies to newly created / re-initialized databases; migrating databases that already store `part`
inside the tag JSON is out of scope.

## Acceptance criteria (the contract)

- **R1** — The `Task` model SHALL define `part` as a first-class column, positioned as the **last
  column, immediately before the `tag` dictionary field**, carrying the same default the tag form uses
  today (`0`).
- **R2** — Task creation SHALL set the `part` **column** and SHALL NOT inject a `part` key into the
  `tag` dictionary; after this change `tag` SHALL contain only user-supplied annotations (the
  `data/model.py:387` `{'part': 0}` injection is removed).
- **R3** — The `part` column SHALL be defined dialect-neutrally so it is usable under both the SQLite
  and PostgreSQL backends, i.e. it SHALL NOT depend on SQLite-only constructs.
- **R4** — WHERE the SQLite database-partition / auto-union logic currently derives `part` from the
  tag JSON, it SHALL instead read the `part` column and SHALL NOT use SQLite JSON functions (e.g.
  `json_extract`) to obtain `part`; the partitioning behavior/outcome SHALL be preserved.
- **R5** — `part` SHALL remain excluded from the task fingerprint/identity (re-running the same work
  still yields the same fingerprint) and SHALL NOT be stored as a user `tag` (it is a column, not a
  tag).
- **R6** — WHEN a database is newly created or re-initialized (`hs initdb`), the created schema SHALL
  include the `part` column with its default. Pre-existing databases that store `part` in the tag JSON
  are out of scope (see Non-goals).
- **R7** — The existing test suite SHALL continue to pass and the docs build SHALL stay clean; the new
  column behavior (set on submit, absent from `tag`, excluded from the fingerprint, read by the
  partition logic) **and the `--part` filter** SHALL be covered by tests, and any documentation that
  describes `part` as a tag or the JSON-based partition query SHALL be updated to match.
- **R8** — `part` SHALL be a **first-class, selectable/displayable field**: it is included in the
  model's `Task.columns` mapping, so it is available wherever other columns are (e.g. `hs list --all`,
  field selection / `--fields`, `hs info`), consistent with columns like `group`.
- **R9** — `hs list` and `hs search` SHALL accept a **`--part N`** option that filters results to tasks
  whose `part` equals `N` (targeting a specific rotated partition). The affected `docs/_include/*.rst`
  help snippets and the `share/` shell completions SHALL be updated in the same commit (per the §12
  same-commit rule).

## Non-goals (no-gos)

- **Migrating pre-existing databases** that already carry `part` inside the tag JSON — no data
  migration, add-column-on-connect, or migration command. This is forward-only; older databases may
  require re-initialization.
- **Implementing PostgreSQL partition/rotation** on the new column. The active auto-union mechanism
  stays SQLite-only; the column merely does not *preclude* future PostgreSQL use. (The `--part` filter,
  being a plain column filter, works on any backend, but on PostgreSQL `part` is always `0` because no
  rotation populates it.)
- The forthcoming **`zone`** concept — this task only clears the way (keeping `tag` clean); `zone` is
  separate future work.
- Any change to the `group` column, to user-`tag` semantics beyond removing the `part` key, or to
  unrelated schema; and no broader rewrite of the partition/auto-union mechanism beyond sourcing
  `part` from the column and simplifying the resulting queries.

## Clarifications

- **Q:** How should existing databases (which store `part` in the tag JSON, with no `part` column) be
  handled? — **A:** Forward-only: new / re-initialized databases get the column; existing part-in-tag
  databases are out of scope (may require re-init) (resolved 2026-07-31).
- **Q:** `part` is SQLite-only today — what is the PostgreSQL scope? — **A:** Make it a dialect-neutral
  column so PostgreSQL *could* use it later; do **not** build PostgreSQL partition/rotation now
  (resolved 2026-07-31).
- **Q:** Should `part` be visible in the CLI? — **A:** *(Revised 2026-07-31, superseding the earlier
  "keep internal" answer.)* Yes — `part` SHALL be a first-class, visible field (in `Task.columns`), and
  `hs list` / `hs search` SHALL gain a `--part N` filter to target a specific rotated partition. (The
  original shaping answer was "keep internal"; the maintainer changed course after seeing the
  internal-vs-external trade-off.)

## Related materials

- `src/hypershell/data/model.py` — the `Task` model (add the `part` column before `tag`; add `'part'`
  to the `Task.columns` mapping); `part` currently injected as `{'part': 0}` (~`:387`) and excluded
  from the fingerprint (~`:414-423`). Task state/query logic lives here as `Task` classmethods.
- `src/hypershell/task.py` — the `list` and `search` apps (add the `--part` option + filter; they
  already carry `--ignore-partitions`, ~`:148-191` / ~`:596-684`) and the SQLite auto-union / partition
  logic (`attach database … as part_N`, ~`:840-856`).
- `src/hypershell/data/__init__.py` — `rotatedb()` (writes/reads `part` via JSON funcs today, ~`:139`,
  `:147`, `:157`) and `InitDBApp` / schema creation.
- `src/hypershell/submit.py` — task ingestion / tag-assembly path where `part` currently enters.
- `docs/_include/*.rst` (generated CLI help for `list`/`search`) and `share/` (bash + zsh completions),
  which the §12 same-commit rule ties to the new `--part` option.
- `AGENTS.md` — "Task lifecycle contract", "Data layer specifics", and §12 same-commit rules;
  `data/model.py` is flagged highest-blast-radius.
- Future context: the planned **`zone`** concept (motivates keeping `tag` for user annotations only).
