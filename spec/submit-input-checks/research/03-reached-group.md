# Research 03 — The reached group: definition, computation, C9 default, R7 timing

## Question

Define "reached group" (GOAL Terms, seed Q3) precisely enough to implement R6/R7 on SQLite and
PostgreSQL. Settle how exhausted failures, pending retries, cancelled rows, retry rows and old
non-integer groups count. Then fix `hs submit`'s default group (C9) and say when R7 reads the value.

**Design rule (the asymmetry).** An over-estimate refuses a submission that would have run in
order. That blocks the user, and Q3 rejected exactly this. An under-estimate accepts a submission
that may run late, which falls back to the shipped late-group warning (#74,
`Scheduler.mark_group_started`). Every choice below picks under-estimating whenever the columns
cannot prove the group was reached in the current pass.

## Findings

- **Nothing persists the scheduler's progress.** `Scheduler.highest_started_group`,
  `last_scheduled_group` and `current_group` exist only in memory (`server.py:143-146`, `170-173`).
  The schema has only `Task`, `Client`, `Source` and `Zone` (`data/model.py:337, 1700, 1815, 1940`),
  and none of them records a group. The reached group has to be derived from task columns. Candidate
  (c), reading persisted scheduler state, is not available without a schema change, and none is
  proposed.
- **Groups are global.** `Scheduler.next_tasks` picks the first group "across every zone"
  (`server.py:336-352`), and `increment_group`/`lowest_incomplete_group` take no zone
  (`model.py:684, 752`). The reached group is therefore global too, and `--zone` does not scope it.
- **Lifecycle columns** (invariants §1): incomplete means `completion_time IS NULL`. Retries are new
  rows with `previous_id` set and `submit_time` stamped when the scheduler creates them
  (`model.py:874-905`). Only the scheduler creates them, and only for the group it is loading.
- **A cancel stamps `schedule_time`.** `hs update --cancel` sets
  `schedule_time = completion_time = now` and `exit_status = -1` (`task.py:1122-1134`), as does
  `Task.cancel_all` (`model.py:1449`). An unscheduled row that is cancelled therefore *looks*
  scheduled. As a side effect, `Task.current_group()` (newest `schedule_time`, `model.py:654-682`)
  returns a cancelled row's group, which is a pre-existing quirk that probe S4 shows.
- **Clocks.** `submit_time` comes from the submitter's host and `schedule_time` from the server's.
  `completion_time` comes from the client's host (`client.py:788, 795, 843`). On SQLite,
  `DateTime(timezone=True)` is stored as naive local text (`'2026-11-21 12:00:00.000000'`, probed),
  and comparisons are string comparisons. PostgreSQL stores `timestamptz`.
- **SQLite text groups.** `max("group")` over a mix of integer, real and text rows returns the text
  (`'x'`), because SQLite orders values by type class with TEXT above all numbers (probe S11). A
  plain `max` would hand Python a `str`, which is the N1 crash class. Filtering on
  `typeof("group") = 'integer'` fixes it. PostgreSQL's column is always integer.
- **Indexes.** The available indexes are `index_tasks_unscheduled(group, schedule_time)`,
  `index_tasks_zone(zone, group, schedule_time, submit_time)` and `index_tasks_retries(exit_status,
  retried)` (`model.py:1682-1697`). Nothing indexes `completion_time` or `submit_time` alone, so
  "incomplete" is a full scan. `lowest_incomplete_group` and `count_remaining` already pay that cost
  on every scheduler pass.
- **Callers of `current_group()`:** `SubmitApp.run` (`submit.py:1449`), `Scheduler.start`
  (`server.py:239`), the autoscaler's fallback (`cluster/demand.py:106`,
  `cluster/remote.py:618`), and `Task.next` when no group is given (`model.py:835`; the scheduler
  always passes one). `cluster/topology.py:422` calls the `AutoScaler.current_group` method, not
  `Task`. Only the `submit.py` call changes for C9.
- **Develop #115** (`02f53777`) touched `Scheduler.load_bundle` and `results_landed`. Nothing below
  needs changes there or anywhere else in `server.py`.

