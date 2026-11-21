# GOAL — Submitted input is checked: `#` is part of the command, a group is an integer, and a group below the reached one is refused

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** submit-input-checks
- **kind:** fix
- **appetite:** big  ·  *Three seeds promoted together (Clarifications C1), each `small` alone. Together
  they change what `hs submit`, `hs cluster` and `hs server` accept, across file, stream, argument
  and `--from-json` input, on SQLite and PostgreSQL, with and without a database. Two of them need
  a check of the whole input before anything is inserted, which the cluster and server file paths do
  not do today, and the third needs a definition of the reached group. That is a research pass and
  more than two phases, so `big`. The fallback split is in C1.*
- **target:** 2.9.0  ·  *all three seeds; each is a behaviour change, so none goes to a 2.8.x patch.*

## Problem

Three ways HyperShell accepts task input it should not, so that the work that runs is not the work
the user asked for. All three are **pre-existing** on `develop` (`dfdbaed5`, 2.9.0a8). Shaping
reproduced each in a throwaway site on Python 3.14 and SQLite, unless noted.

**1. A bare `#` truncates the command.** `Task.split_argline` (`src/hypershell/data/model.py:592-607`)
looks for a `# HYPERSHELL:` tag marker, and failing that cuts the line at the first `#` anywhere in
it, inside quotes or not (`:603-605`). Nothing is reported; the shortened command is stored and run.
From a task file given to `hs submit`:

| Line as written | Stored and run as |
| --- | --- |
| `echo 'issue #42'` | `echo 'issue` |
| `curl 'http://h/p#frag'` | `curl 'http://h/p` |
| `echo $#` | `echo $` |
| `echo a#b` | `echo a` |
| `awk '{print $1}' # trailing` | `awk '{print $1}'` |

The same cut applies to stream input, to `hs cluster` and `hs server` input, and to a command given
as arguments, because `hs submit` with arguments also parses the inline marker
(`SubmitApp.submit_one`, `src/hypershell/submit.py:1465`): `hs submit -- echo 'direct #7'` is stored
as `echo "direct`. A line whose first character is `#` is skipped as a comment and is not a task;
task files rely on that today.

**2. A task group that is not an integer is accepted.** The `task.group` column is `INTEGER NOT
NULL` and `-g/--group` takes an integer (`-g x` exits 2). The inline `# HYPERSHELL: group:…` tag
(also on a tag-only line, which sets the group for the lines after it), `-t/--tag group:…` and the
`group` key of a `--from-json` record take any value: `Task.new` uses the group from the tags as
given (`model.py:537`). What each database does with it:

| Value | SQLite (`hs submit`, reproduced) | PostgreSQL (shaping probe, see below) |
| --- | --- | --- |
| `group:x`, `-t group:x`, JSON `"x"`, a tag-only `group:y` | stored as text | refused at insert |
| `group:` (empty) | stored as text `''` | refused at insert |
| `group:0x10` | stored as text | stored as `16` |
| `group:1.5`, JSON `1.5` | stored as REAL `1.5` | rounded, stored as `2` |
| `group:1e3` | stored as `1000` | stored as `1000` |
| `group:true`, JSON `true` | stored as `1` | stored as `1` |
| `group:null` | stored as `0` | not probed |
| JSON `"7"` | stored as `7` | stored as `7` |
| `group:nan` | NULL breaks `NOT NULL`; the committer dies and the run hangs | refused at insert |

The PostgreSQL column was probed with SQLAlchemy's `Integer` type on a temporary table against a
local PostgreSQL 18, not through `hs`. What `hs` does after PostgreSQL refuses an insert was not
reproduced. On SQLite, `hsx` runs a text group after every integer group and exits 0, an order the
docs never promise. `group:nan` hangs `hs submit -f` and `hsx` alike until they are killed. The
scheduler already carries two guards against text groups (`Scheduler.mark_group_started`,
`src/hypershell/server.py:354`, from `group-advance-strands-lower-group` review cycles 2 and 3), and
any new comparison of groups would need the same care.

