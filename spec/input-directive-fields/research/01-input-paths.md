# Research 01 — Task-input paths: fields, tags, zones (R1, R2, R6, R7)

Tree: `fix/input-directive-fields` @ 8dcbe126. Behaviour reproduced in a throwaway site (`temp_site.sh`).

## 1. Inventory: input → `Task.new`

**Common core (model.py).** `Task.split_argline` 810–836: `TAG_MARKER` → whitespace tokens →
`Tag.parse_cmdline_list` (core/tag.py 33–46, splits at the first `:` only) → `ensure_resources` with
`tag_text` (215; `partition(':')`) → `ensure_valid_tag` → `ensure_group(text=…)`. A parse or tag-rule
failure raises plain `RuntimeError("Failed to parse inline tags …")` (829, 834). **That is not a
`TaskInputError`**, so the stream path ends the input on it (exit 5) instead of refusing the line.
`Task.new` 705–766: `ensure_valid_tag(tag)` 729 → merge `{**tag, **inline}` 733 → pop `group` (737),
`cores`, `memory`, `timeout` → fingerprint 761 → zone from the `zone=` kwarg only (762). R9 bug:
`ensure_valid_tag` returns at the first empty value, 866 (`return` should be `continue`).

| Command / form | Chain | Up-front check (whole → exit 5) | Stream (per line) |
|---|---|---|---|
| `hs submit FILE` (DB) | `check_source` 1872 → `scan_source` 1931 → `scan_task_lines` 279 → `task_lines` 252 → `split_task_line` 170; then Loader `load_line_task` 618 | yes, `scan_task_lines` | — |
| `hs submit` stdin/FIFO (DB) | `read_source` 1915 reads in full → list → same scan | yes (stdin is whole input with a DB) | — |
| `hs submit --queue` file | `scan_source` (1758) then `LiveSubmitThread` 1101 → Loader | yes | — |
| `hs submit --queue` stdin | Loader only | no | `Loader.get_task` 612–616 logs `Refused line N of <stdin>` |
| `hs submit ARGS…` | `direct_args` 1827 (`quote_arg`) → `scan_source` 1938–1944 (`.at('arguments')`) → `submit_one` 1831 → `Task.new(parse_inline=True)` | yes | — |
| `hs submit --from-json` | `load_json_source` 1417 → `validate_json_expansion` 1499 (ArgumentError, exit 2) → `validate_json_records` 1518 (group + resources, `.at('record N')`, exit 5) → Loader `load_json_task` 644 → `json_record_tags` 302 | yes | — |
| `hs cluster FILE` | `ClusterApp.prepare_source` cluster/__init__.py 545 → `scan_task_lines` 568 (DEFAULT_TEMPLATE) → `SubmitThread`/`LiveSubmitThread` via ServerThread | yes (with or without DB) | — |
| `hs cluster` stdin | GatedSource (DB) or raw stream (`--no-db`, 570) → Loader | no | yes |
| `hs cluster --from-json` | `check_arguments` 395–399 (`validate_json_groups` alias) → `prepare_json_source` 593 | yes | — |
| `hs server FILE` | `ServerApp.prepare_source` server.py 2012 → `scan_task_lines` 2023 → `ServerThread` 1285/1289 | yes | — |
| `hs server` stdin | `SubmitThread(source)` **plain, no GatedSource** (1289) or `LiveSubmitThread` (`--no-db`) | no | yes |
| `hs run ARGS…` | task.py `TaskRunApp.run` 446–460: `read_tag_options` → `ensuredb()` → `Task.new(args, tag=…)` (inline parsed in `Task.new`) | n/a — single task; any refusal exit 5 | — |

**`-t` exists only on `hs submit` and `hs run`.** `hs cluster -t` is `--template` (cluster 154);
`hs server` has no tag option, no `--zone`, no `--from-json`. `hs run` has no `--zone`, `-g`, `-c`, `-m`, `-W`.

**`-t` refusal (exit 2):** `SubmitApp.check_tags` 1856–1870 (`read_tag_options` 234 + `ensure_valid_tag` +
`ensure_group`) → `ArgumentError`, before input is read. Queue mode checks but **drops** the tags
(1869). **`hs run` does not meet R6 today:** `-t group:x` → exit 5 (TaskGroupError from `Task.new`),
`-t bad/key:1` → exit 5 with a traceback (no `ensure_valid_tag` up front). Reproduced.

