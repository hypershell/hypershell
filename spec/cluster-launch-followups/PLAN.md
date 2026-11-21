# PLAN — A locked database delays a cluster's decision instead of ending the run; a `--no-db` cluster whose work is done never reports a failed launch

> **Status:** Draft for review · **Last updated:** 2026-11-16
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) ([`00-digest.md`](research/00-digest.md) + briefs 01–03). Every design
> element traces to a GOAL R-ID.

## 1. Summary

Two local fixes in the cluster's own decision code, plus one counter in the server. The autoscaler
(`cluster/remote.py`) and the every-client-exited check (`cluster/watch.py`) catch a locked SQLite
read at the check that made it, log one WARNING, and decide again at their next pass; any other
error ends the run as today. Exits seen during a skipped autoscaler check are kept and counted at
the next check that reads. A `--no-db` cluster stops guessing from "the server is still alive": the
server's Receiver counts the results it has written (`server.py`, three lines), and the watch
compares that count with the tasks the submitter put on the queue once the input has ended. No
option, key, wire format, stop order or docs page changes. `fix`, `small`: three phases (tests
first, then the two fixes) and a gate; the full research fan-out ran because `server.py` and
`cluster/remote.py` are high-blast-radius.

## 2. Design

### 2.1 Telling a locked read apart — `cluster/watch.py::database_locked(error)`

A new public helper (exported in `__all__`), beside `exit_text`:

```python
def database_locked(error: BaseException) -> bool:
    """True if `error` is SQLite reporting the database (or a table in it) locked by another connection."""
```

True only for a `sqlalchemy.exc.OperationalError` whose `orig` is a `sqlite3.OperationalError` and
whose `orig.sqlite_errorcode & 0xFF` is `sqlite3.SQLITE_BUSY` (5) or `sqlite3.SQLITE_LOCKED` (6); the
mask takes the extended code 262 ("database table is locked") to 6. When `orig` has no
`sqlite_errorcode` (a hand-made error, as the existing tests build), the message decides:
`database is locked` or `database table is locked`. Everything else (a PostgreSQL error, a disk I/O
error, `no such table`, any non-`OperationalError`) is False and keeps today's path (R4).
`data/upgrade.py::_is_locked` is not reused: it also matches PostgreSQL's `55P03`, outside this
cycle, and that file stays untouched. [02 §2]

The per-thread `scoped_session` needs no `rollback()` after a locked read: the next read on the same
thread works as-is (probed on the main and a worker thread). [02 §1]

### 2.2 The autoscaler skips a locked check (R1, R2, R4) — `cluster/remote.py::AutoScaler`

- **New state:** `unchecked: List[Tuple[Popen, ZoneRef]]`, `[]` in `__init__`: launched clients that
  have exited and not yet been counted.
- **`check()`** becomes, in order: `self.unchecked += self.clean()`; `self.last_check =
  datetime.now()` (unchanged position: a skipped check is retried one period later, R1); then
  `count_failures(self.unchecked)` inside `try`. On `except OperationalError as error`: if
  `database_locked(error)`, call `self.skip_check()` and return `WAIT`; else bare `raise` (R4). After
  `count_failures` returns, `self.unchecked = []`, then FINAL or CHECK_FIXED/CHECK_DYNAMIC as today.
  With `failed_launches` at 0, `count_failures` reads nothing and returns at once, so `unchecked`
  is cleared every check and nothing changes (the default for relay fleets).
- **`count_failures`** is unchanged; its registration read is its first statement, before any
  mutation of `seen`/`failures`, so a locked read leaves the run of failures as it was. A comment
  states that ordering. The exits it is handed are now every exit since the last counted check, in
  launch order (R2: "counting the ones that exited during skipped checks").
- **`check_fixed()` / `check_dynamic()`**: `try: return self.check_zones()`, the same `except` (a
  locked read → `skip_check()`, `WAIT`; anything else re-raised). `check_zones`, `current_group`,
  `Client.count_connected`, `read_zone_loads` are unchanged: every read in `check_zones` precedes
  `self.pending = {}` and the per-zone loop, so a skipped check changes nothing. Catching here, not
  in `demand.py`, leaves `hs server --autoscaling`'s `TopologyPublisher` (which calls
  `read_zone_loads` directly) exactly as it is. `SiteScaler` overrides `check_zones` but not
  `check_fixed`/`check_dynamic`, so relay site fleets inherit the skip (accepted at the GOAL gate).
