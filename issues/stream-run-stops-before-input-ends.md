---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A run fed by a pipe can stop before the pipe ends

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs cluster` and `hs server` given stream input (standard input, a pipe, a FIFO or a process
substitution) can reach FINAL and exit while the stream is still feeding tasks. The stop branch of
`Scheduler.load_bundle` (`src/hypershell/server.py`) stops once nothing is left to schedule and
every committed task has completed. It consults the co-running submitter only while
`startup_phase` is set, and that flag clears when the first bundle is scheduled. After that, a
pause in the input longer than the time it takes to finish the committed tasks ends the run.
Tasks that arrive later are never scheduled, and the command still exits 0.

Reproduction on `develop`, found during the `group-advance-strands-lower-group` plan:

```shell
(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1
```

It runs `echo a`, exits 0, and leaves `echo b` unscheduled. The same holds for
`hs cluster` and `hs server` with stream input. It is not a hang. File input is not affected once
it is submitted in full before scheduling starts (`group-advance-strands-lower-group`).

## Why it was deferred

Outside `group-advance-strands-lower-group` part A, by the foreman's plan-gate ruling
(2026-11-03). That cycle's "never poll forever" requirement covers a run that hangs with work
left, not a run that stops early. The likely fix needs the scheduler's reference to its
submitter, which only `develop` has, so it cannot go to a 2.8.x patch without new plumbing.
**Pre-existing.**

## Outcome / vision

A run fed by a stream keeps scheduling until the stream ends and every task it delivered has
reached a terminal state, then stops.

## Sketch of the acceptance criteria

- **R1** — WHILE the submitter of a `hs cluster` or `hs server` run is still reading its input, the
  server SHALL NOT reach FINAL, in non-`forever` mode, whatever the timing of the input.
- **R2** — WHEN the input ends and every task has reached a terminal state, a non-`forever` run
  SHALL stop as it does today.
- **R3** — A deterministic test SHALL replay a pause in the input after the first task completes.

## Notes

- Fix idea (`develop` only): add `self.submission_complete()` as a conjunct of the stop branch,
  in place of `(not self.startup_phase or self.submission_complete())`, so a live submitter always
  holds the run open. 2.8.3's `Scheduler` has no submitter reference.
- Related: `spec/group-advance-strands-lower-group/PLAN.md` §5 risk 1.
- Found by: `group-advance-strands-lower-group` planning (2026-11-03).
