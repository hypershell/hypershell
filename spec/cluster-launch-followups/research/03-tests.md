# Research 03: regression tests for R1, R2, R3 and R5 (R7)

> **Kept by the plan:** the prototypes named below (`$T`) are copied to [`prototypes/`](prototypes/),
> renamed `locked_units_proto.py`, `locked_cluster_proto.py`, `nodb_finish_proto.py` (with
> `locking.py`) so a bare `pytest` never collects them; the prototype `conftest.py` is replaced by
> `-p tests.conftest`, and the stand-in oracle is not kept.

Read-only research at `ffdd53cb`. `git diff --stat 9c82842d HEAD -- src tests` is empty, so the
current tree behaves as the base. Every prototype below is red on it, three runs out of three. The
locked prototypes also go green under a crude stand-in fix, which shows they can pass. Prototypes
live in `$SCRATCH (the planning session's scratch directory)/clf-tests/`
(`$T` below).

## 1. Survey

- **Isolation.** `tests/conftest.py` runs `isolate_environment()` at import, before HyperShell is
  imported: it scrubs `HYPERSHELL_*`, blanks `HYPERSHELL_CONFIG_FILE` and pins a free
  `HYPERSHELL_SERVER_PORT`. The autouse `clean_env` fixture repeats this before every test.
  `temp_site` sets `HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE=<site>/local.db` and DEBUG logging.
  It only affects **child processes**: the in-process engine in `data/core.py` is built once at import,
  from a configuration that names no database file.
- **In-process DB.** Tests rebind the global scoped `Session` to their own file:
  `tests/test_zone_model.py::task_db`, through `bind()`, which does
  `create_engine(f'sqlite:///{path}', check_same_thread=False)`, `Session.remove()` and
  `Session.configure(bind=…)`, and restores `data_core.engine` afterwards. Each test gets its own file
  under `tmp_path`, so one test never shares a database with another.
- **Subprocesses.** `tests/test_cluster_exit.py` has `run_child`, which runs a child in its own
  session and fails the test if it outlives `timeout`. Its `kill_group` SIGKILLs the process group
  and reaps it. `run_cli` and `cluster_command` handle bash pipelines, and `CLUSTER_SCRIPT` builds an
  in-process `RemoteCluster` or `SSHCluster` in a child. `test_autoscale_failed_launches.py` has
  `SCALE_SCRIPT` (an `AutoScalingCluster` with `period=1`) and drives `hs cluster` directly with
  `Popen(..., start_new_session=True)`, `wait(timeout)` and `kill_group`.
- **Stand-ins.** `StandInServer` (`test_cluster_exit.py:188`) is an event-backed server with
  `is_alive`, `join` and `stop` for `watch_launched`, and `sh()` makes real exiting processes. For the
  autoscaler, `ScriptedLaunch` and `failing_scaler` (in `test_autoscale_failed_launches.py`) work with
  `FakeProc`, `FakeLaunch`, `FleetDB` and `run_checks` (in `test_zone_autoscale.py`).
- **Markers.** `--strict-markers`; tests use only `@mark.unit` and `@mark.integration`.
- **`.agents/factory/bin/temp_site.sh`** mirrors `temp_site` for CLI drives: a mktemp site,
  `HYPERSHELL_DATABASE_FILE=$site/task.db`, and `UV_PROJECT` pinned to the repo. It removes the site
  on exit.
- No existing test holds a SQLite lock. `test_contended_*` cover PostgreSQL row locks only.

## 2. Findings that shape the design

1. **A real lock in-process is easy and fast.** Bind `Session` to a per-test engine with
   `connect_args={'timeout': 0.5}`. A second raw `sqlite3.connect(path, isolation_level=None)` then
   runs `BEGIN EXCLUSIVE`, and each locked read raises
   `OperationalError … database is locked` after 0.5 s instead of 5 s. That is the test's own engine,
   so the product's busy wait is untouched (a Non-goal). The base fails in
   `client_registrations` (`remote.py:392`) when `failed_launches=3`, and in `current_group`
   (`remote.py:599`) when it is 0. There is no need to construct `OperationalError` by hand for the
   locked cases. Keep a constructed non-lock error (`disk I/O error`) for the R4 guard only.
2. **No rollback is needed.** After a locked SELECT fails, the same scoped session reads again once
   the lock is gone: the `test_probe_session_after_a_locked_read` probe returns 4. This agrees with
   02-locked-reads.
3. **The scheduler dies under a lock too, and that is out of scope.** My first subprocess timing took
   the lock as soon as the 5 task rows landed, at about 0.3 s. That killed `server.Scheduler` with an
   "Uncaught exception" during its first loads (`count_remaining`), so the drive would fail on any
   correct fix. With no client connected, once the scheduler has staged its first tasks it waits on
   demand and reads nothing more. The holder therefore waits until three things are true: the
   cluster's launch line is in its log (`Launching clients` in launcher mode,
   `Autoscale adding client` in autoscaling), `task.schedule_time` is set on at least one row, and
   0.5 s more has passed. Only then does it take the lock. **The shaper's `locked.sh` and
   `locked_launcher.sh` (fixed delays of 3 s and 0.5 s) carry this hazard.**
