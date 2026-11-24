# Research 04 — task resource tags (R9), the shared group parser, and `update-typed-columns`

Worktree `fix/bounded-inputs` at `925e7f0d` (source = `develop` `03a02bd8`). Line numbers are this tree's.
Probes: throwaway SQLite site, the editable install of this worktree.

## 1. How `cores`, `memory`, `timeout` tags become columns today

All three routes end in `Task.new` (`data/model.py:608-670`), which pops the tags into columns:

- **cores** — `other['cores'] = tag.pop('cores', …)` (`model.py:641`). No conversion, no check: the
  value is whatever `smart_coerce` (`core/types.py:40-54`) or JSON gave (int, float, bool, text).
- **memory** — a `str` goes through `parse_bytes` (`model.py:645-649`, `types.py:115-121`), a
  `UnitError` becomes `RuntimeError('Failed to parse task memory …')`; any non-`str` is stored as is.
- **timeout** — a `str` goes through `parse_time` (`model.py:651-656`), same `RuntimeError` wrapping;
  non-`str` stored as is.
- Grammar-only pre-check: `ensure_valid_tag` calls `parse_bytes`/`parse_time` on text `memory`/
  `timeout` values (`model.py:768-773`). Called from `split_argline` (inline, `:726-730`, wrapped as
  `RuntimeError('Failed to parse inline tags …')`) and `SubmitApp.check_tags` (`-t`, `submit.py:1784-1797`,
  `ArgumentError`, exit 2). Aside: its `return` on an empty value (`model.py:765`) stops checking the
  *remaining* tags, so a check placed inside `ensure_valid_tag` would be skipped after a naked tag.
- No range check anywhere. `TASK_RESOURCE_ERROR` (`model.py:144`) is unrelated: it is the client's
  "more than this client has" sentinel (`client.py:708-714`, `:855-862`), set at run time.

Probes (exit; what SQLite stored):

| Input | Exit | Stored |
| --- | --- | --- |
| `hs submit echo a -t cores:100000` | 0 | `cores` 100000 (integer) |
| `hs submit echo d -t cores:x` / `cores:1.5` | 0 | text `'x'` / real 1.5 |
| `hs submit echo c -t timeout:99999999999` | 0 | 99999999999 |
| `hs submit echo b -t memory:8388608TB` | 5 | nothing; `OverflowError: Python int too large to convert to SQLite INTEGER` from the insert |
| `-t timeout:x`, `-t memory:x` | 2 | nothing (grammar check) |
| stdin `cores:x` / `cores:100000` / `cores:-4` / `cores:1.5 memory:true` | 0 | `'x'` / 100000 / −4 / 1.5 and `memory` 1 |
| stdin `timeout:99999999999` | 0 | 99999999999 |
| stdin `memory:x` | 5 | nothing (`Failed to parse inline tags`, no line number) |
| stdin `memory:8388608TB` (inline or tag-only line) | **hang** | committer dies (`OverflowError`), command never exits |
| `--from-json` `cores:100000` / `"x"` / `true` | 0 | 100000 / `'x'` / 1 |
| `--from-json` `timeout:99999999999` / `1e20` | 0 | integer / real 1e+20 |
| `--from-json` `timeout:"x"` | 5 | `RuntimeError` from the committer |
| `--from-json` `memory:"8388608TB"` or `2**63` | **hang** | committer dies |
| `hs submit -c 100000 echo i` (option, for contrast) | 0 | 100000 |

So 2⁶³ bytes hangs on **SQLite** too (64-bit `INTEGER`), not only PostgreSQL; `cores:100000` is
stored on SQLite and is the committer death (`spec/update-client-retry/REVIEW.md`) on PostgreSQL `SMALLINT`. `-t` must follow the
command (`-t` is greedy; `hs submit -t … echo a` submits 0 tasks).

## 2. #118's refusal shape, and R9 beside it

Pieces (`data/model.py:64-115`, `submit.py`):

- `GROUP_MIN/MAX` (`:64-67`); `TaskGroupError(RuntimeError)` with `value`, `origin`, `.at(origin)`
  (`:71-91`) → exit 5.
- `ensure_group(value, text)` — typed value (not bool, `int`, in range), naming `text` as written
  (`:94-106`); `parse_group(text)` = `int(text)` then `ensure_group` (`:109-115`).
