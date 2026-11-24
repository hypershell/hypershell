# PLAN — `hs update` writes only an integer group, and the single-task form refuses the filters it would ignore

> **Status:** Draft for review · **Last updated:** 2026-11-24
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md) (locked at the
> GOAL gate, 2026-11-24); the phased executable roadmap is [`TECH.md`](TECH.md). Lean plan
> (`appetite: small`, `kind: fix`, root causes known, nothing in the high-blast-radius list touched):
> no `research/`. Planned against `03a02bd8`; this branch's `src`, `tests` and `docs` are `03a02bd8`'s.

## 1. Summary

Two argument checks in `hs update` (`src/hypershell/task.py`), beside #119's refusals, both exit 2
through `ArgumentError`. **R1–R2:** one helper types the value of every field change, for both forms;
for `group` it calls #118's `parse_group` (`data/model.py`), the parser `hs submit -g` uses, so no
second group rule or range exists. **R3–R4:** `TaskUpdateApp.run` adds the thirteen selection options to
#119's single-task refusal, in the same message and the same place, before the database is opened.
Tests come first, proved red on `03a02bd8`: the group tests on SQLite and PostgreSQL, the option tests
on SQLite. No schema, scheduler, model or option change.

## 2. Design

### 2.1 R1, R2 — an integer group in both forms

Today the two forms type a value in two copies of the same code: `process_arguments` (filtered form,
`task.py:1197-1201`) and `update_legacy` (`:1096-1099`), each "`None` for `none`/`null` on a text
column, else `smart_coerce`". The fix replaces both copies with one module-level helper in `task.py`:

```python
def change_value(field: str, text: str) -> Any:
    """The value a change writes to task column `field`, given as `text` (exit 2 for a group that is not one)."""
```

- `field == 'group'`: `return parse_group(text)`; a `TaskGroupError` becomes
  `ArgumentError(str(error))` (`raise … from error`). The message is #118's, word for word, as
  `hs submit -t group:x` prints it: `task group must be an integer, not "x"`, or `… an integer from
  -2147483648 to 2147483647, not "2147483648"` out of range. `parse_group` is `int(text)` then
  `ensure_group`: `07`, `+5`, ` 7` and `1_000` are accepted as they are by `hs submit -g`; `x`, empty,
  `1.5`, `1e3`, `0x10`, `true`, `none`, `null` and `nan` are refused, and so is an integer outside
  `GROUP_MIN`…`GROUP_MAX`.
- a text column (`Task.columns[field] is str`): as today, `None` for `none`/`null`, else the text.
- any other column: `smart_coerce(text)`, as today (other integer columns are C3's seed,
  `update-typed-columns`, which extends this helper).

Callers:

- **Filtered form.** `process_arguments` calls `change_value(field, value)` for each `FIELD=VALUE`,
  after its existing `id` and unknown-field checks. `update_tasks` calls `process_arguments` first,
  before the "Searching database" line, `build_query`, the count, the prompt and any write, so a
  refused group stops the whole command: nothing counted, asked or written, and no other change given
  with it lands. `-f` only skips the prompt, which comes later. This is after `ensuredb()` (as every
  `process_arguments` refusal is today); R2 asks for "before it counts", not before the database is
  opened, and moving `process_arguments` ahead of `ensuredb()` would reorder every filtered-form
  error for no requirement.
- **Single-task form.** `update_legacy` calls `change_value(field, value)` for every field other than
  `tag` (whose path is unchanged), before `Task.update`. So `hs update ID group x` exits 2 before any
  read or write; an unknown ID with a bad group now reports the group (exit 2), not the task (exit 5):
  arguments first.
- A **tag** named `group` (`hs update group:x`) is a tag, unchanged (Non-goal).

`hs update -g/--group` (the filter, `type=int`) is unchanged here (Non-goal; it is `bounded-inputs`'
`Group` entry, §2.4).

### 2.2 R3, R4 — the single-task form refuses selection options

