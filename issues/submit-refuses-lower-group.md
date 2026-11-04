---
status: shaped
kind: feature
appetite: small
lane: public
---

# Refuse a submission below the group the schedule has reached

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`. It is
> `shaped`: the shaping conversation already happened with the maintainer (see Clarifications), so
> promotion re-confirms scope against current `develop` and adopts it largely as written. Do not
> copy it verbatim.

**Kind.** `feature`, not `fix`: this is a deliberate behaviour change, not a defect. Commands that
succeed today, a `hs submit` into a lower group or a file run onto an advanced database, will fail
with a clear error. It branches as `feature/submit-refuses-lower-group`, and it ships on `develop`
only (2.9), not in a 2.8 patch.

**Depends on:** `group-advance-strands-lower-group` (part A). A's safety net, that a late lower
group still runs with a warning, covers a submission that races a running server past this
refusal check.

## Problem

Task groups promise ascending order: every task in the active group reaches a terminal state
before any task in the next group is scheduled (`docs/_include/task_groups_alt.rst`). A database
can receive tasks after its schedule has moved on: `hs submit` into a running server's database, a
submission between runs, or `hs cluster`/`hs server FILE` with `--restart`, `--update` or `--repeat`
onto a database that has already advanced. If those tasks are in a group below the one the
schedule has reached, the promise cannot be kept, because the lower group would run after the
higher group that was meant to depend on it.

Part A (`spec/group-advance-strands-lower-group/`) makes such tasks run late with a WARNING rather
than strand and hang the run. That is the right best effort for a pipe, where the input cannot be
known in advance. For a submission the maintainer wants the error to come at submit time, where
the user can act.

## Outcome / vision

A submission that would put a task behind a group the active schedule has already reached is
refused, before anything is inserted, with an error naming both groups. A submission at or above
the reached group, or into a database with no active schedule, behaves exactly as today. Piped
input into `hs cluster`/`hs server` is unchanged: best effort with a warning, as part A ships it.

## Acceptance criteria (the contract)

Terms.

- **File input** is a named, seekable file given to `hs cluster FILE` or `hs server FILE`.
  **Stream input** is `<stdin>`, a pipe, a FIFO or a process substitution.
- A database's **reached group** is the group its schedule has reached, and it counts only while
  unfinished work remains: incomplete tasks or pending retries (Q3). `hs-plan` defines it
  precisely, for example the highest group holding any scheduled row while unfinished work remains.
  It also defines how exhausted failures count, and how the group is computed.

Requirements.

- **R1** — WHEN `hs submit` is given a task whose group is lower than the database's reached group,
  it SHALL refuse the whole submission, all or nothing. It SHALL insert no task from it, and SHALL
  exit nonzero with an error naming the lowest offending group and the reached group. This holds
  for a file, for arguments, and for stream input, which `hs submit` SHALL read in full before
  inserting anything (Q4). It SHALL accept tasks in the reached group or higher as today, and any
  group on a database with no reached group. The rule SHALL hold whether or not a server is running.
- **R2** — WHEN `hs cluster` or `hs server` is given file input on a database that has a reached
  group (`--restart`, `--update`, `--repeat`, or a database kept from an earlier run), and the file
  would insert a task in a lower group, the command SHALL refuse under R1's rule before inserting
  or dispatching any task from the file, and SHALL exit nonzero. Tasks the source gate skips as
  already present are not being submitted, and SHALL NOT trigger the refusal. Stream input to
  these commands is not refused; part A's best effort applies.
- **R3** — Tests SHALL cover R1's and R2's refusals and acceptances, including:
  - a submission at the reached group, and a submission above it;
  - a database with no reached group, both empty and with all work finished (Q3);
  - stream input to `hs submit`, all or nothing;
  - a same-file `--restart` onto a database that a part-A-era hang left stranded, which is not
    refused.
- **R4** — The task-group documentation (`docs/_include/task_groups_alt.rst`) and the group-gating
  text in `AGENTS.md` and `.agents/factory/invariants.md` SHALL state that `hs submit` refuses a
  task below the reached group, and when the reached group counts. The release notes SHALL name the
  new refusal as a behaviour change.

## Non-goals (no-gos)

- **A `--force` override.** It would add a CLI option, which by repo convention also changes the
  docs includes and shell completions. The workaround is to submit at or above the reached group,
  to wait for the active run to finish (Q3), or to use a fresh database. Re-raise it at promotion
  if a real use case appears.
- Refusing stream input to `hs cluster`/`hs server`. The maintainer ruled that best effort with a
  warning (part A).
- Backporting to 2.8. This is a 2.9 behaviour change.
- General all-or-nothing ingest and per-line error policy (`issues/ingest-robustness.md`). R1's
  all-or-nothing applies only to this refusal.
- Queue-mode `hs submit` and servers without a database, where no reached group exists.

## Clarifications

- **Q1 — The rule. Resolved 2026-11-03** (maintainer, verbatim): "In situations where we are handed
  a file to run start-to-finish, we should process the whole file up-front so as to run groups in
  order. But there are situations where this is not possible; (a) because we're piping into the
  cluster process, and (b) because we submitted to the database and it's possible to submit again
  with a group id that precedes what has already run. I want to do the best thing when it's
  possible, otherwise it's a best-effort with a warning (the pipe), or an error at submit time (if
  we're already past group 5 and you submit group 0 it should refuse)." This seed is the "error at
  submit time" half. Part A carries the file and pipe halves.
- **2026-11-03 — Scope** (foreman): the refusal applies to `hs submit` whether or not a server is
  running, and the same rule covers file input onto an advanced database (R2).
- **Q3 — The reached group once work is finished. Resolved 2026-11-03** (maintainer): option (ii).
  The reached group counts only while unfinished work remains: incomplete tasks or pending retries,
  with the plan defining exhausted failures. Once every task has finished, a new submission starts
  a new pass and any group is accepted. This keeps `hs cluster FILE --repeat` of a grouped file
  working after a completed run. Option (i), the highest group ever scheduled, was declined because
  it would refuse every lower-group submission for the life of the database.
- **Q4 — Stream input to `hs submit`. Resolved 2026-11-03** (maintainer): option (b). Buffer the
  whole stream and refuse all or nothing. This is the maintainer's choice, against the shaper's
  recommendation of (a), stopping at the first refused task with earlier bundles committed. As a
  consequence, `hs submit` from a pipe inserts nothing until its input ends.
- **Q5 — Split from part A. Resolved 2026-11-03** (maintainer): this half goes to `develop` only
  (2.9). Part A, order and the race, keeps the original slug and goes to 2.8.4.
- **R2 versus part A's restart recovery** (shaping, 2026-11-03): no conflict. Rows a hung run left
  stranded are already in the database, and a same-file `--restart` inserts nothing new, because
  the source gate skips every task already present. R2 refuses only what `--update`, `--repeat` or
  a different file would newly insert below the reached group.
- **Racing a running server** (shaping, 2026-11-03): R1 reads the reached group at submit time, and
  a server can advance between that read and the insert. Part A's late-group warning covers that
  window, so the tasks run late and are never stranded.

## Related materials

- Part A: `spec/group-advance-strands-lower-group/` (GOAL Q1–Q5 record the shaping).
- Source: `src/hypershell/submit.py` (`SubmitApp`, `SubmitThread`, `apply_source_gate`),
  `src/hypershell/cluster/__init__.py` and `src/hypershell/server.py` (file versus stream input),
  `src/hypershell/data/model.py` (`Task.current_group`).
- Overlap: [`issues/ingest-robustness.md`](ingest-robustness.md). R1 adds a validate-then-insert
  pass, with whole-stream buffering for `hs submit`, on the same ingest path that seed's per-line
  policy will change. Whichever lands second must keep both contracts.
- Found by: `group-advance-strands-lower-group` shaping (2026-11-03).