**3. A submission below the group the schedule has reached is accepted.** With a group-5 task
scheduled and another unfinished, `hs submit -g 0 -- echo late` and a task file carrying
`group:0` are both accepted, exit 0. Such tasks cannot run in the documented ascending order. Since
`group-advance-strands-lower-group` (#74) they run late with a warning instead of stranding the run.
For a submission, where the user can act, the maintainer wants a refusal at submit time instead
(seed Clarification Q1, 2026-11-03).

## Outcome / vision

The command a user writes is the command that runs. `#` is part of a command. A line that begins
with `#` is a comment, and `# HYPERSHELL:` sets tags as it does today. A task's group is an integer
on every database, or the input is refused with an error that names the value and where it came
from. Input read in full is refused before anything is inserted. A stream refuses only the line at
fault. A submission that would put a task behind the group an active schedule has reached is refused
before anything is inserted, with an error naming both groups. Everything else submits as today.

## Acceptance criteria (the contract)

Terms.

- **Read-in-full input:** a named, seekable file given to `hs submit`, `hs cluster` or `hs server`;
  any `--from-json` source (including `-`, which is already loaded whole); a command given to `hs
  submit` as arguments; and stream input to `hs submit` with a database, which R6 reads in full
  (C6).
- **Streamed input:** standard input, a pipe, a FIFO or a process substitution given to `hs cluster`
  or `hs server`, or to `hs submit` without a database (`--queue`). It is scheduled or dispatched as
  it arrives.
- **Reached group** (from the shaped seed): the group a database's schedule has reached, counted only
  while unfinished work remains, meaning incomplete tasks or pending retries. Once every task has
  finished, there is none (seed Q3). `hs-plan` defines it precisely, including how exhausted
  failures count and how it is computed.

**`#` in a command (seed `submit-comment-truncation`).**

- **R1** — WHEN a task line contains a `#` that is neither its first non-blank character nor the
  start of a `# HYPERSHELL:` tag marker, the task SHALL be stored and run with that `#` and all the
  text after it as written. This SHALL hold for file, stream and argument input to `hs submit`,
  `hs cluster` and `hs server`, with a database and without one, and under `--template`. Every
  example in Problem 1 runs as written.
- **R2** — A line whose first non-blank character is `#` SHALL remain a comment and not a task. The
  `# HYPERSHELL:` marker (or `#HYPERSHELL:`), after a command or alone on a line, SHALL set tags and
  resources exactly as today. A file's upfront task count SHALL be unchanged for such lines.

**An integer group (seed `group-tag-integer-validation`).**

- **R3** — Every way a task gets a group SHALL accept only an integer: the inline `group:` tag on a
  task line or a tag-only line, `-t/--tag group:…`, and the `group` key of a `--from-json` record.
  A tag value counts as an integer when `-g/--group` would accept the same text (`7`, `-3`, `+5`,
  `07`). A JSON value counts when it is a JSON integer. Everything else SHALL be refused, including
  `x`, an empty value, `1.5`, `1e3`, `0x10`, `true`, `null`, `nan`, `inf`, and a JSON string, float,
  boolean or null (C5). The rule SHALL be the same on SQLite and PostgreSQL, with a database and
  without one (C8).
- **R4** — IF read-in-full input carries a group that R3 refuses, THEN the command SHALL insert no
  task from it and dispatch none, and SHALL exit with status 5 and an error naming the value and its
  line number, or its record number for `--from-json`. IF `-t/--tag` carries such a group, THEN the
  command SHALL exit with status 2 before reading any input, as `-g x` does today (C7).
- **R5** — IF streamed input carries a group that R3 refuses on a line, THEN that line SHALL NOT be
  inserted or run, and an ERROR SHALL name the value and the line number. Every other line SHALL be
  handled exactly as it would be without that line, so a refused tag-only line sets nothing and the
  lines after it keep the group they had. The run's exit status SHALL be unaffected by the refusal
  (C7). No group value SHALL hang a run: `group:nan` is refused under R4 or R5.

**A group below the reached group (seed `submit-refuses-lower-group`, shaped).**

- **R6** — WHEN `hs submit` with a database is given a task whose group is lower than the database's
  reached group, it SHALL refuse the whole submission, all or nothing. It SHALL insert no task from
  it and SHALL exit with status 2 and an error naming the lowest offending group and the reached
  group (C7). This SHALL hold for a file, for arguments and for stream input, which `hs submit` SHALL
  read in full before inserting anything (seed Q4). It SHALL accept, as today, tasks in the reached
  group or higher, any group on a database with no reached group, and a submission that names no
  group (no `-g`, no group tag; C9). The rule SHALL hold whether or not a server is running.
- **R7** — WHEN `hs cluster` or `hs server` is given a named file on a database that has a reached
  group (`--restart`, `--update`, `--repeat`, or a database kept from an earlier run), and the file
  would insert a task in a lower group, the command SHALL refuse under R6's rule before inserting or
  dispatching any task from the file, and SHALL exit with status 2. Tasks the re-submission gate
  skips as already present are not being submitted, and SHALL NOT trigger the refusal. Streamed
  input to these commands is not refused under this rule: a late lower group runs with a warning, as
  `group-advance-strands-lower-group` ships it.

**Both, and all three.**

- **R8** — Tests SHALL cover, on SQLite and, where a database is involved, on PostgreSQL:
  - R1 and R2: `#` in single quotes, in double quotes, in a URL fragment, in `$#`, inside a word,
    and before a trailing comment; a full-line comment; `#` next to a `# HYPERSHELL:` marker; and a
    tag-only line, through `hs submit` with a database and with `--queue`, `hs cluster`, and
    `hs submit` arguments.
  - R3–R5: each refused value from R3 and each accepted form; each way a group arrives; a file
    refused with nothing inserted; a stream refusing one line and keeping the rest; a refused
    tag-only line on a stream; and `group:nan` ending with no hang.
  - R6 and R7: a submission at the reached group and one above it; a database with no reached
    group, both empty and with all work finished; a submission that names no group onto a database
    whose most recently scheduled group is below its reached group (C9); stream input to `hs submit`
    all or nothing; and a same-file `--restart` onto a database that a hang left stranded, which is
    not refused.
- **R9** — Documentation, per `AGENTS.md` § Docs, each passage marked `.. versionchanged:: 2.9.0`:
  - the `hs submit` description (`docs/_include/submit_desc.rst`) and the `hs cluster` and `hs server`
    pages: a line beginning with `#` is a comment, a `#` elsewhere is part of the command, and the
    `# HYPERSHELL:` marker is recognised anywhere on a line, so a command that must contain that text
    breaks it with shell quoting (`echo '#'' HYPERSHELL:'`);
  - the task-group page (`docs/_include/task_groups_alt.rst`): a group is an integer and anything
    else is refused, with how a file and a stream differ; `hs submit` refuses a task below the
    reached group, and when the reached group counts;
  - the group-gating text in `AGENTS.md` and `.agents/factory/invariants.md`, for R3 and R6.

  The squash commit body (the release note) SHALL name each change listed under
  *User-visible changes* below as a behaviour change.

### User-visible changes (the release note)

1. **`#` is part of a command.** A `#` anywhere after a line's first non-blank character, including
   in quotes, a URL fragment or `$#`, is now submitted and run as written. Earlier releases cut the
   command at the first `#`, silently: `echo 'issue #42'` ran as `echo 'issue`. A line that begins
   with `#` is still a comment, and `# HYPERSHELL:` still sets tags. A trailing `# note` is now part
   of the stored command and is left for the shell, which ignores it. No escape is needed for a
   literal `#`. To audit task files that earlier releases may have cut, look for lines with a `#`
   after their first character that are not `# HYPERSHELL:` markers, and compare their stored
   commands (`hs list args`).
2. **A task's identity changes for such lines.** A line holding a `#` after its first character
   gets a new identity, so `--restart` or `--update` of a file first submitted by an earlier release
   submits those lines again, and they run again (C4).
3. **A task group must be an integer.** A non-integer group from an inline tag, `-t/--tag` or a
   `--from-json` record is refused (R3 lists the forms). A task file or JSON source is refused whole
   with nothing inserted, exit status 5, naming the value and line. `-t group:x` exits 2. On a
   stream into `hs cluster` or `hs server`, only the line at fault is refused, with an ERROR naming
   it. Earlier releases stored such values as given on SQLite and ran them in an undocumented order;
   `group:nan` hung the run.
4. **`hs submit` refuses a task below the reached group.** All or nothing, exit status 2, naming both
   groups; `hs cluster FILE` and `hs server FILE` likewise on an advanced database. `hs submit` now
   reads standard input in full before inserting anything. Streams into `hs cluster` and `hs server`
   are unchanged: a late lower group runs, with a warning.

## Non-goals (no-gos)

- **A quote-aware `# HYPERSHELL:` marker.** The marker is still found anywhere on a line, in quotes
  too: `echo '# HYPERSHELL: group:3'` fails to submit today (exit 5) and will after. R9 documents the
  quoting escape.
- **A compatibility shim for task identity** across the upgrade (C4).
- **`hs update group=…`.** It stores a non-integer group too (`hs update group=x -g 1` writes the text
  `x` on SQLite, reproduced). This cycle is submit time only; a follow-up seed is proposed.
- **Rows already in a database** with a non-integer group are not migrated or refused, and the
  scheduler's guards in `Scheduler.mark_group_started` stay.
- **A `--force` override** for R6/R7 (seed non-goal). The workaround is to submit at or above the
  reached group, to wait for the run to finish, or to use a fresh database.
- **Refusing a lower group on streamed input** to `hs cluster` or `hs server` (seed Q1, part A).
- **Queue-mode `hs submit` and runs without a database for R6/R7**, where no reached group exists.
- **A general per-line error policy or all-or-nothing ingest** for other malformed input, undecodable
  bytes or over-long lines (`issues/ingest-robustness.md`, 2.10.0). R4–R7 apply only to the group
  checks. Whichever cycle lands second keeps both contracts.
- **A failure-aware exit status** for a run (declined, `ROADMAP.md` § Settled questions); R5 leaves a
  stream run's exit status alone.
- **A 2.8.x backport** of any of the three.

## Clarifications

Proposed in shaping on 2026-11-21 by the shaping agent. **C1–C10 are pending the maintainer's
sign-off via the foreman** before `/hs-plan`. The shaped seed's own clarifications, resolved by the
maintainer, follow.

- **C1 — One cycle, `big`, with a fallback split.** All three seeds are 2.9.0 changes to what task
  input is accepted, on the same ingest path, and seeds 2 and 3 both need the whole input checked
  before anything is inserted. Together they are nine R-IDs. **Proposed split, if the plan gate
  finds the reached group or stream buffering heavier than expected:** seed 3 (R6, R7 and their parts
  of R8 and R9) goes to a follow-up cycle under its own slug, `submit-refuses-lower-group`, and
  R1–R5 ship here.
- **C2 — Kind `fix`.** The foreman opened the cycle as a fix; seed 3 called itself a `feature`
  (a deliberate behaviour change). The commit category is `[fix]`. The release note still names all
  four changes as behaviour changes.
- **C3 — A line beginning with `#` stays a comment (R2).** Deleting the cut only past a line's first
  non-blank character keeps every task file that uses comment lines working, and matches the shell,
  where `#` begins a comment only at the start of a word. A trailing ` # note` reaches the shell,
  which ignores it. A quote-aware fallback was the seed's other option. It is not needed, because the
  shell already reads its own comments.
- **C4 — Task identity drifts.** The identity of a task is computed over its command after the cut,
  so a line with a `#` after its first character gets a new identity. A `--restart` or `--update`
  of a file first submitted before 2.9.0 re-submits those lines, and they run again. Most of them
  ran a cut command before. **Proposed:** accept it and say so in the release note (change 2).
- **C5 — Which values are integers (R3).** Proposed: the text `-g/--group` accepts, and a JSON
  integer. Refused: integral floats (`1e3`, JSON `3.0`), booleans, and a JSON string, even `"7"`,
  though both databases store `"7"` as 7 today. Strict typing keeps one rule for every source.
- **C6 — Stream input to `hs submit` follows R4, not R5.** On 2026-11-04 the maintainer asked for
  "bulk (read ahead whole file) and over pipe (limiting to error on just the occurance)". Seed 3's Q4
  (2026-11-03) already has `hs submit` read a stream in full and refuse all or nothing, so a stream
  into `hs submit` with a database is read-in-full input and is refused whole. The per-line rule
  covers input that is scheduled as it arrives, where reading ahead is not possible. **Alternative:**
  per-line for every stream, including `hs submit`'s, so `hs submit` buffers for R6 but skips a bad
  group line.
- **C7 — Exit statuses.** Proposed: 5 for a refused group value in the input, as a malformed inline
  tag exits today; 2 for `-t group:x`, as `-g x` does; 2 for R6/R7, as the re-submission gate's
  refusal of a file does today. Exit status unchanged for a stream line refused under R5: the line is
  logged, and a refused line is not an application failure of the run. **Alternative:** a stream run
  that refused any line exits 5 once it ends, which needs the submitter's error carried to the exit
  status, as `issues/database-locked-during-ingest-hangs.md` notes is missing today.
- **C8 — R3 holds without a database too** (`--no-db`, `hs submit --queue`). A group is the same data
  in every mode, and the check is a parse, not a database read. A file given to `hs cluster --no-db`
  is then checked in full before any task is dispatched.
- **C9 — A submission that names no group is never refused (R6).** Without `-g`, `hs submit` takes
  `Task.current_group()`, the most recently scheduled group (`submit.py:1448-1451`). After a late
  lower group has started, that group is below the reached group (`AGENTS.md`, autoscaler notes), so
  a plain `hs submit cmd` would be refused by its own default. The plan chooses how to prevent that,
  for example by defaulting to the reached group.
- **C10 — Seed 3 re-confirmed against `dfdbaed5`.** Scope holds. Since it was shaped,
  `task-input-fidelity` (#110) made a stream run schedule until its input ends; that does not touch
  R6/R7. Drift: the cycle's kind (C2), its slug and branch (`fix/submit-input-checks`, not
  `feature/submit-refuses-lower-group`), C6, C9, and seed 3 R1–R4 renumbered here as R6, R7 and parts
  of R8 and R9. Two notes for the plan: `hs server FILE` does not pass through the re-submission gate
  as `hs cluster FILE` does (`cluster/__init__.py:531-563`), and seed 2's statement that PostgreSQL
  refuses every non-integer value is not true (Problem 2).

From the shaped seed `submit-refuses-lower-group` (resolved, carried as written):

- **Q1 — The rule. Resolved 2026-11-03** (maintainer, verbatim): "In situations where we are handed
  a file to run start-to-finish, we should process the whole file up-front so as to run groups in
  order. But there are situations where this is not possible; (a) because we're piping into the
  cluster process, and (b) because we submitted to the database and it's possible to submit again
  with a group id that precedes what has already run. I want to do the best thing when it's
  possible, otherwise it's a best-effort with a warning (the pipe), or an error at submit time (if
  we're already past group 5 and you submit group 0 it should refuse)." R6/R7 are the "error at
  submit time" half.
- **2026-11-03 — Scope** (foreman): the refusal applies to `hs submit` whether or not a server is
  running, and the same rule covers file input onto an advanced database (R7).
- **Q3 — The reached group once work is finished. Resolved 2026-11-03** (maintainer): it counts only
  while unfinished work remains. Once every task has finished, a new submission starts a new pass and
  any group is accepted, which keeps `hs cluster FILE --repeat` of a grouped file working. The highest
  group ever scheduled was declined: it would refuse every lower group for the life of the database.
- **Q4 — Stream input to `hs submit`. Resolved 2026-11-03** (maintainer): buffer the whole stream and
  refuse all or nothing, against the shaper's recommendation to stop at the first refused task. As a
  consequence, `hs submit` from a pipe inserts nothing until its input ends.
- **Q5 — Split from part A. Resolved 2026-11-03** (maintainer): this half is 2.9 only.
- **R7 and restart recovery** (shaping, 2026-11-03): no conflict. Rows a hung run left stranded are
  already in the database, and a same-file `--restart` inserts nothing new, because the gate skips
  every task already present.
- **Racing a running server** (shaping, 2026-11-03): R6 reads the reached group at submit time, and a
  server can advance between that read and the insert. Part A's late-group warning covers that
  window, so the tasks run late and are never stranded.

From the seed `group-tag-integer-validation`:

- **Release. Resolved 2026-11-21** (maintainer, `ROADMAP.md` commit `50148373`): 2.9.0, not a 2.8.x
  patch.

## Related materials

- Seeds: [`issues/submit-comment-truncation.md`](../../issues/submit-comment-truncation.md),
  [`issues/group-tag-integer-validation.md`](../../issues/group-tag-integer-validation.md),
  [`issues/submit-refuses-lower-group.md`](../../issues/submit-refuses-lower-group.md)
- Prior cycles: [`spec/group-advance-strands-lower-group/`](../group-advance-strands-lower-group/)
  (part A, #74; `REVIEW.md` cycle 2 N1 and cycle 3 O1),
  [`spec/task-input-fidelity/`](../task-input-fidelity/) (#110)
- Overlap: [`issues/ingest-robustness.md`](../../issues/ingest-robustness.md) (same ingest path,
  2.10.0); [`issues/database-locked-during-ingest-hangs.md`](../../issues/database-locked-during-ingest-hangs.md)
  (the same committer death as `group:nan`)
- Source: `src/hypershell/data/model.py` (`Task.split_argline`, `Task.new`, `Task.current_group`),
  `src/hypershell/submit.py` (`Loader`, `line_is_task`, `source_fingerprint_and_count`,
  `apply_source_gate`, `SubmitApp`), `src/hypershell/cluster/__init__.py` (`ClusterApp.prepare_source`),
  `src/hypershell/server.py` (`is_file_input`, `ServerThread.start_threads`,
  `Scheduler.mark_group_started`)
- Docs: `docs/_include/submit_desc.rst`, `docs/_include/task_groups_alt.rst`