`TaskUpdateApp.run` already computes `legacy` and, for it, lists the action options given
(`--cancel`, `--revert`, `--delete`, `--remove-tag`) and raises
`ArgumentError(f'Cannot use {options} with the single-task form ID FIELD VALUE')` before `ensuredb()`.
The list gains the selection options, after the action options, in GOAL order, each named by its short
and long spelling where it has both (argparse does not say which was typed):

| Attribute | Given when | Named |
| --- | --- | --- |
| `where_clauses` | `!= []` | `-w/--where` |
| `taglist` | `!= []` | `-t/--with-tag` |
| `group_filter` | `is not None` | `-g/--group` |
| `limit` | `is not None` | `-l/--limit` |
| `order_by` | `is not None` | `-s/--order-by` |
| `order_desc` | `is True` | `--desc` |
| `show_failed`, `show_succeeded`, `show_completed`, `show_remaining`, `show_cancelled` | `is True` | `-F/--failed`, `-S/--succeeded`, `-C/--completed`, `-R/--remaining`, `-X/--cancelled` |
| `retry_filter` | `is True` | `--retries` |
| `signal_filter` | `is not None` | `--signal` |

The table lives once, as a module constant of `(attribute, name)` pairs beside `UPDATE_HELP`, read by
a test too. "Given" is tested by identity, never truthiness or `in (None, False)`: `-l 0` and `-g 0`
are given (and `0 in (None, False)` is true in Python). When a selection option is among those named,
the message adds the guarded form:

```
Cannot use -w/--where, -l/--limit with the single-task form ID FIELD VALUE (to write a task only if it matches: hs update FIELD=VALUE -w "id == ID")
```

With action options only, the message is #119's, unchanged. One message names every option given,
action and selection together. `-f/--no-confirm` is not a selection option and stays accepted (C2).
Everything after the check is unchanged: the single-task form without these options writes as today
(R4), and the filtered form never reaches this branch, so its selection options behave as today (R4).

**Both refusals at once.** The option check runs in `run()`, before `ensuredb()`; the group check runs
in `update_legacy`, after it. So `hs update ID group x -F` reports the option only. Either refusal is
exit 2 with nothing written, and the user fixes both by dropping the option and correcting the value.
One combined message would mean typing the group in `run()`, splitting the helper's callers between two
places for no requirement.

### 2.3 R6 — help, docs and the release note

The same two sentences go into `UPDATE_HELP` (`task.py`, plain text), `docs/_include/task_update_desc.rst`
and `docs/_include/task_update_help.rst` (the `ARGS` description and "Legacy Mode"), each phrase on one
line in each file so the verifies can grep it:

- after the `FIELD=VALUE` paragraph: "A group change (``group=VALUE``, or ``ID group VALUE`` in the
  single-task form) takes an integer as ``hs submit -g`` does; any other value exits with status 2
  before anything is counted, asked or written." Grep phrase: `takes an integer as`.
- in the single-task paragraph, after #119's sentence: "The single-task form takes no option that
  selects tasks (``-w``, ``-t``, ``-g``, ``-l``, ``-s``, ``--desc``, ``-F``, ``-S``, ``-C``, ``-R``,
  ``-X``, ``--retries``, ``--signal``); given one, it exits with status 2 and writes nothing. To write
  a task only if it matches, use the filtered form: ``hs update FIELD=VALUE -w "id == ID" -F``."
  Grep phrases: `takes no option that selects tasks` and `-w "id == ID"`.