4. **Gate the client on the lock.** The launcher is
   `sh -c 'n=0; while [ ! -e FLAG ] && [ $n -lt 300 ]; do sleep 0.1; n=$((n+1)); done; exit 1' sh`.
   The holder touches FLAG once it holds the lock. The first failed exit, and every decision read
   after it, then happen under the lock on any machine speed, and the 30 s cap keeps the launcher
   bounded.
5. **Hold lengths.** A read waits 5 s in the product before it fails, so the hold must cover the
   time until a read starts plus 5 s plus a margin:
   - Launcher mode: the watch sees the exit within about 1.1 s, so the hold is **7.5 s**.
   - Autoscaling with `-P 1s`: a check starts within about 2 s, so the hold is **8.5 s**.

## 3. Recommended tests

| file :: test | marker | mechanism | base failure | runtime base / fixed |
|---|---|---|---|---|
| `test_autoscale_failed_launches.py::test_locked_check_is_skipped_with_one_warning[3, 0]` (R1) | unit | `failing_scaler(registrations=remote.client_registrations)`, real DB (0.5 s busy), one CHECK + CHECK_FIXED inside `exclusive(db)`; assert not FINAL/HALT, exactly one WARNING containing "locked", no CRITICAL | `OperationalError` raised from the check | 0.6 s / 0.6 s |
| `…::test_exits_during_a_skipped_check_still_count` (R2) | unit | the first launch has exited 1 before the locked check; after release `run_checks` reaches FINAL with 3 failures, `failures[0][0] == started[0].pid` | `OperationalError` | 0.6 / 0.6 |
| `…::test_limit_off_relaunches_after_a_skipped_check` (R1/R2 at 0) | unit | after release, checks SCALE again; not exhausted | `OperationalError` | 0.6 / 0.6 |
| `…::test_other_operational_errors_still_end_the_check` (R4) | unit | `registrations` raises a constructed `disk I/O error` and it must propagate | passes on base (a guard) | 0.1 |
| `test_cluster_exit.py::test_watch_reads_again_after_a_locked_read` (R3) | unit | `StandInServer` plus a live-scheduler stand-in (`scheduler.is_alive()` true, `machine.current_group=None`, receiver not alive) and the real `work_remains`; lock held 1.5 s; watch in a thread with `join(15)`; assert `ClusterLaunchError` within 10 s of release, a "locked" WARNING, no CRITICAL | the thread ends with `OperationalError` | 1.5 / 3.5 |
| `test_autoscale_failed_launches.py::test_autoscaling_cluster_with_a_locked_database_reaches_the_failed_launch_stop` (R1+R2) | integration | `hs cluster tasks.txt --autoscaling fixed -P 1s --launcher <gated>`, holder from §2.3, hold 8.5 s, `wait(45)`, `kill_group`; assert rc 5, "3 launched clients in a row exited non-zero", a "locked" WARNING, no `exception-*.log` under the site, exit after release | rc 5 with CRITICAL `database is locked`, a traceback file, ended at 6.9 s | 6.9 / 12.4 s |
| `…::test_autoscaling_cluster_with_the_limit_off_keeps_relaunching_after_a_lock` (R1 at 0) | integration | as above with `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0`; still alive 6 s after release, at least 3 "exited with status 1", no traceback file | rc 5 at 9.4 s with a traceback | 9.4 / 15.4 s |
| `test_cluster_exit.py::test_launcher_cluster_with_a_locked_database_stops_with_the_r1_error` (R3) | integration | `hs cluster tasks.txt --launcher <gated>`, hold 7.5 s; assert rc 5, R1 text, exit ≤ 10 s after release, a "locked" WARNING, no traceback file | rc 5 at 7.0 s with a traceback | 7.0 / 12.4 s |
| `test_cluster_exit.py::test_no_db_cluster_whose_client_dies_after_its_last_result_is_not_a_failed_launch` (R5) | integration | `--no-db --launcher "<sys.executable> <tmp_path>/crashy.py"` (see §4); `wait(13)` must time out; stdout holds all 5 results; "crashy client" line present; no R1 text; a WARNING "exited with status 1"; then `kill_group` | rc 5 at 4.8 s with `ClusterLaunchError: every launched client has exited…` | 4.8 / 13.4 s |

Every test above runs in under 16 s. The whole locked set takes about 28 s on the base and 47 s
with the stand-in fix.