## Definition

The **current pass** is the run of work since the last moment the database held no incomplete
task. Its start, T0, is not stored.

```
U  = min(submit_time) over rows with completion_time IS NULL       -- NULL: no reached group
R  = max(group) over rows that are
       (exit_status IS NULL OR exit_status <> -1)                  -- not cancelled
   AND [SQLite only] typeof("group") = 'integer'                   -- old text/real rows ignored
   AND (   (schedule_time IS NOT NULL
            AND (completion_time IS NULL OR schedule_time >= U OR completion_time >= U))
        OR (previous_id IS NOT NULL AND submit_time >= U) )        -- a retry row made this pass
reached group = R   (NULL when U is NULL or no row qualifies)
```

SQLAlchemy sketch, one statement on both dialects. Compute U once in a CTE, because the
scalar-subquery form below repeats U's scan three times on PostgreSQL (EXPLAIN below):

```python
u = select(func.min(Task.submit_time)).where(Task.completion_time.is_(None)).scalar_subquery()
q = (select(func.max(Task.group))
     .where(or_(Task.exit_status.is_(None), Task.exit_status != CANCEL_STATUS))
     .where(or_(and_(Task.schedule_time.isnot(None),
                     or_(Task.completion_time.is_(None), Task.schedule_time >= u, Task.completion_time >= u)),
                and_(Task.previous_id.isnot(None), Task.submit_time >= u))))
if DATABASE_DIALECT == 'sqlite':
    q = q.where(func.typeof(Task.group) == 'integer')
```

The function should be a `Task` classmethod, `Task.reached_group() -> Optional[int]`, which keeps
lifecycle predicates in `Task` (invariants §1).

**How each kind of row counts:**

- **Exhausted failure:** finished. Its `completion_time` is set, so it never opens a pass. Inside an
  open pass it still counts toward R, because it was scheduled. Counting it as unfinished would give
  a reached group for the life of any database with one old failure, which is the behaviour Q3
  rejected.
- **Pending retry:** counted once the scheduler has created the retry row. That row is incomplete,
  so it opens the pass, and the `previous_id` term counts its group even before it is scheduled
  (probe S8). A failure still awaiting its retry row counts as finished.
  - Why not more: `hs submit` cannot know the server's `-r`. Reading `config.server.attempts`
    over-refuses whenever the config is higher than the `-r` the run used, because an exhausted
    failure then looks retryable for the life of the database. Treating every unretried failure as
    pending is worse, for the same reason.
  - The window left out is a scheduler pass, or a stopped server whose run ended on a failure
    awaiting retry. On restart, `Task.starting_group` keeps the retry group (`model.py:735-750`),
    so a lower group accepted meanwhile runs after it with the late-group warning. That is the safe
    fallback.
- **Cancelled (`-1`):** excluded. This is required: probe S4 gives 9 without the filter and 2 with
  it. Tasks killed by SIGHUP share `-1` and are excluded too, which can only under-estimate.
- **Retry rows:** they carry their task's group (`model.py:894`). Their `submit_time` is the moment
  the scheduler was loading that group.
- **Text, real or NULL groups** (left by old releases): ignored on SQLite by the `typeof` filter.
  `group` is `NOT NULL`, so a NULL never reaches `max`. Nothing is migrated, per the non-goal.

## Why it never over-refuses / where it under-estimates

**Claim: every row the definition counts was scheduled at some moment at or after T0.** A row is
only ever counted for its group, so R can be no higher than the true reached group.

- **An incomplete row** has been incomplete since it was submitted. `completion_time` never returns
  to NULL, except through `hs update --revert` (below). So no "nothing incomplete" moment falls
  after its `submit_time`, which gives T0 ≤ every such `submit_time` and therefore T0 ≤ U.
- **A row in flight** (scheduled, not complete) is in the current pass by the same argument.
- **A row with `schedule_time ≥ U`, or `completion_time ≥ U`:** it was incomplete at a moment at
  or after U, and U ≥ T0, so it belongs to the current pass. With `completion_time ≥ U`, it was
  scheduled before it completed; that dispatch could itself fall before T0 only if the row was
  incomplete across T0, which is impossible.
