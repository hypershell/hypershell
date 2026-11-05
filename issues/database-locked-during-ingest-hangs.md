---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A "database is locked" error while a task file is read hangs the run

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hsx` or `hs cluster` given a task file on SQLite hangs when another connection holds the database
locked while the file is submitted. The submitter's `DatabaseCommitter` raises
`OperationalError: database is locked` on its `INSERT INTO task` and dies with a CRITICAL log.
The run neither stops nor recovers once the lock is released: it waits until it is killed.

The likely cause, from the review's reading of the code: with the committer gone, the `Loader`
blocks on its full local queue (sized to one bundle), so the `SubmitThread` never finishes and the
server never gets past `ServerThread.wait_submitter`. `ServerThread.wait_scheduling` cannot see
the failure, because the server thread itself is still alive. Nothing propagates the committer's
error to the server or to the command's exit status.

Evidence: a 20,000-line file through `hsx task.txt -N2 --no-tls`, with a separate connection
holding `BEGIN EXCLUSIVE` from 1.5 s into the read for 60 s, hung both at `b31c66e` (`develop`
before `group-advance-strands-lower-group`) and at that cycle's build head `5c7f381`, in its review
cycle 2. Both logged the committer's `database is locked` CRITICAL and were still
running when killed at 120 s, about a minute after the lock was released.

## Why it was deferred

Out of scope for `group-advance-strands-lower-group`, which changes neither the submitter nor how
its errors reach the server. **Pre-existing.**

## Outcome / vision

A failure that kills the task submitter ends the run promptly with a non-zero exit status and the
submitter's error, instead of hanging.

## Sketch of the acceptance criteria

- **R1** — WHEN the submitter's database committer fails while a task file or stream is being
  submitted, `hs cluster` and `hs server` SHALL stop promptly with a runtime-error exit status and
  log the committer's error.
- **R2** — A submission that completes normally SHALL behave as today.

## Notes

- Check stream input and `hs submit` against the same lock in the same pass.
- Whether SQLite's busy timeout should simply be longer during ingest is a separate choice to
  shape; a longer wait narrows the window but does not remove the hang.
- Related: `spec/group-advance-strands-lower-group/` (REVIEW cycle 2, out-of-scope observation).
- Found by: `group-advance-strands-lower-group` review (2026-11-04).
