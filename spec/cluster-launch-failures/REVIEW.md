# REVIEW — A cluster whose clients all fail says so and exits; one advertised address for every launch mode

> Adversarial QA by `hs-review`, run in an isolated context. The correctness pass was delegated to
> one fresh reviewer that saw only [`GOAL.md`](GOAL.md), the base copy of
> `.agents/factory/invariants.md`, `review-rubric.md`, `git diff origin/develop...HEAD -- .
> ':(exclude)spec/'`, the cycle's drive script (`git diff origin/develop...HEAD --
> 'spec/*/drive/*'`), AGENTS.md and the runnable repo. It did not see `PLAN.md`, `TECH.md`,
> `research/` or `META.md`. It was given the gate rulings that bind grading: R1 fires only on
> all-exited with at least one non-zero or signal exit; R3's counter as ruled; relay fleets never
> stop; the relay override deferred; `submit.py` and direct reads of receiver, scheduler and
> `Client` state in scope; the `--no-db` finish race an accepted gap
> (`issues/nodb-cluster-finish-race.md`); man pages rebuilt at release. Every finding cites an
> executed command.

- **Reviewed commit:** 81c4de6  ·  **Base:** develop (`origin/develop` at `6da633d`; the branch
  sits on it and fast-forwards cleanly)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 (mirrors `review.cycle` in `TECH.md`)
- **Contract drift:** none after the lock. `GOAL.md` changed only in the shaping commit
  (`5dfd1bc`) and the GOAL-gate ruling and lock (`76ece91`, R1 narrowed), both before the plan.

## Verification run

All in throwaway sites (`.agents/factory/bin/temp_site.sh`), bound to `127.0.0.1`. Drives ran one
at a time; every started process was stopped by PID and confirmed gone; the tree was left clean.

- Full suite, Python 3.14: `uv run pytest -q` → **2739 passed, 109 skipped**, exit 0 (20 min).
- Touched tests, Python 3.14: `tests/test_cluster_exit.py tests/test_autoscale_failed_launches.py
  tests/test_cluster_advertise.py tests/test_cluster_launch.py tests/test_zone_autoscale.py` →
  **306 passed, 1 skipped**.
- Python 3.11 (separate environment): the same five files plus `tests/test_submit.py
  tests/test_relay_launch.py tests/test_completions.py` → **414 passed, 1 skipped**.
- `spec/cluster-launch-failures/drive/launch_drive.sh` modes `fail`, `fail-db`, `zero`, `healthy`,
  `ssh-fail`, `ssh-partial`, `autoscale-fail`, `autoscale-off`, `advertise` → **all ok**
  (`fail` 8 s and `fail-db` 7 s to exit 5; `ssh-fail` 6 s; `autoscale-fail` 11 s; `zero` still
  running at 12 s with no error).
- Docs: `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build -W
  --keep-going -q -E -b html docs $TMPDIR/docs-html` → exit 0, no warnings.
- Test strength: `origin/develop`'s tree extracted with `git archive` and the branch tests run
  against it (`PYTHONPATH=<base>/src`). Every R1 integration test of `test_cluster_exit.py` fails on
  base, the launcher and `--ssh` ones by hanging past 60 s, and
  `test_server_without_database_stops_with_tasks_queued` by hanging past 30 s (which shows the
  `submit.py` stop change is needed). `test_autoscale_failed_launches.py` fails on base: on the
  missing API, and `test_cluster_stops_after_three_failed_launches` because base keeps relaunching.
  `test_cluster_advertise.py` fails on base on the new API, and a direct probe shows the defect on
  base: with bind `10.20.30.40`, base tells `--ssh` and autoscaled clients, and a relay bound to
  `127.0.0.1` its site clients, the host's own name; the branch tells them `10.20.30.40` and
  `127.0.0.1`. The R4 and zero-exit tests also fail on base; why was not investigated.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/watch.py` (`watch_launched`, `work_remains`), `cluster/remote.py` `RemoteCluster`, `cluster/ssh.py` `SSHCluster`, `submit.py` stop | Drives `fail`, `fail-db`, `ssh-fail` (exit 5, error names launcher or hosts, statuses, `127.0.0.1:PORT`); `zero` keeps waiting; `test_forever_cluster_waits_until_work_arrives` | ❌ partial: F1 (10 s bound), F2 (streamed input) |
