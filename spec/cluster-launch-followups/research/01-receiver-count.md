# Research 01 — a Receiver result count for the `--no-db` predicate (R5, R6)

Read-only research at `ffdd53cb` (branch `fix/cluster-launch-followups`). Paths are under
`src/hypershell/` unless stated.

## 1. A task's path in `--no-db`

- **Submitter.** `ServerThread.__init__` builds a `LiveSubmitThread` when `in_memory`, else a
  `SubmitThread` (`server.py:1195-1202`); `None` when `not source`. `LiveSubmitThread.run` reads with
  a `LoaderThread`, then offers the end marker, then joins a `QueueCommitterThread` that packs bundles
  and puts them on the server's `scheduled` (inbound) queue over its own `QueueClient`
  (`submit.py:928-940`, `:795-807`, `:809-816`).
- **`task_count`** is `self.loader.machine.count` (`submit.py:989-992`), an `int`. It counts **tasks**,
  not lines: `Loader.put_task` increments it only after a task lands on the *local* queue
  (`submit.py:410-417`, `put` then `count += 1`). Empty lines, tag-only lines and de-duplicated tasks
  are not counted (`:354-377`, `:397-408`). It is set to 0 once (`:324`) and never reset or batched.
- **Dispatch.** In memory, a default-zone request drains the inbound bundles into its pool and cuts
  under one lock, so no task goes to two clients (`core/dispatch.py:8-12`, `:342-366`).
- **Return.** The client's collector puts each results bundle on `completed` with a plain
  `QueueClient`, retrying only on `QueueFull` (`client.py:422-435`, flush at `:437-446`). The server
  side `completed` is a local `queue.Queue` (`core/queue.py:1219`).
- **Receiver.** `unload_bundle` → `unpack_bundle` → `update_tasks` (`server.py:636-695`). In memory
  every result is `APPLIED` (`:668-669`); the tail logs it and, for a non-zero exit, prints its args
  to `redirect_failures` (`:690-694`). That is the only output the server writes: **task output goes
  to standard output on the client side** (`client.py:729`, `:831`), inherited from the cluster in
  launcher mode. "Written" in R5 therefore means *handled by the Receiver*, not printed.
- **Duplicates in memory: none on the `hs cluster` paths.** Eviction reverts nothing in memory
  (`server.py:986-990`); hand-backs are refused in memory (`core/dispatch.py:483-490`); clients do not
  re-send. The one at-least-once sender is a relay's `UpstreamReturner`, which re-sends a bundle whose
  put raised `LinkDown` (`relay/upstream.py:875-880`) — but a relay cannot join a cluster run.
- **Foreign results: none on the `hs cluster` paths.** Launcher and `--ssh` clusters mint a fresh key
  per run (`cluster/remote.py:273`, `cluster/ssh.py:267`) and hand it only to launched processes; the
  cluster's own `LiveSubmitThread` is the only submitter. A standalone `hs server --no-db` can take
  `hs submit --queue` tasks and relays, but `watch_launched` is never called for it.

## 2. Where the counter lives

**`Receiver.written: int`**, set to `0` in `Receiver.__init__` (`server.py:612-619`) and incremented
by one at the end of the APPLIED tail of `update_tasks`, after the failure redirect (insert before
`server.py:695`, inside the loop). Count in **both modes**, APPLIED only: in DB mode APPLIED means the
row was written; DEFERRED/STALE/DUPLICATE `continue` before it. Skipping a mode branch keeps the diff
smallest, and the DB path never reads the count.

```python
    held: List[Task]
    written: int                      # + annotation
...
        self.held = []
        self.written = 0  # Results applied; one writer (this thread), read by the cluster's watch without a lock.
...
                if self.redirect_failures:
                    print(task.args, file=self.redirect_failures)
            self.written += 1
        return ReceiverState.UNLOAD
```

About 3 lines in `server.py`. One writer thread and an `int` attribute: a reader sees either the old
or new value. The barrier in `results_written` (`cluster/watch.py:78-103`) already makes the read
exact: the queue is empty only once the receiver has *taken* the empty bundle put behind the others,
and it takes a bundle only after `update_tasks` for the one before it has returned. The barrier's
empty bundle (`serialize_tasks([])` → `[]`, not the `None` sentinel, `data/model.py:304-307`) adds 0.
**No stop or sentinel order changes** (invariants §6): no new state, queue op, thread or join. **No
wire change**: nothing is sent; the bundle format is untouched.

