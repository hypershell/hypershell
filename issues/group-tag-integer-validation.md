---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A non-integer task group is accepted at submit time

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Task groups are integers: the `task.group` column is `INTEGER NOT NULL`, `hs submit -g` takes an
`int`, and the docs describe groups as running in ascending order. The `# HYPERSHELL: group:…`
inline tag parser, the `-t group:…` tag option and a `--from-json` record's `group` key accept any
value, though. `Task.new` takes the group from the tags as given, so nothing between the input
and the database checks it.

What each database does with such a value today:

- **SQLite** treats a column type as a preference. It stores a value that does not convert to an
  integer as it comes: text for `group:x`, `''` or `0x10`, and REAL for `group:1.5`.
- **PostgreSQL** rejects a non-integer value at insert, as it always has.

What that leads to on SQLite:

- **2.8.3 runs them,** with SQLite ordering every text group after every integer group. A naive
  user who writes `group:x` gets a run that completes, in an order the docs never promise.
- **The group-advance fix had to guard against them.** `group-advance-strands-lower-group` added
  a Python-side comparison of group values for its late-group warning. A text group made that
  comparison raise and crash the scheduler (review cycle 2, N1), and a text group started first
  turned the warning off for the rest of the run (review cycle 3, O1). Both are fixed by guards in
  `Scheduler.mark_group_started`. Each new comparison of group values needs the same care while
  the database can hold a group that is not an integer.
- **`group:nan` hangs the run.** SQLite stores NaN as NULL, so the insert breaks the column's
  `NOT NULL` constraint, the submitter's committer dies, and the run waits until it is killed. It
  does the same before and after the group-advance fix (that cycle's review 3, out-of-scope
  observation).

## Why it was deferred

Out of scope for `group-advance-strands-lower-group`, which keeps what 2.8.3 accepts and only
guards the scheduler against it. The maintainer asked for it as its own seed during that cycle's
cycle-3 triage (2026-11-04). **Pre-existing.**

## Outcome / vision

A task's group is an integer, or the submission is refused with an error naming the bad value and
where it came from. The scheduler and every later comparison of group values can then rely on
integers on every database.

## Scope, as the maintainer set it

Enforce an integer group at submit time:

- **File input** (`hs submit -f`, `hs cluster FILE`, `hs server FILE`): read the whole file ahead
  and reject it before inserting anything.
- **Pipe or stream input:** error on just the offending occurrence, the line that carries the bad
  group, and keep the rest of the stream's behaviour.
- **Every way a group arrives:** the inline `# HYPERSHELL: group:…` tag (including a tag-only line
  that sets the group for the lines after it), `-t`/`--tag group:…`, and the `group` key of a
  `--from-json` record.

## Sketch of the acceptance criteria

- **R1** — IF a task file or a `--from-json` source carries a group that is not an integer, THEN
  the submission SHALL fail before any task from it is inserted, with an error naming the value
  and its line or record.
- **R2** — IF stream input carries a group that is not an integer, THEN that occurrence SHALL be
  reported as an error and SHALL NOT be inserted.
- **R3** — The same rule SHALL hold on SQLite and PostgreSQL, and a submission whose groups are
  all integers SHALL behave as today.

## Open questions for shaping

- **2.8.x patch or 2.9 only?** SQLite accepts these values today and 2.8.3 runs them, so refusing
  them is a behaviour change: a command that succeeds today will fail. It could go into a 2.8.x
  patch as a fix, since the group is documented as an integer and `group:nan` already hangs, or
  into 2.9 only, as `submit-refuses-lower-group` does with its own refusal.
- **Which values count as an integer?** SQLite also stores `group:1.5` as REAL, and inline `true`,
  `1e3` and JSON `"7"` as numbers. Shaping decides whether an integral float or a numeric string
  is converted or refused.
- **On a stream, does an error fail the run's exit status** once the stream ends, or only log?

## Notes

- **The maintainer's request, verbatim** (2026-11-04): "I did not think we allowed non-integer
  group IDs. In SQLite, I understand the types are more of a suggestion; are you saying, that if a
  naive user applies a non-integer group ID, it breaks our logic in a way that is unexpected? If
  so, we should make a note, and commit an issues/ seed calling this out and have it scoped to
  guard against this by enforcing integer group ID at submit time both bulk (read ahead whole
  file) and over pipe (limiting to error on just the occurance)."
- Related: [`database-locked-during-ingest-hangs.md`](database-locked-during-ingest-hangs.md). A
  NULL group breaking `NOT NULL` kills the committer the same way, and leaves the same hang.
- Related: [`ingest-robustness.md`](ingest-robustness.md) (all-or-nothing submission on the same
  ingest path) and [`submit-refuses-lower-group.md`](submit-refuses-lower-group.md) (another
  submit-time refusal of a group).
- Related: `spec/group-advance-strands-lower-group/` (`REVIEW.md`: cycle 2 N1, cycle 3 O1, and the
  cycle-3 triage).
- Found by: `group-advance-strands-lower-group` review (2026-11-04).
