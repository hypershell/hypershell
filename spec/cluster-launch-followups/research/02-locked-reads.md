# Research 02: locked reads in the cluster's own checks (R1–R4, R7)

Read-only brief for `hs-plan`. Worktree `fix/cluster-launch-followups`; line numbers are from that tree.

## 1. Session state and probe

- `data/core.py:267-270`: one module engine, `sessionmaker(bind=engine)`, `Session = scoped_session(factory)`.
  That gives one session per thread. `Entity.query()` (`data/model.py:222-227`) is `Session.query(...)`, and so are
  `Task.count_remaining` (`:915-920`), `count_retryable` (`:808-818`), `current_group` (`:654-679`),
  `Client.count_connected` (`:1768-1774`) and `client_registrations` (`cluster/remote.py:383-394`). The
  autoscaler thread and the cluster thread that runs `watch_launched` each have their own long-lived
  session, which never commits or closes. The engine sets no `timeout` and the pragmas are empty by default
  (`core.py:203-217`), so pysqlite's 5 s busy wait applies (`PRAGMA busy_timeout` reads 5000).
- Probe: `$SCRATCH (the planning session's scratch directory)/clf-plan/probe_lock.py`
  (scratch, may be purged) builds a temp site (`HYPERSHELL_SITE`, `HYPERSHELL_DATABASE_FILE`,
  `HYPERSHELL_CONFIG_FILE=''`) and runs `initdb()` with 5 tasks. A raw `sqlite3` connection holds
  `BEGIN EXCLUSIVE`. Python 3.15.2, SQLAlchemy 2.0.50, SQLite 3.53.1, QueuePool:
  ```
  locked read 1: sqlalchemy.exc.OperationalError after 5.37s
     orig: OperationalError 'database is locked' errorcode= 5 errorname= SQLITE_BUSY
     session.is_active = True  in_transaction = True
  client_registrations / Client.count_connected / Task.current_group: OperationalError after ~5.3s
  lock released
  read after release, no rollback: ok 5 in 0.00s
  read with lock released 1s into busy wait: ok 5 in 1.04s
  thread read under lock: OperationalError after 5.37s   (worker thread, own scoped session)
  thread read after release, no rollback: ok 5 in 0.00s
  ```
- **No `rollback()` or `Session.remove()` is needed.** A failed SELECT leaves the session active, and the next
  read on the same thread works as-is. SQLAlchemy does not invalidate the connection, because pysqlite's
  `is_disconnect` does not match this message. After a failed read the holder could take `BEGIN EXCLUSIVE`
  again, so the failed read leaves no SHARED lock behind. A lock released while a read is still in its busy
  wait lets that read succeed at once, so a read fails only when the lock outlives the whole 5 s wait.
- **Shorter waits in tests, product defaults unchanged:**
  - In-process: bind `Session` to a per-test engine, following the existing `task_db` fixtures
    (`tests/test_retry_gate.py:36-47`), with `connect_args={'check_same_thread': False, 'timeout': 0.3}`.
    Alternatively, monkeypatch `hypershell.data.core.pragmas['busy_timeout'] = 300`, then `Session.remove()`
    and `engine.dispose()`. The `connect` listener (`core.py:288-295`) reads the module dict at each new
    connection. With `PROBE_SHORT=1` the probe's locked read failed in 0.34 s.
  - Subprocess drives: a config file with `[database.pragmas]` and `busy_timeout = 300`, passed as
    `HYPERSHELL_CONFIG_FILE`, gave `PRAGMA busy_timeout` = 300 (default 5000). The environment form cannot
    express this key, because `busy_timeout` would split at the underscore. The default 5 s is fine for the
    GOAL drives (a 9 s lock); keep this for unit or fast tests only.

## 2. Classifying "locked"

- `data/upgrade.py:424-427` `_is_locked` is private. It also matches PostgreSQL `55P03` and
  `'database is busy'`, and it is tuned for upgrade steps with their own timeouts. Don't move or reuse it.
  Moving it touches `upgrade.py` and its tests, and `55P03` widens R1 beyond the GOAL's "SQLite only". Under
  R4, any non-lock error must end the run as on `develop`.
- `sqlite3.OperationalError.sqlite_errorcode` and `.sqlite_errorname` exist on 3.11+ (`requires-python
  >=3.11`). Checked on 3.11.18, 3.14.6 and 3.15.2:
  - A held exclusive lock gives `'database is locked'`, code 5, `SQLITE_BUSY`.
  - A shared-cache table lock gives `'database table is locked: t'`, code **262**, `SQLITE_LOCKED_SHAREDCACHE`.
    That is an extended code, so mask it with `& 0xFF` to get 6. `SQLITE_BUSY_SNAPSHOT` (517) and
    `SQLITE_BUSY_RECOVERY` (261) mask to 5 the same way.
  - `sqlite3.SQLITE_BUSY` and `sqlite3.SQLITE_LOCKED` constants exist on 3.11+.
  - A hand-made `sqlite3.OperationalError('database is locked')` has **no** `sqlite_errorcode`, so a
    predicate that reads only the code would miss the existing test idiom
    `OperationalError('SELECT', {}, Exception('database is locked'))` (`tests/test_relay_roster.py:452`).
