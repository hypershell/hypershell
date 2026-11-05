# PLAN — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

> **Status:** Draft for review · **Last updated:** 2026-11-04
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

When a `task`-row UPDATE matches 0 rows, HyperShell asks the database once whether the row still
qualifies, and if it does, re-runs the statement: a new statement under READ COMMITTED sees the
holder's commit and plain-matches, which sidesteps TimescaleDB 2.29+'s broken re-check. Confirm and
the eviction revert move off the count-asserting `bulk_update_mappings` onto a per-row `Task` helper
whose counts HyperShell checks; the Receiver's conditional result write gains the same re-run behind
its existing classifying SELECT. Every recovery is bounded (3 extra attempts), each extra attempt
logs at DEBUG, and exhaustion is safe: Confirm warns and keeps running, a result is held by the
Receiver and presented again, a revert warns and leaves the row in flight. A write that matches on
its first attempt runs exactly the statements it runs today, so SQLite, plain PostgreSQL and
TimescaleDB < 2.29 see no change. A new integration module forces the race with a holder connection
and observes the wait in `pg_stat_activity`; the foreman proves it red on `0e8275e` and green on the
fix with a runner script on TimescaleDB 2.31.0 and 2.24.0, and CI is the second proof. The
Scheduler's and `hs task update`'s writes are seeded, not fixed (the foreman's R4 ruling).

## 2. Design

### 2.1 The per-row helper (`src/hypershell/data/model.py`)

- **Constant.** `CONTENDED_RERUNS: Final[int] = 3`, module level, next to `CANCEL_STATUS` (not in
  `__all__`). A write makes at most `1 + CONTENDED_RERUNS` attempts.
- **Statements.** A private `functools.cache`d classmethod `Task.__row_statements(names: Tuple[str, ...])`
  returns, for one column set: `update(table).where(table.c.id == bindparam('r_id', type_=…))
  .values({name: bindparam(f'r_{name}', type_=table.c[name].type) …})` and
  `select(table.c.id).where(table.c.id == bindparam('r_id', …))`. Built once per column set, binds
  typed from the table, prefixed bind names, as `__result_statements` does (#73).
- **Helper.** `Task.__write_rows(ids: List[str], values: Dict[str, Any], path: str) -> List[str]`:
  the same `values` for every id (true of both callers). For each id: execute the UPDATE with one
  parameter dict; `rowcount == 1` → landed, next id. Otherwise execute the exists SELECT:
  - no row → `Session.rollback()` and raise `sqlalchemy.orm.exc.StaleDataError` naming the table
    and id. This is `develop`'s outcome for a missing row (the bulk path raises and nothing of the
    call commits), kept because R6 covers uncontended writes (§5, ruling 1).
  - row present, attempts left → DEBUG `Re-running contended {path} write for task ({id}) (attempt
    {k} of {1 + CONTENDED_RERUNS})`, re-run.
  - row present, no attempts left → WARNING (below), next id.
  One `Session.commit()` at the end of the call (one per Confirm bundle, one per revert pass, as
  today); any exception → rollback and re-raise, as `Entity.add_all` does. Returns the ids that
  landed. Trace lines as today's callers emit them.
- **Callers.**
  - New `Task.confirm_all(ids, client_id, client_host)` → `__write_rows(ids, {'client_id': …,
    'client_host': …}, 'confirm')`. Exhaustion WARNING: `Contended confirm write did not land for
    task ({id}) after {n} attempts - client not recorded`.
  - `Task.revert_all(ids)` keeps its column dict and moves onto `__write_rows(…, 'revert')`;
    returns the landed ids (was `None`; `Task.revert` ignores it). Exhaustion WARNING: `Contended
    revert write did not land for task ({id}) after {n} attempts - task left in flight`.
  - `revert_orphaned` and `revert_interrupted` keep their `while tasks := select_…(100)` loops but
    **stop when a pass lands nothing**, so a row left in flight at the bound cannot spin the loop
    (research 02, finding 2). Bounded by the row count.
- **Unchanged:** `Entity.update_all`/`update` (still used by `hs task update`, `Client`, the
  `retried` mark and `cancel_all`; `hs task update` maps its `StaleDataError` to `Task.NotFound`).
  `ClientInfo.transpose` stays in `client.py` (no `client.py` edit).

### 2.2 The result write (`Task.__result_statements`, `Task.__apply_result`, `ResultOutcome`)

- The prebuilt classifying SELECT becomes `select(table.c.completion_time).where(*same_dispatch)`
  (was: `id` where same dispatch and `completion_time IS NOT NULL`). Still one prebuilt object
  (`test_writeback_reuses_its_statements` holds).
- `__apply_result`: keep the `schedule_time is None` → STALE guard and both comments. Up to
  `1 + CONTENDED_RERUNS` attempts: UPDATE; `rowcount == 1` → APPLIED. Else run the SELECT: no row →
  STALE; `completion_time` set → DUPLICATE; `completion_time` NULL → the row still qualifies:
  attempts left → DEBUG (same text as 2.1, path `result`) and re-run; none left → `DEFERRED`.
  An uncontended STALE or DUPLICATE still costs exactly one UPDATE and one SELECT.
- `ResultOutcome.DEFERRED = 4`, docstring extended: the row still belongs to that dispatch but the
  write did not land within the bound; nothing was written, and the caller may present the result
  again. `apply_results` is otherwise unchanged (one commit per list).

### 2.3 Holding a deferred result (`src/hypershell/server.py`, `Receiver`)

- `Receiver.held: List[Task]` (initialised empty) — results whose row still qualifies but whose
  write did not land.
- `update_tasks`: `results = self.held + self.tasks`; the `in_memory` branch is unchanged (it never
  defers). After `apply_results`, `self.held` is rebuilt from the `DEFERRED` results. A result
  deferred for the first time logs WARNING `Contended result write did not land for task ({id}) after
  {n} attempts - result held for retry`; one presented again logs DEBUG `Retrying held result for
  task ({id})` before the call. Every other outcome is handled exactly as today (APPLIED → completion
  DEBUG, non-zero exit WARNING and `--failures`; STALE/DUPLICATE warnings).
- `unload_bundle`: on `QueueEmpty`, if `self.held` is non-empty, set `self.tasks = []` and go to
  `UPDATE` (so the last result of a run is retried without a new bundle); otherwise as today. The
  queue op keeps its finite timeout (invariant §5).
- `unpack_bundle`: on the sentinel, log WARNING `Result for task ({id}) not written at shutdown -
  task left interrupted` for each held result, then `FINAL` as today.
- **Why the run neither hangs nor double-runs (R5):** the row stays in flight under its dispatch,
  so nothing reschedules it; the result is presented on every Receiver cycle (each bundle, or each
  empty-queue timeout) and lands as soon as no writer holds the row. If eviction reverts the row
  meanwhile, it classifies STALE as an uncontended result would. A normal stop needs
  `count_remaining() == 0`, so the sentinel cannot arrive while a result is held unless the server
  is signalled or stopped; then the row is interrupted and reverted at the next start, as for any
  result in flight at a stop. In-server, a result write has at most two other writers (Confirm, the
  eviction revert), so the bound is exhausted only by cross-process writers.

### 2.4 Confirm (`server.py`)

`Confirm.update_info`: `Task.update_all(self.client_info.transpose())` →
`Task.confirm_all(info.task_ids, info.client_id, info.client_host)`, still under
`if not self.in_memory:`. Nothing else in Confirm changes.

### 2.5 Tests

- **`tests/test_contended_write.py`** (new, `@mark.integration`, `needs_server`): the R7 module of
  research 03. Fixture `contended_db` from `test_timescale`'s `ts_database`/`ts_site` (provider
  `timescale` when `HS_TEST_TIMESCALE` is set, else `postgres`; hypertable asserted when set), the
  in-process `Session` bound to it as `pg_tasks` does, ids from `uuid7()`. The helper
  `contend(engine, holder_sql, params, write)` takes the row lock on a holder connection, runs
  `write` in a thread (capturing its return value or exception, `Session.remove()` in `finally`),
  polls `pg_stat_activity` with `pg_blocking_pids` until that backend is blocked by the holder with
  `wait_event_type = 'Lock'` on an `UPDATE task` (20 ms poll, 30 s deadline, `pytest.fail` if the
  write finished first or never blocked), commits the holder, joins (30 s, `pytest.fail` on
  timeout). Five tests, names fixed by `check_evidence.py`:
  `test_rerun_after_contended_update_matches`, `test_contended_confirm_records_client`,
  `test_contended_result_applies`, `test_contended_result_refused_when_reverted`,
  `test_contended_orphan_revert_lands` (cases and expectations in research 03's table). Only symbols
  present on `0e8275e`.
- **`tests/test_contended_recovery.py`** (new, `@mark.unit`, SQLite): R5 and R6 by fault
  injection. A fixture wraps the scoped session's `execute` (`monkeypatch.setitem(vars(Session),
  'execute', …)`, as the statement guard does) so that the next *k* UPDATEs for a chosen task id
  return a 0-row result without executing; it records every statement. Cases: one contended attempt
  then landing (Confirm, result, revert) with the DEBUG line naming the id; exhaustion for each
  path (Confirm: WARNING, that row's client columns NULL, the bundle's other rows stamped,
  `update_info` returns `UNLOAD`; revert: WARNING, `revert_orphaned` returns, row in flight; result:
  `DEFERRED`, WARNING once, the next empty-queue cycle applies it with the normal completion
  logging, a sentinel with a held result logs the shutdown WARNING); a missing row raises
  `StaleDataError` and commits nothing; and the R6 counts (uncontended Confirm and revert: one
  UPDATE per row and no SELECT; uncontended APPLIED: one UPDATE; uncontended STALE/DUPLICATE: one
  UPDATE and one SELECT).
- **CI:** `.github/workflows/tests.yml`'s `postgres` pytest line gains `tests/test_contended_write.py`.
  No image, matrix or other change.

### 2.6 Docs and seeds (R8, R4)

- `AGENTS.md` § Data layer specifics: one bullet. Under the `timescale` provider on TimescaleDB
  2.29+, an UPDATE of a `task` row that waited on another transaction's row lock can match 0 rows
  although the row exists and qualifies; a 0-row match is therefore not proof the row is gone or no
  longer qualifies. A new task-row write checks its own count and, on 0, re-runs while a fresh read
  says the row qualifies, bounded by `CONTENDED_RERUNS` with DEBUG per extra attempt and a WARNING
  and a safe outcome at the bound (`Task.confirm_all`/`revert_all` and `apply_results` are the
  pattern); never `bulk_update_mappings`/ORM flush for a write another writer can contend.
  § High-risk files: one clause on `data/model.py`. `.agents/factory/invariants.md` §1: the same rule,
  in lockstep.
- New seed `issues/hypertable-contended-scheduler-and-cli-writes.md` (`status: unshaped`, `kind:
  fix`, `lane: public`) with a `ROADMAP.md` entry in Part I: the Scheduler's `Task.next` stamp (ORM
  flush) and `retried` mark, `Task.cancel_all`, and every `hs task update` write (legacy, `--limit`
  batches, `Query.update`). Evidence from research 02 (rows 3, 4, 7–10), including the in-server
  late-Confirm contender for the Scheduler and that an exhausted `retried` mark would double a retry.
  Cross-references `stale-confirm-hides-orphan`, `late-confirm-overwrites-attribution`,
  `orphan-revert-loses-result`, `coordinator-resilience-retry-backoff`; also notes the unprobed
  DELETE paths.

### 2.7 Evidence (`spec/hypertable-update-retry/`)

- `contention_run.py` (committed with this plan): runs `tests/test_contended_write.py` from this
  branch against any commit's `src/` on a given server, in a throwaway worktree with its own
  environment; prints a JSON summary (research 03; its docstring is the manual).
- `check_evidence.py` (committed with this plan): `r7` checks the foreman's four runs (develop and
  the fix, on 2.31.0 and 2.24.0: develop on 2.31.0 fails exactly the three red tests by assertion,
  everything else passes); `ci` checks the PR run (14 jobs green, each `postgres` entry passed all
  five contended tests and skipped none; the one documented 3.13 flake rerun allowed, as in
  `writeback-prebuilt-update`).
- `EVIDENCE.md` (build): the hand-back (P5), then the foreman's blocks (P6).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `Task.confirm_all` → `__write_rows` re-run (2.1, 2.4); `test_contended_confirm_records_client`; unit cases |
| R2 | `__apply_result` re-run behind the classifying SELECT (2.2); `test_contended_result_applies` (exit 3, `--failures` buffer) |
| R3 | re-run only when the fresh read qualifies the row; revert/confirm keep `develop`'s "row exists" condition (2.1, 2.2); `test_contended_result_refused_when_reverted` |
| R4 | audit table and verdicts in `TECH.md`; `revert_orphaned` (and `revert_interrupted`, `revert`) via `revert_all` (2.1); `test_contended_orphan_revert_lands`; the seed (2.6) |
| R5 | `CONTENDED_RERUNS`, DEBUG per extra attempt, the three exhaustion outcomes (2.1–2.3), the bounded revert loops; `tests/test_contended_recovery.py` |
| R6 | first-attempt matches issue today's statements only; missing row raises as today; no schema, wire, predicate or `in_memory` change; R6 count tests; existing suites |
| R7 | `tests/test_contended_write.py` + CI line (2.5); `contention_run.py` + `check_evidence.py` (2.7) |
| R8 | `AGENTS.md`, `invariants.md` (2.6) |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (before research) named §1, §3, §4, §5, §6 and §12; the GOAL is sane against all of them (a
re-run never weakens a write's condition). Gate #2, against this design:

- **§1 Task lifecycle.** No predicate changes. The result write's condition (`id`, dispatched
  `schedule_time`, `completion_time IS NULL`) is untouched; DUPLICATE/STALE classification is the
  same partition, now read from one column. Transition logic stays in `Task` classmethods
  (`confirm_all`, `revert_all`, `__write_rows`); `server.py` only calls them and holds results.
- **§2 `exit_status`.** Not touched; `DEFERRED` is a `ResultOutcome`, not an exit status.
- **§3 Retry model.** A result still writes only `Task.result_columns`, only for its dispatch; a
  stale result cannot land by waiting (R3). The `retried` mark and `Task.next` are not changed
  (seeded), so the one-retry-per-failed-row argument is untouched.
- **§4 Server modes.** Confirm's write stays under `if not self.in_memory:`; the Receiver's
  `in_memory` branch never defers, so `held` stays empty. `no_confirm` unchanged.
- **§5 Concurrency.** Receiver keeps its states and `HALT`; the new `UNLOAD → UPDATE` edge on an
  empty queue keeps the finite timeout; no new blocking op; no thread added; `stop()` overrides
  unchanged.
- **§6 Shutdown ordering.** Sentinel handling unchanged apart from a WARNING per held result
  before `FINAL`.
- **§12 Conventions.** No CLI change (no `docs/_include`/`share/` update); no version, dependency or
  schema change; new tests tagged `unit`/`integration`; declarative comments, no R/P ids in source.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| Confirm and revert issue one UPDATE per row instead of one executemany | a recoverable write needs a per-row count; the bulk path gives one total and raises | "executemany, then re-check the batch when short" re-checks rows that matched first time (R6) and cannot tell which rows missed |
| Receiver holds deferred results across cycles (`held`, an `UNLOAD → UPDATE` edge) | the signed-off R5: a result unapplied at the bound must neither hang the run nor run twice | STALE leaves the row in flight under a live client, and the non-`forever` Scheduler waits on it forever; reverting runs the task twice |
| A fourth `ResultOutcome` (`DEFERRED`) | the Receiver must tell "not written, still yours" from STALE | overloading STALE loses the hold and its distinct logging |

## 4. Rabbit holes (resolved)

- Does a re-run match after the contended 0? → Yes by READ COMMITTED's per-statement snapshot; not
  observable locally, so pinned by `test_rerun_after_contended_update_matches` on 2.31.0 in the
  foreman's runs and CI ([01](research/01-recovery-mechanism.md)).
- Which writes need it, and which get it for free? → Audit table in TECH.md
  ([02](research/02-write-path-audit.md)).
- How to make the race deterministic and observed without sleeps? → holder connection +
  `pg_blocking_pids` poll ([03](research/03-contention-test.md)).
- How to prove red on `0e8275e` when the tests exist only on the branch, with no local server? →
  `contention_run.py` overlays a commit's `src/` on this branch's tests ([03](research/03-contention-test.md)).
- Would the revert loops spin once revert stops raising? → Yes; they stop on a pass that lands
  nothing ([02](research/02-write-path-audit.md), finding 2).

## 5. Risks & open questions

Rulings wanted at the plan gate:

1. **Missing row (uncontended).** The helper keeps `develop`'s `StaleDataError` (Confirm dies, as
   today) because R6 covers uncontended writes. The alternative is a WARNING and skip, which no
   in-server path can reach (rows are deleted only by an external command). Default: keep.
2. **The Scheduler has an in-server contender** (research 02, rows 3–4): a late Confirm stamping a
   row eviction just reverted (which `Task.next` may be stamping), or a Confirm lagging a fast
   failure (which the `retried` mark may be writing). Losing raises `StaleDataError` in the
   Scheduler and the server exits at once. Rare (eviction plus a late confirm). Per ruling 3 it is
   seeded, not fixed: moving `Task.next` off the ORM flush is not the same few lines, and an
   exhausted `retried` mark would double a retry, which needs its own outcome. Default: seed.
3. **Per-row cost.** Confirm and revert become one statement per row. For bundle size 1 (the
   cluster runs) Confirm is unchanged; for large bundles on PostgreSQL it is one round trip per task
   in the Confirm thread, and `revert_interrupted` at start over a very large interrupted set
   likewise. Not measured; no CPU or throughput bar in this GOAL. Default: accept.
4. **New test module plus a CI line** rather than appending to `tests/test_timescale.py` (no
   workflow edit). The module runs on every `postgres` entry, not only TimescaleDB ones, and the
   runner targets one file. Default: new module.

Residual risks:

- **The premise is unproved until the foreman's 2.31.0 run.** If a re-run also matches 0 on 2.31.0,
  `test_rerun_after_contended_update_matches` fails there and the design is wrong; the cycle returns
  to planning (a lock-first fallback is the next candidate, research 01).
- **Pre-existing deadlock** between a Confirm bundle and a result bundle locking rows in different
  orders, on every PostgreSQL provider (research 02, finding 4). Not introduced or worsened here;
  noted in the seed for `coordinator-resilience-retry-backoff`.
- The R7 tests cannot run on the build machine; P1 can only prove they collect, skip cleanly, and
  import nothing absent from `0e8275e`. A wrong test is caught at P6 and costs a loop.

## 6. Verification strategy

- **Local (every build phase):** unit suites on SQLite, including the fault-injection module, which
  exercises every R5 branch and the R6 statement counts deterministically; the existing writeback,
  statement-guard, server, cancel and update suites; the contended module collecting and skipping;
  a CLI drive in a throwaway site (`hsx`, 100 tasks, one failure, `-b1 -f`): 99 succeeded, 1 failed,
  no row without a `client_id`, no stale/duplicate/contended/shutdown warnings.
- **Final local gate (P5):** the full suite.
- **External (P6):** the foreman runs `contention_run.py` four times — `0e8275e` and the fix, each
  on TimescaleDB 2.31.0 and 2.24.0 — and returns the four JSON summaries and logs; then pushes the
  branch and opens the draft PR so CI's five `postgres` entries run the module. `check_evidence.py
  r7 ci` must pass on the recorded blocks.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
