# 00 — Research digest

Two read-only briefs were fanned out because the change touches `data/model.py` and `server.py`
(high blast radius), even at `appetite: small`:

- [`01-test-harness.md`](01-test-harness.md) — how `unit` tests can drive `Task` and `Scheduler`
  in-process and inject the Receiver's writeback deterministically.
- [`02-final-safety.md`](02-final-safety.md) — liveness of the new hold, scoping, ordering and
  visibility, and the other paths that end a run.

## Decisions

1. **One query, one count.** `select_failed`'s filters move into a `Task` query helper. A count
   helper built on it, with a required `group`, is the only predicate the group-advance hold and the
   stop check use.
2. **The hold mirrors `Task.next` exactly, including `eager`.** No hold when `attempts <= 1` and
   not `eager`, because non-eager `Task.next` selects no failures then. This removes the one stall
   path (a user-set `attempt < 1` row at `attempts == 1`) and keeps R3 exact in both modes.
3. **Scope to `current_group`.** A global check can stall on failures in a group the scheduler has
   already left. R3 requires the group scope.
4. **Order is load-bearing.** Incomplete rows first, then retryable failures, then (in
   `increment_group`) exhausted failures. The reverse order reproduces the defect, and the
   exhausted check after the retryable one is what R2 requires.
5. **`count_remaining` keeps its meaning.** The autoscaler and ETA depend on it; the stop branch
   gains a separate conjunct.
6. **Tests rebind `Session` to a per-test file SQLite database.** In-process queries otherwise hit
   the developer's real per-user database. `Scheduler(queue=None, poll=0)` runs `start` and
   `load_bundle` with no queue and `sleep(0)`. A one-shot `Task.next` wrapper commits the failure
   through a separate session, as the Receiver thread would.
7. **Red first is provable mechanically.** A scratch prototype failed both replays on this
   worktree's source (identical to `c1d4470`) in both `eager` modes. Committed with
   `xfail(strict=True, raises=AssertionError)`, they prove red by assertion; removing the markers
   with the fix proves green.
8. **Out of scope, recorded only:** `Scheduler.start`'s early FINAL on restart into a pending
   retry, and the late-duplicate-result overwrite of `retried`. Neither is in the R1/R7 window, and
   the fix does not worsen either.