- Inline: `split_argline` calls `ensure_group(tags['group'], text=tag_text(tokens, 'group'))`
  (`:731-732`). Because `scan_task_lines`/`task_lines` (`submit.py:203-234`) and the Loader both go
  through `split_task_line` → `split_argline`, one check there covers read-in-full scans
  (`hs submit` file/stdin-with-db/args `:1859-1880`, `hs cluster FILE` `cluster/__init__.py:575`,
  `hs server FILE` `server.py:2030`) **and** streams (`Loader.get_task` catches `TaskGroupError`,
  `refuse_lines` → ERROR and read on, `submit.py:537-554`).
- `-t`: `check_tags` → `ensure_group` → `ArgumentError` exit 2 (`submit.py:1784-1797`). Only
  `hs submit` has `-t`.
- JSON: `validate_json_groups` (`submit.py:1449-1461`), called by `hs submit` (`:1807`) and
  `hs cluster` (`cluster/__init__.py:406`).

**Recommendation for R9.** Generalize, do not fork:

1. A base `TaskInputError(RuntimeError)` with `value`, `origin`, `.at()`; `TaskGroupError` becomes a
   subclass (name, import and `except TaskGroupError` sites unchanged), plus `TaskResourceError`.
   The four catch sites (`submit.py:217`, `:549`, `:1460`, `:1871`) widen to `TaskInputError`.
2. `ensure_resources(tags, texts=None) -> tags` beside `ensure_group`, table-driven:
   `{'cores': 'task.cores', 'memory': 'task.memory', 'timeout': 'task.timeout'}` → the setting's
   parser and range from the bounded-inputs table. Read the **text as written** (`tag_text`) through
   the kind's parser for inline and `-t`, so `memory:1.5` reads as `-m 1.5` does and `cores:1e3` is
   refused as `-c 1e3` is; read a JSON value as a configuration-file value of that kind (a JSON
   integer is a number; bool, float-for-int, non-finite refused, per R3). Return the converted
   values so `Task.new`'s own `parse_bytes`/`parse_time` lines become no-ops for checked input.
3. Call it at the same four places as `ensure_group`: `split_argline`, `check_tags` (→ exit 2),
   `validate_json_groups` (rename to `validate_json_records`, keep the old name as an alias for
   `cluster/__init__.py`), and nowhere in `Task.new_retry` (retries copy stored columns, invariants §3).
   Leave `Task.new`'s explicit `cores=/memory=/timeout=` keyword arguments unchecked (R10/C3); they
   come from options already parsed.
4. The out-of-grammar `memory:x` / `timeout:x` refusals then become `TaskResourceError`s too, gaining
   a line number on streams instead of ending the input (`submit.py:551-554` only catches the group
   error today).

Client side (`client.py`, `core/resource.py`): no change needed. In-range values are safe there —
`cores ≤ 32,767` just yields `TASK_RESOURCE_ERROR` on a small client (`client.py:708-714`),
`timedelta(seconds=2³¹−1)` and `start_time + timeout` (`:627`) are fine. Today a text `cores` reaching
`acquire_resources` (`:663`) would raise `TypeError` (int < str); R9 closes that. Invariants §7
(balanced acquire/release) is untouched. Unrelated R6 note: `hs client` refuses `-W 0` with its own
`ArgumentError` (`client.py:1788-1789`) although 0 means none in the Duration row; reconcile under R6.

## 3. The parallel cycle `update-group-and-legacy-filters`

Its GOAL R1–R2: `hs update group=V` and `ID group V` write only what `hs submit -g` accepts, exit 2
naming the value. Its **PLAN and TECH are written and gate-approved** (`eec25fc0`, "Plan gate …
approved"); no source change yet (`git diff develop...HEAD -- src tests` empty). PLAN §2.1 adds
`task.py: change_value(field, text)` which calls `parse_group(text)` and turns `TaskGroupError` into
`ArgumentError`; TECH:164-165 pins "`task.py` calls `parse_group` and names neither `GROUP_MIN`/
`GROUP_MAX` nor `int()`". PLAN §2.4 already expects bounded-inputs to "re-point `parse_group`", says
its tests assert exit 2 + `group` + the value named (not #118's sentence), and asks to land first.

**One parser, either order.** Keep `parse_group(text: str) -> int` and its raising of
`TaskGroupError` exactly (name, signature, exception class); make it a thin wrapper:
`GROUP_MIN, GROUP_MAX` read from the table's Group entry (or the entry built from them — one
definition), body = the table's int parser for that entry, catching its range/form error and
re-raising `TaskGroupError(text, out_of_range=…)`. `group_option` (`submit.py:1464-1469`) and the
`hs list`/`hs update -g` filters (`task.py:675`, `:1039`, now `type=int`) switch to the table's
option parser for R4's `-g/--group: expected …` message; `parse_group` stays the input-side door
(exit 5 via `TaskGroupError`, exit 2 where a caller converts). If the `TaskGroupError` message moves
to R4's form, keep the word `group` and the value in it (the sibling's tests and
`tests/test_group_integer.py`, `test_group_stream.py`, `test_group_order.py`,
`docs/_include/submit_desc.rst` pin "must be an integer" — 5 assertions to update).

