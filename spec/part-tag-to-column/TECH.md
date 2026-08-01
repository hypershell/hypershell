---
slug: part-tag-to-column
title: "Promote `part` from a bookkeeping tag to a first-class column"
kind: refactor
appetite: small
status: in_progress
branch: feature/part-tag-to-column
base: develop
current_phase: P1
last_updated: "2026-07-31"
phases:
  - id: P1
    name: "Data layer: `part` column (visible) + rotatedb via column + same-commit initdb help"
    status: pending
    satisfies: [R1, R2, R3, R4, R5, R6, R8]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -q && uv run sphinx-build -E -b html docs docs/_build"
  - id: P2
    name: "CLI: `--part N` filter on hs list/search + same-commit help snippets + completions"
    status: pending
    satisfies: [R9, R7]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: "uv run pytest -q && uv run sphinx-build -E -b html docs docs/_build"
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
---

# TECH.md — Promote `part` from a bookkeeping tag to a first-class column

The **context engine and finite-state machine** for building this feature. The YAML frontmatter above
is the resume ground-truth (read it with `uv run python .agents/factory/bin/next_phase.py
spec/part-tag-to-column/TECH.md`); the per-phase checklists below are the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract (reshaped
  2026-07-31: `part` is now visible + gains `--part`).
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`06`.

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Touched sections: **§1** (task lifecycle —
  `part` is orthogonal to the state predicates; keep state logic in `Task` classmethods), **§10**
  (schema via `create_all`; forward-only), **§12** (same-commit help snippets + completions; tag tests
  `@mark.unit`). No deviations (PLAN §3).
- One atomic commit per phase containing the code, the same-commit docs/help/completions, the tests,
  **and** the `TECH.md` state change. Subjects: `[refactor] Build part-tag-to-column P<n>: …`.
- **No `Co-Authored-By` trailer** (repo convention).
- **Coupled core:** `data/model.py` is highest-blast-radius. In P1, change the model + `rotatedb` with a
  single coherent view (the model change breaks `rotatedb` until it is updated too).

---

## Phase P1 — Data layer: visible `part` column + rotation via the column
**Satisfies:** R1, R2, R3, R4, R5, R6, R8 (+ data-layer tests/docs of R7) · **Depends on:** —
**Goal:** `part` is a real, dialect-neutral, **visible** `Task` column (in `Task.columns`, default 0),
no longer in the `tag` dict; `rotatedb()` reads/writes the column with no SQLite JSON functions; fresh
`hs initdb` schema includes it; suite + docs build green. End-to-end verifiable without the CLI filter.

### Model — `src/hypershell/data/model.py`
- [ ] Add the column between `fingerprint` (`:299`) and `tag` (`:301`):
      `part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)` with a one-line declarative
      comment (partition index for SQLite database rotation) (R1, R3).
- [ ] Add `'part': int` to the `Task.columns` dict (`:303-336`) in the matching position (after
      `fingerprint`, before `tag`) so `part` is selectable/displayable like `group` (R8).
- [ ] `Task.new`: change `:387` → `tag = {**(tag or {}), **inline_tags}` (drop `**{'part': 0, }`); leave
      the `Task(...)` constructor as-is (column `default=0` supplies the value; retries → `part=0`) (R2).
- [ ] `compute_fingerprint`: remove the `if key != 'part'` filter at `:423` (→ `'tags': dict(tags or {})`)
      and fix the docstring at `:414` (R5).

### Rotation — `src/hypershell/data/__init__.py` (`rotatedb`)
- [ ] `:139` write → `.update({Task.part: part_id})`; `:147` → `Task.part == part_id`; `:157` →
      `Task.part != part_id` (drop the `json_set` / `Task.tag['part']` / `type_coerce` / `JSON`) (R4).
- [ ] Remove now-unused `type_coerce`/`JSON` imports **after** grep-confirming they're unused elsewhere
      in the file (`text` stays — used by `VACUUM`). Leave the `exit_status.isnot(None)` filter (`:138`)
      and `next_rotate_path` untouched; do not touch `auto_union_sqlite`.

### Docs / help (R7, §12 same-commit)
- [ ] Update `INITDB_HELP` (`data/__init__.py`) so the `--rotate` description no longer calls `part` a
      "special purpose `part:N` tag", and hand-edit the matching `docs/_include/initdb_desc.rst`. (Man
      page is release-time; completions carry no long description → unchanged.)

### Tests (R7) — `@mark.unit`
- [ ] `tests/test_source.py:63-67,:86`: drop the hardcoded `{'part': …}` literals from
      `compute_fingerprint` calls; confirm fingerprints stay stable.
