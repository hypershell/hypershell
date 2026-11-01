---
status: accepted-behaviour
kind: docs
appetite: small
lane: public
---

# Stance: `CANCEL_STATUS = -1` overlapping death-by-SIGHUP is intended

> **Stance record, not a defect.** This file exists so the question is not re-opened every time
> someone reads the sentinel table or runs a comparison review. It is not queued for a cycle; the
> only actionable residue is documentation, noted at the bottom.

## The observation

`CANCEL_STATUS` is `-1` (`src/hypershell/data/model.py:49`) and the client records `Popen.wait()`
directly (`src/hypershell/client.py:769`), which returns `-N` for death by signal N. A task killed
by SIGHUP therefore also lands at `-1`. Because every failure query excludes cancels —
`increment_group` (`model.py:531`), `select_failed` (`model.py:567`) both filter
`exit_status != CANCEL_STATUS` — a SIGHUP-killed task is not retried, does not count as a group
blocker, and reads as a cancel in every listing.

The GNU Parallel comparison recorded this as a high-severity defect ("silent task loss"), reasoning
by analogy with Parallel, which keeps exit value and death signal in separate joblog fields
(`parallel:6429-6437`).

## The stance

**Accepted as intended behaviour** (maintainer, 2026-08-07). The overlap is subtle but useful rather
than harmful: `kill -1` against a task process is a natural way for an operator on the node to say
*cancel this task*, and the semantics that follow are exactly right — the task lands terminal, it is
not retried, it does not block its group, and `revert_interrupted`/`revert_orphaned` cannot
resurrect it (cancel is made terminal by stamping `completion_time` precisely so reverts can't,
`model.py:700-714`). A SIGHUP arriving from a session teardown is, in practice, the same instruction.

The alternative — moving the sentinel below `-1000` or adding a `cancelled` column — costs a schema
migration and a sweep of every `CANCEL_STATUS` predicate across `data/model.py`, `server.py`, and
the task CLI, to *remove* a behaviour that is working as wanted. Not a trade worth making.

`AGENTS.md` already acknowledges the collision in its `exit_status` table; what it does not yet say
is that the consequence is deliberate.

## Residue worth fixing (small, docs-only)

1. **Say it out loud.** `AGENTS.md`'s `exit_status` table notes the collision as a fact; it should
   state that the cancel semantics for a SIGHUP-killed task are *intended*, so the next reader does
   not file it again. The user-facing docs should mention that `kill -1` on a task reads as a cancel.
2. **Exit-code conflation is a separate question.** `hs task wait -r` maps the raw `-1` onto process
   exit code 255 (`task.py:332-366`), which conflates a cancel with a generic failure at the shell
   boundary. That is about the *process contract*, not the sentinel, and is in scope for
   `issues/run-exit-status-failure-aware.md`.

## Notes

- Do not re-litigate without new evidence — specifically, a case where a SIGHUP death that the
  operator did **not** intend as a cancel caused real loss. That would be new information.
- Related: `issues/run-exit-status-failure-aware.md`, `issues/docs-and-runtime-surfacing.md`.
- Origin: GNU Parallel comparison review, 2026-08 (H3); rejected as a defect 2026-08-07.