- **A retry row with `submit_time ≥ U`:** the scheduler was loading its group at that moment, which
  is at or after T0.

**Where it under-estimates** (safe, so the late-group warning covers each case):

- A high group whose rows all finished before *every* currently incomplete row was submitted, while
  the pass was kept open by rows that have since finished. Probe S9: group 9 completes at t=5 and
  the only open row arrives at t=5.5, so R = 1 where the true value is 9. An exact T0 would need a
  window-function scan over every row's interval. It is not worth the cost for a case the warning
  already covers.
- The pending-retry window above, rows deleted by `hs update --delete` or by a relay's outbox, and
  cancelled `-1` rows that actually ran.

**Where the inputs can make it over-estimate** (risks, not logic):

1. `hs update --revert` on a *finished* row puts back an old `submit_time`, so U reaches into an
   old pass. This matches Q3's literal rule, since unfinished work does remain. The cure is
   `--cancel`, or a fresh database.
2. Clock skew between hosts larger than the gap between one pass ending and the next being
   submitted. With `hs cluster` the submitter and the server share a host and clock.
3. On SQLite, the DST fall-back hour: naive local text runs backward for an hour, so a pass started
   in the repeated hour can include the previous pass's rows. PostgreSQL is immune.

Risks 2 and 3 already affect `current_group()`'s ordering by `schedule_time` today.

## C9 default

