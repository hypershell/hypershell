# Research 08 — Presentation of `Task.source` (UUID → path / `<direct>` / `<stdin>`)

Revises PLAN/digest under **maintainer correction 1**: reserved sources are now **real `Source`
rows** (a normal row whose `path` field holds the `<direct>`/`<stdin>` sentinel, each with a real
UUID). `Task.source` becomes a **native UUID** column (`UUID` type at `model.py:74`, i.e.
`POSTGRES_UUID(as_uuid=False)` on postgres, `Text` elsewhere), nullable only for historical rows.
So `Task.source` no longer *is* the display string — every human-facing surface must resolve the
UUID back to `Source.path`. This brief maps today's presentation, designs that resolution, and
recommends where it lands.

## How Task rows are presented today

- **Field list is derived from `Task.columns`.** `ALL_FIELDS = list(Task.columns)` (`task.py:434`)
  is the default field set for `hs list`/`hs search`. `--fields` prints `' '.join(Task.columns)`
  (`task.py:605`). `-x/--extract` choices are `Task.columns` (`task.py:169`). Field-name validation
  is `name in Task.columns` (`task.py:548`).
- **`hs info` (single task):**
  - default/normal → `print_normal(task)` (`task.py:202`, module fn `task.py:1352`) renders a
    **fixed** `NORMAL_MODE_TEMPLATE` (`task.py:1285`). It formats `task.to_dict()` values but only
    the keys named in the template appear; **extra columns are silently ignored** by `str.format`.
  - `-f json|yaml` → `print_formatted` formats `task.to_json()` (**all** columns) → new columns
    **auto-appear as raw values** (`task.py:222-231`, `format_method` `task.py:250`).
  - `-x FIELD` → `json.dumps(task.to_json().get(field))` (`task.py:209`) → new columns become
    extractable, **raw value**.
- **`hs list`/`hs search` (many):** query selects the chosen columns as positional tuples
  (`fields` = `getattr(Task, name)`, `task.py:541`/`759`). Format handling in `check_output_format`
  (`task.py:811`): all-fields ⇒ `normal`; a field subset ⇒ `plain`; `normal`+subset is rejected.
  - `normal` → `print_normal(Task.from_dict(dict(zip(Task.columns, record))))` (`task.py:781`) →
    same fixed template, extra columns ignored.
  - `plain`/`table` → `format_json(value)` per selected field (`task.py:769`,`785`) — only shows a
    column if the user names it; raw value.
  - `json` → `{field: to_json_type(value) ...}` over `field_names` (`task.py:793`); default
    field_names = ALL_FIELDS ⇒ **new columns auto-appear raw**.
  - `csv` → header = `field_names`, rows = `to_json_type(value)` (`task.py:803`) ⇒ **auto-appear**.

**Net auto-appearance when `source`/`identity` are added to `Task.columns` (required anyway for the
wire round-trip via `serialize_tasks`/`server.py` writeback):** they surface **automatically and
raw** in `hs info -f json/yaml`, `hs info -x source|identity`, `hs list --json/--csv` (default all
fields), `hs list source ...` (explicit), and `--fields`. They do **not** appear in the `normal`
template for either `hs info` or `hs list` unless the template is edited.

## The problem the correction creates

Raw auto-display now prints an **opaque UUID** for `source` (the `Source.id`) and a 32-hex md5 for
`identity`. A `<direct>`/`<stdin>` task shows some UUID, **not** the sentinel — because the sentinel
now lives in `Source.path`, one indirection away. Raw is acceptable for machine formats (json/csv/
`-x`) but useless to a human scanning `hs list`/`hs info`.

## Design — resolution layer (additive, no format breakage)

**Resolver in the model (invariant: query logic lives on entities).** Add a batch classmethod to
`Source`:
`Source.paths_for_ids(ids: set[str]) -> dict[str, str]` — one `SELECT id, path FROM source WHERE
id IN (:ids)`. The id-set is tiny (distinct sources on a page of results, or one for `hs info`), so
no batching/temp-table needed — consistent with correction 2 (small IN-lists). A single-id
convenience (`Source.from_id(id).path`) already fits the `Client`-mirrored `from_id` pattern.

**Display formatting** — the resolved value is either:
- a filesystem path (absolute, as stored via `os.path.abspath`; PLAN §2), or
- a reserved sentinel matching `^<.*>$` (`<direct>`/`<stdin>`) → pass through unchanged.

A small helper `format_source(path, *, relative=False) -> str`: sentinels pass through; real paths
return `os.path.relpath(path)` when `relative` else `path`. Natural home is
`core/pretty_print.py` (alongside `format_tag`/`format_bytes`/`format_json`), keeping `task.py`
import-light. NULL `source` (historical rows) → display `null`, exactly as other nullable columns
render today.

