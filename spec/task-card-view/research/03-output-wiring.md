# Output-format wiring: where `card` plugs in

All refs `src/hypershell/task.py` unless noted. Verified 2026-08-01.

## 1. `TaskSearchApp` (`hs list` / `hs search`), L605+

Format plumbing:
- `output_format: str = '<default>'` (L662) — sentinel resolved later by `check_output_format`.
- `output_formats: List[str] = ['normal', 'plain', 'table', 'json', 'csv']` (L663) — the argparse
  `choices` for `-f/--format` (L665-666).
- Const aliases (L667-668): `--json` → `'json'`, `--csv` → `'csv'`, all in one
  `add_mutually_exclusive_group()` (L664) with `-f`. (`hs list` has **no** `--yaml`.)
- `check_output_format()` (L821-843): if `field_names == ALL_FIELDS` and format is `'<default>'`
  → `'normal'` (L823-825). Else (a field subset): `'<default>'` → `'plain'` (L827-828), and
  `'normal'` with a subset raises `ArgumentError('Cannot use --format=normal with subset of
  field names')` (L829-830). Then `--delimiter` is only valid for `plain`/`csv` (L831-832),
  size-checked (L833-835), defaulted (`,` for csv else `\t`, L836-840), and csv demands a
  single-char delimiter (L841-843).
- Dispatch: `print_output` `cached_property` (L742-745) returns
  `getattr(self, f'print_{self.output_format}')`, called with `query.all()` at L711. So a new
  format name `card` requires a `print_card` method (else AttributeError).
- Color helpers only matter for `plain`/`table`: `colorize_rows` (L747-750, true only for
  `plain`/`table` + COLOR_STDOUT), `status_injected` (L752-755), `status_index` (L757-762),
  `fields` (L764-770, appends `Task.exit_status` when injected). **`card`/`normal` do NOT use
  these** — they color per-task inside the renderer via `select_color`.
- `print_normal` (static, L783-790): rebuilds full `Task` objects via
  `Task.from_dict(dict(zip(Task.columns, record)))`, batch-resolves sources with
  `Source.paths_for_ids([t.source for t in tasks if t.source])` (L787, avoids N+1), prints a
  `'---'` separator per task, then calls the **module-level** `print_normal(task,
  source_map=...)` (L1382). For `normal`, `colorize_rows` is false so `record` == all columns
  in order (no injected col) — the `zip(Task.columns, record)` is exact.

## 2. `TaskInfoApp` (`hs info`), L153-286 — SINGLE task

- `output_format: str = 'normal'` (L171); `output_formats = ['normal', 'json', 'yaml']` (L172).
- `-f/--format` choices + `--json`/`--yaml` const aliases in one mutually-exclusive group
  (L173-176). No `--csv`; no plain/table.
- `run()` (L187-204): `elif self.output_format == 'normal': print_normal(self.task)` (L201-202,
  the **module-level** function, no source_map) else `self.print_formatted()` (L204).
- `format_method` dict (L243-249): `{'yaml': yaml.dump…, 'json': json.dumps…}` — used by
  `print_formatted` (L222-231) and `print_field` (L206-220). It maps format name → a
  `Callable[[dict], str]` applied to `task.to_json()`, then Syntax-highlighted if a TTY.
- Task loaded once via `task` `cached_property` (L268-271): `Task.from_id(self.uuid)`.
- `TaskWaitApp` (L319-344) also carries the same `normal/json/yaml` triple and forwards
  `output_format` into `TaskInfoApp` (L361) — add `card` there too for parity.

## 3. Shared `normal` renderer today

The module-level `print_normal(task, source_map=None)` (L1382-1400) is the single shared
renderer: `hs info` calls it with one task (L202), `hs list` calls it per-task with a batched
`source_map` (L790). It builds `task_data` from `task.to_dict()` via `format_json`, overrides
`waited`/`duration`/`timeout` as `timedelta(seconds=int(...))` (L1389-90, 1396), `tag` via
`format_tag` (L1391), `cores`/`cores_max` (L1392-93), `memory`/`memory_max` via `format_bytes`
(L1394-95), `source_id`+`source` via `resolve_source` (L1397-98, L1363-1379), then colorizes
the whole `NORMAL_MODE_TEMPLATE` (L1294-1320) block with `select_color(task.exit_status)`
(L1335-1345) and prints. **`card` should mirror this exactly** — a `render_card(task,
source_map=None)` built the same way, just a different template/layout.

## 4. Recommended plug-in points (minimal edits)

**Shared renderer — put it in `task.py` next to `print_normal` (~L1382).** It needs `timedelta`
(imported L20), `format_json`/`format_tag`/`format_bytes`/`format_source` (imported L47),
`resolve_source` (L1363, local), `select_color` (L1335, local), and a new
`CARD_MODE_TEMPLATE`. All already in-module → keep `render_card` in `task.py`, not
`core/pretty_print.py` (which has no access to `select_color`/`resolve_source`/`Task` and would
create an import cycle). Signature: `def render_card(task: Task, source_map=None) -> None`
(or return str + print at call sites), same field-derivation block as `print_normal`.

**`TaskSearchApp`:**
1. `output_formats` (L663): add `'card'` → `['normal', 'plain', 'table', 'json', 'csv', 'card']`.
2. `check_output_format` (L821-830): make `card` behave like `normal` — it needs ALL_FIELDS.
   In the `else` (subset) branch add `card` to the `normal` rejection:
   `elif self.output_format in ('normal', 'card'): raise ArgumentError(...)`. (Leave the
   `<default>` logic alone; `card` is only reached when explicitly passed, so ALL_FIELDS is
   already guaranteed after this guard.) No delimiter interaction (card isn't plain/csv, so the
   L831 "Unused --delimiter" check already covers it).
3. Add `print_card(self, results)` mirroring `print_normal` (L783-790): build `Task` objects,
   `source_map = Source.paths_for_ids(...)`, loop calling `render_card(task, source_map=...)`
   (decide whether to keep the `'---'` separator — cards likely self-delimit).

**`TaskInfoApp`:**
1. `output_formats` (L172): add `'card'`.
2. `run()` (L201): `elif self.output_format in ('normal', 'card'):` and dispatch
   `render_card(self.task)` vs `print_normal(self.task)` — or a small local map. `card` must
   NOT go through `print_formatted`/`format_method` (those are for json/yaml structured dumps).
3. (Parity) `TaskWaitApp.output_formats` (L339): add `'card'`.

## 5. Validation / conflicts

- `-f/--format` uses argparse `choices=output_formats`, so adding `'card'` to that list is the
  only change needed for the flag to accept it in both apps.
- `card` needs no const alias, so it does NOT touch the `--json`/`--csv`/`--yaml`
  mutually-exclusive groups.
- Field-subset handling: `card` (like `normal`) requires ALL_FIELDS. The single guard in
  `check_output_format` (`hs list`) enforces this. `hs info` always loads the full task, so no
  subset concern there.
- Help snippets: update `INFO_HELP` (L141), `SEARCH_HELP` (L591), and `WAIT_HELP` (L310) plus
  `docs/_include/*.rst` and `share/` completions in the same commit (repo rule).
