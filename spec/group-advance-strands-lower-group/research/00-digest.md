# 00 — Research digest

The change touches `data/model.py` and `server.py`, both on the high-blast-radius list. The
fan-out was therefore run at `appetite: small` as three briefs, which the planner wrote one after
another (the same arrangement #68 used):

- [`01-next-group-race.md`](01-next-group-race.md): the atomic next-group query, late lower
  groups, the empty-database start, and the R5 warning.
- [`02-file-input-order.md`](02-file-input-order.md): how file input runs in group order under
  R9's 2.8.3 constraint.
- [`03-tests-and-port.md`](03-tests-and-port.md): deterministic replays, hang-safe verification,
  the known flake, and the 2.8.4 port trial.

## Decisions

1. **One query picks the next group.** `Task.lowest_incomplete_group()` returns
   `min(group) WHERE completion_time IS NULL`. `increment_group` calls it after #68's three gates,
   which keep their order. It may return a group lower than the previous one, and it may return
   the previous group itself (rows committed during the checks), which holds the scheduler there.
2. **The empty-database start is provisional.** When `start()` finds no tasks, each LOAD
   resolves the group as the lowest incomplete group until the first task is scheduled, and it
   skips `Task.next` while none exists. Without this, ascending input beginning below 0 can run
   group 0 first (R4, R5). It is limited to the empty-database start so that `--restart` on a
   halted database still halts (R7).
3. **R5 is run-local bookkeeping in `Scheduler`.** The scheduler tracks the highest started
   group and the last scheduled group. It logs one WARNING, naming both groups, on the first
   bundle of a group that is lower than one already started this run.
4. **R3 is met by ingest first in `ServerThread`.** For file input (a seekable non-stdin file, or
   an in-memory list), the scheduler thread starts only after the submitter finishes. This is the
   only R3 design whose code exists unchanged on 2.8.3. The census option needs the post-2.8.3
   source gate.
5. **The tests are the #68 pattern.** Unit replays use strict `xfail(raises=AssertionError)` red
   on `b31c66e`, with bounded loops and an injection armed only inside `increment_group`. The
   integration test (c) lands with the R3 code and is never run red.
6. **Every CLI or integration verify runs under a process-group kill timer**, and suite runs
   allow one recorded `--lf` rerun for the TLS localhost flake.
7. **R9 is proved by a scripted trial in a detached `origin/master` worktree.** It applies #68
   with one scripted conflict resolution, then this cycle's `src/` and `tests/` diff with no
   conflicts, runs the group tests, and removes the worktree.
8. **For the foreman, out of scope:**
   - the pre-existing early FINAL while a stream submitter is still committing;
   - client `-T/--timeout` shorter than the file ingest.