| R2 | `watch_launched` warnings; `LaunchActuator.clean` names pid and zone | `ssh-partial` (one warning for hostB, status 255, while hostA works); `autoscale-off` (a warning at each check); `test_watch_warns_at_each_exit_while_others_run` | ✅ |
| R3 | `AutoScaler.count_failures`, `client_registrations` (`server_id=INSTANCE`), `configured_failed_launches`, `AutoScalingCluster.run_with_exceptions` | `autoscale-fail` (exit 5 after 3 failed launches, statuses and address named); `autoscale-off` (still relaunching at 30 s); setting read from default, file and environment, env over file, bad values refused naming the source; `test_client_registrations_count_this_server_only` | ✅ |
| R4 | `work_remains` barrier (`results_written`, `scheduled_work`) | `healthy` (with and without a database: exit 0, all output, none remaining); `test_cluster_whose_clients_finish_ends_as_before` (launcher exits 3 after the work: one warning, no error); `ssh-partial` exits 0 | ✅ |
| R5 | `site_scaler`, `zone_fleet` leave `failed_launches` at the `AutoScaler` default 0 | `test_relay_fleets_are_off`, `test_site_scaler_on_a_failing_launch_never_stops`; `hs server --autoscaling` runs no `AutoScaler` | ✅ |
| R6 | `cluster/launch.py::advertised_host` at every launch site incl. `relay/site.py::zone_launch` | base-vs-branch probe above; drive `advertise` (launcher, `--ssh`, autoscaling recordings show `-H 127.0.0.1`, work completes); unit tests for the wildcard bind | ✅ |
| R7 | `ClusterApp.advertise_host`, `--advertise`, `server.advertise` | drive `advertise` (`--advertise localhost`, `HYPERSHELL_SERVER_ADVERTISE=localhost`, option over key, local cluster refused with exit 2 naming the option); `test_cluster_app_tells_each_mode[mpi-*]` | ✅ |
| R8 | `--help`, `docs/_include/cluster_{usage,help,desc}.rst`, `config_param_ref.rst`, `cli/cluster.rst`, `security.rst`, `relay_help.rst`, bash and zsh completions, AGENTS.md, `invariants.md` §11 | `hs cluster --help` lists `--advertise` after `-H`; `versionadded:: 2.9.0` on both keys and the option; TLS name rule matches `core/tls.py`/`core/queue.py`; Sphinx `-W` clean; no stale "routable `HOSTNAME`" text; the AGENTS.md and §11 edits match the code (exit 5 is `runtime_error`; constructors take `advertise=`; `AutoScaler` defaults to 0). Man page: rebuilt at release, not graded | ✅ |

Unmapped changes (possible scope creep): none. The `submit.py` change is in scope by ruling; a
normal `LiveSubmitThread` run behaves as before. The `issues/` and `ROADMAP.md` status flips to
`adopted:cluster-launch-failures` match the seeds' adoption.

AGENTS.md invariants (base copy): §11 holds (`client_launch()` is still the one builder; no key on
an argv; Open MPI and `HYPERSHELL_ZONE` paths untouched; the autoscaler terminates nothing). §12
holds (docs and completions in the CLI change's commit, `be73027`; every new test tagged; exit
status from the `RuntimeError` mapping). No §1–§10 surface is touched.

## Findings

### [MEDIUM/CONFIRMED] F1 — R1's 10 s bound fails while the scheduler is in its poll back-off
- **Where:** `src/hypershell/cluster/watch.py:182` (`server.stop(wait=True)`), which reaches
  `ServerThread.stop` → `scheduler.stop(wait=True)` (`src/hypershell/server.py:1293`).
  `SchedulerThread.stop` (`server.py:441-445`) halts the machine and joins it, but the halt is seen
  only after the current `dispatcher.wait_need(generation, timeout=self.poll)` returns
  (`server.py:290`); that back-off doubles up to `server.poll` (30 s by default, `server.py:288`)
  and nothing wakes it on stop.
- **Failure scenario:** with a database, R1 fires while every remaining task is already staged or
  in flight, so the pools ask for a refill that cannot be loaded and the scheduler sits in its
  back-off: (a) clients took the last tasks and then all died (in scope per GOAL's
  Clarifications); (b) a small task file with a launcher that fails late, such as a queued `srun`;
  (c) `--forever` with a task submitted after the clients left. The exit comes up to about
  `server.poll` + 3 s after the last exit. The same delay applies to R3's stop, which has no bound.
