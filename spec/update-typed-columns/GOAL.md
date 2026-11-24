# GOAL — `hs update` reads a task's resource and status columns by their kind

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** update-typed-columns
- **kind:** fix
- **appetite:** small  ·  *From the seed. Five columns, one argument check in `hs update` before
  anything is counted or written, beside #124's group check; the parsers and ranges are
  `bounded-inputs`', reused. No scheduler, schema or retry change.*
- **target:** 2.9.0  ·  *for 2.9.0a10. It refuses values accepted today, an interface change, which
  the roadmap's Target rule sends to a minor release; 2.9.0 is one.*
- **builds after:** `bounded-inputs` lands on `develop` (in review on `fix/bounded-inputs`,
  `a0c227a6`). This GOAL names that cycle's range table (`SETTINGS`, `core/config_schema.py`) and its
  refusal form; `hs-plan` designs against the table as it lands, not as it stands on `develop` today.

## Problem

`hs update` writes the value it is given into a task's integer columns without reading it by the
column's kind. **Pre-existing.** Reproduced on `develop` `39834600` (2.9.0a9 plus #124) in a
throwaway site (`.agents/factory/bin/temp_site.sh`), on SQLite and on a local PostgreSQL 18, in both
the filtered form (`hs update COL=VALUE -w "id == ID" -f`) and the single-task form
(`hs update ID COL VALUE`), one fresh task per probe. The two forms behaved the same in every case.
Line numbers are that commit's.

Both forms build the value with `change_value` (`src/hypershell/task.py:1036-1046`): a group goes
through `parse_group` since #124, a text column takes the text as given, and every other column
takes `smart_coerce`, which turns `true` into 1, `1.5` into a float, `nan` into a float, and leaves
anything else as text. `hs submit` reads the same three resource values through its own grammars:
`-c`, `-m` (a size: `4G`, `512MB`) and `-W` (a duration: `90m`, `1:30:00`), and the `cores`, `memory`
and `timeout` tags; once `bounded-inputs` lands, all of them through one range table.

| Given | SQLite (exit, stored) | PostgreSQL (exit, stored) |
| --- | --- | --- |
| `cores=x`, `memory=x`, `timeout=x`, `exit_status=x`, `attempt=x` | 0, the text `'x'` | 5, nothing; a `DataError` traceback |
| `memory=4G`, `timeout=1:30:00` (forms `hs submit` takes) | 0, the text | 5, nothing; a `DataError` traceback |
| `cores=1.5` (any of the five) | 0, the real `1.5` | 0, rounded to `2` |
| `cores=true` | 0, `1` | 5 for `cores`, `memory`, `exit_status`, `attempt` (`CannotCoerce`); 0 and `1` for `timeout` |
| `cores=` (empty) | 0, the text `''` | 5, a `DataError` traceback |
| `cores=100000`, `exit_status=100000`, `attempt=100000` | 0, the integer | 5, `smallint out of range` traceback |
| `timeout=3000000000` | 0, the integer | 5, `integer out of range` traceback |
| `cores=nan` | 0, NULL | not probed |
| `attempt=none` | 5, `NOT NULL constraint failed: task.attempt` traceback | 5, `NotNullViolation` traceback |
| `cores=none`, `memory=none`, `timeout=none`, `exit_status=none` | 0, NULL (correct: unconstrained, or remaining) | 0, NULL |
| `cores=7` (any of the five) | 0, `7` (correct) | 0, `7` |

So on SQLite a value that is not a number reaches the database and `hs update` reports success; on
PostgreSQL the same value fails with exit 5 and a traceback instead of a usage error, a fraction is
rounded silently, and a size or duration that `hs submit` accepts is refused.