The new default is `G = Task.current_group().value` (today's value). If `R = Task.reached_group()`
is not None and `G` is not an `int` or `G < R`, use `R` instead. Log the reason as today:
`'reached group (late lower group running)'`.

- With no reached group (empty database, every task finished, only exhausted failures), the default
  is exactly today's: probes S0, S3, S6 and S7 return `R = None`.
- With a reached group, the default changes only when today's value would be refused:
  - S5, a late lower group running: `G = 1`, `R = 5`, so the default becomes 5.
  - S4, a cancelled high group: `G = 9 ≥ R = 2`, so the default stays 9.
- The guard also stops an old SQLite text group (`'x'`) being auto-selected and inserted as the
  default. A text `G` with no reached group is today's behaviour; the plan may want R3 to cover it.
- A task with group R runs without a warning: `mark_group_started` warns only when
  `group < highest`.
- Leave every other `current_group()` caller alone. `Scheduler.start` and the autoscaler need "most
  recently scheduled" as the input to `starting_group` (invariants §11). `Task.next`'s default is
  unused.
- Untagged lines in a submitted file take the default, so with this rule a file without group tags
  is never refused either.

## R7 timing

- **The read happens before any revert.** `ClusterApp.run` evaluates `self.source`
  (`prepare_source` → `apply_source_gate`) as an argument to the launcher
  (`cluster/__init__.py:282-285`). That is before `ServerThread` is built, before the submitter
  inserts, and before `Scheduler.start`.
- **`Scheduler.start` reverts in-flight rows on every start with incomplete work** (`server.py:230-235`),
  whether or not `--restart` was given (`self.restart_mode` is only stored, `:167`). For file
  input, the scheduler starts only after the whole file is submitted (`start_threads`,
  `:1247-1261`).
- **Where R7 must read.** It goes in `prepare_source`/`prepare_json_source`, after the gate returns
  its skip set (novel-only for `--restart`/`--update`, all lines for `--repeat`; table at
  `submit.py:218-226`). It checks only the lines that would be inserted, and must run before the
  `GatedSource` is consumed. A same-file `--restart` onto a stranded database skips every line, so
  it is never refused, whatever R is.
- **Recommendation: R7 leaves in-flight rows out of R.** Drop the bare `completion_time IS NULL`
  disjunct; the `previous_id` term stays. Those rows are reverted to unscheduled before anything is
  scheduled, and then the lower group runs first, in order (probe S10: 5 with them, 0 without).
  - This only lowers R. Its one blind spot is a second live server on the same database, which is
    unsupported (invariants §3).
  - `hs submit` (R6) cannot tell a live server's in-flight rows from stranded ones, so it keeps
    them.
  - Expose this as `Task.reached_group(in_flight: bool = True)`.
- **`hs server FILE`.** It refuses `--restart` with FILE (`server.py:1901`) and has no gate (C10).
  R7 there checks every line of the file against R, read in the same place before `serve_from`. It
  takes the same `in_flight=False`, since its scheduler reverts on start too.

## Risks/unknowns

- **Racing a running server** (accepted by GOAL). R is read once, after the input is buffered (C6)
  and before the first insert. A server can advance between the read and the commits, and the
  late-group warning covers it.
  - Under-estimation means R6 can also accept a group that a live server's in-memory
    `highest_started_group` already passed. That is the same warning path.
- **`hs submit -g 1` while a late group 1 is running** (`R = 5`) is refused. Group 1 is already
  late, so this matches R6's letter. The C9 default avoids it for unnamed groups.
- **Pre-existing, out of scope.** An exhausted failure in group g halts any later pass that reaches
  g again, because `increment_group` counts the old row (`model.py:710-719`). R is unaffected.
- **Cost.** The full scan matches the cost `lowest_incomplete_group` already pays on every
  scheduler pass. No new index is needed, and none is recommended: a schema change is out of scope.
  Use the CTE form, or two reads in one transaction, so PostgreSQL scans for U once rather than
  three times.

## Probe evidence

Built through the HyperShell model on a temporary SQLite file (throwaway site) and on a temporary
PostgreSQL 18 database, `sic_research_03`, which was dropped afterwards. Rows were inserted with
`Task.new`/`Task.add`, then their columns were set. `b` is the definition above, `b_r7` excludes
in-flight rows, `unfilt` has no cancel or `typeof` filter, `a` is candidate (a), and `cg` is
`current_group()`. Both dialects gave identical results for S0–S10.

| Scenario | b | b_r7 | unfilt | a | cg |
| --- | --- | --- | --- | --- | --- |
| S0 empty | None | None | None | None | 0 |
| S1 g0 done; g5 in flight + g5 queued (GOAL P3) | 5 | 0 | 5 | 5 | 5 |
| S2 pass 1 to g9 finished; pass 2 g1 running | **1** | None | 1 | **9** | 1 |
| S3 all finished | None | None | None | None | 9 |
| S4 pass in g2; queued g9 cancelled | **2** | 0 | **9** | 9 | **9** |
| S5 g5 done, late g1 running (C9) | 5 | 5 | 5 | 5 | **1** |
| S6 finished + exhausted failure g9 | None | None | None | None | 9 |
| S7 only a failure awaiting retry, no retry row | None | None | None | None | 9 |
| S8 retry row g9 queued, original done before U | **9** | 9 | 9 | 9 | 9 |
| S9 under-estimate case | 1 | 1 | 1 | 9 | 1 |
| S10 stranded: g0 done, g5 only in flight | 5 | **0** | 5 | 5 | 5 |
| S11 (SQLite) groups `x` text, `1.5` real, `5` int, all in flight | **5** | None | **'x'** | 'x' | 5 |

S2 shows (a) over-refusing. S8 needed the `previous_id` term: without it, b returned None. S11's
raw `SELECT max("group")` returns `'x'`.

**Scale** (1,000,000 rows, 60% complete, groups 0–9, best of 3):

| Engine | `reached_group` | `lowest_incomplete_group` |
| --- | --- | --- |
| SQLite | 65 ms | 57 ms |
| PostgreSQL | 78 ms | 46 ms |

The SQLite plan is `SEARCH task USING INDEX index_tasks_unscheduled`, plus a single evaluation of
each scalar subquery. The PostgreSQL plan is an `Index Scan Backward using index_tasks_unscheduled`
(max group first) plus three parallel sequential scans, one for each repeat of U, hence the CTE
advice.

Storage: SQLite `submit_time` is `TEXT` `'2026-11-21 12:00:00.000000'` (tz dropped); PostgreSQL
is `timestamp with time zone`.