Conflict risk: `task.py` `TaskUpdateApp` (theirs: `run`, `update_legacy`, `process_arguments`,
`change_value`, `UPDATE_HELP` prose; ours: `-g`/`-l` `type=` at `:1024`, `:1039` and their help) —
separate hunks; `docs/_include/task_update_help.rst` (`-g`/`-l` entries vs. `ARGS`/legacy text);
`.agents/factory/invariants.md` §3 (both amend the group bullet); `data/model.py` only if we touch
`parse_group`'s body (they do not). **Land the sibling first** (small, approved, ready); we rebase,
and `change_value` picks up the table through `parse_group` with no edit.

## 4. What `update-typed-columns` needs from bounded-inputs

It wants `hs update` to read `cores`, `memory`, `timeout` (and `attempt`, `exit_status`) by kind,
range from our table, exit 2, in the sibling's `change_value`. Expose, importable outside argparse
and outside configuration load:

- `parse_setting(name: str, value: str | int | float) -> int | float` (or `RANGES[name].parse(...)`)
  in the table module, raising one `ValueError` subclass (e.g. `RangeError`) whose `str()` is R4's
  `<name>: expected <form> from <A> to <B>, given <V>` with `V` cut short, without the
  ` (from: …)` suffix (the configuration path adds it). `name` is the key (`task.cores`) so a caller
  can re-label it (`cores`). It must not be an `ArgumentTypeError`-only path and must not read
  `config`.
- The option adapters (`type=`) and `ensure_resources` are both built on it, so the follow-up calls
  the same function `hs submit` uses: `change_value('memory', '4G') = parse_setting('task.memory', '4G')`.

`attempt` and `exit_status` have no configuration key. `server.attempts` (1…32,767) is the
per-task ceiling for `attempt`, but `exit_status` (SMALLINT, holds −1, −1001, −1002 and signal
codes) is not a setting. Recommendation: **leave column-only entries to the follow-up**; R1 scopes the
table to keys and options, and adding `task.attempt`/`task.exit_status` rows here would put
non-settings under R1's "every entry is a setting" test. Do make the int parser callable with an
explicit range (`parse_int(text, lo, hi, name)`) so the follow-up can add a two-line column map
(`attempt` 1…32,767, `exit_status` −32,768…32,767) without a new parser.

## 5. Column types (PostgreSQL), confirming the ceilings

`SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')`, `BIG_INTEGER = … BIGINT`
(`model.py:240-242`); SQLite `INTEGER` is 64-bit for all.

| Column | Declared (`model.py`) | PostgreSQL | GOAL ceiling | OK |
| --- | --- | --- | --- | --- |
| `cores` | `:436` SMALL_INTEGER | SMALLINT ±32,767 | 0…32,767 | yes |
| `memory` | `:437` BIG_INTEGER | BIGINT (widened from INTEGER by `data/upgrade.py:61` `WIDENED`) | 0…2⁶³−1 | yes |
| `timeout` | `:440` INTEGER | INTEGER | 0…2³¹−1 s | yes |
| `attempt` | `:458` SMALL_INTEGER | SMALLINT | `server.attempts` ≤ 32,767; `-r` ≤ 32,766 | yes |
| `group` | `:429` INTEGER | INTEGER | −2³¹…2³¹−1 | yes |
| `part` | `:478` INTEGER | INTEGER | 0…2³¹−1 | yes |
| `exit_status` | `:452` SMALL_INTEGER | SMALLINT | not in table (§4) | — |

`waited`/`duration` are INTEGER seconds too (`:461-462`), consistent with the Duration ceiling.