**`hs info` (one task):** resolve the single id and inject a `source` line into
`NORMAL_MODE_TEMPLATE`. In `print_normal` (`task.py:1352`) set
`task_data['source'] = format_source(Source.from_id(task.source).path)` when `task.source` else
`'null'`, and add a `      source: {source}` line to the template (`task.py:1285`). One extra
lookup per `hs info` call — negligible.

**`hs list` normal (many):** avoid N+1. `TaskSearchApp.print_normal` (`task.py:777`) already
materializes the full page; before the loop, collect distinct non-null `source` values and call
`Source.paths_for_ids(...)` once, then pass the map into `print_normal`. Minimal signature change:
`print_normal(task, source_map=None)` — resolve from the map, else fall back to a single `from_id`
(so `hs info`'s call site needs no map). This is the only touch to a shared helper.

**Should raw `identity`/`source` be shown vs hidden?**
- **`identity`** — an internal fingerprint (md5), noise for humans. **Do not** add it to the normal
  template; leave it reachable via `hs info -x identity`, `hs list identity`, and json/csv for power
  users/scripts. Zero extra work (auto-flows from `Task.columns`).
- **`source`** — **resolve** it in the normal template (path/sentinel). Keep the **raw UUID** in
  json/csv/`-x source`/explicit-field selection — those are machine surfaces where a stable UUID is
  the correct value and resolving would break scriptability and column/type expectations. This is
  additive: existing columns, orders, and `-f plain/json/csv` semantics are untouched; we only add
  one `normal`-template line plus a resolver.

**Relative-path option:** cheap to offer via `format_source(relative=True)`, but it needs a flag
(`hs info`/`hs list --source-relative` or similar) and a completion/docs update. Recommend
**defer the flag**; ship absolute-path display now (matches how `outpath`/`errpath`/`csvpath` show
absolute paths). Expose `relative=` in the helper so a later flag is a one-line wire-up.

## Scope / phase placement

Presentation is **adjacent** to R18 (logging/ergonomics), not a distinct R-ID. The `Task.columns`
additions land in **P1** (schema core) and immediately make `source`/`identity` visible-raw in
json/csv/`-x` — so P1 must at minimum accept that raw exposure (it's harmless and expected). The
**human-readable resolution** (normal-template `source` line + `Source.paths_for_ids` +
`format_source`) is small and self-contained; it belongs in **P6** (the "polish" + full-sweep
phase, `TECH.md:60`, which already owns R18 and docs). It has no dependency on the gate matrices,
so it could equally ride P1, but P6 keeps P1 tightly scoped to schema/identity and lets the display
be verified against real rows produced by P2–P4.

## RECOMMENDATION

1. **P1:** add `source` (native `UUID`, nullable) + `identity` (`TEXT`, nullable) to the `Task`
   model and `Task.columns` (`model.py:281`); add `Source.paths_for_ids` classmethod. Accept that
   json/csv/`-x`/explicit-field surfaces now show raw UUID/md5 — correct for machine output.
2. **P6 (owner of presentation):** add `format_source(path, *, relative=False)` to
   `core/pretty_print.py`; add a resolved `source:` line to `NORMAL_MODE_TEMPLATE` (`task.py:1285`);
   resolve in module `print_normal` (`task.py:1352`, single `from_id`) and in
   `TaskSearchApp.print_normal` (`task.py:777`, one batched `paths_for_ids` per page passed via a
   new optional `source_map` arg). **Do not** add `identity` to the normal template.
3. Update the `docs/_include/*.rst` normal-output examples and any completion notes for the new
   `source` line in the same P6 commit (§12 doc/completion lockstep).

**Touchpoints (exact):** `src/hypershell/data/model.py:281` (columns) + new `Source.paths_for_ids`;
`src/hypershell/core/pretty_print.py` (`format_source`, add to `__all__`);
`src/hypershell/task.py:1285` (template line), `:1352` (module `print_normal`), `:777`
(`TaskSearchApp.print_normal` batch resolve). No change to `print_json`/`print_csv`/`print_plain`/
`print_table`/`print_field` — raw is intended there.

## Open questions

- **Relative-path flag now or later?** Recommend later; helper is ready either way.
- **`--from-json` source `path = FILE[@node]`** (digest): `format_source` shows it verbatim; a real
  path with an `@node` suffix isn't a normal filesystem path — confirm it reads acceptably (it does
  not match `^<.*>$`, so it prints as-is; `relative=True` would misfire on the `@node` — treat
  `@`-suffixed specs as opaque, i.e. never relativize).
- **CSV/JSON default column growth:** adding two columns to `ALL_FIELDS` changes the default
  `hs list --json/--csv` column set (additive keys/columns). Confirm no downstream parser assumes a
  fixed column count; if it's a concern, that's a GOAL-level call, not a display bug.
