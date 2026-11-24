# GOAL — `hs update` reads every column it writes by the column's kind

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** update-typed-columns
- **kind:** fix
- **appetite:** big  ·  *Widened at the GOAL gate (maintainer, C4, 2026-11-24): every column
  `hs update` can write, not the seed's five. Still one argument check in `hs update` before
  anything is counted or written, beside #124's group check, but over 33 columns of eight kinds,
  with new rows in `bounded-inputs`' table, a datetime rule for two backends, and a test matrix of
  every column, both forms and both backends. The seed's `small` is recorded here, not changed.*
- **target:** 2.9.0  ·  *for 2.9.0a10. It refuses values accepted today, an interface change, which
  the roadmap's Target rule sends to a minor release; 2.9.0 is one.*
- **builds after:** `bounded-inputs` lands on `develop` (in review on `fix/bounded-inputs`,
  `a0c227a6`). This GOAL names that cycle's range table (`SETTINGS`, `core/config_schema.py`), its
  parsers, its refusal form and its advisory tier; `hs-plan` designs against the table as it lands.
  The new rows this GOAL needs (*Column kinds*) are added to that table by this cycle, after it lands.

## Problem

`hs update` writes the value it is given into a task's columns without reading it by the column's
kind. **Pre-existing.** Reproduced on `develop` `39834600` (2.9.0a9 plus #124) in a throwaway site
(`.agents/factory/bin/temp_site.sh`), on SQLite and on a local PostgreSQL 18, in both the filtered
form (`hs update COL=VALUE -w "id == ID" -f`) and the single-task form (`hs update ID COL VALUE`),
one fresh task per probe. The two forms behaved the same in every case. Line numbers are that
commit's.

Both forms build the value with `change_value` (`src/hypershell/task.py:1036-1046`): a group goes
through `parse_group` since #124, a text column takes the text as given, and every other column
takes `smart_coerce`, which turns `true` into 1, `1.5` into a float, `nan` into a float, and leaves
anything else as text. What happens next depends on the backend: SQLite stores almost anything,
and PostgreSQL's column types refuse it with a traceback.

**The seed's five columns** (`cores`, `memory`, `timeout`, `exit_status`, `attempt`):

| Given | SQLite (exit, stored) | PostgreSQL (exit, stored) |
| --- | --- | --- |
| `cores=x`, `memory=x`, `timeout=x`, `exit_status=x`, `attempt=x` | 0, the text `'x'` | 5, nothing; a `DataError` traceback |
| `memory=4G`, `timeout=1:30:00` (forms `hs submit` takes) | 0, the text | 5, nothing; a `DataError` traceback |
| `cores=1.5` (any of the five) | 0, the real `1.5` | 0, rounded to `2` |
| `cores=true` | 0, `1` | 5 for `cores`, `memory`, `exit_status`, `attempt` (`CannotCoerce`); 0 and `1` for `timeout` |
| `cores=` (empty) | 0, the text `''` | 5, a `DataError` traceback |
| `cores=100000`, `exit_status=100000`, `attempt=100000` | 0, the integer | 5, `smallint out of range` traceback |
| `timeout=3000000000` | 0, the integer | 5, `integer out of range` traceback |
| `attempt=none` | 5, `NOT NULL constraint failed` traceback | 5, `NotNullViolation` traceback |
| `cores=none` (and `memory`, `timeout`, `exit_status`) | 0, NULL (correct) | 0, NULL |

**The effect on dispatch** (SQLite, one `echo` task, `hs cluster -N 1` given 45 s): after
`cores=x` or `memory=4G`, the client's executor stopped with `TypeError: '<' not supported between
instances of 'int' and 'str'`; the run did not finish, and the task was left scheduled and never
completed. After `timeout=1:30:00`, `exit_status=x` or `attempt=x`, that short run completed.

**Every other column** (`Task.columns`, `src/hypershell/data/model.py:482-517`, 34 columns; the
filtered form refuses `id`, exit 2):

| Column | Kind (SQL type) | What a bad value does today |
| --- | --- | --- |
| `id` | uuid | filtered form: exit 2; single-task `ID id x`: exit 5, traceback |
| `group` | int | exit 2 since #124 (correct) |
| `waited`, `duration` | int, seconds (`INTEGER`) | SQLite stores `x`, exit 0; PostgreSQL exit 5 |
| `part` | int (`INTEGER`, not null) | SQLite stores `x`, exit 0; `none` exit 5; PostgreSQL exit 5 |
| `cores_max`, `memory_max` | float (`DOUBLE`) | `x` exit 5, traceback (both); SQLite stores `inf` |
| `retried` | bool (not null) | `true`, `false`, `1`, `0` stored; `yes`, `on`, `x`, `2` exit 5 (`Not a boolean value`); `none` exit 5 (not null); both backends |
| `submit_time` (not null), `schedule_time`, `start_time`, `completion_time` | datetime (`timestamp with time zone`) | SQLite: every value but `none` exits 5 (`SQLite DateTime type only accepts Python datetime`), ISO 8601 included. PostgreSQL: the server parses the text, so ISO 8601 (with or without an offset), `now`, `today` and `epoch` are stored, a naive value in the server session's zone; `x`, `5`, `1h` exit 5. `none` clears all but `submit_time` (exit 5) |
| `submit_id` (not null), `server_id`, `client_id`, `previous_id`, `next_id` | uuid | SQLite stores `x`, exit 0; PostgreSQL exit 5; `submit_id=none` exit 5 |
| `source` | uuid: a `Source` row's id, or a reserved id (`<direct>`, `<stdin>`) | as the uuid columns |
| `zone` | uuid: `uuid5(ZONE_NAMESPACE, name)` of a zone name; NULL for the default zone, whose reserved id is never stored | SQLite stores `default` or `x` as text; PostgreSQL exits 5 for them. Both store the default zone's reserved id when given it, which the default-zone filter (`zone IS NULL`, `model.py:937`) does not match (read, not run) |
| `args` | text (not null) | text as given; `none` exit 5, traceback (both) |
| `command`, `outpath`, `errpath`, `csvpath`, `submit_host`, `server_host`, `client_host`, `fingerprint` | text | any text is a value; `none` clears (correct) |
| `tag` | mapping (JSON, not null) | filtered `tag=x` replaces the whole tag with the JSON string `"x"` (both backends), `tag=none` with JSON `null` (SQLite); single-task `ID tag x` merges the tag `x` (correct) |

So a bad value either reaches the database (SQLite) and reports success, or fails with exit 5 and a
traceback instead of a usage error; a datetime cannot be written at all on SQLite and is parsed by
the server on PostgreSQL; and the two backends disagree on almost every column.

## Outcome / vision

`hs update` reads every value it writes by the column's kind, through `bounded-inputs`' parsers and
ranges where the column has one: integers, sizes, durations and decimal numbers in range, booleans,
datetimes, uuids, a zone, and text. A value of the kind inside its range is stored as read, on both
backends and in both forms, so the two backends hold the same thing. Any other value is refused
with a usage error naming the column, the range or form and the value, exit 2, before anything is
counted, asked or written. No bad value on any column ends in a traceback or exit 5. `id` is never
written, and `tag=VALUE`, which would replace the whole tag, is refused. An assignment `KEY=VALUE`
whose key is not a task column is a tag change, the same as `KEY:VALUE` (R16), and the prompt shows
which changes are columns and which are tags. Text columns, `KEY:VALUE` changes and the action
options behave as today.

## Column kinds (the contract's table)

`table:` names an entry of `bounded-inputs`' table. **New** rows are added to that table by this
cycle; each was ruled at the gate (C3 the first, C9 the others). "Clears" means `none` or
`null`, in any case, writes NULL; "refused" means R9.

| Column | Read as | Range (table entry) | `none` |
| --- | --- | --- | --- |
| `cores` | whole number | `task.cores`: 0 … 32,767 (advise above 1,024) | clears |
| `memory` | size, the forms `hs submit -m` takes | `task.memory`: 0 … 2⁶³−1 bytes | clears |
| `timeout` | duration, the forms `hs submit -W` takes | `task.timeout`: 0 … 2³¹−1 s | clears |
| `exit_status` | whole number | **new**, Exit status: −32,768 … 32,767 (C3) | clears |
| `attempt` | whole number | `server.attempts` (`ATTEMPTS`): 1 … 32,767 (advise above 100) (C3) | refused |
| `part` | whole number | `option.part` (`PART`): 0 … 2³¹−1 | refused |
| `waited`, `duration` | duration (seconds; a bare whole number is seconds) | `task.timeout`'s Duration class (`DURATION`): 0 … 2³¹−1 s | clears |
| `cores_max` | decimal number | **new**, Peak cores: 0 … 32,767 (C9) | clears |
| `memory_max` | decimal number of bytes | **new**, Peak memory: 0 … 2⁶³−1 (C9) | clears |
| `retried` | boolean: the configuration's words, any case: `true`/`false`, `yes`/`no`, `on`/`off`, `1`/`0` | — | refused |
| `submit_time` | datetime, ISO 8601 (C6) | — | refused |
| `schedule_time`, `start_time`, `completion_time` | datetime, ISO 8601 (C6) | — | clears |
| `submit_id` | uuid | — | refused |
| `server_id`, `client_id`, `previous_id`, `next_id`, `source` | uuid | — | clears |
| `zone` | a zone name, as `hs submit --zone` reads it; `default` is the default zone (C8) | — | clears (the default zone) |
| `group` | as #124 | `option.group` | refused (#124) |
| `args` | text as given | — | refused |
| `command`, `outpath`, `errpath`, `csvpath`, `submit_host`, `server_host`, `client_host`, `fingerprint` | text as given | — | clears (as today) |
| `tag` | never assigned whole: `tag=VALUE` is refused (R11, C7) | — | refused |
| `id` | never written (R10) | — | — |

A **uuid** is the 8-4-4-4-12 hexadecimal form, in any case, stored in lower case. Its value is not
checked against another table (`source` against `Source`, `previous_id` against a task).

A **datetime** is ISO 8601: a date (`2026-01-01`, midnight), or a date and a time joined by `T` or a
space, with optional seconds and fraction, and an optional offset or `Z`. A value without an offset
is local time, the zone HyperShell writes and shows times in. `now`, `today`, `epoch` and relative
forms are not datetimes (C6).

Any other key, one that is not a column of `Task`, makes `KEY=VALUE` a tag change (R16).

## Acceptance criteria (the contract)

Terms. A **change** is `COLUMN=VALUE` in the filtered form, or `ID COLUMN VALUE` in the single-task
form. **The table** is `bounded-inputs`' range table (`SETTINGS`), its parsers, its refusal form
(that GOAL's R4) and its advisory tier (R13), as landed, with the rows *Column kinds* adds.

**The principle.**

- **R1** — Every column `hs update` can write on `develop` today SHALL stay writable, except `id`
  (R10) and the whole-tag assignment (R11). WHEN `hs update` is given a change, it SHALL read VALUE
  by the column's kind, as *Column kinds* states, and store the value read. The rule SHALL be the
  same in both forms and on SQLite and PostgreSQL, so that the same change leaves the same value on
  both.

**By kind.**

- **R2** — `cores`, `memory` and `timeout` SHALL be read as `hs submit` reads `-c`, `-m` and `-W`
  (the `task.cores`, `task.memory` and `task.timeout` entries): `memory` in every size form
  (`4G`, `512MB`, `1.5GiB`, a bare number of bytes), `timeout` in every duration form (`90m`,
  `1h30m`, `1:30:00`, `1-12`, a bare number of seconds) (C2).
- **R3** — `exit_status`, `attempt` and `part` SHALL be read as whole numbers, and `waited` and
  `duration` as durations, each within its entry in *Column kinds*. `exit_status` and `attempt`
  stay writable (C1).
- **R4** — `cores_max` and `memory_max` SHALL be read by the table's decimal-number parser,
  finite and within their new rows (C9).
- **R5** — `retried` SHALL take the configuration's boolean words (`true`/`false`, `yes`/`no`,
  `on`/`off`, `1`/`0`, any case), as a boolean key does since #113.
- **R6** — `submit_time`, `schedule_time`, `start_time` and `completion_time` SHALL take ISO 8601
  only, as *Column kinds* defines it, with a value without an offset read as local time (C6). They
  SHALL hold the same instant on SQLite and PostgreSQL, shown the same by `hs info` and `hs list`.
  `now`, `today`, `epoch` and any other form SHALL be refused under R9.
- **R7** — `submit_id`, `server_id`, `client_id`, `previous_id`, `next_id` and `source` SHALL take a
  uuid. `zone` SHALL take a zone name, read as `hs submit --zone` reads it: `default` and `none`
  SHALL write NULL (the default zone), and any other name its zone (C8).

**`none`, refusals, `id` and the tag.**

- **R8** — `none` and `null`, in any case, SHALL clear a column that may be empty (*Column kinds*),
  as today. For a column that may not (`args`, `submit_id`, `submit_time`, `attempt`, `retried`,
  `part`, and `group` as #124 rules), they SHALL be refused under R9, naming the column.
- **R9** — IF a change's VALUE is not a value of the column's kind, or lies outside its range,
  including an empty value for any column but a text one, `x`, `true` for a number, `nan`, `inf`, a
  fraction for a whole-number column, a duration for `memory`, a size for `timeout`, a value one past
  either bound, and `none` for a column R8 names, THEN `hs update` SHALL exit with status 2 and the
  table's one message form naming the column, the range or form, and the value, before it counts
  the matching tasks, prints the count or prompts, and SHALL write nothing, neither that column nor
  any other change given with it. `-f/--no-confirm` SHALL NOT change this. No value given to any
  column SHALL end in a traceback or exit status 5.
- **R10** — `id` SHALL never be written: the single-task `hs update ID id VALUE` SHALL exit 2 and
  write nothing, as the filtered form's `id=VALUE` does today.
- **R11** — `tag=VALUE` in the filtered form SHALL be refused under R9, exit 2, with a message
  pointing to tag changes by `KEY=VALUE` and `KEY:VALUE`, and to `--remove-tag` (C7). `KEY:VALUE`
  changes, `--remove-tag`, and the single-task `hs update ID tag KEY:VALUE` SHALL behave as today.
- **R12** — WHERE a VALUE is inside its range but past the advisory limit of a reused entry
  (`cores` above 1,024; `attempt` above 100), `hs update` SHALL write it and log the table's one
  WARNING, as `bounded-inputs` R13 does for the resource tags; the exit status SHALL NOT change. No
  new advisory limit is added.

**Unchanged, proof and docs.**

- **R13** — Every other change SHALL behave as today: a group change (#124), the text columns
  (text as given, `none` clears), `KEY:VALUE` tag changes, `--cancel`, `--revert`, `--delete`,
  `--remove-tag`, and the single-task form's refusals (#119, #124). The one exception is a key that
  is not a task column: today's refusal of it (`Unrecognized task field` in the filtered form,
  `Invalid field name` in the single-task form, exit 2) is replaced by R16.
- **R14** — Tests SHALL cover R1–R12 for every column in *Column kinds*, in both forms, on SQLite
  and PostgreSQL: for each kind, each accepted form, each bound and one past it, each refused value
  R9 lists, and `none`; a refused change given with another change (nothing written); a refusal
  under `-f`; R6's same instant on both backends for a naive value and one with an offset; R10 in
  the single-task form; R11; and R12's WARNING once just past each reused limit and not at it.
- **R15** — The `hs update` help and `docs/_include/task_update_desc.rst` SHALL say that every
  column is read by its kind, point to the ranges the documentation lists, name the forms for
  `memory`, `timeout`, `waited`, `duration`, `retried`, the datetime columns, the uuid columns and
  `zone`, and say that any other value exits with status 2 before anything is counted, asked or
  written; and the documentation of the table SHALL list the new rows. Changed passages carry
  `.. versionchanged:: 2.9.0`. Help and docs name top-level commands only. The squash commit body
  (the release note) SHALL list each change below as a behaviour change.

**Any other key is a tag** (maintainer, 2026-11-24).

- **R16** — WHEN the filtered form is given `KEY=VALUE` whose KEY is not a column of `Task`, or the
  single-task form `hs update ID KEY VALUE` with such a KEY, `hs update` SHALL make it a tag
  change, the same as `KEY:VALUE` and as `hs update ID tag KEY:VALUE` today: the same key rules and
  the same reading of VALUE. `KEY:VALUE` SHALL be unchanged. `id` and `tag` are columns, so
  `id=VALUE` and `tag=VALUE` stay refused (R10, R11). IF KEY is not a valid tag key, THEN
  `hs update` SHALL exit 2 with the tag rules' message, before counting or prompting, and write
  nothing. WHEN the filtered form prompts (without `-f`), the prompt SHALL show each change as a
  column change or a tag change, so that a misspelled column (`exit_stauts=0`) is visible as a tag
  before anything is written. Tests SHALL cover both forms and both backends: a key that collides
  with nothing, a misspelled column, an invalid tag key, and the prompt's text. The help and
  `docs/_include/task_update_desc.rst` SHALL say that any key that is not a column is a tag, and
  that a misspelled column becomes a tag.

  *Consequence, accepted by the maintainer:* a misspelled column is written as a tag instead of
  being refused. Without `-f` the prompt shows it; under `-f`, and in the single-task form, which
  never prompts, it is written without one. *Amended by R17 (maintainer, 2026-11-24):* a
  misspelling within one edit of a column is refused; only a farther one becomes a tag.

**A near-miss column key is refused** (maintainer, 2026-11-24).

- **R17** — IF, under R16, a `KEY=VALUE` in the filtered form, or `hs update ID KEY VALUE` in the
  single-task form, has a KEY that is not a column of `Task` but is within one edit of a column
  name, THEN `hs update` SHALL exit 2 before it counts the matching tasks, prints the count or
  prompts, and SHALL write nothing, neither that change nor any other given with it; `-f` SHALL NOT
  change this. *One edit* is Damerau distance 1 (an insertion, a deletion, a substitution, or a
  swap of two adjacent characters), compared without regard to case, so `exit_stauts` and
  `Exit_Status` (and any key equal to a column name ignoring case) are caught. Every column counts,
  the short names included (`id`, `tag`, `zone`, `part`, `group`). The message SHALL name the key,
  say it is not a task column, suggest the nearest column (every tie, if several), and say how to
  set a tag by that name, for example:
  `exit_stauts: not a task column; did you mean exit_status? To set a tag by that name, write
  exit_stauts:VALUE`. `KEY:VALUE` SHALL never be checked: it always sets a tag, even when KEY is
  close to or equal to a column name, and is the way to reach a tag whose name resembles a column.
  Tests SHALL cover both forms and both backends: a transposition, an insertion, a deletion, a
  substitution and a case-only difference; a short-name near-miss; a key two edits away, which
  becomes a tag (R16); the `:` way out with a near-miss key and with an exact column name; and
  nothing written on a refusal under `-f`. The help, `docs/_include/task_update_desc.rst` and the
  release note SHALL state the rule and the `:` way out.

  *Consequence, accepted by the maintainer:* a tag whose name is one edit from a column (`tags`,
  `ip`, `groups`, `zones`) cannot be set by `KEY=VALUE`; it is refused, and `KEY:VALUE` sets it.

### User-visible changes (the release note)

Each is a change from `develop` `39834600` and from 2.8.x.

1. **`hs update` reads `cores`, `memory` and `timeout` as `hs submit` does.** `memory=4G` and
   `timeout=1:30:00` are stored as bytes and seconds. Earlier releases stored the text on SQLite,
   where a text `cores` or `memory` stopped the client that took the task, and failed with exit 5
   and a traceback on PostgreSQL.
2. **`hs update` refuses a value that is not of the column's kind, or out of its range, on every
   column:** exit 2 naming the column, the range or form and the value, before counting or asking,
   with nothing written. Earlier releases stored text, fractions and out-of-range integers on
   SQLite, took `true` as 1, and failed with exit 5 and a traceback on PostgreSQL, except that a
   fraction was rounded there. *Affects:* scripts that pass such a value; `cores=1.5`,
   `exit_status=true` and the like now fail instead of storing 1.5, 2 or 1.
3. **`waited` and `duration` take durations,** as `timeout` does (`1h30m` is 5,400 seconds).
4. **`retried` takes `yes`/`no` and `on`/`off`** as well as `true`/`false` and `1`/`0`.
5. **`none` on a column that may not be empty** (`args`, `submit_id`, `submit_time`, `attempt`,
   `retried`, `part`) exits 2 naming the column, instead of exit 5 and a traceback.
6. **The datetime columns take ISO 8601, on both backends.** A value without an offset is local
   time. Earlier releases refused every value with exit 5 and a traceback on SQLite, and on
   PostgreSQL stored whatever the server parsed, in the server session's zone. `now`, `today` and
   `epoch`, which PostgreSQL stored, now exit 2. *Affects:* scripts that wrote `now` on PostgreSQL;
   use `$(date -Iseconds)`.
7. **`hs update ID id VALUE` exits 2,** as `id=VALUE` does; earlier it failed with a traceback.
8. **`tag=VALUE` is refused,** exit 2, pointing to `KEY=VALUE`, `KEY:VALUE` and `--remove-tag`.
   Earlier releases replaced the task's whole tag with the value as JSON text (`"x"`, or `null`).
9. **`zone=` takes a zone name,** as `hs submit --zone` does; `zone=default` and `zone=none` put the
   task in the default zone. Earlier releases stored the text on SQLite, refused a name with exit 5
   on PostgreSQL, and on both stored the default zone's reserved id when given it, which the default
   zone's clients do not match.
10. **`KEY=VALUE` with a key that is not a task column is a tag change,** the same as `KEY:VALUE`, in
    both forms (`hs update ID KEY VALUE` too). Earlier releases refused it, exit 2 (`Unrecognized
    task field`, `Invalid field name`). The prompt shows each change as a column or a tag.
    *Affects:* a misspelled column more than one edit from any column is now written as a tag
    instead of refused; check the prompt, and take care under `-f` and in the single-task form,
    which do not prompt.
11. **A key one edit from a column is refused** (`exit_stauts=0`, `Exit_Status=0`, `tags=x`), exit 2,
    naming the key and the nearest column, with nothing written. `KEY:VALUE` always sets a tag, and
    is the way to set a tag whose name is close to a column's.

## Non-goals (no-gos)

- **New parsers, or ranges outside the table.** Ranges, parsers, the refusal message and the
  advisory tier are `bounded-inputs`'. The rows this GOAL adds go into that table, not beside it,
  and no new advisory limit is added.
- **Referential checks.** A uuid is checked for its form, not for a row it names (`source`,
  `previous_id`, `next_id`, `submit_id`).
- **Rows already stored** with a text, out-of-range or misplaced value are not migrated, read back
  or refused.
- **The scheduler and the client** are unchanged, including how they read a stored value.
- **Query values.** `-w` comparisons are queries, not writes, as `bounded-inputs` rules.
- **The Python API.** `Task.update` and the other model writes take what a caller passes, as
  `bounded-inputs` C3 rules for explicit arguments.
- **The group** and **the single-task form's selection options** (#124) are unchanged.
- **The 2.8 line.** 2.9 only.

## Clarifications

Proposed in shaping on 2026-11-24 by the shaping agent, who could not ask the maintainer. C1–C4
were ruled at the GOAL gate on 2026-11-24; C6–C9, raised by the widening, were ruled at the second
gate the same day (*GOAL gate (2026-11-24)* below). No marker remains.

- **C0 — Kind, appetite, target** (seed, `status: unshaped`): `fix`, target 2.9.0, for 2.9.0a10;
  appetite `small` from the seed, widened to `big` by C4. Builds after `bounded-inputs` lands. The
  seed's other dependency, `update-group-and-legacy-filters`, landed as #124 (`39834600`); its
  refusal style, exit 2 before counting or writing in both forms, is reused here.
- **C1 — `exit_status` and `attempt` stay writable. Resolved 2026-11-24** (maintainer): **(a)**,
  writable and read by kind. The options were (a) writable and typed, (b) refuse both, pointing at
  `--cancel` and `--revert`, (c) refuse `attempt` only, (d) writable with a WARNING. (a) was
  recommended: `--cancel` and `--revert` do not cover marking a failed task as succeeded or giving
  a task another try, and the project's own tests set up states with `hs update exit_status=…`.
- **C2 — `memory` and `timeout` take the full grammars. Resolved 2026-11-24** (foreman, under the
  maintainer's mandate): **(a)**, every size and duration form, through `bounded-inputs`' entries.
  The alternative, bare numbers only, would need a second parser.
- **C3 — Ranges for `exit_status` and `attempt`. Resolved 2026-11-24** (foreman, under the
  maintainer's mandate): as recommended. `attempt` takes the `ATTEMPTS` entry (1 … 32,767, advise
  above 100), whose stated reason already names the column. `exit_status` gets one new row, Exit
  status, −32,768 … 32,767, the `SMALLINT` column's limits by the table's ceiling rule, with no
  advisory limit; it holds every status HyperShell writes (0 to 255, negative signal statuses, −1
  for cancelled, −1001 and −1002 for tasks that never ran). The row is added to `bounded-inputs`'
  table by this cycle, after that cycle lands.
- **C4 — Which columns. Resolved 2026-11-24** (maintainer): **every column, in this cycle**,
  overriding the recommendation of the seed's five. Appetite becomes `big`.
- **C5 — Exit status 2** for R9 (settled by precedent, no marker): a change is a command-line
  argument, refused as `group=x` is (#124 C4), and the table's R4 sends an option's refusal to 2.
  The `DataError`, `StatementError` and `IntegrityError` tracebacks (exit 5) become status-2
  refusals.
- **C6 — The datetime rule. Resolved 2026-11-24** (maintainer): **(a) and (i)**, ISO 8601 only, a
  value without an offset is local time. Today SQLite takes nothing but `none`, and PostgreSQL takes whatever its server parses,
  in the server session's zone. HyperShell writes these columns as aware local times
  (`datetime.now().astimezone()`, as `--cancel` does); SQLite keeps the local wall time and
  PostgreSQL the instant (`timestamp with time zone`).
  - *Forms.* **(a) ISO 8601 only:** a date (`2026-01-01`, midnight), or a date and time with `T` or a
    space, optional seconds and fraction, and an optional offset or `Z`. **(b) (a) and `now`.**
    **(c) (b) and a relative duration** (`-1h`, `+30m` from now).
  - *Time zone of a value without an offset.* **(i) local,** the zone HyperShell writes and shows
    times in; **(ii) UTC.**
  - *`none`.* Clears `schedule_time`, `start_time` and `completion_time`; refused for `submit_time`
    (R8). Not a marker.

  **Recommended: (a) and (i).** ISO 8601 is one grammar Python reads as-is (`fromisoformat`), with
  no new parser to specify; `now` and relative times are conveniences a shell already gives
  (`$(date -Iseconds)`), and they add rules (whose clock, rounding) for little gain. A naive value
  read as local matches what `--cancel` writes, what SQLite already holds, and what a user sees, so
  `start_time=2026-01-01T09:00` means 09:00 as `hs info` shows it. A value with an offset is the
  same instant on both backends. On PostgreSQL this refuses `now`, `today` and `epoch`, which its
  server accepts today (release note 6).
- **C7 — `tag=VALUE`. Resolved 2026-11-24** (maintainer): **(a)**, refused, exit 2, the message
  pointing to `KEY=VALUE`, `KEY:VALUE` and `--remove-tag`. Today the
  filtered form's `tag=x` replaces the task's whole tag with the JSON string `"x"` (and `tag=none`
  with JSON `null`), where every reader expects a mapping.
  - **(a) Refuse `tag=VALUE`,** exit 2, pointing to `KEY:VALUE` changes and `--remove-tag`.
  - **(b) Read VALUE as a tag set** (for example `tag=a:1,b`), replacing the whole tag.
  - **(c) Keep it** as today.

  **Recommended: (a).** `KEY:VALUE` and `--remove-tag` are the documented way to change tags, and
  together they cover every edit but "replace all", which `--remove-tag` of each name and the new
  `KEY:VALUE` pairs already express. (b) invents a second tag syntax for one column, with its own
  quoting and refusals; (c) keeps a write that corrupts the column. The single-task
  `ID tag KEY:VALUE`, which merges one tag, is unchanged under every option.
- **C8 — `zone=VALUE`. Resolved 2026-11-24** (maintainer): **(a)**, a zone name. `zone` holds a uuid, but one
  derived from a zone's name (`uuid5(ZONE_NAMESPACE, name)`), and NULL for the default zone, whose
  reserved id is never stored; users name zones (`hs submit --zone NAME`), and `hs list` shows the
  name. Today SQLite stores the text given, and both backends store the default zone's reserved id
  when given it, which the default-zone filter does not match.
  - **(a) A zone name,** read as `hs submit --zone` reads it: `default` and `none`
    write NULL (the default zone); any other name writes its zone id, recording the zone as
    `hs submit --zone` does.
  - **(b) A uuid or `none`,** as the other uuid columns, with the default zone's reserved id
    written as NULL.
  - **(c) Refuse `zone=`,** exit 2: a task's zone is set at submission only.

  **Recommended: (a).** It is the reading users already have from `hs submit` and `hs list`, it
  cannot strand a task under the reserved id, and it needs no uuid from the user. (b) keeps a
  value only the database shows.
- **C9 — New rows for `cores_max` and `memory_max`. Resolved 2026-11-24** (foreman, under the
  maintainer's mandate): **(a)**, the two rows as proposed. They are
  the peaks a client measures while a task runs (`client.py:932-933`), stored as `DOUBLE`; the
  table has no number entry for them.
  - **(a) Two rows by meaning:** Peak cores, a number 0 … 32,767, the cores ceiling (no host a
    task runs on has more); Peak memory, a number 0 … 2⁶³−1 bytes, the size ceiling.
  - **(b) One row by the column type:** a number from 0 to the largest finite double, for both.

  **Recommended: (a).** It follows the table's rule that a ceiling is the narrowest place a value is
  used: a peak is shown beside `cores` and `memory` and compared with them, and (b)'s ceiling is
  one no measurement reaches. Both refuse a negative value, `nan` and `inf`. No advisory limit.
- **Reused entries, not markers.** `part` takes `option.part` (`PART`, 0 … 2³¹−1, "a task's part
  is a 32-bit integer"); `waited` and `duration` take the Duration class (`DURATION`, 0 … 2³¹−1 s),
  the class `task.timeout` uses, since both are seconds. Neither adds a row. `retried` takes the
  boolean words configuration keys take since #113 (`BOOL_WORDS`), so no new grammar.
- **C10 — Seed re-confirmed against `39834600`** on SQLite and PostgreSQL 18. Drift from the seed:
  it named `03a02bd8` and SQLite only; on PostgreSQL each bad value fails with exit 5 and a
  traceback, `1.5` is rounded to 2, `true` is stored as 1 for `timeout` only, and `memory=4G` is
  refused; on SQLite `1.5` is stored as a real, `true` as 1, `nan` as NULL, an empty value as `''`,
  and `attempt=none` fails with a traceback. "Can stall" is confirmed for `cores` and `memory`.
  The inventory found the same defect in every typed column (C4).
- **Left for `/hs-plan`** (not markers): the refusal's `<name>` (`cores`, or `cores=` as given) and
  the form named for kinds the table has no range for (uuid, datetime, boolean, zone); whether the
  check reuses the table's error directly or wraps it as #124 wraps the group's; how a refusal of
  two changes at once is reported; where the advisory WARNING is flushed in `hs update`; how a
  datetime with an offset is stored on SQLite so R6 holds; for R16, which separator wins in an
argument holding both (`note:a=b` is refused today as the field `note:a`), and how the prompt
lists column and tag changes.

**Ruled at the first GOAL gate** (2026-11-24): C1 and C4 by the maintainer, C2 and C3 by the foreman
under the maintainer's mandate. The GOAL was widened from the seed's five columns to every column,
as one amendment.

### GOAL gate (2026-11-24)

Approved. Rulings, with who made each:

- **C1 (maintainer):** `exit_status` and `attempt` stay writable and are read by kind (R3).
- **C2 (foreman, under the maintainer's mandate):** `memory` and `timeout` take the full size and
  duration grammars, through `bounded-inputs`' entries (R2).
- **C3 (foreman, under the maintainer's mandate):** `attempt` takes the `ATTEMPTS` entry;
  `exit_status` gets a new row, −32,768 … 32,767, added to `bounded-inputs`' table by this cycle.
- **C4 (maintainer):** every column in this cycle; appetite `big`.
- **C6 (maintainer):** ISO 8601 only; a value without an offset is local time; `now`, `today` and
  `epoch` exit 2; `none` clears every timestamp but `submit_time` (R6, R8).
- **C7 (maintainer):** `tag=VALUE` is refused, exit 2, the message pointing to `KEY=VALUE`,
  `KEY:VALUE` and `--remove-tag` (R11).
- **C8 (maintainer):** `zone=` takes a zone name as `hs submit --zone` does; `zone=default` and
  `zone=none` write NULL, the default zone (R7).
- **C9 (foreman, under the maintainer's mandate):** (a), the Peak cores and Peak memory rows as
  proposed (R4).
- **C11 (maintainer, new):** `KEY=VALUE` for any key that is not a task column is a tag change, the
  same as `KEY:VALUE`, in both forms; the prompt shows column and tag changes apart; today's
  unknown-field refusal is replaced (R16, R13). In the maintainer's words: "I want to keep the ':'
  operator for tags, but … it's a better design to simply allow any `key=value` and if it's not a
  primary column, treat it as a tag." He accepts that a misspelled column becomes a tag; the prompt
  is where it shows.

No R-ID was renumbered; R16 is appended. The contract is locked; `hs-plan` designs against it.

- **C12 (maintainer, 2026-11-24, amendment after the gate):** a `KEY=VALUE` whose key is not a
  column but is within one edit (Damerau distance 1, case-insensitive) of a column name is refused,
  exit 2, suggesting the nearest column and the `KEY:VALUE` way out; `KEY:VALUE` is never checked
  (R17, appended). R16's misspelling consequence is amended: only a misspelling farther than one
  edit becomes a tag. Short column names count, so `tags=`, `ip=`, `groups=` and `zones=` are
  refused.

## Related materials

- Seed: [`issues/update-typed-columns.md`](../../issues/update-typed-columns.md).
- Dependency: `bounded-inputs` (`spec/bounded-inputs/GOAL.md` on `fix/bounded-inputs`; the table,
  R3, R4, R9 and R13; `src/hypershell/core/config_schema.py` `SETTINGS`, `CORES`, `SIZE`,
  `DURATION`, `ATTEMPTS`, `PART`, `BOOL_WORDS`, `SettingError`, `parse_setting`, `advise`).
- Precedent: [`spec/update-group-and-legacy-filters/`](../update-group-and-legacy-filters/) (#124:
  `change_value`, exit 2 before counting or writing, both forms; its C3 filed this seed);
  [`spec/update-client-retry/`](../update-client-retry/) (#119: `hs update` refusals);
  [`spec/submit-input-checks/`](../submit-input-checks/) (#118: the group rule);
  `spec/env-config-keys/` (#113: the boolean words).
- Source: `src/hypershell/task.py` (`change_value`, `TaskUpdateApp.run`, `update_legacy`,
  `process_arguments`, `UPDATE_HELP`), `src/hypershell/data/model.py` (`Task` columns and types,
  `Task.columns`, `DATETIME`, `UUID`, `zone_id`, `zone_name`, `DEFAULT_ZONE_ID`, the reserved
  source ids), `src/hypershell/client.py` (resource checks that a text value stops; the peaks),
  `src/hypershell/core/types.py` (`smart_coerce`, `parse_bytes`, `parse_time`),
  `src/hypershell/submit.py` (`--zone`).
- Docs: `docs/_include/task_update_desc.rst`, `docs/_include/task_update_help.rst`,
  `docs/cli/task_update.rst`.