- **Recommend** a new narrow public predicate:
  ```python
  def database_locked(error: BaseException) -> bool:
      """True when SQLite reported the database, or a table in it, locked by another connection."""
      if not isinstance(error, sqlalchemy.exc.OperationalError):
          return False
      code = getattr(error.orig, 'sqlite_errorcode', None)
      if code is not None:
          return code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
      message = ' '.join(str(error.orig).split()).lower()
      return 'database is locked' in message or 'database table is locked' in message
  ```
  The message fallback covers drivers without the code (Turso) and stand-in errors in tests. A disk error,
  `unable to open database file` (`SQLITE_CANTOPEN`, 14), a closed connection, or any `DatabaseError` that
  is not an `OperationalError` returns False and is re-raised. Put the predicate in `cluster/watch.py`, which
  `remote.py` already imports (`remote.py:36`), so the change stays inside `cluster/`.
  `data/core.py` is the other home if the plan wants it beside the engine. Either way, add a test for each
  branch: code 5, code 262, message only, and a non-lock `OperationalError`.

## 3. The autoscaler FSM (R1, R2, R4)

- Failure path today:
  1. `StateMachine.next` (`core/fsm.py:58-63`) catches every non-`RuntimeError`, logs CRITICAL
     `Uncaught exception from <class>`, writes the traceback file through `write_traceback`, and re-raises.
  2. `Thread.run` (`core/thread.py:36-41`) stores the exception, and `join()` (`:56-60`) re-raises it.
  3. `AutoScalingCluster.run_with_exceptions` (`remote.py:1022-1039`) re-raises it from `self.autoscaler.join()`.
     The server is never stopped and `ClusterLaunchError` is never reached.
  4. The cluster thread's join re-raises in the app, where the shared mapping `Exception: write_traceback`
     (`core/exceptions.py:142`, `status=runtime_error` = 5) prints the second CRITICAL line and writes the
     traceback file again.
- Reads per check:
  - `CHECK` → `check()` (`remote.py:576-585`) runs `self.clean()`, which removes the exited Popens from the
    actuator and warns about each (`launch.py:344-359`). It then sets `last_check` and calls
    `count_failures(exited)`, which reads `self.registrations()` first (`:670`).
  - `CHECK_FIXED` or `CHECK_DYNAMIC` → `check_zones()` (`:604-629`) reads `current_group()` (`Task.current_group`
    and `starting_group`), `Client.count_connected()` when `registered` is None, and `read_zone_loads(...)`
    (`demand.py:111-150`, about six `Task`/`Zone` reads).
  - `SCALE`, `INIT` and `WAIT` read nothing. In both methods **every read happens before any state is
    mutated**: `count_failures` assigns `seen` and `failures` only after both counts; `check_zones` resets
    `pending` only after `read_zone_loads`. A skip at either point therefore leaves the machine consistent.
- **Narrowest catch: per check state, not per read.** Wrap the body of `check()`'s `count_failures` call, and
  `check_fixed()` and `check_dynamic()` around `self.check_zones()`, or put one helper behind both. On
  `database_locked(error)`, log one WARNING and return `AutoScalerState.WAIT`; otherwise use a bare `raise`,
  which keeps the R4 traceback identical. A skip in `CHECK` returns before `CHECK_FIXED`, so each check logs
  at most one warning. A per-read catch would need about 8 sites and could warn twice in one check.
- **R2 carry-over.** Today a locked registration read drops the exits that `clean()` already took. Add
  `self.unchecked: List[Tuple[Popen, ZoneRef]] = []` and change `check()` as follows:
  ```python
  exited = self.unchecked + self.clean()      # earlier exits first: launch order is kept
  self.last_check = datetime.now()
  try:
      stop = self.count_failures(exited)
  except OperationalError as error:
      if not database_locked(error): raise
      self.unchecked = exited                 # counted at the next check; already warned about by clean()
      log.warning(...); return AutoScalerState.WAIT
  self.unchecked = []
  ```
  With `failed_launches` at 0, `count_failures` returns before reading, so the list is always cleared. A
  locked `check_zones` after a successful `count_failures` must not re-carry the exits, because they were
  counted. `count_failures` calls `proc.poll()` on carried Popens, which returns the cached `returncode`.
- **`last_check`.** It is set in `check()` before the reads, and `wait()` (`:563-572`) returns `CHECK` once
  `now - last_check > period`. A skipped check returns `WAIT`, and the next check comes one period after the
  skipped check began. With `-P 2s`, the 5 s busy wait already exceeds the period, so the next check is
  immediate. That meets R1's "check again at its next period" with no further change.
