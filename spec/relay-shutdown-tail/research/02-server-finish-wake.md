# 02 — The result that finishes a run (R3, R4) and the GOAL's cut

Plan-time research, 2026-11-21, worktree head `6f4e809f` (`src/` identical to `f3fb4dd2`). Probes ran a
scratch copy of the tree (`git archive`), never this worktree's `src/`.

## How the scheduler stops today

- `Scheduler.load_bundle` (`src/hypershell/server.py:244-297`): with nothing loaded it reads
  `submission_complete()` first, then `Task.count() > 0 and Task.count_remaining() == 0 and
  Task.count_retryable(attempts, group=current_group, eager) == 0` (short-circuited). With the input
  ended that is FINAL; otherwise `back_off`.
- `back_off` (`:299-309`) doubles `poll` (from `SERVER_POLL_MIN` 0.5 s up to `server.poll`) unless the
  previous wait was cut, and waits in `wait_need` (`:311-322`) on the dispatcher's condition, which a
  cut, new demand, a hand-back, `halt()` or `Dispatcher.wake()` (`core/dispatch.py:254-261`) ends: each
  bumps `generation` under the lock, and a bump after the pass read its generation skips the wait.
- `Scheduler.wake` (`:332-334`, added by #110) is that `Dispatcher.wake()`. `ServerThread` calls it once
  the submitter ends (`:1223`). Tests: `tests/test_stream_input.py:373-453` (`test_wake_ends_a_back_off`,
  `test_wake_before_the_wait_is_not_lost`, the `LocalCluster` integration test with a 600 s poll).
- The Receiver (`:598-701`) writes each bundle with `Task.apply_results` (one commit per bundle) and
  classifies every result `APPLIED`, `STALE`, `DUPLICATE` or `DEFERRED`. It holds no reference to the
  scheduler; `ServerThread.__init__` builds the scheduler before the receiver (`:1204-1211`), and
  `in_memory` has no scheduler.
- `invariants.md` §3 says the out-of-work stop "takes effect on its next pass, within `server.poll`,
  not during a back-off". R3 changes that sentence for the out-of-work stop only.

## Options for R3, against the cut

The GOAL's cut applies if R3 needs **a database read for every landed result bundle on every server**,
or **a back-off change while work remains**.

| Option | Reads | Back-off while work remains | Verdict |
|--------|-------|-----------------------------|---------|
| A. Receiver wakes the scheduler after every bundle | none in the receiver, but a full scheduler pass (refills, `Task.next` per zone, the counts) per bundle | changed: every landing cuts the wait, and a cut wait does not grow the next | **cut** on both counts |
| B. Cap the back-off once the input has ended | none | changed: a run's tail polls at the cap | **cut** (back-off change) |
| C. Receiver checks the counts after every bundle once the input has ended, wakes only on zero | `count_remaining` (no index on `completion_time`: a scan) per landed bundle for the whole run of a file input, whose submission is complete before scheduling | unchanged | **cut** (a read per landed bundle) |
| **D. Counted arm (chosen)** | the receiver reads the counts only once the results landed since the scheduler last counted could have finished the run: about once per run | unchanged | **not cut** |

## The chosen design (D)

- `Scheduler.landed` (int, written by the receiver only) counts results written back (`APPLIED`).
- On a pass that loaded nothing, a non-`--forever` scheduler whose input has ended **arms** before it
  counts: `finishing = (landed, 0)`, then, once `Task.count_remaining()` is read, `finishing = (mark,
  remaining)`. A pass that loads tasks disarms (`finishing = None`). The counts are the ones the pass
  reads today, each at most once, in the same order: no new scheduler query.
- `Scheduler.results_landed(n)`, called by the receiver after a write with `n > 0` results applied:
  `landed += n`; when armed and `landed - mark >= remaining`, it reads `count_remaining() == 0` and then
  `count_retryable(attempts, group=current_group, eager) == 0` (the scheduler's own predicate) and on
  both calls `wake()`. The woken pass reads the counts itself and returns FINAL.
- Why it is exact for a run finished by results: `remaining` is read after `mark`, so every result
  committed after the count is also counted in `landed - mark` (results committed between the mark and
  the count are counted twice, which only makes the check earlier, never wrong). The run cannot finish
  until all `remaining` tasks have a result, so the check runs no later than the finishing result.
- No lost wake: arming precedes the counts. A result committed before the count is seen by the count
  (the pass stops itself); one committed after sees the arm (`(mark, 0)` arms with no threshold until
  `remaining` is known), and its `wake()` bumps the generation, which ends or skips the wait.
- R4: the receiver wakes only when nothing is left incomplete or retryable, so while work remains no
  wait is cut and `poll`/`woken` evolve as today; `--forever` never arms. Retries, group advance,
  dispatch, eviction and hand-backs are untouched (a pass that loads disarms; a pass re-arms with a
  fresh count).
- Residual edge: tasks cancelled or deleted by `hs update` during the back-off lower `remaining`
  without a result, so the threshold may not be reached in that back-off; the run then stops at the
  end of the wait, as today, and the next pass re-arms with the right count. New rows from another
  writer raise `remaining`: an early check reads non-zero and does nothing.
- Cost: one or two count queries in the receiver thread per run (plus one per extra back-off pass that
  re-arms during the last results); the scheduler's own reads are unchanged.

## Probe evidence (scratch prototype of D, about 30 lines in `server.py`)

- `tests`-style probe, `LocalCluster(['sleep 1'], poll=600)` with the tail back-off forced to the full
  600 s: **base** fails its 15 s join (16.0 s, bounded); **prototype** passes, landed → scheduler FINAL
  0.001–0.005 s, run 2.1 s.
- CLI drive `end_drive.sh finish` (`sleep 9`, default poll, the result lands ~2 s into the 8 s back-off):
  **base** killed at its 13.5 s bound three times of three; **prototype** 9.74–9.87 s.
- Existing suites on the prototype (`test_stream_input`, `test_group_order`, `test_server_fsm`,
  `test_writeback`, `test_retry_gate`, `test_dispatch`, `test_restart`, `test_cluster_exit`): 128 passed
  with `hs` on `PATH` (five `test_group_order`/`test_writeback` cluster tests need it; `uv run` gives it).
- Relay tail with R1 + D prototyped (`tail_run.py`, 2004 tasks): tail 1.54 s (baseline 6.75, 7.45 s),
  direct tail 0.68 s (baseline 1.65–1.76 s); `evidence/plan-probe/`.
