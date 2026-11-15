---
status: unshaped
kind: fix
appetite: small
lane: public
---

# With `-b N>1`, a line read before a pause waits for the next line to be committed

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The database committer buffers submitted tasks and commits them once the buffer reaches the bundle
size or `-w`/`--bundlewait` has passed since the last commit. It checks the wait only when a task
arrives (`DatabaseCommitter.get_task`, `src/hypershell/submit.py`): on an empty local queue it loops
back to waiting without looking at the clock. So with `-b N>1`, a line read just before a pause in a
stream sits uncommitted, and unscheduled, until the next line arrives or the input ends, however long
the pause and whatever `--bundlewait` says. The same holds for `hs submit` reading a stream, and for
`hs cluster`/`hs server` given one.

Found by reading the code during `task-input-fidelity`'s plan (research/02, side finding); not
reproduced as a drive. That cycle's tests use a bundle size of 1, where every line is committed as it
is read.

## Why it was deferred

It is not a stop defect: the line is committed and run once the input moves on or ends, so the
`task-input-fidelity` fix (a run waits for its input to end) does not depend on it. **Pre-existing**
on `develop` and in 2.8.

## Outcome / vision

A line read from a stream is committed within `--bundlewait` of being read, pause or no pause.

## Sketch of the acceptance criteria

- **R1** — WHILE the committer holds buffered tasks and no new task arrives, it SHALL commit them
  once `bundlewait` has passed since the last commit.
- **R2** — A test SHALL replay a pause after fewer than `bundlesize` lines and see them committed
  within the wait, with no wall-clock race.

## Notes

- Related: `issues/stream-pause-client-timeout.md`, `issues/ingest-robustness.md` (same ingest
  path).
- Found by: `task-input-fidelity` plan, `spec/task-input-fidelity/research/02-stream-stop.md` Q5.