`docs/cli/task_update.rst` gains one `.. versionchanged:: 2.9.0` entry after #119's, without a `:pr:`
role (added at release, `AGENTS.md` § Docs): both refusals and what happened before (a text,
fractional or out-of-range group stored, `1e3` and `true` stored as 1000 and 1, `none`/`null`/`nan`
failing with exit 5 and a traceback; the options ignored and the task written). Markers on the page:
8 → 9. No `share/` change (no option added or renamed; man pages are rebuilt at release). Docs name
top-level commands only. The release note (two behaviour changes, GOAL's list) is `hs-publish`'s.

`AGENTS.md` and `.agents/factory/invariants.md` §3 ("A task group must be an integer … wherever input
sets it") each gain, in lockstep: "`hs update group=VALUE` and `hs update ID group VALUE` take it by
`parse_group` (exit 2, before counting or writing)".

### 2.4 How this meets `bounded-inputs`

`bounded-inputs` (`fix/bounded-inputs`, shaped, gate pending) puts every numeric option under one range
table and one parser per kind; its `Group` row (−2³¹…2³¹−1) covers `-g/--group` for `hs submit`,
`hs list` and `hs update`, and its R6 turns #118's 32-bit group into a table entry.

- **One parser.** This cycle adds no group rule: `change_value` calls `parse_group`, as `group_option`
  (`hs submit -g`) and every #118 path do. When `bounded-inputs` moves the range into its table, it
  re-points `parse_group` (or replaces its body) and `hs update group=VALUE` follows with no edit
  here. Nothing in `task.py` names `GROUP_MIN`, `GROUP_MAX` or `int()` for a group.
- **The filter is theirs.** `hs update -g` stays `type=int` here; `bounded-inputs` switches it, with
  `hs list -g`, to the group parser. Its value is a selection option, so here it only appears in R3's
  refusal, by name.
- **Wording.** `bounded-inputs` plans one message form (`<name>: expected <form> from A to B, given
  V`). This cycle's tests assert exit 2, that `group` and the value are named, and that nothing is
  written, not #118's sentence, so a wording change there breaks none of them.
- **Text overlap.** Both edit `TaskUpdateApp` in `task.py`: this cycle `run`, `update_legacy`,
  `process_arguments`, the new helper and constant, and `UPDATE_HELP`'s prose; `bounded-inputs` the
  `-g`/`-l` declarations (`type=`) and perhaps their help lines. Separate hunks; whichever lands second
  rebases. The docs overlap is `task_update_help.rst`'s `-g`/`-l` entries (theirs) against its `ARGS`
  and Legacy Mode text (ours).
- **Order.** This cycle targets 2.9.0a10 and is small; it should land first. If `bounded-inputs` lands
  first, P2 rebases and keeps calling `parse_group` (or its successor), and the red list is unaffected
  (`bounded-inputs` changes neither form's group write nor the single-task form's options).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `change_value` → `parse_group` in `process_arguments` and `update_legacy` (§2.1); stores the `int` |
| R2 | `TaskGroupError` → `ArgumentError` (exit 2, #118's message naming the value), raised before the count/prompt/write and before any change lands; `-f` irrelevant (§2.1) |
| R3 | the selection-option table and identity test in `run()`'s single-task refusal, before `ensuredb()`; `-f` accepted (§2.2) |
| R4 | the check is the only change on the single-task path; the filtered form never reaches it (§2.2) |
| R5 | `tests/test_update_group.py` (SQLite + PostgreSQL) and `tests/test_update_refusals.py` additions (§6), red on `03a02bd8` |
| R6 | help, both `_include` files, the page's `versionchanged`, `AGENTS.md`/`invariants.md` §3 (§2.3); release note at `hs-publish` |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (pre-design, on the GOAL) and gate #2 (on this design) touch three sections; the rest are
untouched (no scheduler, server, client, queue, config or schema change).

- **§1 Task lifecycle — every `hs update` write is conditional.** No write path changes: both checks
  raise before `Task.update`, `update_selected` or `update_matching` is reached. Honoured.
- **§3 Retry model / group rules — "a task group must be an integer wherever input sets it".** This
  extends the rule to `hs update`, by the same `parse_group`, and the invariant text is amended in
  lockstep (§2.3). Rows already stored with a text group are untouched; `Task.new_retry` still copies a
  legacy group, and the scheduler's #118 guards stay (Non-goal). The reached-group refusal is
  `hs submit`'s and is not extended to `hs update` (not in the GOAL).
- **§12 Conventions.** Help, `_include` snippets, the prose page and its `versionchanged:: 2.9.0` land
  in the phase that changes behaviour; no `share/` change (no option added or renamed). Exit status 2
  is `cmdkit`'s `ArgumentError` → `exit_status.bad_argument`, no literal. New tests carry
  `@mark.integration`, use `@mark.parametrize`. "Verify real equivalence": the given-option test uses
  identity, not `in (None, False)` (§2.2).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| — | — | — |

## 4. Rabbit holes (resolved)

No fan-out (small fix, known root causes, no high-blast-radius file). Resolved by reading the code:

- **How R3 tells an option was given** (left to the plan by the GOAL): argparse defaults are `[]`,
  `None` or `False`, and `0` is a real `-l`/`-g` value → identity tests per attribute (§2.2).
- **The shared `--where` default.** `-F` and its kin append to `where_clauses`, which is argparse's
  default list object, shared by every parse in one process (confirmed on `03a02bd8`:
  `TaskUpdateApp.from_cmdline` twice returns the same list, holding the first run's alias). A CLI run
  parses once, so R3 is exact there. In-process callers already reset it
  (`tests/test_contended_recovery.py::hs_update`); no fix here, and none needed by the GOAL.
- **Wording and one message** (left to the plan): #119's sentence extended; one message for all
  options; the group refusal separate and later (§2.2).

## 5. Risks & open questions

- **`1e3` and `true` start failing** (GOAL change 1): scripts that relied on them break with a clear
  exit 2. Accepted at the gate (C4).
- **`ID group x` with an unknown ID** now exits 2 for the group rather than 5 for the task (§2.1).
  Arguments before lookups, as `process_arguments` does; no requirement either way. Plan-gate may
  overrule (move the check after `Task.from_id`), at the cost of a database read before an argument
  error.
- **PostgreSQL coverage in CI.** `tests/test_update_group.py` runs on PostgreSQL only where
  `HS_TEST_POSTGRES` is set: locally (required by every verify here), and in CI only if the file is
  added to the PostgreSQL job's list in `.github/workflows/tests.yml`. #118's `test_group_integer.py`
  is not on that list. **Proposed:** add `tests/test_update_group.py` to it in P2 (one word; the job
  already runs `test_timescale.py`, whose fixtures `db_site` needs). Plan-gate question.
- **Run time.** About 150 new subprocess-driven tests (§6), a few minutes serially on SQLite plus the
  PostgreSQL half. The red check runs each red test alone on the base build: ~106 runs, bounded at
  40 min.
- **`bounded-inputs` lands first** (§2.4): a rebase in `task.py`/`task_update_help.rst`, no design
  change.
- **Verifies not dry-run at plan time.** Lean plan: the counts (106, 29 `pg:`) and the commands are
  derived from §6, not run. P1 is where they meet reality; a corrected count or command is amended
  through `set_phase.py` with a PLAN note, not by weakening a check.

## 6. Verification strategy

**Tests first** (P1), every red one proved red on `03a02bd8` with `spec/relay-hardening/drive/red_check.sh`
(`RED_BASE_REV=03a02bd8`, list `spec/update-group-and-legacy-filters/red.txt`, `pg:` prefix for the
PostgreSQL ids), the rest green with `spec/contended-writes/drive/rest_green.sh`. Bounded with
`spec/update-client-retry/drive/bound.sh`. pytest runs serially.

`tests/test_update_group.py` (new; `db_site` from `tests/input_checks.py`, so SQLite and, with
`HS_TEST_POSTGRES`, PostgreSQL; three tasks `n:0`–`n:2` in group 0; rows read through the private
engine; `HS_TEST_TIMESCALE` empty):

| Test | Cases | Red on base |
| --- | --- | --- |
| `test_group_change_refused` | 2 forms (`group=V -t n:1 -f`; `ID group V`) × 11 values (`x`, empty, `1.5`, `1e3`, `0x10`, `true`, `none`, `null`, `nan`, `2147483648`, `-2147483649`): exit 2, stderr names `group` and the value (non-empty ones), no `Searching database`, no `Updated`, every row unchanged | 22 per dialect |
| `test_group_change_refused_f_and_prompt` | `x`, `none`, `2147483648` × (filtered without `-f`, stdin `yes`: no count, no prompt; single-task with `-f`) | 6 per dialect |
| `test_group_refusal_writes_no_other_change` | `group=x args=changed color:red -t n:1 -f`: exit 2, `args` and tags unchanged | 1 per dialect |
| `test_group_change_accepted` (guard) | 2 forms × `07`→7, `+5`→5, `3`→3, `-2147483648`, `2147483647`: exit 0, stored integer (SQLite `typeof` = `integer`) | green |

`tests/test_update_refusals.py` (additions; SQLite `temp_site`, as #119's: the check runs before the
database is opened, so it is dialect-free). Spellings: `-w 'exit_status == 7'`/`--where …`,
`-t n:1`/`--with-tag n:1`, `-g 0`/`--group 1`, `-l 0`/`--limit 1`, `-s args`/`--order-by args`, `--desc`,
`-F`/`--failed`, `-S`/`--succeeded`, `-C`/`--completed`, `-R`/`--remaining`, `-X`/`--cancelled`,
`--retries`, `--signal KILL` (23):

| Test | Cases | Red on base |
| --- | --- | --- |
| `test_legacy_form_with_selection_refused` | 23 spellings × with and without `-f`: exit 2, the long name in stderr, rows unchanged | 46 |
| `test_legacy_form_names_every_option` | `-w … -t n:1 -s args -l 1 --desc`: each named, and the `-w "id == ID"` hint | 1 |
| `test_legacy_form_names_action_and_selection` | `--cancel -F`: both named | 1 |
| `test_filtered_form_selection_unchanged` (guard) | 23 spellings, change `hit:yes`, `-f`; five tasks: `n:0` never ran; `n:1` exit 0; `n:2` exit 7, attempt 2; `n:3` exit −1; `n:4` exit −9, group 1. Written: `-w exit_status == 7` {2}; `-t n:1` {1}; `-g 1` {4} (`-g 0` {0,1,2,3}); `-l 1` one task; `-s args -l 2` {0,1}, with `--desc` {4,3}; `-F` {2,3,4}; `-S` {1}; `-C` {1,2,3,4}; `-R` {0}; `-X` {3}; `--retries` {2}; `--signal KILL` {4} | green |
| `test_filtered_form_selection_with_prompt` (guard) | `hit:yes -t n:1`, stdin `yes`: written {1} | green |
| `test_legacy_form_alone` (guard) | `ID group 7` with and without `-f`: written | green |

`-s`, `--desc` and `-l` need their partners in the filtered form (`-s` requires `-l`; `--desc` requires
`-s`), so each such spelling carries them there; in the single-task form each is given alone.

Red total: 29 per dialect (58) + 48 = **106** lines in `red.txt`, 29 of them `pg:`. A changed count is
amended in `TECH.md` with a note here.

**CLI drive** (`spec/update-group-and-legacy-filters/drive/ugl_drive.sh`, run under
`.agents/factory/bin/temp_site.sh`, modes `group` and `legacy`): submit three tasks; `group` mode
checks `group=x`, `group=1e3`, `group=none` and `ID group 2147483648` exit 2 with nothing written,
`group=07` stores 7 and `hs list -g 7` finds it; `legacy` mode checks `ID args 'echo changed' -g 99 -w
'exit_status == 7' --limit 1 -F` (the seed's probe) exits 2 naming the options and writes nothing,
`ID args 'echo changed' -f` writes, and `args='echo changed' -w "id == ID" -F -f` writes nothing on a
task that never ran and writes it with `-R`.

**Gate (P4):** the whole suite serially; the PostgreSQL half of the new tests with `HS_TEST_POSTGRES`;
every red-listed test green; both drive modes; the docs build with `-W`; no `hs task ` in the changed
docs.

## Plan gate (2026-11-24)

**Approved** by the foreman, gate judge under the maintainer's mandate.

- **Accepted:**
  - adding `tests/test_update_group.py` to the CI PostgreSQL job (§5);
  - argument checks before the database is read, so an unknown ID with a bad group exits 2 (§2.1,
    §5);
  - the option refusal reported before the group refusal (§2.2);
  - the filtered-form timing: the group check after `ensuredb()`, before the count (§2.1);
  - the shared `--where` default left as is (§4);
  - unrun checks are met at P1 (§5);
  - `1e3`/`true` refused (gate C4).
- **The seed `issues/update-typed-columns.md` is now filed on develop** (d132d45c). This branch is
  cut from `03a02bd8` and does not need it.
