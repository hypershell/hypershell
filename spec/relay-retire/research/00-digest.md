# Research digest — relay-retire

Four briefs, fanned out for the `big` appetite (rounded up from medium; GOAL Clarifications):

| Brief | Topic | Author |
|---|---|---|
| [`01-wire-compat.md`](01-wire-compat.md) | topology reply and heartbeat tolerance across 2.9.0a7, `8403388`/`fb89670` and this branch; encodings for the mark and the hand-back | research subagent |
| [`02-signal-wake.md`](02-signal-wake.md) | the nine untimed main-thread joins, `Thread.join` semantics, the shared helper, measured hangs, R15 test design | research subagent |
| [`03-evidence.md`](03-evidence.md) | how RV5 was reproduced, the drive harness, R7 timing, old/new pairings, founding case, cluster changes | research subagent |
| [`04-handback.md`](04-handback.md) | what a relay holds, what "started" means, upstream revert paths, where the write happens, R7 budget | planner |

## Decisions (one per question; contradictions resolved)

1. **The mark.** A top-level `"retired": true` on the topology reply, present only when some
   requested identity is retired; protocol stays 1. An `8403388` relay ignores it (01 §2); a reserved
   key in `zones`, a protocol bump, or a mark on a `version: null` reply is ruled out. Only develop
   builds from `8403388` read a topology at all; a 2.9.0a7 relay is never recorded and never retired
   (01 §1).
2. **Where the mark is computed.** In `TopologyPublisher.check`, from the same `_arrange` that warns
   and zeroes the entries, over every identity of every older instance (not only `dup.older`), and
   carried on `Topology` like `checks`, **outside the version decision**. 01 §4 flagged that the
   republish rule compares entries only; rather than adding the retired set to that comparison (which
   would bump the version an old relay sees for no plan change), it rides on every check. Supersession
   always changes entries anyway (new identities appear), so the mark is never late by more than one
   check.
3. **The hand-back.** A new dispatcher call, `handback`, behind `getattr` (01 §5 option 1), naming
   each task with its upstream `schedule_time`. Not a heartbeat field (no acknowledgement), never a
   new `ClientState` (kills older heart monitors, 01 §3), never `revert_orphaned` (breaks R5, 04 §3).
   The dispatcher stays database-free: it queues and wakes the scheduler, which writes the revert on
   its next pass, as `revert_unstaged` does (04 §5). The reply acknowledges what was queued; the
   relay deletes its copies on that acknowledgement, which is safe because every queued row is either
   reverted or no longer the relay's.
4. **The revert's match.** `id` + `schedule_time` as dispatched + `completion_time IS NULL` +
   `client_id` the identity or NULL (a lost confirmation), one conditional UPDATE per row with the
   §1 contention rule (04 §4). By id alone (`revert_staged`) could revert a re-dispatched row.
5. **"Started".** Delivered: cut from the relay-local pool by a site client's request. On
   retirement the relay stops its local scheduler, withdraws staged rows with `take_staged` (atomic
   under the dispatcher lock), reverts them locally, and hands back every incomplete row with no local
   `schedule_time` (04 §2). Hand back everything undelivered rather than keep feeding the connected
   clients: it makes R7's timed half independent of task length.
6. **The wake.** A function `join_waking(thread)` in `core/thread.py`, not a change to
   `Thread.join` (02 §3). Interval **0.5 s**: 02 §3 shows the "Drain requested" line can wait 2 s
   behind an outstanding request at the puller's gate, so R11's 2 s is held by a once-only drain log
   also called from the relay thread's 1 s check (≤ 1.5 s), not by the wake alone.
7. **R15 tests.** An injector wrapper written as a file named `hs` (02 §4: a `python -c` wrapper
   makes the relay re-execute and loses the thread), gated on the main thread being parked in its
   join; the server test connects a client first (a server with no client never exits on SIGUSR1,
   today's behaviour). Measured red 3 of 3 per command on macOS; Linux by CI.
8. **Evidence.** A new `spec/relay-retire/drive/retire_drive.sh` built from
   `spec/relay-topology/drive/topology_drive.sh` (that cycle's drive stays its record; its `lower`
   mode no longer holds once A retires, 03 §2.5). Relays log in the system style for R7's timestamps;
   a read-only sampler of the upstream task table and a `$TASK_ID` run log back the judge; a
   non-vacuity check makes a run with nothing handed back INCONCLUSIVE. Pairings by a `git archive`
   of `fb89670` on `PYTHONPATH` for one role (03 §3; `git worktree add` is refused by the sandbox).
   The red run is its own mode (`retire-red`, all roles on `fb89670`) that passes only when the
   stranding is reproduced, bounded at 20 s rather than RV5's 150 s window (03 §4).
9. **Cluster.** The `topology_cluster.sh` contract (environment-configured, inside the foreman's
   allocation, no submission); a third relay role A2 at site A; heartrate 2 s, so R7's bound is 6 s
   judged with the harness's skew allowance (03 §2.6).