**Gaps the plan should add, which need no red test:**
- R1 asks for coverage of "every read". With `failed_launches=3`, registrations come from a lambda
  returning `(0, 0)` and only the sizing reads are locked, which pins zone sizing at the default
  limit too.
- `check_dynamic` / `read_zone_loads`.
- R6 and R5 predicate units with a stand-in server, as the GOAL's Evidence plan says: results equal
  to submitted with the submitter done gives no work; fewer gives work; a submitter still alive gives
  work. These cannot fail on the base.

**R2 working-launcher drive:** not recommended as a test. Real clients register and heartbeat
through the server's own threads, which would meet the lock. That is a Non-goal, and the hazard
from §2.3 applies. Leave it to a review drive with the lock taken while a single client runs a
long task, or pin it in units only.

**R6** is already covered and green on the base:
`test_cluster_exit.py::test_launcher_cluster_exits_when_every_client_fails[status-no-db]` and
`[signal-no-db]`, which exit within 10 s with no results, plus
`test_cluster_reading_open_input_exits_when_every_client_fails[no-db]` (CLI `--launcher false --no-db`)
and `test_cluster_reading_a_stream_given_as_file_exits_when_every_client_fails[*-no-db]`. Also keep
`test_cluster_whose_clients_finish_ends_as_before[no-db]` green: the client sends FINISHED and then
the launcher exits 3. None of these covers "some results, then a failure". That case needs a
predicate unit.

## 4. R5 client without a shebang

The launch argv is `[*shlex.split(launcher), remote_exe, 'client', …]` (`launch.py:253`), so
`--launcher "<quoted sys.executable> <quoted tmp_path/crashy.py>"` runs
`python crashy.py hs client …`. The wrapper is the shaper's `crashy_hs` with
`sys.argv = ['hs', *sys.argv[2:]]` in place of the shebang. It patches
`ClientThread.wait_heartbeat` to `os._exit(1)`, which runs after `wait_collector`
(`client.py:1325-1329`), so every result has been returned first. `--remote-exe` is left at its
default. Launcher mode takes no eviction period, so the pre-#107 ending (about 600 s) cannot be
shortened. The test pins "still running past 10 s, every result printed, no R1" and kills the
cluster.

## 5. Prototypes and red runs

| path | contents |
|---|---|
| `$T/locking.py` | `locked_db()` (0.5 s busy engine), `exclusive()`, and the bounded `LockHolder` thread (waits for rows, the launch line and staged tasks, then locks, touches the flag, holds, releases) |
| `$T/test_locked_units.py` | the 5 unit tests, the R4 guard and the session probe |
| `$T/test_locked_cluster.py` | the 3 `hs cluster` lock tests |
| `$T/test_nodb_finish.py` | R5 |
| `$T/standin.py` + `$T/standin_site/sitecustomize.py` | the stand-in fix (`CLF_STANDIN=1`) and an R5 oracle (`CLF_STANDIN_R5=1`). **Prototype only, not a design.** |
| `$T/runs-locked.txt` | raw output: 3 base runs and 2 stand-in runs |

The command, run from the worktree:

    UV_CACHE_DIR=/tmp/claude-501/uv-cache-clf PYTHONPATH=$PWD:$T uv run pytest -p no:cacheprovider \
      --rootdir=$PWD -c pyproject.toml $T/test_locked_units.py $T/test_locked_cluster.py -q

Results:
- **Base, 3 of 3 runs:** 8 failed and 2 passed (the guard and the probe), 28.2–28.4 s each. Unit
  failures are `OperationalError` at the lines in §2.1. In the cluster tests the base exits 5 with
  `CRITICAL … database is locked` and an `exception-*.log` file.
- **With `CLF_STANDIN=1`, 2 of 2 runs:** 10 passed in 47 s.
- **R5 on the base, 3 of 3 runs:** failed at the "no R1 text" assert, with exit 5 at 4.8 s.
- **R5 with `CLF_STANDIN_R5=1`:** passed in 13.4 s.

Once the tests are moved into the suite, run them by node id. Do not use `-n auto`.

    uv run pytest -q tests/test_autoscale_failed_launches.py -k "locked or skipped_check or other_operational"
    uv run pytest -q tests/test_cluster_exit.py -k "locked or dies_after_its_last_result"

## 6. Shaper's scratch scripts

`hold.py` is a sound lock holder for review drives. Use it with `isolation_level=None` and
`BEGIN EXCLUSIVE`, as it is written. `locked.sh` and `locked_launcher.sh` should **not** be reused
as they are, for three reasons:
- the fixed port 51991 can collide;
- a hard-coded `.venv` path;
- fixed lock delays that can meet the scheduler's start-up reads (§2.3) or miss the decision read.

`locked.sh`'s watchdog also kills only the cluster's PID, not its process group. For review drives,
use the gated launcher with `hold.py` started after the launch line appears.
