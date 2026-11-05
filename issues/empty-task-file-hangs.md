---
status: unshaped
kind: fix
appetite: small
lane: public
---

# An empty task file never finishes

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hsx` given an empty task file on an empty database never exits. It logs `Found 0 tasks`, then
the server logs `Empty database - waiting for initial tasks` and waits for tasks that cannot
arrive. With no client timeout the local client waits as well, so the run hangs until it is
killed.

The likely cause, from reading the code: the stop branch of `Scheduler.load_bundle` requires
`Task.count() > 0`, so a run that submits no task on an empty database never reaches FINAL. That
guard keeps an empty-database start waiting for its first task, which is right while a submitter
may still commit rows, but not once the submitter has finished with nothing.

Evidence: `hsx empty.txt -N2 --no-tls` on a fresh database timed out at 25 s both at `b31c66e`
(`develop` before `group-advance-strands-lower-group`) and at that cycle's build head `31d8771`,
in its review cycle 1. On a database that already holds finished tasks, the same run exits 0 in
about 2 s on both (review cycle 2).

## Why it was deferred

Out of scope for `group-advance-strands-lower-group`, which changes neither the stop branch nor
an empty run's outcome. Whether an empty file should exit 0 or report an error is a choice to
shape. **Pre-existing.**

## Outcome / vision

A run handed an empty task file ends promptly with a clear outcome, instead of hanging.

## Sketch of the acceptance criteria

- **R1** — WHEN `hs cluster` or `hs server` is given a task file with no tasks, and is not in
  `forever` mode, the run SHALL end promptly once the file is read, with a defined exit status and
  a message saying no tasks were found.
- **R2** — A `forever` server, and a server whose database already holds tasks, SHALL behave as
  today.

## Notes

- Check stream input with no lines (`hsx < /dev/null`) and `--from-json` with an empty list in
  the same pass.
- Related: `spec/group-advance-strands-lower-group/` (REVIEW cycle 1, out-of-scope observation).
- Found by: `group-advance-strands-lower-group` review (2026-11-03).