- [ ] `tests/test_source.py:99-116`: assert `'part'` **is** in `Task.columns` **and** the real `Task`
      table has a `part` column.
- [ ] `tests/test_initdb.py::test_rotate` (currently a stub): make it actually rotate — submit + complete
      tasks, run `rotatedb()` / `hs initdb --rotate`, assert completed rows carry the right `part` value,
      land in the partition file, and are dropped from `main`; assert `tag` never contains `part`.
- [ ] Fix any existing test that asserts an exact column/serialization set now that `part` is in
      `columns` (grep for surprises; the full-suite verify will surface them).

### Verify
- [ ] Frontmatter gate: `uv run pytest -q && uv run sphinx-build -E -b html docs docs/_build` (full suite
      green; docs build with only the 2 pre-existing baseline toctree warnings).
- [ ] CLI drive: `.agents/factory/bin/temp_site.sh sh -c 'uv run hs initdb && printf "echo a\necho b\n" | uv run hs submit && uv run hs initdb --rotate && uv run hs list part'` runs clean and `part` shows as a column.
- [ ] Column + tag-clean (sqlite3 in the same `sh -c`):
      `sqlite3 "$HYPERSHELL_DATABASE_FILE" "select part from task; select count(*) from task where json_extract(tag,'\$.part') is not null"`
      → `part=0`, count `0`.
- [ ] Grep: no `json_set`/`json_extract`/`type_coerce` reference to `part` remains in `src/hypershell`.

**Touches:** `src/hypershell/data/model.py`, `src/hypershell/data/__init__.py`,
`docs/_include/initdb_desc.rst`, `tests/test_source.py`, `tests/test_initdb.py`.

## Phase P2 — CLI: `--part N` filter on `hs list` / `hs search`
**Satisfies:** R9 (+ CLI tests/docs of R7) · **Depends on:** P1
**Goal:** `hs list --part N` / `hs search --part N` return only tasks whose `part == N` (targeting a
rotated partition), with the same-commit help snippets and shell completions updated. End-to-end
verifiable: rotate, then filter by partition.

### CLI — `src/hypershell/task.py` (single `TaskSearchApp`)
- [ ] Add `part_filter: Optional[int] = None` to `SearchableMixin` and
      `interface.add_argument('--part', type=int, default=None, dest='part_filter')` to `TaskSearchApp`,
      mirroring the `-g/--group` option (`:465`).
- [ ] In `__build_filters` (`:513-538`), append `f'part == {self.part_filter}'` when `part_filter is not
      None` (mirroring the group filter at `:531-532`, `:646-647`). `--part 0` is a valid filter (main
      partition); absent ⇒ no filter. `--part` is added **once** (list and search are the same app).

### Docs / help / completions (R7, R9, §12 same-commit)
- [ ] Update `SEARCH_USAGE`/`SEARCH_HELP` (`task.py:554`/`:568`) to document `--part`, and hand-edit
      `docs/_include/task_search_help.rst` (and `task_search_usage.rst` iff the synopsis changes) — per
      the `961fb22 [feature] Add --all` precedent.
- [ ] bash completion `_hs_task_search`: add `--part` to the `all_opts` string (~`:377-380`) (+ optional
      `--part)` value branch). zsh completion `_hs_list`: add an `_arguments` line mirroring
      `--ignore-partitions` (~`:422`). Do not touch `hsx`/`hs cluster` completions.

### Tests (R7) — `@mark.unit`
- [ ] `tests/test_list.py`: add `--part` cases mirroring existing filter tests — after a rotate,
      `--part N` returns only partition-N rows, `--part 0` returns the main partition, and omitting it
      returns all; an out-of-range `--part` returns nothing.

### Verify
- [ ] Frontmatter gate: `uv run pytest -q && uv run sphinx-build -E -b html docs docs/_build`.
- [ ] CLI drive (partition targeting): `.agents/factory/bin/temp_site.sh sh -c "…submit + complete tasks + uv run hs initdb --rotate…; uv run hs list --part 1; uv run hs list --part 0"` returns the right partition subsets.
- [ ] Completions: `--part` appears in `share/` bash + zsh completions for `hs list`/`hs search`.

**Touches:** `src/hypershell/task.py`, `docs/_include/task_search_help.rst` (± `task_search_usage.rst`),
`share/` bash + zsh completions, `tests/test_list.py`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `feature/part-tag-to-column`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone; the CLI-drive/sqlite/completions
   bullets are part of the gate.
5. Amend this file if reality diverges; STOP and escalate only on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[refactor]` commit; stop and report.
