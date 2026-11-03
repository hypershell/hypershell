# 02 — File input in group order (R3) under the 2.8.4 constraint (R9)

> Read-only research brief for `hs-plan`, written by the planner. Question: how should
> `hs cluster FILE` / `hs server FILE` run an unsorted file's groups in ascending order, given
> that the same `src/` diff must apply to 2.8.3 after #68?

## What 2.8.3 lacks

`origin/master` (2.8.3) is behind `develop` in exactly the places an R3 design would naturally
use:

- `Scheduler` has no `submitter` reference and no `submission_complete()`. They came with the
  source-gated re-submission feature (`b27ee58`, #50), along with the `(not startup_phase or
  submission_complete())` stop conjunct.
- `GatedSource`, `source_fingerprint_and_count` and `apply_source_gate` do not exist. On 2.8.3,
  `ClusterApp.source` passes the opened file object straight through (or `[]` under
  `--restart`), and `ServerApp.input_stream` opens `FILE` the same way on both branches.
- `--from-json` does not exist.

R9 forbids depending on any of these. The constructors that exist on both branches, with the
same shape, are `ServerThread.__init__` (it builds the `SubmitThread` and the `SchedulerThread`)
and `ServerThread.start_threads`.

## Options

| Option | Order guarantee | R9 | Cost / risk |
|--------|-----------------|----|-------------|
| **A. Ingest first in `ServerThread`**: for file input, start the submitter, wait for it to finish, then start the scheduler | Complete. The scheduler starts on a database that holds the whole file, and `current_group()` picks the lowest group (nothing is scheduled yet). The race fix (01) keeps every later choice ascending. | Uses only `ServerThread`, which is the same on both branches. | The first dispatch waits for the full ingest (the accepted cost). Thread start order changes, with the scheduler last. |
| B. Ingest first in `Scheduler.start` via `submission_complete()` | Complete | **Breaks R9**: needs the post-2.8.3 `submitter` plumbing | Smallest diff on `develop` |
| C. Per-group census from the source gate's up-front read; hold a group while a lower census group is short of its count | Complete, but only if the census mirrors the Loader's group resolution exactly (inline `group:` tags, `-t group:`, JSON records) and the gate's skip set under `--restart`/`--update` | **Breaks R9**: needs `source_fingerprint_and_count` and the gate | Duplicates tag parsing, adds a second gate path and a new hold to keep in step with #68 |
| D. Ingest in the App layer (`hs submit` semantics) before launching the cluster | Complete | Conflicts: `ClusterApp.source` differs heavily between branches | Touches `cluster/__init__.py`, server and app wiring, and every launcher's source handling |

**Choice: A.** It is the GOAL's default (ingest before the first dispatch), and it is the only
option that ports. Option C was the "something cheaper" the GOAL allowed. It is rejected because
it needs code that 2.8.3 lacks, and because "provably the same order" would rest on a second
copy of the Loader's group resolution.

## Mechanics of A

- **Classification.** File input means a seekable file object that is not `sys.stdin`, or an
  in-memory list or tuple of tasks. On `develop`, `ClusterApp` wraps a file in a `GatedSource`.
  The check looks through it by duck typing (`getattr(source, 'iterable', source)`) and does not
  import the class, which keeps the code identical on 2.8.3. The seekable test matches the source
  gate's own split (`stream is sys.stdin or not stream.seekable()`), so a FIFO, a process
  substitution or a piped `/dev/stdin` counts as stream input. The list case covers
  `--from-json`, whose records are read whole before ingest anyway. That is a foreman choice, and
  R3 does not require it.
- **Scope.** Ingest first applies only when there is a scheduler, a submitter, and file input.
  `--no-db`/`in_memory` (no scheduler), `--forever` with no source, and a bare `--restart` (no
  submitter) behave as today, as R7 requires. `--autoscaling` with a file does ingest first: it is
  file input, and the autoscaler sees the pressure grow while the file is read.
- **Start order.** Start the submitter, then confirm, heartmonitor and receiver, then (for file
  input) join the submitter, then start the scheduler. `AGENTS.md` holds only the *shutdown*
  order load-bearing. The start order of these independent threads is not, and starting the
  scheduler last means queue consumers are already up while a long file is read.
- **Failure and stop paths.** Joining the submitter re-raises its exception in
  `ServerThread.run_with_exceptions`, the same place `wait_submitter` raises it today. The
  scheduler is then never started, and `stop()` reaches it with `wait=False` from every in-tree
  caller (`serve_from`, the cluster threads), which only sets halt flags. A `stop(wait=True)` on a
  never-started scheduler would raise `RuntimeError` from `join`. No in-tree caller does that, but
  the plan guards it, because `ServerThread` is public API. A stop during the ingest stops the
  submitter, the join returns, and the scheduler starts with its halt flag already set.
  `StateMachine.next()` honours that flag on the first step.
- **Signals.** USR1/USR2 during the ingest are seen by the scheduler's first `load_bundle` after
  it starts. Today `wait_submitter` already holds shutdown until the ingest ends, so the
  observable result is the same.
- **`develop`'s startup logic** is unaffected. By the time `Scheduler.start` runs,
  `submission_complete()` is true, so the "waiting for incoming tasks" branch and the
  `startup_phase` conjunct see a finished ingest.

## Cost and side effects

- The first task of a large file waits for the whole file to be committed. With the default
  submit bundle size, that is one commit per row. The GOAL names this cost and accepts it.
- **Client idle timeout.** `hs cluster -T/--timeout` shuts a client down after that many seconds
  with no task. Clients are launched alongside the server, so a timeout shorter than the ingest
  can let local, SSH or MPI clients exit before the first task is scheduled, and the run would
  then wait for clients that are gone. The default is "never", and the autoscaler relaunches
  clients. This interaction is new with ingest first and is not handled in scope. It goes to the
  foreman as a risk, with a docs note as the minimum.
- Stream input is unchanged. It keeps its streaming start and gets 01's provisional
  empty-database start plus the race fix.