- **Evidence:** in a throwaway site, `printf 'echo 1\n' > t.in`, a launcher script that runs
  `sleep 40; exit 1`, then `hs cluster t.in --no-tls -H 127.0.0.1 -p PORT --launcher
  "$PWD/slowfail.sh"` → exit 5, **22.3 s** after the launcher exit. With the detailed log style,
  the warning for the client is at 05:34:57.6, "Stopping (scheduler)" at 05:34:59.8, "Stopping
  (heartbeat)" at 05:35:18.3, and the error at 05:35:19.5. Control, the same with `-Q 2` → 4.4 s.
  A launcher that runs a real client on two `sleep 300` tasks (`-N 2`), kills it after 45 s and
  exits 1 → exit 5, **17.7 s** after the launcher exit, 2 tasks remaining. `hs cluster --forever
  --launcher false` with `hs submit 'echo late'` at 40 s → **22.2 s** after the submit (4.4 s when
  submitted at 60 s: it depends on where the back-off is). With 4 immediately failing tasks the
  run stays inside the bound (6.3 s), which is why drive `fail-db` and the new tests pass; no test
  covers this path.
- **Touches:** R1 (the 10 s bound). Path through `server.py` and the R1 wiring in
  `cluster/remote.py`/`cluster/ssh.py` (high-blast-radius).

### [MEDIUM/CONFIRMED] F2 — With tasks streamed on standard input and the producer idle, R1's stop blocks until the pipe closes
- **Where:** `src/hypershell/cluster/watch.py:182` → `ServerThread.stop` →
  `submitter.stop(wait=True)` (`src/hypershell/server.py:1289`) → the loader's `stop(wait=True)`,
  whose thread is blocked in `next(self.source)` (`src/hypershell/submit.py:345`) reading standard
  input and sees the halt only when a line or EOF arrives. Both the database path (`SubmitThread`)
  and `--no-db` (`LiveSubmitThread`).
- **Failure scenario:** `hs cluster` reading tasks from standard input (its input when no FILE is
  given) from a producer that stays open (`tail -f`, a pipeline still running): every client
  fails, R1 decides to stop, and the cluster then waits for the producer, forever if it never
  closes. The R1 error is raised only after the stop returns, so nothing but the per-client
  warning is printed meanwhile. For such input this is the original hang.
- **Evidence:** in a throwaway site, `(echo 'echo 1'; sleep 60) | hs cluster --no-tls -H 127.0.0.1
  -p PORT --launcher false` → exit 5 after **62.7 s**: the launcher warning at 05:44:50.1,
  "Stopping (loader)" at 05:44:52.2, nothing until "Stopping (committer: database)" at 05:45:48.7
  when the pipe closed, the error at 05:45:51. The same with `--no-db` and a 30 s producer → exit
  at **30.5 s** ("Stopping (loader)" 05:46:08.0, "Stopping (committer: no database)" 05:46:33.5).
- **Touches:** R1 (stop and exit within 10 s). Path through `ServerThread.stop` (`server.py`) and
  the R1 wiring in `cluster/remote.py`/`cluster/ssh.py` (high-blast-radius).

Neither finding meets the deferral exception: no `GOAL.md` criterion requires keeping the slow or
blocked stop, so both block.

Candidates the reviewer dropped under refutation: under `--no-db`, a registered client that
crashes holding no tasks while another finishes the work makes R1 fire instead of waiting out
eviction (the accepted finish-race mechanism, already seeded); under autoscaling with zones, R3
can fire while another zone's launch is still pending and unregistered (R3 as ruled: counted
across zones; the autoscaler still terminates nothing).

## Human-gate triggers

- **Triggered.** F1 and F2 are CONFIRMED on R1's stop path: it is wired into
  `cluster/remote.py` (`RemoteCluster`) and `cluster/ssh.py` (`SSHCluster`), and both delays are in
  `server.py`'s `ServerThread.stop` (the scheduler's back-off wait, the submitter's loader).
  `GOAL.md` Non-goals say `server.py` should not need changes this cycle and that the plan must say
  so before building if it does, so a fix there needs the maintainer's sign-off before `hs-build`
  resumes. No security or DB-lifecycle invariant is touched.