- **Wording.** The drive greps for "database was locked", for example:
  `log.warning(f'Autoscale check skipped: the database was locked ({error.orig}); checking again in {period} s')`.

## 4. The every-client-exited check (R3)

- `watch_launched` runs in the `hypershell-cluster` thread, from `RemoteCluster.run_with_exceptions`
  (`remote.py:323`) and from ssh (`cluster/ssh.py:326`). There is no FSM: a raise propagates through the
  thread join to the app's `write_traceback` mapping, so one CRITICAL line, the traceback file, and exit 5.
- The only DB reads are in `scheduled_work` (`watch.py:120` and `:124`), called twice from `work_remains`
  (`:147` and `:150`). `results_written` and the `--no-db` branch read nothing.
- **Recommend** catching in `work_remains` around both `scheduled_work` calls. On `database_locked`, log one
  WARNING (for example, `Could not read whether tasks remain: the database was locked; reading again`) and
  return False ("not known to remain"). Re-raise anything else. Say in the docstring that False also means
  "not known yet".
- `watch_launched`'s loop already re-calls `work_remains` after `server.join(timeout=WATCH_INTERVAL)` (1 s)
  whenever it returns False, so "read again" needs no new loop. `watch_launched` stays unchanged, and the
  `tests/test_cluster_exit.py` tests that replace `watch.work_remains` are unaffected.
- **Timing after release.** A read whose busy wait spans the release succeeds at once. The worst case is a
  release just after a read gave up: about 1 s (interval), then about 0.1–0.3 s for `results_written`'s empty
  bundle, then the first read, `SCHEDULE_SETTLE` (2 s), and the second read. That is about 3.5 s plus the
  server stop, well inside 10 s. During a long lock the loop logs at most one WARNING about every 6 s
  (5 s wait plus 1 s interval).
- **Unit test.** Use a stand-in server with a live scheduler stand-in, and monkeypatch `Task.count_remaining`
  to raise a locked `OperationalError` once and then return 1. The first `work_remains` call returns False
  with one WARNING, the second returns True. A non-lock error propagates.

## 5. Who else inherits it

- **Relay site fleets** inherit it. `site_cluster` (`relay/site.py:132-148`) is an `AutoScalingCluster`
  whose machine is replaced by `SiteScaler`, and `zone_fleet` (`:182-200`) is an `AutoScalerThread` with
  `SiteScaler`. `SiteScaler` overrides `init`, `wait`, `check_zones` and `reconcile`, not `check` or
  `check_fixed`.
  - Fleets run with `failed_launches=0` (`tests/test_autoscale_failed_launches.py:208-215`), so the
    registration catch never fires there.
  - Without a topology, `SiteScaler.check_zones` calls `super().check_zones()` and reads the relay's own
    SQLite through `Session`. A catch in `check_fixed`/`check_dynamic` covers that path. `reconcile` reads
    nothing.
- **`hs server --autoscaling` does NOT use `AutoScaler`.** `AutoScalingServer` (`cluster/topology.py:554+`)
  runs a `TopologyPublisher` FSM whose `check()` (`:415-424`) calls `scheduler_group` and `read_zone_loads`
  from `demand.py` directly. **Don't put the catch inside `demand.py`** (`read_zone_loads` or
  `scheduler_group`), or the publisher's behaviour changes silently. The GOAL lets only `AutoScaler` users
  inherit the handling.
- Also leave alone `relay/upstream.py`'s `SQLAlchemyError` retries (own engine, `store.count_connected`),
  `data/upgrade.py`, and every server thread (Non-goal).

## Risks

- **Server threads can meet the same lock.** The scheduler, receiver and heartbeat monitor have no lock
  handling, and they are out of scope. If one of them reads during the drive's lock window, the run can still
  end with a traceback from a server thread, not from our check. The GOAL's reproductions failed only in the
  cluster's reads, but drive timing should keep the server idle: no clients and a scheduler blocked on a full
  queue. A flake here is out of scope, not a regression.
- **Keep reads before mutation.** If a later change adds a write or mutation before the last read in
  `count_failures` or `check_zones`, a skipped check leaves the state half-updated. Note the invariant in a
  comment.
- **`database_locked` may be too broad.** It matches any `OperationalError` with code 5 or 6 on any thread
  that calls it, which is fine because only these sites call it. The message fallback could match a
  non-SQLite driver whose text contains "database is locked", which is acceptable.
- **The FSM is blocked for up to 5 s per locked read**, so `halt()` and the stop are delayed by that much.
  This is pre-existing, and the GOAL leaves the busy timeout alone.
- **A long-lived session** stays `in_transaction` after failures (pre-existing). The probe shows reads
  continue, so there is no new leak.
