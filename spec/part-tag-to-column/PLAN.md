# PLAN — Promote `part` from a bookkeeping tag to a first-class column

> **Status:** Draft for review · **Last updated:** 2026-07-31
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Add `part` as a real integer column on the `Task` model (between `fingerprint` and `tag`, mirroring the
dialect-neutral integer columns, `default=0`) and stop stuffing it into the `tag` JSON dict. The only
consumer — the SQLite `rotatedb()` maintenance routine behind `hs initdb --rotate` — is rewritten to
read/write the column instead of `json_set`/`json_extract` on the tag, which is the promised
simplification (R4). `part` is kept **internal**: it is a real DB column but is deliberately omitted from
the `Task.columns` allowlist, so it never reaches the CLI or the wire. This is a small, cohesive,
coupled-core change (`data/model.py` + `data/__init__.py`) shipped as a single vertical slice with its
tests and the one same-commit doc/help update.

## 2. Design

### Data model — `src/hypershell/data/model.py`

- **New column (R1, R3):** insert between `fingerprint` (`:299`) and `tag` (`:301`, the current last
  column):
  ```python
  part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)  # Partition index (SQLite rotation bookkeeping); internal.
  ```
  `INTEGER` (`model.py:96`) maps to `INTEGER` on both SQLite and PostgreSQL, so the column is
  dialect-neutral with no `with_variant`. `part` is not a reserved word (unlike `group`), so no `quote=`.
  It is created automatically in a fresh DB by `metadata.create_all` (R6).
- **Keep it internal (R5, no-CLI-surface non-goal):** do **not** add `'part'` to the `Task.columns` dict
  (`:303-336`). That dict is the allowlist for `to_dict`/`to_json`/`from_dict`/`repr`/`serialize_tasks`
  and for CLI field validation / `--fields` / `hs list`. Omitting `part` keeps it off the wire and out of
  the CLI while it remains a first-class DB column read via the `Task.part` ORM attribute. (This asymmetry
  is the one deliberate complexity — PLAN §3 deviation table.)
- **Stop hijacking the tag (R2):** in `Task.new`, change `:387`
  `tag = {**(tag or {}), **inline_tags, **{'part': 0, }}` → `tag = {**(tag or {}), **inline_tags}`. The
  `Task(...)` constructor (`:404-406`) needs no explicit `part=` — the column default supplies `0`. The
  retry builder copies `tag` but not `part`, so retry rows correctly start at `part=0` (a new row lives in
  `main`).
- **Fingerprint (R5):** in `compute_fingerprint`, drop the `if key != 'part'` filter at `:423`
  (→ `'tags': dict(tags or {})`) and update the docstring at `:414`. Byte-identical output, because the
  tag dict it receives no longer contains `part`.

### Rotation — `src/hypershell/data/__init__.py` (`rotatedb`, R4)

Replace the three JSON expressions with plain column access; behavior is preserved:

| Line | Before | After |
|------|--------|-------|
| `:139` (write) | `.update({Task.tag: text("json_set(task.tag, '$.part', :v)")…})` | `.update({Task.part: part_id})` |
| `:147` (read)  | `Task.tag['part'] == type_coerce(part_id, JSON)` | `Task.part == part_id` |
| `:157` (read)  | `Task.tag['part'] != type_coerce(part_id, JSON)` | `Task.part != part_id` |

Then remove the now-unused `type_coerce` / `JSON` imports **after grep-confirming** they're unused
elsewhere in the file (`text` stays — used by the `VACUUM` statements). The `exit_status.isnot(None)`
filter (`:138`) and `next_rotate_path` (`:163-174`, filesystem-based) are untouched. `auto_union_sqlite`
(`task.py`) is untouched — its `part_{i+1}` attach alias is a filesystem index unrelated to the column,
and it never reads `part`.

### Docs / help (R7, §12 same-commit)

`part` is no longer a tag, so update `INITDB_HELP` (`data/__init__.py`, the `--rotate` description that
says "applies a special purpose `part:N` tag") and regenerate the matching `docs/_include/initdb_desc.rst`
in the **same commit**; regenerate the `hs` man page if it carries the initdb description. `--ignore-partitions`
help and `docs/database.rst` reference file numbering (not the tag) and need no change. `share/` completions
carry no long descriptions → no completion change.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | New `part` `mapped_column(INTEGER, nullable=False, default=0)` inserted between `fingerprint` and `tag` in `data/model.py`. |
| R2 | `Task.new:387` drops the `{'part': 0}` merge; task creation sets the column (via default), `tag` holds only user annotations. |
| R3 | `INTEGER` type — dialect-neutral (SQLite/PG), no SQLite-only construct. |
| R4 | `rotatedb` `:139/:147/:157` rewritten to `Task.part` (update / `==` / `!=`); JSON functions & dead imports removed; partition outcome preserved. |
| R5 | Fingerprint `part`-exclusion removed (byte-identical); `part` omitted from `Task.columns` → absent from tag output, CLI, and wire. |
| R6 | Column is a mapped column → `metadata.create_all` includes it in fresh `hs initdb` schema. Forward-only (create_all never ALTERs old DBs). |
| R7 | Update `tests/test_source.py` fingerprint/column tests, fill the `tests/test_initdb.py` `test_rotate` stub with real column-based rotation coverage; update `INITDB_HELP` + `initdb_desc.rst`; full suite + docs build green. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked before research and again after this design (both checkpoints).