## 3. How the watch reads it

`server.receiver.machine.written`, as `results_written` reads `server.receiver.machine.held`
(`watch.py:98`). `server.submitter` is `None` only when `not source` (`server.py:1197`): on the CLI,
`--no-db` always has a source (a file, `-` for standard input, or `--from-json` records,
`cluster/__init__.py:382-387`); `--forever` and `--restart` are refused with `--no-db`
(`cluster/__init__.py:409-412`). A programmatic empty list gives `None`, which today's
`submitter is None → False` already covers (the server ends by itself).

**The window.** Between the loader's `put` and its `count += 1` (`submit.py:413-414`) a task can be
committed, run and returned, so `written` may briefly exceed `task_count` by one — but only while the
loader, and so the submitter, is alive. Once `LiveSubmitThread` has ended, its loader has ended and
its committer has finalized (`submit.py:933-939`, `:809-816`), so every counted task is on the
server's queue and the count is final. The one exception is a halted committer, which logs
`Stopped with N tasks not committed` (`:814-815`); it is reached only from `stop()`, i.e. after the
watch has decided.

## 4. Is `MEMORY_SETTLE` still needed? — Drop it

Its only job was to give a finished server time to end on its own before "alive" was read as
"work". With an exact count:

- *Normal finish* (clients sent FINISHED; the launcher then exits non-zero, as in
  `tests/test_cluster_exit.py::test_cluster_whose_clients_finish_ends_as_before`): `written ==
  task_count`, submitter dead → no work; the watch loop keeps polling and the server ends by itself.
- *Results in flight*: covered by the `results_written` barrier, not the settle.
- *R5* (last result returned, exit non-zero before FINISHED): count equal → no error; the server
  evicts after 600 s and exits as before #107.
- *R6* (some results missing, or input still read): `written < task_count`, or the submitter alive
  → raise at once. Dropping the 3 s settle takes R6 from ~1 s poll + barrier + 3 s to ~1 s + barrier,
  well inside 10 s (plan-gate ruling 4's margin returns).

Keep the `server.is_alive()` check (a server that ended has no work) and drop the `join`. Remove
`MEMORY_SETTLE` from `__all__` and the `memory_settle` parameter (`watch.py:37`, `:52-53`, `:129`);
nothing else references them (grep: only `watch.py`). Replace the known-gap paragraph (`:140-142`).

```python
    submitter = server.submitter
    if not server.is_alive() or submitter is None:
        return False
    if submitter.is_alive():
        return True  # Input still being read: no client is left to run what follows
    return server.receiver.machine.written < submitter.task_count
```

Use `<`, not `!=`: on cluster paths `written` never exceeds the final count, and if a duplicate ever
did arrive the error falls on the side of the pre-#107 ending, not a false R1.

## 5. Other consumers of `Receiver`

Constructed only in `ReceiverThread.__init__` (`server.py:713`) and in tests that build one directly
(`tests/test_server_fsm.py:132-153, 325`, `test_writeback.py:120, 445`,
`test_writeback_statement.py:116`, `test_writeback_postgres.py:209`, `test_contended_write.py:155`,
`test_contended_recovery.py:210`); none compares the machine's attributes wholesale, so a new one with
a default is inert. The relay's site server (`relay/__init__.py:863`) only joins `server.receiver`;
its count grows unread. `SubmitThread.task_count` (`submit.py:713`) is the DB-mode twin and is
untouched.

## Risks

- `server.py` is high-blast-radius: the human review gate applies (GOAL ruling). The edit adds no
  state, queue op or lock.
- A stand-in server for the unit test (GOAL evidence plan) needs `receiver.is_alive()`,
  `receiver.machine.held`, `receiver.machine.written`, `queue.completed`, `submitter.is_alive()` and
  `submitter.task_count`.
- A launcher that exits non-zero while its clients keep running elsewhere now reads as work
  remaining as soon as the barrier passes, not 3 s later. Same verdict as today and as DB mode; only
  sooner.
- Counting in DB mode is unused; if the plan prefers, guard it with `if self.in_memory:` at one more
  line.
