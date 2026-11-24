# GOAL — `hs update` writes only an integer group, and the single-task form refuses the filters it would ignore

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** update-group-and-legacy-filters
- **kind:** fix
- **appetite:** small  ·  *Two `small` seeds promoted together on the foreman's brief. Both are
  argument checks in `hs update`, before anything is counted or written, beside the refusals #119
  added; neither touches the scheduler, the retry model or the schema.*
- **target:** 2.9.0  ·  *for 2.9.0a10. Both seeds are 2.9.0; both refuse input accepted today, an
  interface change, which the roadmap's Target rule sends to a minor release, and 2.9.0 is one
  (C5).*

## Problem

Two ways `hs update` writes something other than what the user asked for. Both are **pre-existing**,
and both were reproduced on `develop` `03a02bd8` (2.9.0a9) in a throwaway site
(`.agents/factory/bin/temp_site.sh`, SQLite, one task in group 0 that never ran). Line numbers are
that commit's.

**1. `hs update` stores a group that is not an integer.** Since #118 a task group is an integer in
the 32-bit range wherever a submission gives one (`parse_group`, `ensure_group`,
`src/hypershell/data/model.py:94-114`): `-g x` and `-t group:x` exit 2. `hs update` does not apply
that rule. The filtered form types the value with `smart_coerce` (`task.py:1197-1201`), and so does
the single-task form (`:1099`). With `-f`, each exit 0 unless noted:

| Given | Stored (SQLite) |
| --- | --- |
| `group=x`, `ID group x` | text `'x'` |
| `group=` | text `''` |
| `group=0x10` | text `'0x10'` |
| `group=1.5`, `ID group 1.5` | real `1.5` |
| `group=1e3` | integer `1000` |
| `group=true` | integer `1` |
| `group=2147483648`, `group=-2147483649`, `ID group 2147483648` | the integer, outside the 32-bit range |
| `group=none`, `group=null`, `group=nan`, `ID group nan` | nothing; exit 5 with an `IntegrityError` traceback (`NOT NULL constraint failed: task.group`) |
| `group=07`, `group=+5`, `group=3`, `ID group 7` | integers `7`, `5`, `3`, `7` (correct) |

So a text, fractional or out-of-range group reaches the database after submit time refuses one,
and a null-like value fails with a traceback instead of a usage error. *Found while verifying, not
in the seed:* the single-task form `hs update ID group VALUE` stores the same values.

**2. The single-task form ignores the filter options given with it.** `run` (`task.py:1067-1084`)
sends three positional arguments led by a task id to `update_legacy` (`:1086-1102`), which reads
only the id, the field and the value. Since #119 it refuses `--cancel`, `--revert`, `--delete` and
`--remove-tag` (`:1071-1076`), but no other option is read. Each of these was accepted, exit 0, and
the write went ahead on a task in group 0 that never ran:

- `hs update ID args 'echo changed' -g 99 -w 'exit_status == 7' --limit 1 -F`, the seed's probe;
  the filtered form with the same filters, `hs update args='echo bulk' -g 99 -w 'exit_status == 7'
  -F -f`, matched nothing and wrote nothing.
- `ID args … -t nosuch:tag`, `-S`, `-X`, `-C`, `-R`, `--retries`, `--signal SIGKILL`,
  `-w 'group == 5'`, `-s args`, `--desc`, `-l 0`.

The help lists every option for every form and says only that the legacy interface "remains valid"
(`task.py:969-970`; `docs/_include/task_update_desc.rst`). A user who adds a filter as a guard
("only if it failed") gets an unconditional write. The guard is already expressible: the filtered
form with `-w "id == ID"` writes that task only when it matches (`hs update args='…' -w "id == ID"
-F -f` wrote nothing on the task that never ran; with `-R` it wrote it).

## Outcome / vision