- **§1 Task lifecycle (`data/model.py`, highest blast radius)** — `part` is **orthogonal** to the
  nullable-column state predicates (`schedule_time`/`completion_time`/`exit_status`); it is not added to
  any state query and does not alter task state. Task-state transition logic stays in `Task` classmethods
  (we touch only `Task.new`/`compute_fingerprint`/a column). Identity semantics are preserved — removing
  the `part` fingerprint-exclusion is byte-identical. **Honored.**
- **§10 Config / data layer** — schema materializes via `metadata.create_all` at `hs initdb`; `create_all`
  never `ALTER`s, so pre-existing DBs are out of scope (GOAL non-goal, forward-only). No config singleton
  change. **Honored.**
- **§12 Project conventions** — the `initdb` help snippet (`docs/_include/initdb_desc.rst`) is updated in
  the same commit as the behavior change; new tests are `@mark.unit`; no hardcoded version; no dependency
  floor changes; no new CLI so no completion churn. **Honored.**
- Sections **§2–§9, §11** (exit_status, retry, server modes, FSM/thread, shutdown, resource, signals,
  queue/TLS, cluster) — **not touched**; this is a pure data-model + single-query-site change with no
  server/client/queue/FSM/signal/cluster involvement.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `part` is a real mapped column but is **omitted from the `Task.columns` allowlist** (every other column is listed there). | R5 + the no-CLI-surface non-goal require `part` to stay internal — off the wire, out of `hs list`/`--fields`/tag output. Omitting it from `columns` achieves that while it remains a queryable DB column via `Task.part`. | Adding `part` to `columns` (the "consistent" choice) would expose it on the wire and in the CLI, violating R5 and the non-goal. Mitigated by a test asserting `part` is in the real table but **not** in `Task.columns`. |

## 4. Rabbit holes (resolved)

- **What is "database rotation" and where does `part` flow?** → It's `rotatedb()` behind the manual,
  SQLite-only `hs initdb --rotate`; `part` is written once by `json_set` and read twice by `json_extract`
  — exactly three expressions to convert ([`research/01`](research/01-part-lifecycle-and-partition-mechanism.md)).
- **Does the auto-union depend on the `part` value?** → No; `part_{i+1}` is a filesystem index and the
  union is `SELECT *` — no change needed, but it is the forward-only column-count hazard
  ([`research/01`](research/01-part-lifecycle-and-partition-mechanism.md), [`research/03`](research/03-initdb-submit-and-config.md)).
- **Column placement, type, and dict bookkeeping** → between `fingerprint` and `tag`; `INTEGER` default 0;
  omit from `columns` ([`research/02`](research/02-task-model-column-and-fingerprint.md), contradiction
  resolved in [`research/00`](research/00-digest.md)).
- **Fresh-schema vs existing DBs** → `create_all` auto-includes the column; forward-only is inherent
  ([`research/03`](research/03-initdb-submit-and-config.md)).

## 5. Risks & open questions

- **Forward-only breakage (documented, accepted):** running new code against an old DB → INSERT/SELECT
  fail on the missing column, and auto-union of old partition files raises a column-count mismatch until
  `--ignore-partitions`. This is the GOAL's forward-only non-goal; no migration is built. Worth a one-line
  note in `docs`/release notes at ship time.
- **User `-t part:5` now persists as a plain tag** (previously clobbered to 0 by `:387`). This is a
  deliberate, benign consequence of decoupling — `part`-in-tag is now inert w.r.t. rotation (which reads
  the column). No key reservation is added. Flag for the human in the hand-off; trivial to add a reserve
  later if undesired.
- **`columns`-omission asymmetry:** future code that assumes `Task.columns` enumerates *all* columns could
  silently skip `part`. Mitigated by the explicit test in R7.

## 6. Verification strategy

Seeds the phase `verify:`. Prefer driving the real CLI in a throwaway site.

- **CLI drive (throwaway site):**
  `.agents/factory/bin/temp_site.sh sh -c 'uv run hs initdb && printf "echo a\necho b\n" | uv run hs submit && uv run hs initdb --rotate && uv run hs list'`
  — proves the column code paths (submit → rotate → auto-union list) run without error.
- **Column + tag-clean assertion (sqlite3 inside the same `sh -c`):** submit a task, then
  `sqlite3 "$HYPERSHELL_DATABASE_FILE" "select part from task; select count(*) from task where json_extract(tag,'\$.part') is not null"`
  → expect `part = 0` and count `0`.
- **Internal-only:** `hs list part` still errors "Invalid field name"; `hs info <id>` no longer prints a
  `part:` tag.
- **Tests:** `tests/test_source.py` (fingerprint stable, `part` in table but not in `Task.columns`),
  `tests/test_initdb.py::test_rotate` (real column-based rotation: completed tasks land in the partition
  file, `main` keeps the rest), full `uv run pytest -q`.
- **Docs:** `uv run sphinx-build -E -b html docs docs/_build` builds with only the 2 pre-existing baseline
  toctree warnings (`docs/cli/task_submit.rst`, `docs/manual.rst`).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