## Optional completeness sub-pass

Not run (not requested).

## Review cycle 2 — changes-requested (2026-11-14)

> Mode: a fresh blind pass over the full spec-excluded diff, plus a check of the remediation of
> F1 and F2. One fresh reviewer saw `GOAL.md`, the base copies of `invariants.md` and `AGENTS.md`,
> `review-rubric.md`, `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, the drive script
> (`git diff origin/develop...HEAD -- 'spec/*/drive/*'`), the remediation diff
> (`git diff 959b07e..HEAD -- src/ tests/`), F1 and F2 as written above, and the runnable repo. It
> did not see `PLAN.md`, `TECH.md`, `research/`, `META.md`, this file, or commit message bodies.
> The gate rulings were passed as in cycle 1, plus the review-cycle-1 ruling: fix both findings in
> `server.py`; the scheduler's stop wakes its dispatcher wait; on the failure stops (R1 and R3)
> only, the server does not wait for a loader blocked on its input, committed rows kept; a normal
> shutdown reads input to its end. The orchestrator reproduced N1 independently.

- **Reviewed commit:** f41da91  ·  **Base:** develop (`origin/develop` at `6da633d`; the branch
  sits on it and fast-forwards cleanly)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3. One build↔review cycle remains before escalation.
- **Contract drift:** none since cycle 1.

### Verification run

All runs used throwaway sites (`.agents/factory/bin/temp_site.sh`) bound to `127.0.0.1`. Drives ran
one at a time. Every started process was stopped by PID, and an out-of-sandbox `ps` confirmed none
remained. The tree was left clean.

- **Full suite, Python 3.14:** `uv run pytest -q` → **2748 passed, 109 skipped**, exit 0 (1304 s).
- **Python 3.11, separate environment:** the touched test files plus the server, submit, relay,
  dispatch, restart and signal-wake tests → **1030 passed, 9 skipped**, exit 0.
- **Drives:** all nine `launch_drive.sh` modes pass.
  - `fail`: exit 5 in 9 s.
  - `fail-db`: exit 5 in 5 s.
  - `ssh-fail`: exit 5 in 5 s.
  - `zero`: still running at 12 s, as it should be.
  - `healthy`, `ssh-partial`, `autoscale-fail`, `autoscale-off` and `advertise`: ok.
- **Docs:** the Sphinx `-W` build exits 0. The docs are unchanged since cycle 1.
- **Test strength:** the 10 new or changed tests were run against the cycle-1 code (`81c4de6`'s
  `src`, extracted, on `PYTHONPATH`). All 10 fail there.
  - The late-exit tests take 15.0, 16.4 and 15.1 s, against a 10 s bound.
  - The three open-input tests (`--no-db`, database, autoscaling) do not exit within 30 s.
  - The unit tests fail on the missing API.
  - No test covers a busy producer (N1) or a FIFO or process substitution given as FILE (N2).
- **Lost wakeup:** the window between a halt and the start of a wait is not tested
  deterministically. A 3000-iteration probe found no slow wake (worst 1.1 ms); details under
  Invariants.

### Status of cycle-1 findings

- **F1: fixed.** Each of cycle 1's reproductions now exits 5 well inside the 10 s bound.

  | Reproduction | Cycle 1 | Now |
  |---|---|---|
  | One task, launcher `sleep 40; exit 1` (database) | 22.3 s | 4.05 s after the launcher exit |
  | The same with `--no-db` | — | 4.06 s |
  | Real client on two `sleep 300` tasks, killed at 45 s, launcher exits 1 | 17.7 s | 3.64 s, 2 tasks remaining |
  | `--forever --launcher false`, `hs submit 'echo late'` at 40 s | 22.2 s | 4.09 s after the submit |
  | `--ssh hostA,hostB` (stub ssh sleeps 20 s, then exits 255) | — | 3.93 s |
  | R3 (`--autoscaling fixed -P 12s --launcher false`) | — | error 1.74 s after the third failure |

- **F2: fixed for standard input only.**
  - Database, standard input from a FIFO fed by `(echo 'echo 1'; sleep 60)`: exit 5 in
    4.42–4.45 s over 4 runs. The task is kept, and there is no fatal error at interpreter
    shutdown.
  - `--no-db`, the same input: 6.4 s.
  - R3 with open standard input: 1.90 s after the third failure.
  - A FIFO path or process substitution given as FILE is still held: see N2.

### Requirement → evidence matrix (cycle 2)

| R-ID | Status | Note |
|------|--------|------|
| R1 | ❌ partial | F1 fixed. N1: a busy input stream with a database hangs the failure stop. N2: stream input given as FILE holds it. |
| R2 | ✅ | `ssh-partial`: exactly one hostB/255 warning, exit 0. The late-ssh run warns for both hosts. |
| R3 | ❌ partial | Same stop path as R1, so N1 and N2 apply. `autoscale-fail` and `autoscale-off` pass. |
| R4 | ✅ | `healthy` with and without a database. A healthy `--no-db` stdin stream finishes 60/60. Normal stops never pass `leave_input`: only `cluster/watch.py:184` and `cluster/remote.py:1033` do. |
| R5 | ✅ | Unchanged since cycle 1. The relay tests pass on 3.14 and 3.11. |
| R6 | ✅ | Unchanged since cycle 1. |
| R7 | ✅ | Unchanged since cycle 1. |
| R8 | ✅ | Unchanged since cycle 1. |

Unmapped changes: none.

**Invariants (base copy):**
- **§5:** holds.
  - `Scheduler.halt` calls the machine's halt, then sets `halting` and notifies the dispatcher's
    `need` condition under its lock. `Scheduler.wait_need` checks `halting` under the same lock.
    So a halt is either seen before the wait or ends it, and a wakeup cannot be lost.
  - No caller of the scheduler's stop holds the dispatcher lock.
  - `halting` is never reset, which is safe because one `Scheduler` serves one thread.
  - The 3000-iteration probe found no slow wake (worst 1.1 ms).
- **§6:** the server's stop order is unchanged, and staged rows are still reverted after an R1
  stop. On the normal path, `join_unless_left` is `join()`. The failure stops skip the loader
  join, as ruled. N1 arises there.
- **§11 and §12:** hold.
- **The branch's AGENTS.md and `invariants.md` §11 edits:** still match the code.

### Findings (cycle 2)

#### [HIGH/CONFIRMED] N1 — An R1 or R3 failure stop hangs forever when tasks stream in faster than they are committed (database)
- **Where:** `src/hypershell/submit.py:661-662` and `:686-691`.
  - The run thread waits in `join_unless_left()`, then puts its end marker with no timeout.
  - `SubmitThread.stop(leave_input=True)` calls `loader.leave()`, puts a marker, halts the
    committer, then joins the run thread.
  - The queue is `Queue(maxsize=bundlesize)` (`:649`), one slot at the default `-b 1`.
- **Reached from:** `ServerThread.stop(leave_input=True)` (`src/hypershell/server.py:1317`), from
  `cluster/watch.py:184` (R1) and `cluster/remote.py:1033` (R3).
- **Failure scenario:** `hs cluster` reads a fast stream, such as `find … | hs cluster --launcher
  srun`, and every client fails.
  1. `leave()` releases the run thread from `join_unless_left` within 0.1 s.
  2. The committer is halted right after `stop`'s marker lands, without draining the queue. A
     task the left loader already read can also land after that marker.
  3. The one-slot queue is now full with no reader. The run thread's untimed `put(None)` blocks
     forever, and so does `stop`'s join of it.
  4. No error is raised and the process never exits. The output shows only the "Stopping"
     warnings. The producer blocks as well, since nothing reads its pipe.

  For the original defect this is the same outcome: a silent, endless wait. `LiveSubmitThread`
  (`--no-db`) avoids it because `put_end` gives up once the committer has ended.
- **Evidence:**
  - In a throwaway site, `yes 'echo 1' > in.fifo &` and then
    `PYTHONFAULTHANDLER=1 hs cluster --no-tls -H 127.0.0.1 -p PORT --launcher false < in.fifo`:
    still running at 25–40 s in 5 of 5 runs, with about 5,700–6,000 tasks committed.
  - The orchestrator's own rerun also hung at 25 s, with 4777 tasks committed.
  - The thread dump on SIGABRT:
    - `hypershell-submit` is at `submit.py:662` (`queue.put`).
    - `hypershell-cluster` is at `submit.py:691` ← `server.py:1317` ← `watch.py:184` ←
      `remote.py:323`.
    - The loader and committer threads are gone.
  - `seq 1 300000 | sed 's/^/echo /'` as the producer also hangs.
  - R3 (`--autoscaling fixed -P 2s`, `yes` on stdin) hangs the same way, through `remote.py:1033`.
  - Controls:
    - `-b 10` exits 5 in 4.55 s.
    - A producer at about 40 lines/s exits 5 in 4.55 s.
    - `--no-db` with `yes` exits 5 in about 8.5 s.
  - Against the cycle-1 code (`81c4de6`), R1 with the same busy stream exits 5 in 6.4–6.5 s (3 of
    3). The remediation introduced the R1 hang. The R3 hang was already there in the cycle-1 code.
- **Touches:** R1 and R3 (exit within the bound, and exit at all). The defect lines are in
  `submit.py`. The hang is in the server's stop sequence (`ServerThread.stop`'s submitter stage,
  `server.py`) and in R3's stop in `cluster/remote.py`, both high-blast-radius. It is reached
  through the failure-stop exception to §6's submit flush order (`loader.join()` then
  `queue.put(None)`) that the cycle-1 ruling allowed. The ruling allows not waiting on the
  loader. It does not allow a stop that never returns.

#### [MEDIUM/CONFIRMED] N2 — F2 is only partly fixed: a FIFO path or process substitution given as FILE still holds R1 and R3 until the producer closes
- **Where:** `src/hypershell/cluster/__init__.py:601`. `ClusterApp.__exit__` closes an input stream
  that is not standard input. The left loader is still blocked reading that stream
  (`submit.py:346`, `next(self.source)`) and holds the stream's buffer lock, so the close waits
  for the producer.
- **Failure scenario:** the F2 scenario with the stream named as FILE instead of piped to standard
  input. `is_file_input` treats FIFOs and process substitution as streams, as it does standard
  input.
  - The server stops within the bound.
  - The process then waits in `__exit__` until the producer closes, and logs the R1 error only
    after that.
- **Evidence:**
  - `hs cluster tasks.fifo --no-tls -H 127.0.0.1 -p PORT --launcher false`, with a 60 s producer
    writing to `tasks.fifo`: still running at 25 s. The thread dump shows the main thread at
    `cluster/__init__.py:601` in `__exit__` and the server threads gone.
  - With a 30 s producer: exit 5 at 30.53 s (database) and 30.44 s (`--no-db`).
  - `bash -c 'hs cluster <(echo "echo 1"; sleep 30) --launcher false …'`: exit 5 at 30.12 s.
  - The same under `--autoscaling fixed -P 2s` (R3): 30.11 s.
  - The same input on standard input exits in 4–6 s.
- **Touches:** R1 and R3 (the 10 s bound for stream input). The file is not on the
  high-blast-radius list.

Neither finding meets the deferral exception. Both arise from this branch's stop path, and no
`GOAL.md` criterion requires keeping them. Both block.

Candidates dropped under refutation:
- **A lost wakeup or deadlock between halt and `wait_need`:** the shared lock closes it, and the
  probe is clean.
- **A left loader enqueueing a line it read after `leave()`:** the machine checks the halt before
  PUT, and a unit test covers it.
- **A left loader holding process exit, or a fatal error at shutdown:** it is a daemon thread, and
  five standard-input runs exited 5 cleanly.
- **A normal run losing input:** healthy stdin and file runs read everything.
- **"Stopping (loader: input left unread)" logged for a file already read to the end:** cosmetic.
- **A healthy database cluster on a pausing stdin stream exiting 0 with tasks left (30 of 60):**
  identical on `origin/develop`, so pre-existing and outside this cycle. It is not graded.

### Human-gate triggers (cycle 2)

- **Triggered.** N1 is CONFIRMED on the server's stop sequence. It hangs inside
  `ServerThread.stop` (`server.py`) on R1's path, and inside `AutoScalingCluster`'s R3 stop
  (`cluster/remote.py`). Both are high-blast-radius.
- A fix revisits the failure-stop exception to §6's submit flush order that the maintainer ruled
  on in cycle 1. It needs the maintainer's sign-off before `hs-build` resumes.
- N2's fix is in `cluster/__init__.py`, outside the core, but it shares N1's input-leaving design.
- No security or DB-lifecycle invariant is touched, and committed rows are kept.
- This is cycle 2 of ≤3. If cycle 3 does not converge, escalate to the maintainer.
