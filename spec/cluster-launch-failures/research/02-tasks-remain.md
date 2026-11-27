# Research 02 — reading "tasks remain" without racing the last results

Topic: the predicate `hs cluster` (RemoteCluster / SSHCluster, in the ServerThread's process) evaluates
once every launched process has exited and at least one failed (R1), so that a run whose clients did
the work ends as today (R4). Code is `develop` @ `4400e04` (this worktree). Lines are `src/hypershell/`.

## Findings

**Nobody else can ever serve the cluster's server.** Every cluster mints a random key
(`cluster/remote.py:261`, `:855`, `cluster/ssh.py:253`) and hands it only to its own launches. Once
those processes are gone no client can ever connect again, so "the server would otherwise wait
forever with work left" is exactly "work is pending now, or arrives later" (R1 is a state).

**DB mode: the scheduler's own stop rule** (`server.py:262-266`): FINAL when `not forever_mode and
Task.count() > 0 and Task.count_remaining() == 0 and Task.count_retryable(attempts,
group=current_group, eager) == 0` (plus the startup/submitter guard). Remove the forever guard and
that rule is the predicate:

- `Task.count_remaining()` (`data/model.py:858`) counts `completion_time IS NULL` in **every group and
  every zone**. The scheduler counts tasks in other zones and other sources in a shared DB too
  (confirmed: no zone or group filter at `:262`). A non-forever cluster whose zone finished keeps
  waiting today while another zone has work (`report_held`, `server.py:365`). Mirroring it, R1 fires
  there, which is right: no client can arrive to run that work.
- It already includes the hard cases. **Staged rows** are stamped by `Task.next` (schedule_time set,
  completion NULL), so they count even with no client. With no requests the default pool is still
  refilled (`core/dispatch.py:191-203`). **A crashed client's in-flight rows**
  (schedule_time set, completion NULL) count too: R1 fires, as GOAL's clarification wants
  (`--restart` resumes them; `ServerThread.stop` skips `revert_staged`, so staged rows read as
  interrupted on the next start, as after Ctrl-C today).
- `count_retryable` must use **the scheduler's group and retry rule**: `scheduler.machine.current_group`
  (the attribute FINAL reads; `active_group` hides it while `first_group_pending`), plus
  `machine.attempts` and `machine.eager`. Reading these keeps `attempts == max_retries + 1` in one place
  (invariant §3). With `group=None` it would count stale retryables in old groups that are never
  retried, a false R1 under `--forever`. **Order matters:** count remaining *before* retryable
  (`server.py:264-265`, `model.py:680-682`). Otherwise a failure written between the two reads gives 0+0.
- **A failed (non-viable) group.** Under `--forever` the scheduler warns and waits (`:272-276`).
  Exhausted failures are excluded from `count_retryable` (`attempt < attempts`, `model.py:762-771`),
  so a failed last group with nothing incomplete reads "no work": the cluster keeps waiting (R4). If
  later groups are incomplete it reads "work" and R1 fires (nobody can clear the gate). Without
  `--forever` the scheduler halts itself (`:278`). So the cluster should **defer whenever the scheduler
  thread is no longer alive**: the server is winding down on its own, and it should end as it does
  today. This also covers SIGUSR1/2 FINAL (`:245`).

**The race (both modes).** The client returns results, then sends FINISHED, then exits
(`client.py:1328-1329`). A result `put` is a synchronous manager RPC into the server's in-process
`queue.Queue` (`core/queue.py:1032-1040`), so once every launched process has exited, all its results
are *in* `queue.completed`, but maybe **not yet written**. The Receiver calls `task_done()` right
after `get()`, **before** the write (`server.py:596-598` vs `update_tasks` `:619`). So
`completed.join()`, `unfinished_tasks` and `empty()` are **not** barriers. Contended writes can also
sit in `receiver.machine.held` (DEFERRED, retried each 2 s cycle, `:600-603`, `:634-639`).
Non-forever DB runs are race-free by construction: clients get `shutdown` only after
`dispatcher.finish()`, which follows scheduler FINAL, which needs `count_remaining() == 0`. The window
is real for `--forever`/`--restart` clients leaving on `-T`, for launcher ranks, and for crashes.

**`--no-db`.** `Scheduler` is `None` (`server.py:1125-1129`) and nothing is counted in a database; `--forever`
and `--restart` are refused with `--no-db` (`cluster/__init__.py:376-381`). The ServerThread ends
only via submitter join → `signal_clients`/`dispatcher.finish()` → HeartMonitor FINAL, which needs
`not startup_phase` (some heartbeat ever arrived, `:773`) **and** no `beats` (`:905`) → receiver and
confirm sentinels. So: no client ever connected → never ends (the reported bug), **even with an
empty source**. A client that died without FINISHED → ends only after eviction (default 600 s). A
graceful finish → ends ≤ ~2-4 s after the last FINISHED (2 s `get_next` timeout, then drains).
`LiveSubmitThread.task_count` (`submit.py:914`, Loader count) exists; nothing counts results received
in memory (the Receiver has no counter).

## Probes (temp site, SQLite, real `hs client` subprocess, in-process ServerThread)

- DB, non-forever (400 tasks, client `-b 1600 -w 30`): `count_remaining` hit 0 **2.18 s before**
  the client exited, and the scheduler and server had already ended at the exit. A healthy CLI
  `hs cluster t.in --launcher env -H 127.0.0.1` logs every result, then `Done (scheduler)`, then
  `Signaling clients`, then the client's `Done`, then the server's `Done (heartbeat|receiver|confirm)`.
  It exits 0.
- DB `--forever`, client `-T 2`: results landed 1.08 s before exit. Then `Task.apply_results` was
  patched to sleep 2.5 s (a slow write). **At client exit, `completed.qsize()=0` and
  `unfinished_tasks=0` but `count_remaining()=400`.** Putting a no-op bundle `serialize_tasks([])` and
  waiting until it was taken returned after 1.445 s, and then `remaining=0` and `held=0`. So the
  marker is a true barrier; the queue counters are not.
- `--no-db`, healthy: the server ended **before** the client process exited.
- `--no-db`, empty source, no client: server still alive 8 s later, `task_count=0`. A bare
  "server alive" proxy would raise a false R1 here.
- The first CLI attempt with the default advertised host (bind `0.0.0.0`) hung until killed at 40 s,
  as the seed describes.

## Recommendation

One cluster-side function, shared by RemoteCluster and SSHCluster (e.g. in `cluster/launch.py` or a
small new `cluster/` module), called once all launched processes have exited with ≥ 1 non-zero, then
**re-polled every few seconds while the server lives** (R1 is a state):

1. **Barrier** (both modes): if `server.receiver.is_alive()`, put `serialize_tasks([])` on
   `server.queue.completed` (`put(timeout=…)`), and wait (bounded) until it is taken and
   `server.receiver.machine.held` is empty. The Receiver is single-threaded and FIFO, so every
   earlier bundle has been written. Skip it if the receiver has ended (the probe hung waiting on a
   dead receiver). This is §6-clean: it is the same encoding as a client's bundle, and
   `apply_results([])` is a no-op commit.
2. **DB** (`server.scheduler is not None`): `work = Task.count_remaining() > 0 or
   Task.count_retryable(m.attempts, group=m.current_group, eager=m.eager) > 0`, with `m =
   server.scheduler.machine`. Fire R1 only if `work` is true at two reads about 2 s apart **and**
   the scheduler thread is still alive at the second. That lets a scheduler woken by the clients'
   FINISHED (`dispatcher.forget` signals need) reach its own FINAL first.
3. **`--no-db`**: `work = server.is_alive() after a ~5 s settle and (submitter.is_alive() or
   submitter.task_count > 0)`.

Total is about 5 s, inside R1's 10 s.

**Placement (Q4):** keep it in cluster code, composing existing `Task` classmethods with no new SQL.
The precedents are `ZoneLoad.work = remaining + retryable` (`cluster/demand.py:89-92`) and the
autoscaler reading `scheduler.machine` (`cluster/remote.py:898-899`). Invariant §1 asks for
*transitions* in `Task` classmethods; this is a read. If the reviewer wants it on `Task` anyway, add
`Task.count_pending(attempts, group, eager)` (remaining first, then retryable) after
`count_retryable_by_zone` (`model.py:~892`). That sits outside #103's model.py hunks (`:796-818`,
`:913+`, `:1209+`). It is not a `Scheduler` method: its FINAL rule is load-bearing and #103 shares
the file.

**No edit to server.py, dispatch.py or model.py is required.** One residual `--no-db` gap remains.
A client that returned every result but died before its FINISHED heartbeat (a millisecond window,
`client.py:1328-1329`) keeps today's server alive until eviction, so the proxy fires R1 where today
exits 0 after 600 s. If the plan wants R4 exact there, the fix is a received-results counter on the
**Receiver** (`server.py:569-576` init, `+= len(data)` in `unpack_bundle` `:608-617`) and
`work = submitter alive or task_count > receiver count`. The Receiver is outside #103's server.py
hunks (HeartMonitor `:708-990`, ServerThread `:1214-1270`), but still touches the file.

**Rejected:** `completed.join()`, `unfinished_tasks` or `empty()` as a barrier (task_done comes before
the write, as the probe shows). A pure time-based settle in DB mode (it fails on a slow or contended
write, and DEFERRED holds results for 2 s cycles). `count_retryable(group=None)` (stale retries cause a
false R1 under `--forever`). Scoping to the cluster's zone (the scheduler waits on every zone, and
nobody else can connect). "Server alive" alone in memory (false R1 on an empty source). Reading
HeartMonitor `beats` (#103 changes HeartMonitor).