- **`skip_check()`**: one WARNING per skipped check, then `WAIT`:
  `Autoscale check skipped: database was locked (checking again in {period})`, with the period
  formatted as the `wait` trace formats it. No traceback, no CRITICAL. A check is either skipped at
  `count_failures` or at `check_zones`, never both, so it logs at most once.
- **Why per check, not per read:** every read a check makes is in one of two states, all before any
  state change, so two `try` blocks cover every read today and every read added to `check_zones`
  later, and "one WARNING per skipped check" falls out. [02 §3]
- `AutoScalingCluster.run_with_exceptions`, `AutoScalerThread`, the FINAL path and the
  `ClusterLaunchError` wording are unchanged: once the lock is released the next check reads, counts
  the carried exits, and the existing stop fires (R2).

### 2.3 The every-client-exited check reads again (R3, R4) — `cluster/watch.py::work_remains`

The database branch wraps both `scheduled_work(server)` calls: on an `OperationalError` that
`database_locked` accepts, log one WARNING and return `False`; anything else re-raised bare.
`watch_launched` is unchanged: its loop calls `work_remains` again after `WATCH_INTERVAL` (1 s), so
"read again" is the next pass, and a still-held lock produces one WARNING per failed read (each
after its own 5 s busy wait). Wording:
`Could not read whether tasks remain: database was locked (reading again)`. Once the lock is gone,
the two reads `SCHEDULE_SETTLE` apart find the work and #107's R1 error is raised: worst case after
the release ≈ 1 s + barrier + read + 2 s + read + the server stop, inside R1's 10 s (probe: ended
3 s after the release). [02 §4]

### 2.4 The `--no-db` predicate counts results (R5, R6) — `server.py::Receiver`, `cluster/watch.py`

- **`server.py`** (the only edit there): `Receiver.written: int`, `0` in `__init__`, its annotation
  commented ("results applied since the receiver started; written by its own thread only"), so no
  existing line changes. In `update_tasks`, `self.written += 1` as the last statement of the APPLIED tail (after the
  failure redirect), so DEFERRED, STALE and DUPLICATE results, which `continue` earlier, are never
  counted. Counted in both modes; in database mode nothing reads it. No new queue operation,
  thread, join, sentinel or payload: the §6 stop order and the wire format are unchanged. Only the
  receiver thread writes the int; the watch thread reads it under the GIL. [01]
- **Exactness:** the watch reads it only after `results_written(server)`, whose empty bundle is
  taken only once `update_tasks` for every earlier bundle has returned; the empty bundle itself
  deserializes to `[]` and adds nothing. [01]
- **`work_remains`, no-database branch** becomes: results written (as today); if the server has
  ended or has no submitter → False; if the submitter is alive (input still being read) → True;
  else `server.receiver.machine.written < server.submitter.task_count`. `task_count` is the loader's
  count of tasks put on the queue (`submit.py:989-992`); once `LiveSubmitThread` has ended it is
  final. `<`, not `!=`: a duplicate result, which no `hs cluster` path can produce (each run mints its
  own key; eviction reverts nothing in memory; hand-backs are refused in memory), would fall to the
  pre-#107 ending rather than a false R1 error. [01]
- **`MEMORY_SETTLE` is removed**, with the `memory_settle` parameter, its `__all__` entry and the
  "known gap" docstring paragraph; the docstring says what the count does instead. Its only job was
  letting a finished server end before "alive" counted as work; the count makes that exact. Nothing
  outside `watch.py` uses it (the tests replace `work_remains` with one-argument lambdas). R6 gains
  ≈ 3 s of margin (probe: `nodb-fail` 9 s → 4 s, start-up included). [01]