Reproduced refusals: `zone=gpu` / `group=2` in a file or stdin → exit 5 "Failed to parse inline tags
(… key … "zone=gpu:")"; `-t naked other:a/b` and inline `naked other:a/b` stored; `-t zone:x`,
inline `zone:4` with `--zone gpu`, and JSON `"zone":"gpu"` with `--zone cpu` all stored in the option's
zone with a `zone` tag.

## 2. Zone

- Read: `zone_name` model.py 295 (strip, non-empty, ≤256, printable) — any printable text, so `/`
  etc. are legal; the tag charset (`ensure_valid_tag` 873–877) would refuse them. Id: `zone_id` 307 is
  a pure uuid5 of the name (no lookup); `default`/'' → `DEFAULT_ZONE_ID` → stored NULL (762).
- Register: `Zone.ensure` 2248 — idempotent insert-or-ignore, **commits** the session.
  Callers: `SubmitApp.run` 1764–1765 (after every check, before `prepare_source`); cluster
  `zone_id` cached property 632; `TaskSubmitApp` (hidden form) 117; `Scheduler.start` server 254
  (default only); `hs update` via `Changes.zones` (`read_column`, task.py 1360).
- Stamp: GatedSource.zone_id → `Loader.__init__` 561 → `Task.new(zone=…)` 629/652.
- Refusals: `--zone` with `--queue` submit.py 1724–1726; with `--no-db` cluster 404–406 (ArgumentError, exit 2).
- No FK on `task.zone` (model 571). Unknown zone id: scheduler selects by id (`_filter_zone` 1033),
  clients derive the same id from the name, so it runs; only display degrades (`resolve_zone` task.py
  1779 falls back to the raw id; `report_held` server 495 likewise).

**What a per-task zone needs.** Read the text as written (not `smart_coerce`d: `zone:1e3` would
become `'1000.0'`, `zone:true` → `'True'`) through `zone_name`, store `zone_id(name)` in `Task.new`
(pure, no DB), and call `Zone.ensure(name)` before the write:
- Whole input (submit file/stdin/args/JSON; cluster/server file): collect names in the scan
  (extend `InputScan` with `zones`) and ensure them where `SubmitApp.run` ensures `--zone` (1764), and
  in cluster/server `prepare_source` (DB branch).
- Streams with a DB (`hs cluster`/`hs server` stdin): ensure in **`DatabaseCommitter.commit`** (submit
  814) before `Task.add_all`, cached per committer. Same thread as the insert (Session is
  `scoped_session`, data/core.py 270); ensuring in the Loader thread would add a second SQLite writer.
  The committer needs the name: carry it from the Loader (e.g. an id→name map shared by the two).
- `hs run` / `submit_one`: `Zone.ensure` just before `Task.add`.
- Running tags: a tag-only line (`# HYPERSHELL: zone:gpu`) sets zone for later lines
  (`task_lines` 276, Loader 639) — same as group today.

**No-database modes for R7** (must refuse a zone from input): `hs submit --queue` (`-t` in
`check_tags` exit 2 even though tags are dropped; file/args in `scan_source`; stdin per line in the
Loader; JSON in `validate_json_records`); `hs cluster --no-db` (file scan 568, JSON
`check_arguments` 399, stdin Loader); `hs server --no-db` (file scan 2023, stdin Loader). Also the
implicit no-DB case: `ServerThread` silently turns `in_memory` on when `not DATABASE_ENABLED`
(server 1271–1273) and cluster gates on `in_memory or not DATABASE_ENABLED` (570, 606). Simplest
switch for streams: every `LiveSubmitThread` Loader is no-DB by construction, so pass
`zones=False` there; scans need the same flag. `hs run` always opens a DB (`ensuredb`).

## 3. Precedence today (R2) — reproduced

Lines: inline directive > running tag-only line > `-t` > option (`-g`, `-c`, `-m`, `-W`) > default
(`task_lines`/Loader `{**self.tags, **inline}` 637; `Task.new` pops tag values over `other[...]`).
`hs submit -g5 -c1 -t group:6 cores:4` over `echo k # group:7 cores:2` / `# cores:3` / `echo l`
stored (7,2) and (6,3). JSON: record keys > `-t` (651) > options. `--zone` is the only zone source
today; R1/R2 make it the lowest layer, which `Task.new` gets for free if `zone` is popped like `cores`.
Cluster/server have no `-t`, so their order is inline > tag-only line > option.