`hs update` writes a task group only when it is an integer by the rule `hs submit -g` uses, and
otherwise refuses with a usage error naming the value before it counts, asks or writes. The
single-task form `hs update ID FIELD VALUE` takes no option that selects tasks, so no option given
with it is silently dropped: it refuses the combination before writing, as it refuses the action
options since #119, and the docs show the filtered form for a guarded write. Everything else `hs
update` accepts behaves as today.

## Acceptance criteria (the contract)

Terms. A **group change** is `group=VALUE` in the filtered form or `ID group VALUE` in the
single-task form. A **selection option** is any of `-w/--where`, `-t/--with-tag`, `-g/--group`,
`-l/--limit`, `-s/--order-by`, `--desc`, `-F/--failed`, `-S/--succeeded`, `-C/--completed`,
`-R/--remaining`, `-X/--cancelled`, `--retries` and `--signal` (C2).

**An integer group (seed `update-group-stores-text`).**

- **R1** — WHEN `hs update` is given a group change, it SHALL write the group only when VALUE is a
  task group by the rule `hs submit -g/--group` applies since #118: text that rule accepts, within
  its 32-bit range. It SHALL store the integer (`07` and `+5` as 7 and 5, as today). The rule SHALL
  be the same in both forms and on SQLite and PostgreSQL.
- **R2** — IF a group change's VALUE is not such a group, including `x`, an empty value, `1.5`,
  `1e3`, `0x10`, `true`, `none`, `null`, `nan` and an integer outside the range, THEN `hs update`
  SHALL exit with status 2 and a message naming the value, before it counts the matching tasks,
  prints the count or prompts, and SHALL write nothing, neither the group nor any other change given
  with it. `-f/--no-confirm` SHALL NOT change this (C4).

**The single-task form and selection options (seed `update-legacy-form-ignores-filters`).**

- **R3** — WHEN `hs update ID FIELD VALUE` is given any selection option, it SHALL exit with status
  2 and a message naming each selection option given, and SHALL write nothing. It SHALL do so with
  or without `-f/--no-confirm`, which it still accepts (C1, C2).
- **R4** — The single-task form without a selection option SHALL behave as today, including #119's
  refusal of `--cancel`, `--revert`, `--delete` and `--remove-tag`. The filtered form's handling of
  every selection option SHALL be unchanged.

**Both.**

- **R5** — Tests SHALL cover R1 and R2 for each value listed in R2 and each accepted form in R1, in
  both forms, on SQLite and PostgreSQL, including a refused group change given with another change
  (nothing written) and a refusal under `-f`; and R3 and R4 for each selection option, by short and
  long name where it has both, with and without `-f`, and the single-task form alone.
- **R6** — The `hs update` help and `docs/_include/task_update_desc.rst` SHALL state that a group
  change takes an integer as `hs submit -g` does and is refused with status 2 otherwise, and that the
  single-task form takes no selection option, with the filtered form for a guarded single-task write
  (`hs update FIELD=VALUE -w "id == ID" -F`). Changed passages carry `.. versionchanged:: 2.9.0`.
  Help and docs name top-level commands only. The squash commit body (the release note) SHALL list
  each change below as a behaviour change.

### User-visible changes (the release note)

Each is a change from `develop` `03a02bd8` and from 2.8.x.

1. **`hs update` refuses a group that is not an integer.** `group=VALUE`, and `ID group VALUE` in
   the single-task form, now exit 2 naming the value, before counting or asking and with nothing
   written, unless VALUE is an integer `hs submit -g` accepts, in the 32-bit range. Earlier releases
   stored text, fractional and out-of-range groups on SQLite, took `1e3` as 1000 and `true` as 1,
   and failed on `none`, `null` and `nan` with exit 5 and a traceback. *Affects:* scripts that set a
   group with one of those forms; `1e3` and `true` now fail instead of storing 1000 and 1.
2. **The single-task `hs update ID FIELD VALUE` refuses selection options.** Given `-w`, `-t`, `-g`,
   `-l`, `-s`, `--desc`, `-F`, `-S`, `-C`, `-R`, `-X`, `--retries` or `--signal`, it now exits 2
   naming them and writes nothing. Earlier releases ignored them and wrote the task unconditionally.
   For a write that happens only when the task matches, use the filtered form:
   `hs update FIELD=VALUE -w "id == ID" -F`. *Affects:* anyone who combined them, believing the
   write guarded.

## Non-goals (no-gos)

- **Applying the filters in the single-task form** (C1): the conditional write is the filtered
  form's job.
- **Other columns.** `hs update` also stores `x` in `exit_status`, `attempt`, `cores`, `memory` and
  `timeout` (reproduced, exit 0). This cycle is the group only (C3); typed writes to those
  columns are the seed `issues/update-typed-columns.md` (2.9.0), filed by the foreman.
- **A `group` tag.** `hs update group:x` writes a tag named `group`, not the column, and a retry
  copies it as a tag (`Task.new_retry`). Unchanged.
- **Rows already stored** with a non-integer or out-of-range group are not migrated or refused; the
  scheduler's guards from #118 stay.
- **Ranges.** The group's 32-bit range is #118's rule, reused, not redefined. Every other bound on
  numeric input, including `-l`'s, belongs to `issues/bounded-inputs.md`.
- **Selection options in the filtered form**, including `-g`'s value, are unchanged; so are the
  prompt and the `Updated N tasks` line.
- **The single-task form's tag write** losing a concurrent change
  (`issues/update-legacy-tag-lost-write.md`, 2.9.x).
- **Retiring the single-task form**, or a deprecation warning for it.
- **The 2.8 line.** 2.9 only.

## Clarifications

Proposed in shaping on 2026-11-24 by the shaping agent, who could not ask the maintainer. C1–C3,
marked for the gate in shaping, were resolved at the GOAL gate on 2026-11-24 (below).

- **C0 — One GOAL for both seeds**, slug `update-group-and-legacy-filters`, kind `fix`, appetite
  `small`, target 2.9.0 for 2.9.0a10 (foreman's brief, 2026-11-24). Both seeds marked
  `adopted:update-group-and-legacy-filters`.
- **C1 — Apply or refuse. Resolved 2026-11-24** (maintainer): **(a) refuse, exit 2.** Options were: **(a) refuse, exit 2** (R3 as written); **(b) apply**, writing
  the task only when it matches, which still has to refuse or define `-l`, `-s` and `--desc` on one
  task, and has to say what a non-match reports. **Recommended: (a).** It is #119's ruling for the
  same form with the action options: same check, same place, same status. The seed's concern, that
  refusing is an interface change sent to a minor release rather than a 2.9.x patch, does not bite:
  the target is 2.9.0, a minor (C5). The guard the user wanted already exists in the filtered form
  (`-w "id == ID"`, verified on `develop`), so (b) would give a legacy form a second way to do it,
  with new semantics to document and test.
- **C2 — Which options R3 refuses. Resolved 2026-11-24** (foreman, under the maintainer's mandate):
  all thirteen selection options are refused, and `-f/--no-confirm` stays accepted. The seed lists `-l` and `-s` among the filters, and omits `--desc`, which is
  dropped the same way (reproduced). **Recommended:** refuse all thirteen selection options
  (including `-l`, `-s` and `--desc`, which mean nothing for one task), and keep accepting
  `-f/--no-confirm`, which selects nothing and which the single-task form never needed (it does not
  prompt); refusing it would break scripts that pass `-f` by habit for no safety gain. Alternative:
  refuse only the filters that change what matches (`-w`, `-t`, `-g` and the aliases), and leave
  `-l`, `-s` and `--desc` ignored.
- **C3 — Other integer columns. Resolved 2026-11-24** (maintainer): **(a)**, the group only here.
  `exit_status=x`, `attempt=x`, `cores=x`, `memory=x` and `timeout=x` are stored as text too (found
  while verifying). Options: **(a) the group only here, and the foreman files a seed** for typed
  writes to every column; **(b) extend R1–R2 to every integer column.** **Recommended: (a).**
  `memory` and `timeout` have their own grammars at submit time (`4G`, `1:30:00`), so which text
  `hs update` should take for them is a design question, and `cores`, `memory` and `timeout` feed
  dispatch: that is beyond a small appetite and beyond either seed. The foreman files the seed
  `update-typed-columns`, target 2.9.0.
- **C4 — Exit status 2 for R2** (the seed's direction): a group change is a command-line argument,
  refused as `-t group:x` and `-g x` are at submit time (#118 C7), and as #119's refusals are. The
  values accepted and refused are #118's (C5 there: `1e3`, `true` and `0x10` refused), so `group=1e3`
  and `group=true`, which store 1000 and 1 today, are refused. The `none`/`null`/`nan` traceback
  (exit 5) becomes a status-2 refusal. Settled by precedent; no marker.
- **C5 — Release.** The roadmap's Target rule sends "refusing input accepted today" to a minor
  release; 2.9.0 is the minor both seeds target. No re-targeting.
- **C6 — The single-task group change** (`ID group VALUE`, found while verifying) is in R1–R2: it is
  the same write by another route, and leaving it open would keep the seed's defect. Recommended and
  taken as written; the gate may strike it.
- **C7 — Seeds re-confirmed against `03a02bd8`.** Both reproduce. Drift: `update-client-retry`
  (#119) has landed, so the seed's "sequence after" dependency is met and the refusal sits beside
  #119's in `run`; the line numbers moved (`run` `:1067-1084`, `update_legacy` `:1086-1102`, help
  `:969-970`); `--desc` is dropped too and was not listed; `none`/`null`/`nan` fail with a traceback
  rather than being stored; `1e3` and `true` are stored as integers, not text.
- **Left for `/hs-plan`** (not markers): the refusal messages' wording, in #119's style ("Cannot use
  … with the single-task form ID FIELD VALUE"); whether a group refusal and a selection-option
  refusal given together are reported in one message; how R3 tells a selection option was given
  (argparse defaults are `[]`, `None` or `False`).

**Ruled at the GOAL gate** (2026-11-24). Approved by the foreman, gate judge under the maintainer's
mandate, with the maintainer's own rulings on C1 and C3.

- **C1 (maintainer, 2026-11-24):** refuse, exit 2. The single-task form does not apply selection
  options; R3 stands as written.
- **C2:** refuse all thirteen selection options, `-l`, `-s` and `--desc` included, and keep
  `-f/--no-confirm` accepted. This follows from C1 and leaves no option silently ignored.
- **C3 (maintainer, 2026-11-24):** this cycle covers `group` only. `exit_status`, `attempt`,
  `cores`, `memory` and `timeout` go to a new seed, `update-typed-columns`, target 2.9.0, filed by
  the foreman.
- **C4–C6:** settled as written: exit status 2 for R2, release 2.9.0, and the single-task group
  change in R1–R2.

The contract is locked; `hs-plan` designs against it.

## Related materials

- Seeds: [`issues/update-group-stores-text.md`](../../issues/update-group-stores-text.md),
  [`issues/update-legacy-form-ignores-filters.md`](../../issues/update-legacy-form-ignores-filters.md).
- Precedent: [`spec/submit-input-checks/`](../submit-input-checks/) (#118: the integer group rule,
  its range and exit statuses; its Non-goals name `hs update group=…`);
  [`spec/update-client-retry/`](../update-client-retry/) (#119: the `hs update` refusals, R1–R2, and
  their wording; its Non-goals name the single-task form with filters).
- Neighbours: [`issues/update-legacy-tag-lost-write.md`](../../issues/update-legacy-tag-lost-write.md),
  [`issues/bounded-inputs.md`](../../issues/bounded-inputs.md).
- Source: `src/hypershell/task.py` (`UPDATE_HELP`, `TaskUpdateApp.run`, `update_legacy`,
  `process_arguments`), `src/hypershell/data/model.py` (`GROUP_MIN`, `GROUP_MAX`, `TaskGroupError`,
  `ensure_group`, `parse_group`), `src/hypershell/submit.py` (`group_option`).
- Docs: `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`.
