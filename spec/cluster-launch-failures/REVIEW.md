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