## 4. Fingerprint

`compute_fingerprint(raw, group, tags)` 787 sees the tags after the resource fields are popped; the
zone column was never part of it. Popping `zone` changes the fingerprint of any task whose input
named a zone. Effects: `--update`/`--restart` de-dup (`GatedSource.skip_fingerprints` →
`Loader.skip_task` 660; `fingerprints_for_sources` 1934, index `(source, fingerprint)` 1963) will
treat pre-upgrade rows that carried a `zone` tag as absent and re-submit them once; two lines
differing only in zone now share an identity (as `--zone` and `cores:` already do).
`task_fingerprints`/`json_fingerprints` (294, 314) and `refuse_below_reached_group` use `Task.new`,
so they follow automatically. Gating keys on file md5, not tags — unaffected.

## 5. Tests to extend; helpers

- `tests/input_checks.py`: `bounded`, `db_site` (SQLite/PostgreSQL), `stored_args/groups/tags`, `submit`, `submit_ok`.
- `tests/test_group_integer.py` — template for every R6 form: `split_argline` refusals (98–110),
  stdin whole (178), JSON (204), `-t` exit 2 (232, both modes), cluster/server file (272, 289),
  `assert_refused(stderr, value, 'line 2')`.
- `tests/test_group_stream.py` — `load()`/`refusals()` Loader helpers for per-line refusal.
- `tests/test_bounded_tags.py` — resource tags across `-t`, file, stdin, JSON, cluster JSON, stream (296).
- `tests/test_zone_dispatch.py` 236–254 (`test_zone_usage_errors`, `test_submit_stamps_zone`) —
  add input-zone cases and R7 rows; `tests/test_zone_model.py` (`task_db`); `tests/test_zone_display.py`.
- `tests/test_submit_json.py` 186 (`reserved_key_promotion`); `tests/test_submit.py` 222.
- `hs update` reader: `tests/test_update_tags.py` (near-miss 205, colon-is-tag 225, every-path 252),
  `tests/update_checks.py` — R8 must keep these green.

## 6. Risks / rabbit holes (one recommendation each)

1. **The `hs update` reader raises `ArgumentError` (exit 2) and treats every `KEY:VALUE` as a tag**
   (task.py 1304–1357); task input needs exit 5 / per-line, and `zone:`/`group:` with `:` must set the
   field (R1, back-compat). → Move a pure reader to `data/model.py` (or `core/`) that returns
   (fields, tags) and raises a `TaskInputError` subclass; `hs update` wraps it into `ArgumentError`, and
   a `declarable` set decides which `:` keys become fields for task input only.
2. **Inline parse/tag errors are plain `RuntimeError`** (829, 834), so a stream ends on a typo instead
   of refusing the line. → Make every R3–R5/R7 refusal a `TaskInputError` subclass with `.at()`.
3. **`hs cluster` clients serve one zone**; a task zoned elsewhere from input waits forever (scheduler
   backs off, `report_held` logs). → Keep R1 as written; log one WARNING per such zone at ingestion and
   raise it at the gate.
4. **`hs run` `-t` exits 5, not 2**, and has no up-front tag check. → Give `TaskRunApp` the same
   `check_tags` as `SubmitApp` (read, ensure, group, zone) before `ensuredb`.
5. **`quote_arg` (submit 2031) wraps a whitespace-bearing argument in quotes**, so
   `hs submit "echo e # HYPERSHELL: zone:4"` reaches the parser as `… zone:4"` and is refused; the
   GOAL's own example fails for that reason (reproduced). → Out of scope; write tests with files or
   split arguments and file a seed.
6. **Zone value typing**: `smart_coerce` alters text; JSON may give int/bool/null. → Read inline/`-t`
   zones from the token text; accept only a JSON string (as `ensure_group` accepts only a JSON integer).
7. **Implicit no-DB** (`DATABASE_ENABLED` false) is not in R7's list. → Refuse on the same
   `in_memory or not DATABASE_ENABLED` test the gate uses.
8. **Fingerprint drift** for pre-upgrade rows with a `zone` tag. → Accept (non-goal); one changelog line.
9. **`-t` with `--queue` is silently dropped** (1869). → Leave it; only add the R7 zone refusal there.
