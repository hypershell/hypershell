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
    name: "Convert `part` to a column (model + rotatedb) with same-commit help/doc + tests"
    status: pending
    satisfies: [R1, R2, R3, R4, R5, R6, R7]
    depends_on: []
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

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/part-tag-to-column/TECH.md`); the per-phase
checklist below is the work.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`04`.

## Conventions (apply to every phase)

- Invariants and code style come from [`AGENTS.md`](../../AGENTS.md) /
  [`invariants.md`](../../.agents/factory/invariants.md). Touched sections: **§1** (task lifecycle —
  `part` is orthogonal to the state predicates; keep state logic in `Task` classmethods), **§10**
  (schema via `create_all`; forward-only), **§12** (same-commit help/doc snippet; tag tests
  `@mark.unit`). The one recorded deviation (a real column deliberately omitted from `Task.columns` to
  stay internal) is in PLAN §3.
- One atomic commit containing the code, the docs/help snippet, the tests, **and** the `TECH.md` state
  change. Subject: `[refactor] Build part-tag-to-column P1: …`.
- **No `Co-Authored-By` trailer** (repo convention).
- **Coupled core:** `data/model.py` is highest-blast-radius. Change model + `rotatedb` with a single
  coherent view; do not split them (the model change breaks `rotatedb` until it is updated too).

---

## Phase P1 — Convert `part` to a first-class column
**Satisfies:** R1, R2, R3, R4, R5, R6 (docs/tests of R7) · **Depends on:** —
**Goal:** `part` is a real, dialect-neutral `Task` column (created in fresh schema, defaulting to 0),
no longer stored in the `tag` dict; `rotatedb()` reads/writes the column with no SQLite JSON functions;
`part` stays internal (off the wire and CLI); the suite and docs build are green. One atomic, end-to-end
verifiable change.

### Model — `src/hypershell/data/model.py`
- [ ] Add the column between `fingerprint` (`:299`) and `tag` (`:301`):
      `part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)` with a one-line declarative
      comment (partition index / SQLite rotation bookkeeping / internal). `INTEGER` is already
      dialect-neutral (R1, R3).
- [ ] **Do NOT** add `'part'` to the `Task.columns` dict (`:303-336`) — keeps `part` off
      `to_dict`/`to_json`/`serialize_tasks`/`--fields`/`hs list` (R5, no-CLI-surface non-goal). See PLAN
      §3 deviation.
- [ ] `Task.new`: change `:387` `tag = {**(tag or {}), **inline_tags, **{'part': 0, }}` →
      `tag = {**(tag or {}), **inline_tags}`. Leave the `Task(...)` constructor (`:404-406`) as-is — the
      column `default=0` supplies the value; the retry builder (copies `tag`, not `part`) correctly yields
      `part=0` (R2).
- [ ] `compute_fingerprint`: remove the `if key != 'part'` filter at `:423` (→ `'tags': dict(tags or {})`)
      and update the docstring at `:414` (`part` is now a column, not an excluded tag). Byte-identical
      output (R5).

### Rotation — `src/hypershell/data/__init__.py` (`rotatedb`)
- [ ] `:139` write → `.update({Task.part: part_id})` (drop the `json_set` text expression) (R4).
- [ ] `:147` read → `Task.part == part_id`; `:157` read → `Task.part != part_id` (drop
      `Task.tag['part']`/`type_coerce`/`JSON`) (R4).
- [ ] Remove the now-unused `type_coerce` / `JSON` imports **after** grep-confirming they are unused
      elsewhere in the file (`text` stays — used by `VACUUM`). Leave the `exit_status.isnot(None)` filter
      (`:138`) and `next_rotate_path` untouched. Do not touch `auto_union_sqlite` in `task.py`.

### Docs / help (R7, §12 same-commit)
- [ ] Update `INITDB_HELP` in `data/__init__.py` (the `--rotate` description that calls `part` a "special
      purpose `part:N` tag") to reflect that `part` is now an internal column, and regenerate the matching
      `docs/_include/initdb_desc.rst`. Regenerate the `hs` man page (`share/man/man1/hs.1`) if it carries
      the initdb description. `--ignore-partitions` help and `docs/database.rst` need no change;
      completions carry no long descriptions so `share/` completions are unchanged.

### Tests (R7) — `@mark.unit`
- [ ] `tests/test_source.py:63-67,:86`: drop the hardcoded `{'part': …}` literals from the
      `compute_fingerprint` calls (the tag no longer carries `part`); confirm fingerprints stay stable.
- [ ] `tests/test_source.py:99-116`: assert the real `Task` table **has** a `part` column **and** that
      `'part'` is **absent** from `Task.columns` (locks the internal-only deviation).
- [ ] `tests/test_initdb.py::test_rotate` (currently a stub): make it actually rotate — submit + complete
      tasks, run `rotatedb()` (or `hs initdb --rotate`), assert completed rows carry the right `part`
      column value, land in the partition file, and are dropped from `main`; assert the tag never contains
      `part`.

### Verify
- [ ] Frontmatter gate: `uv run pytest -q && uv run sphinx-build -E -b html docs docs/_build` (full suite
      green; docs build with only the 2 pre-existing baseline toctree warnings).
- [ ] CLI drive (throwaway site):
      `.agents/factory/bin/temp_site.sh sh -c 'uv run hs initdb && printf "echo a\necho b\n" | uv run hs submit && uv run hs initdb --rotate && uv run hs list'`
      runs clean.
- [ ] Column + tag-clean (sqlite3 inside the same `sh -c`):
      `sqlite3 "$HYPERSHELL_DATABASE_FILE" "select part from task; select count(*) from task where json_extract(tag,'\$.part') is not null"`
      → `part=0`, count `0`.
- [ ] Internal-only: `hs list part` still errors "Invalid field name"; `hs info <id>` shows no `part:` tag.
- [ ] Grep: no `json_set`/`json_extract`/`type_coerce` reference to `part` remains in `src/hypershell`.

**Touches:** `src/hypershell/data/model.py`, `src/hypershell/data/__init__.py`,
`docs/_include/initdb_desc.rst` (+ `INITDB_HELP` source), `tests/test_source.py`,
`tests/test_initdb.py`, and possibly `share/man/man1/hs.1`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses authoritative).
2. Pre-flight: clean tree, on `feature/part-tag-to-column`, `develop` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` — never advance on a checkbox alone; the CLI-drive/sqlite/grep bullets are
   part of the gate.
5. Amend this file if reality diverges; STOP and escalate only on a `GOAL.md` contradiction (e.g. a
   hidden second reader of the `part` tag, or a state-predicate entanglement not seen in research).
6. Mark P1 `done`, `--touch`; one `[refactor]` commit; stop and report.
