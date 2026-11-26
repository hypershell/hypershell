# Research 02 — one shared reader for `KEY=VALUE` / `KEY:VALUE` (R3, R4, R5, R8, R9)

Tree: `fix/input-directive-fields` @ 8dcbe126. Behaviour below was reproduced in a throwaway site
(`temp_site.sh`, direct calls to `split_argline`, `read_tag_options`, `read_changes`, `RangedOption`).

## 1. The `hs update` reader (#126), generic vs update-specific

`read_changes` (task.py:1304) is the entry. Per argument: refuse a `-w`-looking arg (1314, **update**),
refuse a token with neither separator (1318, "missing = or :", **update**: input allows naked tags),
split at the first of `=`/`:` (1320–1326, **generic**). `:` → `add_tag(smart_coerce(value))` always,
never near-checked (**generic**). `=` → `read_assignment` (1339): key in `Task.columns` →
`read_column`; else `near_columns` (1291, `edit_distance` OSA ≤1 on `key.lower()`, 1278) →
`ArgumentError("{key}: not a task column; did you mean …? To set a tag by that name, {way_out}")`;
else tag via `as_tags()` (**generic**, with `way_out` the caller's hint). Single form `ID FIELD VALUE`
(1315–1322) skips the split and calls `read_assignment` directly (**update**).
`add_tag` (1353) → `check_tag` (1296) = `Task.ensure_valid_tag({key: value})` one tag at a time (the
R9 workaround, D11 of update-typed-columns) → `ArgumentError` (**generic** rule, update error class).
Update-specific: `read_column` (1360) → `change_value` (1252: `none`/`null` clears when
`kind.clears`), `COLUMN_KINDS` (1233, every column writable, incl. `id`/`tag` refusers 1201/1207),
`ColumnKind` (1087), `Changes` (1263: `columns`, `tags`, `zones` to `Zone.ensure` before the write,
`advice` → `advise()` at task.py:1462, `shown` for the prompt). Readers: `by_range` (1100,
`parse_setting` → `ArgumentError`), `read_group` (1111, `parse_group`), `read_zone` (1147:
`zone_id(zone_name(text))`, default → `None`), `read_datetime` (1177), `read_uuid`, `read_bool`,
`read_args`, `read_text`.

## 2. Layering and where the reader lives

Import graph (checked): `core/config_schema.py` imports nothing internal; `core/types.py` →
config_schema; `core/tag.py` → core.types only; `data/model.py` → core.types, config_schema,
core.tag, data.core; `submit.py` → model, core.tag; `task.py` → model, submit, core.tag. So model
cannot import task.py or submit.py, and anything model's `split_argline` calls must sit in model or
below.

**Recommend `core/tag.py`** (already "Tag interface and parsing", imported by all three callers, no
engine import, no cycle). Move there, parametrised so it needs no model import:
- `TagError(ValueError)` — the neutral error.
- `split_token(token, *, naked)` → `(key, sep, text)`; `sep == ''` only when `naked` (input).
- `edit_distance`, `near_names(key, names)` (today `near_columns` with `Task.columns` passed in).
- `read_token(key, sep, text, *, columns, fields, colon_fields, way_out)` → `('field', key, text)` |
  `('tag', key, value)`: `=` + key in `fields` → field; `=` + key in `columns` but not `fields` → R5
  refusal; `=` + near → R4 refusal (today's message, verbatim); `=`/`:` otherwise → tag
  `smart_coerce(text)`; `:` + key in `colon_fields` → field; naked → tag `''` (or field if
  `colon_fields`, so the field reader refuses the empty value).
  Update passes `fields = columns = Task.columns`, `colon_fields = ()`; input passes the five,
  `Task.columns`, and the five.
- `check_tag(key, value, *, strict)` — the body of `Task.ensure_valid_tag` (model.py:838) per item;
  it needs only `parse_bytes`/`parse_time` (core.types). `Task.ensure_valid_tag` keeps its
  signature and loops over it (15 uses in tests/test_data/test_model.py, others).

Field readers stay per caller: update keeps `COLUMN_KINDS` in task.py (R8: only split, near rule,
tag check are shared); input gets one table of the five in model.py beside `RESOURCE_SETTINGS`
(`group` → `parse_group`, `zone` → `zone_name`, `cores/memory/timeout` → `_read_resource`).
Errors: core raises `TagError`; update wraps → `ArgumentError` (exit 2, as `check_tag` does now);
`-t` already maps `ValueError` → `ArgumentError` in `SubmitApp.check_tags` (submit.py:1867) and
`TaskSubmitApp.check_tags` (task.py:128), but **`TaskRunApp.run` (task.py:450) catches only
`TaskInputError`** and its tag check runs in `Task.new` after `ensuredb()` — fix to exit 2 early.
Inline: `split_argline` (model.py:822–829) wraps tag errors as a plain `RuntimeError("Failed to parse
inline tags…")`, which is not a `TaskInputError`: no `.at(line)` (message carries `from: "<line>"`
instead) and on a stream it ends the input rather than refusing the line. R6 needs a
`TaskInputError` subclass (e.g. `TaskFieldError` with `.at`) for R1/R3–R5/R7 refusals.

## 3. Field readers: input vs `hs update` vs the option (reproduced)

| given | inline / `-t` today | `hs update KEY=` | `hs submit` option |
|---|---|---|---|
| `cores:4.0` | 4 (`WHOLE_DECIMAL`, model.py:153; "maintainer ruling", tests/test_bounded_tags.py:111) | refused | `-c 4.0` refused (test :132) |
| `cores:none` | `None` stored, **overrides `-c`** | clears column | refused |
| `cores:` (naked) | `''` stored in the integer column (bug) | n/a | — |
| `memory:` / `timeout:` naked | plain `RuntimeError` from `Task.new` (model.py:746/752), no line | n/a | — |
| `memory:1.5` / `1.5G` | 1 / 1610612736 | same | same |
| `timeout:90.5`, `1:30:00`, `1-12` | 91, 5400, 129600 | same | same |
| `group:+5`, `1_000`, `4.0`, `1e3` | 5, 1000, refused, refused | same | same |
| `zone:007` | tag `7` (smart_coerce) | zone "007" (text) | — |
| `zone:none` | tag `None` | default zone | `--zone none` = zone named "none" |

So: memory/timeout/group agree everywhere. Differences one reader would change: `cores:4.0`
(ruling says keep), `cores:none` (option refuses; today silently unsets `-c`), naked fields
(should be refused as `TaskInputError`), and zone `none`/`null` (update's `read_zone` ≠ `--zone`;
R1 says `--zone`, so input must **not** reuse `read_zone`). Field readers must read the **text**,
never the smart-coerced value (today's `tag_text`, model.py:215, which splits at `:` only and
must go or learn `=`; the token reader returning the text removes the need for it).
Advice: input notes resource values in `TagAdvice` (submit.py:204; `note` per task with origin,
one summary per submission, `task_lines` 273, Loader 627, `validate_json_records` 1531,
`advise_one_task` 245); update queues `Changes.advice` → `advise()` (config_schema.py:902) once per
process. Keeping input fields riding in the tag dict (popped in `Task.new`) leaves `TagAdvice`,
the running tag-only-line merge and R2 precedence untouched.

## 4. `Tag.from_cmdline` and separators

`from_cmdline` (core/tag.py:33) strips, splits at the first `:`, `smart_coerce`s the stripped value;
no `:` → naked tag `''`. `=` is only refused later by the key charset (`zone=gpu` → key
`"zone=gpu"`, "Tag key must only contain…"). Split-at-first-separator effects: `timeout:1:30:00`,
`timeout=1:30:00` → field; `a=b:c` → key `a`, value `b:c`; `x:a=b` → tag `x` value `a=b`;
`url:http://x` → value `http://x`. All of these with `=`, `/`, `:` in a plain tag value are
refused today and after (value charset `[A-Za-z0-9_.+-]`). **No token accepted today contains
`=`**, so adding `=` changes no accepted inline tag; the only meaning changes are the intended ones
(`zone:` → field). `smart_coerce` (core/types.py:43): none/null → None, true/false → bool, `int()`
(so `007`→7, `1_000`→1000), `float()` (so `1e3`→1000.0, also `nan`/`inf`), else text.
**Do not change `Tag.from_cmdline`**: `SearchableMixin.__build_tag_criteria` (task.py:525) parses
`hs list -t` / `hs update -t` filters with it; R8 is about assignments, not filters.
Whitespace: `from_cmdline` strips key and value (`-t 'a: b'` → `a:b`); `read_changes` does not.
Keep stripping a caller step (input strips, update does not) to keep R8's "unchanged".

## 5. R9: `ensure_valid_tag` returns at the first empty value

model.py:866 `return` → `continue`. Callers: `Task.new` (model.py:729, `strict_tag`; Loader line
tasks strict, JSON 321/654 `strict=False`), `split_argline` (829), `SubmitApp.check_tags`
(submit.py:1864), `TaskSubmitApp.check_tags` (task.py:127), `check_tag` (task.py:1299, per tag).
The `return` is after `if not strict: continue`, so the JSON path is unaffected. Reproduced:
`-t naked other:a/b` and inline `naked other:a/b` both stored today. After the fix update's
per-tag workaround is redundant but harmless. No test relies on the bug: test_model.py naked tags
come last (`z`), and test_update_tags.py:249 `after-naked` already expects refusal.

## 6. Tests and docs

Tests: tests/test_update_tags.py (near-miss 205, colon-always-tag 225, first-separator 274, tag
rules every path 252, column value with colon 285), test_update_kinds.py / _numbers / _datetime
(import `change_value`, `COLUMN_KINDS` from `hypershell.task`, so keep those names there),
test_update_refusals.py, test_bounded_tags.py (resource tags incl. whole-decimal ruling 111/132,
stream refusal 296), test_group_integer.py (`split_argline` group 98–129, `-t group` 232),
tests/test_data/test_model.py:20 (`split_argline`). None covers `=` in input or `zone:` as field.
Docs: docs/_include/submit_desc.rst (inline syntax 17, group 32, resource tags 50–77),
submit_help.rst (`-t` 166, `--zone` 149, `--from-json` 13), task_groups_alt.rst (group:), 
task_run_desc/help.rst (`hs run -t`), task_submit_desc/help.rst, task_update_desc.rst (separator
rules 3–15), docs/cli/task_update.rst:116; help strings `-t … as key:value` at submit.py:1629,
task.py:82, task.py:424; man pages share/man/man1 regenerate.

## 7. Risks, each with one recommendation

1. **`cores:4.0` vs R3's "the option's parser".** Keep `_read_resource` (whole-decimal) for input on
   both separators; it is a recorded maintainer ruling and pinned by tests. Raise it at the gate.
2. **`cores:none` silently unsets `-c`.** Refuse it, as `-c none` is (field reader on text); add a
   `versionchanged` line.
3. **`zone:none` / `zone:default`.** Read input zones with `zone_name` + `zone_id` as `--zone` does
   (`none` = a zone named none, `default` = default); never `read_zone`.
4. **Zone names vs tag charset.** `Task.new` checks tags before popping fields (model.py:729 vs 736);
   a `-t 'zone:a/b'` would hit the tag charset. Pop declarable fields first, then check the rest.
5. **Refusal class for inline tag-rule errors.** Raise every refusal from the shared reader in
   inline input as a `TaskInputError` subclass so R6's line/record naming and stream-line refusal
   hold; state in the plan that a bad inline tag on a stream now refuses one line instead of ending
   the run.
6. **Filters share `Tag.parse_cmdline_list`.** Leave it, and `hs list -t`, untouched; add the new
   functions beside it.
7. **`hs update` regression (R8).** Move code, not messages: keep `near_columns`, `check_tag`,
   `change_value`, `COLUMN_KINDS` importable from `hypershell.task` and run the `test_update_*`
   suite unchanged as the R8 gate.
8. **`tag_text` splits at `:` only.** Retire it: the token reader hands each field its text.
9. **`hs run -t` refuses late.** Catch `ValueError` with `TaskInputError` at task.py:450 and check
   tags before `ensuredb()`.