- **R5's ending:** with the count equal and the input ended, `work_remains` is False, the watch keeps
  polling, `record()` has already warned about the non-zero exit (#107 R2), and the server ends when
  it evicts the client (600 s), exiting as it did before #107. No prompt end (Non-goal).

### 2.5 Not changed

`demand.py`, `data/` (no busy timeout or pragma change), `submit.py`, `core/*`, `relay/*`,
`cluster/topology.py`, `cluster/__init__.py`, `cluster/launch.py`, the server's scheduler, heartbeat
and stop path, docs, completions, configuration. The new WARNING lines are log output only.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `database_locked` (2.1); `AutoScaler.check`/`check_fixed`/`check_dynamic` catch → `skip_check()` one WARNING, `WAIT`, `last_check` already advanced (2.2) |
| R2 | `AutoScaler.unchecked` carries exits to the next counting check; `count_failures` reads before it mutates; the existing FINAL → `ClusterLaunchError` stop; a working fleet keeps scaling (2.2) |
| R3 | `work_remains` catch around `scheduled_work` → WARNING, False; `watch_launched` re-polls every 1 s (2.3) |
| R4 | Bare `raise` for every error `database_locked` refuses, in all three catch sites (2.1–2.3) |
| R5 | `Receiver.written` (2.4); `work_remains` no-db branch: input ended and `written >= task_count` → no work |
| R6 | Same predicate: input still read, or `written < task_count` → work, R1 raised; `MEMORY_SETTLE` removed (2.4) |
| R7 | P1's tests, red on `9c82842d` (§6, TECH P1) |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before research
(gate #1) and again on this design (gate #2). Gate #1 found the intent sane: §4, §5, §6, §11, §12
touched; nothing in §1–§3 (no task query or retry change), §7–§10.

- **§4 Server modes** — The Receiver's count is incremented in both modes and written to no
  database; the in-memory branch of `update_tasks` (every result APPLIED) is unchanged. The watch
  reads the count only when `server.scheduler is None` (in-memory).
- **§5 Concurrency** — No new state, thread or blocking op in any FSM. `AutoScaler` gains no state
  enum member; `check`/`check_fixed`/`check_dynamic` still return a next state. The skip returns
  `WAIT`, which sleeps 1 s per pass as today (the shutdown bound is unchanged). The Receiver's counter
  is a plain int assigned by its own thread. `stop()` overrides are untouched.
- **§6 Shutdown/sentinel ordering** — Nothing in `ServerThread`'s stop, `dispatcher.finish()`, the
  reverts or the receiver/confirm sentinels changes; the counter adds no queue put/get. The barrier
  in `results_written` is reused as is.
- **§11 Cluster orchestration** — "A cluster whose launched clients are all gone with work left exits
  5": unchanged rule, now exact for `--no-db` (the documented FINISHED gap closes). Autoscaling's
  failed-launch count: same rule, with exits carried across skipped checks. Relay fleets
  (`failed_launches` 0) still never stop for it; `hs server --autoscaling` is untouched (catch kept
  out of `demand.py`). The autoscaler still never terminates clients.
- **§12 Conventions** — Tests `@mark.unit`/`@mark.integration` only; no spec ids in source;
  declarative comments; no new exit-status literal (exit 5 stays `ClusterLaunchError`'s mapping).
  No CLI change, so no snippet or completion edit; no user-visible prose change (log lines only), so
  no docs page or `versionchanged` marker. The PR note (drafted by `hs-publish`) records the fix.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `server.py` edited (high-blast-radius) | R5/R6 need an exact result count, which only the Receiver knows | Inferring from queue counters: `task_done()` precedes the write, so they are no barrier; keeping `MEMORY_SETTLE` and its gap is what R5 forbids. Accepted at the GOAL gate; three lines, no order change |
| A lock helper in `cluster/watch.py`, not `data/` | Keeps the change inside `cluster/` plus the one `server.py` counter | Reusing `data/upgrade.py::_is_locked` would widen the match to PostgreSQL and touch the upgrade path |

## 4. Rabbit holes (resolved)

- **Does the session survive a locked read?** Yes, no rollback needed; a read whose lock is released
  mid-wait succeeds ([`research/02-locked-reads.md`](research/02-locked-reads.md) §1).
- **Will a skipped check drop exits?** Yes today: `check()` removes exited processes before the read
  that fails. Carried in `unchecked` (02 §3).
- **Is the Receiver count exact against the barrier?** Yes; the empty bundle adds 0 and is taken only
  after every earlier write ([`research/01-receiver-count.md`](research/01-receiver-count.md)).
- **Can the drives hold a lock without the server's own threads meeting it?** Probed at plan time
  with the design emulated in a `sitecustomize` patch (scratch only, not the build): the
  `--launcher false` autoscaling drives, the launcher-mode drive and the `--no-db` drives pass, each
  twice. The working-launcher drive failed while a client was connected: the scheduler reads on
  every client request, met the lock and died with its own traceback (the Non-goal's territory), at
  any `--poll`. It passes, twice, once the lock is taken only after every task is done, the client
  has left (`-T 1s`) and the scheduler's TRACE log says it is backing off 16 s or more (`-Q 60s`),
  so it sleeps through a 9 s hold. Recorded in the drive's header ([`drive/followups_drive.sh`](drive/followups_drive.sh)).
- **Short busy waits for unit tests without changing defaults:** each in-process test binds the
  scoped `Session` to its own engine with `connect_args={'timeout': 0.5}`, so a locked read fails in
  0.5 s; subprocess tests keep the product's 5 s ([`research/03-tests.md`](research/03-tests.md) §2).
- **Locking deterministically in the CLI tests:** the holder waits for the launch line and a staged
  task (the scheduler then waits on demand and reads nothing), and a gated launcher exits only once
  the lock is held, so the first failed exit and every decision read after it fall under the lock on
  any machine (03 §2). The prototypes, red 3 of 3 on the base, are kept in
  [`research/prototypes/`](research/prototypes/).

## 5. Risks & open questions

- **Server threads under a lock (Non-goal, unchanged).** A lock held past 5 s while clients are
  active still ends a run through the scheduler, receiver or heartbeat monitor. This cycle makes the
  cluster's own checks survive; it does not make a locked run survive. The working-launcher drive is
  shaped around that (§4); the PR note should say so plainly.
- **A locked check blocks the autoscaler up to 5 s** (the busy wait), pre-existing, unchanged; at
  `-P 2s` checks space out to ≈ 7 s while locked.
- **`MEMORY_SETTLE` removal (sign-off).** R1 fires ≈ 3 s sooner in `--no-db`. A launcher that exits
  non-zero while its clients still run elsewhere with tasks unreturned gets the R1 error sooner, as
  in database mode; the verdict is unchanged.
- **Relay site fleets inherit the skip** (GOAL gate: acceptable). No separate requirement or drive.
- **Warning wording** is new log text; the drive and tests match `database was locked` only.

## 6. Verification strategy

- **Tests first (P1, R7)**, red on `9c82842d`, none hanging: see TECH P1 for the list and the
  expected red set. Locked cases use a real `BEGIN EXCLUSIVE` from a second `sqlite3` connection on
  the test's own database file; in-process tests shorten the busy wait through their own engine, and
  subprocess tests keep the default. Every subprocess has a timeout and is stopped by PID; every
  thread join is bounded.
- **Drive:** [`drive/followups_drive.sh`](drive/followups_drive.sh), six modes, run in
  `.agents/factory/bin/temp_site.sh`. Dry-run at plan time on `9c82842d`'s code: `locked-scale-fail`,
  `locked-scale-off`, `locked-scale-ok`, `locked-launch` and `nodb-crashy` fail as the defects
  predict (traceback file / `ClusterLaunchError` at 5 s); `nodb-fail` passes (R6 holds today). With
  the design emulated, all six pass.
- **Gate:** the whole suite serially under Python 3.14 (`.venv`) and 3.11, every drive mode, and
  `tests/test_cluster_exit.py` / `tests/test_autoscale_failed_launches.py` unchanged in what they
  assert. No docs build (no docs change); the gate checks `git diff --quiet 9c82842d -- docs share`.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-16)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

- **D1:** accepted. `MEMORY_SETTLE` goes; without a database, the launch error fires sooner, with the
  same verdict.
- **D2:** accepted. The working-launcher drive takes the lock only once the server is idle. A lock
  held while clients work still ends the run through the server's own threads (a Non-goal; the
  standing seed is `issues/coordinator-resilience-retry-backoff.md`). The PR's description says so
  plainly.
- **D3:** accepted. R6 rests on existing tests.
- **D4:** accepted. The lock helper goes in `cluster/watch.py`, with two new test files.
- **D5:** amended. Every phase uses a Python 3.14 environment, not the worktree's 3.15 `.venv`, which
  is outside the supported range. P4 runs 3.14 and 3.11 as planned.
- **`server.py`:** additions only (the `Receiver.written` counter), as P3's verify enforces. The
  review's human gate applies as the rubric says.

## Review-cycle-1 rulings (2026-11-21)

Review cycle 1 approved at `836ae5bc` with no CONFIRMED finding; the human gate was not triggered.
Foreman's triage of the two remaining items, under the maintainer's standing mandate:

- **F2 (PLAUSIBLE, docs).** Fix it. Behaviour differs from the stable line, where a locked SQLite
  database during an autoscaling check ended the run with a traceback, so `AGENTS.md` § Docs calls
  for a marker. Add one `versionchanged:: 2.9.0` clause in `docs/cli/cluster.rst`, beside the
  autoscaling include, saying that a check finding the database locked is skipped with a warning and
  retried at the next period. `hs server --autoscaling` is untouched by this cycle (no change in
  `demand.py`), so `docs/cli/server.rst` gets none. This is a marker, not a new page, so the GOAL's
  "no doc page" stands.
- **F1 (coverage note).** Add a command-line test for R2's working-launcher clause: a lock taken once
  the server is idle, every task done, the run still serving, one WARNING, no traceback. It must be
  red on `9c82842d`, or else name the base behaviour it pins.