**The effect on dispatch** (SQLite, one `echo` task, `hs cluster -N 1` given 45 s): after
`cores=x` or `memory=4G`, the client's executor stopped with `TypeError: '<' not supported between
instances of 'int' and 'str'`; the run did not finish, and the task was left scheduled and never
completed. After `timeout=1:30:00`, `exit_status=x` or `attempt=x`, that short run completed (exit
0); the text stays in the row.

### Every column `hs update` can write

`Task.columns` (`src/hypershell/data/model.py:482-517`) names 34 columns; `hs update` accepts all of
them but `id` in the filtered form (refused, exit 2). Probed with `x`, and the values listed, on
SQLite and (where marked) PostgreSQL. "By kind" means the value is read by the column's kind
before it is written.

| Column | Kind (SQL type) | By kind today | What a bad value does today | Here |
| --- | --- | --- | --- | --- |
| `id` | uuid | refused (filtered form, exit 2) | single-task `ID id x`: exit 5, traceback | seed |
| `group` | int (`INTEGER`) | yes, #124 | exit 2 | unchanged |
| `cores` | int (`SMALLINT`) | no | table above | **this cycle** |
| `memory` | size, bytes (`BIGINT`) | no | table above | **this cycle** |
| `timeout` | duration, s (`INTEGER`) | no | table above | **this cycle** |
| `exit_status` | int (`SMALLINT`) | no | table above | **this cycle** (C1) |
| `attempt` | int (`SMALLINT`, not null) | no | table above | **this cycle** (C1) |
| `waited`, `duration` | int, s (`INTEGER`) | no | SQLite stores `x`, exit 0; PostgreSQL exit 5 | seed (C4) |
| `part` | int (`INTEGER`, not null) | no | SQLite stores `x`, exit 0; `none` exit 5; PostgreSQL exit 5 | seed (C4) |
| `cores_max`, `memory_max` | float (`DOUBLE`) | no | `x` exit 5, traceback (both); `inf` stored on SQLite | seed (C4) |
| `retried` | bool | no | `true`/`false` stored; `x`, `2`, `none` exit 5, traceback (both) | seed (C4) |
| `submit_time` | datetime (not null) | no | SQLite: every value exit 5 (`SQLite DateTime type only accepts Python datetime`); PostgreSQL: `2026-01-01` stored as midnight in the session's zone, `x` exit 5 | seed (C4) |
| `schedule_time`, `start_time`, `completion_time` | datetime | no | as `submit_time`; `none` clears (both) | seed (C4) |
| `submit_id` | uuid (not null) | no | SQLite stores `x`; `none` exit 5, traceback | seed (C4) |
| `server_id`, `client_id`, `previous_id`, `next_id`, `source`, `zone` | uuid | no | SQLite stores `x`, exit 0; PostgreSQL exit 5 (`server_id` probed) | seed (C4) |
| `args` | text (not null) | yes (text as given; `args=1` stays text) | `none` exit 5, traceback (both) | seed (C4), the `none` case only |
| `command`, `outpath`, `errpath`, `csvpath`, `submit_host`, `server_host`, `client_host`, `fingerprint` | text | yes (text as given; `none` clears) | any text is a valid value | unchanged |
| `tag` | mapping (JSON, not null) | no | filtered `tag=x` replaces the whole tag with the JSON string `"x"` (both), `tag=none` with JSON `null` (SQLite); single-task `ID tag x` merges the tag `x` | seed (C4) |

The datetime columns, `outpath`, `errpath` and `args` were asked about in the brief. The text
columns already take the text as given and have no kind to read by; the datetime columns need a
decision this small cycle cannot take (which forms, which time zone, whether `hs update` should
write scheduler times at all), and they fail differently on the two backends. Both belong with the
other columns in one follow-up seed (C4).

## Outcome / vision

`hs update` reads a change to `cores`, `memory`, `timeout`, `exit_status` or `attempt` by the
column's kind, with the parser and range `hs submit` uses for the same value: `cores` as a whole
number, `memory` as a size, `timeout` as a duration, `exit_status` and `attempt` as whole numbers.
A value of the kind inside its range is stored as the number it reads, on both backends, in both
forms. Any other value is refused with a usage error naming the column, the range and the value,
exit 2, before anything is counted, asked or written. Every other change behaves as today.

## Acceptance criteria (the contract)

Terms. A **typed change** is `COLUMN=VALUE` in the filtered form, or `ID COLUMN VALUE` in the
single-task form, where COLUMN is `cores`, `memory`, `timeout`, `exit_status` or `attempt`. **The
table** is `bounded-inputs`' range table (`SETTINGS`), its parsers, its refusal form (that GOAL's
R4) and its advisory tier (R13), as landed.

- **R1** — WHEN `hs update` is given a typed change to `cores`, `memory` or `timeout`, it SHALL read
  VALUE as `hs submit` reads `-c`, `-m` and `-W` (the table's `task.cores`, `task.memory` and
  `task.timeout` entries), and store the number read: `cores` a whole number; `memory` a size in the
  forms `hs submit -m` takes (`4G`, `512MB`, `1.5GiB`, a bare number of bytes); `timeout` a duration
  in the forms `hs submit -W` takes (`90m`, `1h30m`, `1:30:00`, `1-12`, a bare number of seconds)
  (C2). The rule SHALL be the same in both forms and on SQLite and PostgreSQL.
- **R2** — WHEN `hs update` is given a typed change to `exit_status` or `attempt`, it SHALL read
  VALUE as a whole number in the range C3 names, and store the integer. Both columns stay writable
  (C1).
- **R3** — `none` and `null`, in any case, SHALL clear `cores`, `memory`, `timeout` and
  `exit_status`, as today. For `attempt`, which is never empty, they SHALL be refused under R4.
- **R4** — IF a typed change's VALUE is not a value of the column's kind, or lies outside its range,
  including `x`, an empty value, `true`, `nan`, `inf`, a fraction for `cores`, `exit_status` or
  `attempt`, a duration for `memory`, a size for `timeout`, and a value one past either bound, THEN
  `hs update` SHALL exit with status 2 and the table's one message naming the column, the range and
  the value, with no traceback, before it counts the matching tasks, prints the count or prompts,
  and SHALL write nothing, neither that column nor any other change given with it. `-f/--no-confirm`
  SHALL NOT change this.
- **R5** — WHERE a typed change's VALUE is inside its range but past the table's advisory limit for
  it (`cores` above 1,024; `attempt` above 100 if C3 takes that entry), `hs update` SHALL write it
  and log the table's one WARNING, as `bounded-inputs` R13 does for the resource tags; the exit
  status SHALL NOT change.
- **R6** — Every other change SHALL behave as today: a group change (#124), the text columns, tag
  changes (`KEY:VALUE`), `--cancel`, `--revert`, `--delete`, `--remove-tag`, the single-task form's
  refusals, and the columns this GOAL leaves out (Non-goals).
- **R7** — Tests SHALL cover R1–R5 for each of the five columns, in both forms, on SQLite and
  PostgreSQL: each value R4 lists, each bound and one past it, each accepted form R1 lists, `none`
  (R3), a refused typed change given with another change (nothing written), a refusal under `-f`,
  and the advisory WARNING once at a value just past its limit and not at the limit.
- **R8** — The `hs update` help and `docs/_include/task_update_desc.rst` SHALL say that `cores`,
  `memory` and `timeout` take what `hs submit -c`, `-m` and `-W` take, that `exit_status` and
  `attempt` take whole numbers, each within the range the documentation lists for it, and that any
  other value exits with status 2 before anything is counted, asked or written. Changed passages
  carry `.. versionchanged:: 2.9.0`. Help and docs name top-level commands only. The squash commit
  body (the release note) SHALL list each change below as a behaviour change.

### User-visible changes (the release note)

Each is a change from `develop` `39834600` and from 2.8.x.

1. **`hs update` reads `cores`, `memory` and `timeout` as `hs submit` does.** `memory=4G` and
   `timeout=1:30:00` are stored as bytes and seconds. Earlier releases stored the text on SQLite,
   where a text `cores` or `memory` stopped the client that took the task, and failed with exit 5
   and a traceback on PostgreSQL.
2. **`hs update` refuses a value that is not a number of the column's kind, or out of its range,**
   for `cores`, `memory`, `timeout`, `exit_status` and `attempt`: exit 2 naming the column, the range
   and the value, before counting or asking, with nothing written. Earlier releases stored text,
   fractions and out-of-range integers on SQLite, took `true` as 1, and failed with exit 5 and a
   traceback on PostgreSQL, except that a fraction was rounded there. *Affects:* scripts that pass one
   of those values; `cores=1.5`, `exit_status=true` and the like now fail instead of storing 1.5
   (SQLite), 2 (PostgreSQL) or 1.
3. **`attempt=none` is a usage error,** exit 2, instead of exit 5 and a traceback.

## Non-goals (no-gos)

- **New ranges or parsers.** The ranges, the parsers, the refusal message and the advisory tier
  are `bounded-inputs`'. This cycle applies them; any entry C3 adds goes into that table, not
  beside it.
- **Other columns** (C4): `waited`, `duration`, `part`, `cores_max`, `memory_max`, `retried`, the
  datetime columns, the uuid columns, `tag=VALUE`, `args=none`, and the single-task `ID id VALUE`.
  The foreman files them as one seed if C4 is taken as recommended.
- **Refusing writes to bookkeeping columns.** Whether `hs update` should write `exit_status` and
  `attempt` at all is C1; this GOAL keeps them writable unless the gate rules otherwise.
- **Rows already stored** with a text or out-of-range value are not migrated, read back or refused;
  the scheduler and client are unchanged.
- **The group** (#124) and **the single-task form's selection options** (#124) are unchanged.
- **Query values.** `-w` comparisons are queries, not writes, as `bounded-inputs` rules.
- **The Python API.** `Task.update` and the other model writes take what a caller passes, as
  `bounded-inputs` C3 rules for explicit arguments.
- **The 2.8 line.** 2.9 only.

## Clarifications

Proposed in shaping on 2026-11-24 by the shaping agent, who could not ask the maintainer. C1–C4 are
marked for the gate.

- **C0 — Kind, appetite, target** (seed, `status: unshaped`): `fix`, `small`, 2.9.0, for 2.9.0a10.
  Builds after `bounded-inputs` lands. The seed's other dependency,
  `update-group-and-legacy-filters`, landed as #124 (`39834600`); its refusal style, exit 2 before
  counting or writing in both forms, is reused here.
- **C1 — Should `exit_status` and `attempt` stay writable?** [NEEDS CLARIFICATION: keep both
  writable and typed, or refuse writes to them?] Options:
  - **(a) Keep both writable, read as whole numbers** (R2 as written).
  - **(b) Refuse both,** exit 2, pointing at `--cancel` and `--revert`.
  - **(c) Keep `exit_status` writable, refuse `attempt`.**
  - **(d) Keep both writable, with a WARNING** that they are bookkeeping.

  **Recommended: (a).** `--cancel` and `--revert` cover cancelling and re-running, but not every
  use: marking a failed task as succeeded so it is not retried (`exit_status=0 -F`), or giving a
  task another try by lowering `attempt`. The project's own tests set up states this way
  (`tests/test_list.py` writes `exit_status=0`, `-1`, `-9`, `-15` with `hs update`, and so do other
  test modules), which suggests scripts in the wild do too. The defect is the untyped write, which
  (a) fixes; refusing is a separate interface decision, and doing it in 2.9.0 without a deprecation
  release would break those scripts with no replacement for the first use. If the maintainer wants
  them read-only, that is a seed with a deprecation step (a WARNING in 2.9, a refusal later).
- **C2 — Do `memory` and `timeout` take the full grammars?** [NEEDS CLARIFICATION: full size and
  duration forms, or bare numbers only?] Options:
  - **(a) The full grammars** `hs submit -m` and `-W` take: units, and the Slurm time forms
    (R1 as written).
  - **(b) Bare numbers only** (bytes and seconds), refusing units.

  **Recommended: (a).** The seed asks for "the same parsers `hs submit` uses", and after
  `bounded-inputs` that is one parser per kind, which reads the units and Slurm forms already (its
  R2 keeps every form accepted today). A user who submitted with `-m 4G` expects `memory=8G` to
  work; under (b) it would be refused, and a bare number would mean bytes, a unit the user rarely
  writes. (b) also needs a second parser, which `bounded-inputs` exists to prevent.
- **C3 — Ranges for `exit_status` and `attempt`.** [NEEDS CLARIFICATION: which table entries?] The
  table has `task.cores`, `task.memory` and `task.timeout`, but no entry for the `exit_status` or
  `attempt` columns.
  - `attempt`: **(a) reuse `server.attempts`' entry** (`ATTEMPTS`, 1 … 32,767, advisory above 100),
    whose stated reason is already "a task's attempt is a 16-bit integer on PostgreSQL";
    **(b) add a column entry** of the same bounds without the advisory limit.
  - `exit_status`: **(a) add one entry to the table**, an "Exit status" class of −32,768 … 32,767,
    the `SMALLINT` column's own limits, by the table's rule that a ceiling comes from the narrowest
    place a value is stored; no advisory limit. It holds every status HyperShell writes (0 to 255,
    negative signal statuses, `-1` for cancelled, `-1001` and `-1002` for tasks that never ran).
    **(b) A narrower range of meaningful statuses** (for example −1,002 … 255), which would refuse a
    status a future sentinel might use.

  **Recommended: (a) for both.** Neither redefines a range: `attempt` reuses an entry about the same
  column, and `exit_status` gains one row in the one table, so a reviewer still has one place to
  look. Moot for a column C1 makes read-only.
- **C4 — Which columns this cycle covers.** [NEEDS CLARIFICATION: the five, or every column?]
  - **(a) The seed's five** (`cores`, `memory`, `timeout`, `exit_status`, `attempt`), and the
    foreman files one seed for the rest of the inventory: the integer bookkeeping columns
    (`waited`, `duration`, `part`), the floats, `retried`, the datetime columns, the uuid columns,
    `tag=VALUE` replacing the whole tag with a scalar, `none` on a not-null column (`args`,
    `submit_id`, `part`), and the single-task `ID id VALUE` traceback.
  - **(b) Every column,** each read by its kind or refused.

  **Recommended: (a).** The five are the ones that drive dispatch or that a user plausibly sets,
  and they have parsers and ranges ready in the table. The rest are mostly scheduler bookkeeping
  with no table entry, and the datetime columns need their own design (forms, time zone, and whether
  `hs update` should write them at all), on two backends that already disagree. (b) would add a
  dozen columns, new table entries and a datetime grammar: beyond a small appetite. The new seed's
  first question is which of those columns `hs update` should write at all; a target of 2.9.x or
  2.10.0 is the foreman's call.
- **C5 — Exit status 2** for R4 (settled by precedent, no marker): a typed change is a command-line
  argument, refused as `group=x` is (#124 C4), and the table's R4 sends an option's refusal to 2.
  The `DataError` and `IntegrityError` tracebacks (exit 5) on PostgreSQL become status-2 refusals.
- **C6 — Seed re-confirmed against `39834600`** on SQLite and PostgreSQL 18. Drift from the seed:
  it named `03a02bd8` and SQLite only; on PostgreSQL nothing is stored but each bad value fails with
  exit 5 and a traceback, `1.5` is rounded to 2, `true` is stored as 1 for `timeout` only, and
  `memory=4G` (a form `hs submit` takes) is refused; on SQLite `1.5` is stored as a real, `true` as
  1, `nan` as NULL, an empty value as `''`, and `attempt=none` fails with a traceback. The seed's
  "can stall" is confirmed for `cores` and `memory` (the client stops); a short run with a text
  `timeout`, `exit_status` or `attempt` completed. The inventory found more columns with the same
  defect (C4).
- **Left for `/hs-plan`** (not markers): the refusal's `<name>` (`cores`, or `cores=` as given);
  whether the check reuses the table's error directly or wraps it as #124 wraps the group's; how a
  refusal of two typed changes at once is reported; where the advisory WARNING is flushed in
  `hs update`.

## Related materials

- Seed: [`issues/update-typed-columns.md`](../../issues/update-typed-columns.md).
- Dependency: `bounded-inputs` (`spec/bounded-inputs/GOAL.md` on `fix/bounded-inputs`; the table,
  R3, R4, R9 and R13; `src/hypershell/core/config_schema.py` `SETTINGS`, `CORES`, `SIZE`,
  `DURATION`, `ATTEMPTS`, `SettingError`, `parse_setting`, `advise`).
- Precedent: [`spec/update-group-and-legacy-filters/`](../update-group-and-legacy-filters/) (#124:
  `change_value`, exit 2 before counting or writing, both forms; its C3 filed this seed);
  [`spec/update-client-retry/`](../update-client-retry/) (#119: `hs update` refusals);
  [`spec/submit-input-checks/`](../submit-input-checks/) (#118: the group rule).
- Source: `src/hypershell/task.py` (`change_value`, `TaskUpdateApp.run`, `update_legacy`,
  `process_arguments`, `UPDATE_HELP`), `src/hypershell/data/model.py` (`Task` columns and their
  types, `Task.columns`), `src/hypershell/client.py` (resource checks that a text value stops),
  `src/hypershell/core/types.py` (`smart_coerce`, `parse_bytes`, `parse_time`).
- Docs: `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`,
  `docs/cli/task_update.rst`.
