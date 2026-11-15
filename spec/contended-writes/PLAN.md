# PLAN — Contended writes: the `retried` mark and `Task.cancel_all` land when contended; concurrent first submits share the reserved source row

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

The Scheduler's `retried` mark moves onto `Task.__write_rows`, the per-row re-run helper the server's
other task writes already use, as a conditional write ("the row still awaits retry"), and it shares
one transaction with the retry rows: the marks run first, only the retry rows whose mark landed are
inserted, and one commit ends it. A contended mark is re-run; one still contended at the bound is
logged and left retryable with no retry row; a stop anywhere before the commit leaves nothing.
`Task.cancel_all`/`cancel` move onto the same helper unconditionally and return what they cancelled.
`Source.reserved` inserts with `ON CONFLICT (id) DO NOTHING` and re-reads. Tests come first, in a
commit of their own that CI shows red; the fix is two small slices in `data/model.py`; AGENTS.md and
the invariants lose the two exceptions in the same change. The in-server-contender question the
GOAL left open is answered by probe: yes, narrowly (§4).

## 2. Design

### 2.1 `Task.__row_statements` / `Task.__write_rows` (`src/hypershell/data/model.py`)

Three additions, each optional and defaulting to today's behaviour, so every existing caller is
untouched:

- **Condition `retryable`.** The UPDATE matches `id` and the row still awaiting retry: `retried IS
  FALSE`, `exit_status IS NOT NULL`, `exit_status != 0`, `exit_status != CANCEL_STATUS`. The lookup
  SELECT applies the same clauses and reads `id`: no row means gone or no longer awaiting retry
  (cancelled, revived, marked by another writer, deleted), which is skipped at DEBUG
  (`Ignored {path} for task ({id}) - task gone or no longer awaiting retry`), never raised on; a row
  found is re-run. These four clauses are the row-state filters of `Task.query_retryable`; build them
  once in a private helper that `query_retryable` also uses (same SQL as today), so the "change them
  together" rule of invariants §3 holds by construction. Selection scoping (`attempt < attempts`,
  group, zone) is not re-asked: the selection applied it and no contender in scope changes it.
  `__write_rows` takes it as `retryable: bool = False`, beside `pending`/`held_by`/`unscheduled`.
- **Per-row values, `each: Optional[Dict[str, Dict[str, Any]]] = None`.** Values for one id, merged
  over the shared `values`; every entry has the same keys (a `ValueError` otherwise), so the
  statement is still built once per column set. The mark passes `{'retried': True}` shared and
  `{'next_id': <retry id>}` per row.
- **`commit: bool = True`.** With `False` the call leaves its transaction open for the caller to
  extend and commit; on an exception it still rolls back and re-raises (all of the caller's
  transaction goes, which is the point).

The bound, the DEBUG per extra attempt, the WARNING at the bound (`Contended {path} write did not land
for task ({id}) after N attempts - {consequence}`) and the gone-row rule for `condition=None` stay as
they are.

### 2.2 The retry path: `Task.__schedule_next_failed_tasks` (R1, R2)

1. `select_failed(...)` as today; mint one retry `Task` per failed row with `Task.new` (ids minted in
   Python, UUIDv7 where the `timescale` provider requires it, as today).
2. Mark: `__write_rows(failed ids, {'retried': True}, 'retry mark', 'task left to retry later',
   retryable=True, each={failed id: {'next_id': retry id}}, commit=False, prefix='m_')`.
3. `Session.add_all` only the retry rows whose failed row's mark landed, then `Session.commit()`;
   any exception rolls back and re-raises.
4. Return those retry rows, in selection order; `Task.next` stamps them for dispatch as today
   (its own transaction, unchanged).

Consequences: one transaction holds every mark and every retry row, so a kill before the commit
leaves neither, and the failed row is selected again next round (R2). No row is marked without its
retry row, nor inserted without its mark. A row at the bound gets a WARNING naming it, no retry row,
and stays `retried = FALSE`; it holds its group exactly as an unretried failure does (non-goal). A
row cancelled, deleted, revived or marked meanwhile gets no retry row. The Scheduler never sees an
exception from a contended mark, so the server keeps running (R1). Mark-first order is deliberate:
no foreign key ties `next_id` to a row (`model.py:363-370`), and on SQLite it keeps the file's write
lock free until the first mark, which the fault-injection tests rely on. Uncontended cost: one
UPDATE per failed row (the old bulk mark was one executemany row per failed row) and one commit
fewer.

### 2.3 `Task.cancel_all` / `Task.cancel` (R3)

`cancel_all(ids) -> List[str]`: `__write_rows(ids, {'schedule_time': now, 'completion_time': now,
'exit_status': CANCEL_STATUS}, 'cancel', 'task not cancelled', prefix='c_')`; returns the ids
cancelled. A gone row raises `StaleDataError` and nothing of the call is committed (as today, now
with a rollback); a contended row that exists is re-run; one not written at the bound is logged and
left out of the returned list, so the caller compares the list with its ids. `cancel(id) -> bool`
returns whether it cancelled. What a cancel overwrites is unchanged (any state; non-goal). The
docstrings say all of this; there is no prose docs page for these methods.

### 2.4 `Source.reserved` (R4)

Look up (`from_id(caching=False)`) as today; on a miss, build `INSERT … ON CONFLICT (id) DO NOTHING`
for the bind's dialect (`sqlalchemy.dialects.sqlite.insert` / `sqlalchemy.dialects.postgresql.insert`
by `Session.get_bind().dialect.name`; any other dialect keeps today's plain insert), execute, commit
(rollback and re-raise on error), and re-read with `from_id(caching=False)`. The INSERT's rowcount is
never read (PostgreSQL reported `-1`). `source` has its primary key on `id` on fresh, 2.8.3-upgraded
(`data/upgrade.py` creates it from `CreateTable`) and `timescale`-provider databases (only `task` is
a hypertable). SQLite's upsert clause needs SQLite 3.24 (RHEL 8: 3.26). No caller changes.

### 2.5 Tests (R5)

All in existing modules; every new test tagged `unit` or `integration`.

- **Fault injection below the ORM** (`tests/test_contended_recovery.py`). The existing `Contention`
  wraps `Session.execute`, which today's bulk mark and bulk cancel never call, so on the base build a
  lost mark would silently land and the red would be about instrumentation. A new driver-level
  injector, installed on the per-test engine through the `do_execute`/`do_executemany` dialect
  events, makes the chosen task's UPDATE (the mark, or the cancel) match 0 rows by executing a 0-row
  UPDATE in its place (an executemany drops that task's parameter set), optionally committing another
  writer's change first on a separate connection (`meanwhile`), and records each statement per task
  id. On the base build the bulk path then raises `StaleDataError`, the defect itself; after the fix
  the per-row path re-runs. Tests: the mark lands on a re-run; at the bound it leaves the row
  retryable with no retry row and a WARNING, and the next round retries it once; it skips a row
  cancelled, deleted, or marked `retried` meanwhile (no retry row); the cancel lands on a re-run; at
  the bound `cancel_all` returns the other ids and warns, and `cancel` returns `False`. A stop
  between the two writes is a `BaseException` raised by a `before_cursor_execute` hook at whichever
  of {the retry row's INSERT, the mark's UPDATE} comes second, then the connection dropped: no retry
  row survives and the next round retries once (red on the base build by assertion). Guards, green
  before and after: an uncontended mark and an uncontended cancel cost one UPDATE per row with no
  re-check (the existing tests' pin), and a cancel of a gone row raises `StaleDataError` and cancels
  nothing.
- **Real race** (`tests/test_contended_write.py`, existing `contend` harness): a holder updates the
  failed row (`client_host`, as `hs update` might) while `Task.next(attempts=2)` marks it; once
  released, the mark lands, `Task.next` returns the one retry row, and the row has exactly one retry.
  Likewise `Task.cancel_all` on a held row. They assert outcomes only, not the new return value, so
  they pass on local PostgreSQL 18 and TimescaleDB 2.32 before and after the fix (GOAL Clarifications)
  and fail before it only on CI's TimescaleDB 2.31 jobs. The "no longer awaiting retry" cases are not
  real-race tests: the base build's unconditional mark would make them red locally too.
- **Two sessions, both missing the lookup** (R4): two threads (the scoped session is per thread) call
  `Source.reserved`; a `before_cursor_execute` barrier on `INSERT INTO source` holds each at its
  INSERT until both have looked up and missed. Both must return the reserved row, with one row in
  the table. SQLite (`tests/test_source.py`): fresh and the 2.8.3 fixture upgraded by
  `upgrade_schema`, for `<direct>` and `<stdin>`. PostgreSQL (`tests/test_contended_write.py`, so
  CI's PostgreSQL jobs run it): fresh under the entry's provider, and the `hs283` template upgraded
  under the `postgres` provider when `HS_TEST_POSTGRES_TEMPLATES` lists it (CI) and skipped otherwise.
- **The red list**: [`red.txt`](red.txt) (checked by `spec/relay-hardening/drive/red_check.sh`
  against the base `92af9c1`) and [`ci-red.txt`](ci-red.txt) (CI-only), judged on CI by
  [`drive/ci_check.py`](drive/ci_check.py).

### 2.6 AGENTS.md and invariants (R6)

- AGENTS.md § Task lifecycle: drop "a process killed between the retry insert and the `retried`
  mark" from the unguarded list (two cases remain), and the pre-2.9 `UNIQUE` sentence's "three cases"
  becomes two.
- AGENTS.md § Data layer specifics: add the retry mark (conditional on still awaiting retry,
  committed with its retry row) and `Task.cancel_all` (by id; raises on a gone row; returns the ids
  cancelled) to the pattern list; the closing sentence names only `hs update`, pointing at
  `issues/hs-update-contended-writes.md`.
- AGENTS.md § High-risk files: the `data/model.py` line names the mark committing with its retry
  row beside the `retried` filter.
- `.agents/factory/invariants.md` §1 (pattern list as above) and §3 (unguarded list), in lockstep.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | §2.1 `retryable` condition + re-run; §2.2 the mark through `__write_rows`; no exception reaches the Scheduler |
| R2 | §2.2 one transaction, marks first, only marked rows' retries inserted; bound → WARNING, no retry row, row retryable; no longer awaiting → skipped |
| R3 | §2.3 `cancel_all` on `__write_rows(condition=None)`; gone row raises; returns ids cancelled; `cancel` returns a bool |
| R4 | §2.4 `ON CONFLICT (id) DO NOTHING` + re-read on SQLite and PostgreSQL (both providers), fresh or upgraded |
| R5 | §2.5 driver-level fault injection, stop-between hook, real-race tests, two-session tests, cost pins; TECH P1/P2 show the red |
| R6 | §2.6, TECH P5 |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (before research): the change touches §1 (task lifecycle, the 0-row-match rule), §2 (the
retry selection's `CANCEL_STATUS` filter), §3 (retry model) and §12 (tests, conventions), all in
`data/model.py`. The GOAL is sane against them: it moves two writers onto the §1 pattern and closes
one §3 unguarded case. Gate #2 (on this design):

- **§1 state predicates.** No new state query; the mark's condition reuses `query_retryable`'s
  row-state filters verbatim (one helper). Cancel still writes `schedule_time`, `completion_time`,
  `exit_status = CANCEL_STATUS` together (terminal, never read as interrupted).
- **§1 0-row rule.** Both writes check their own count, re-run only while a fresh read says the row
  still qualifies (still awaiting retry / still exists), at most `CONTENDED_RERUNS`, DEBUG per extra
  attempt, WARNING naming task and path at the bound, safe outcome (no crash; no stranded task; no
  write without its condition); a first-attempt match is neither re-run nor re-checked. No
  `bulk_update_mappings` or ORM flush remains for either.
- **§2 / §3 `CANCEL_STATUS` filter.** The mark refuses a cancelled row in the statement itself, so a
  cancel that lands between selection and mark is never resurrected as a retry.
- **§3 retry model.** Retries stay new rows (`attempt + 1`, `previous_id`, `retried`, `next_id`);
  nothing is mutated in place. One retry per failed row now also survives a kill between the two
  writes; the Scheduler stays the sole `Task.next` caller; `query_retryable`/`select_failed`/
  `count_retryable`/`increment_group` keep the same SQL. Pre-2.9 SQLite `UNIQUE` constraints are
  never tripped (each mark names a fresh id; each retry row a distinct `previous_id`).
- **§3 group gating.** A row left retryable at the bound holds its group as any unretried failure
  does (GOAL non-goal: no group-gating change).
- **§4 `in_memory`.** No server path changes; `Task.next` is not called in `in_memory` mode.
- **§12.** Tests tagged; no CLI change, so no `docs/_include` or `share/` change; no version or
  dependency change (`on_conflict_do_nothing` is in the `sqlalchemy>=2.0.50` floor).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `__write_rows` gains `each=` and `commit=False` | The mark needs a per-row `next_id` and must share the retry rows' transaction (R2) | A second copy of the re-run loop for the mark would duplicate the §1 pattern the helper exists to hold in one place |
| A new driver-level injector beside `Contention` in the test module | The base build's bulk mark/cancel bypass `Session.execute`, so `Contention` could not make them miss: the red would be about instrumentation, not the defect | Reusing `Contention` alone gives a red on the base build only by statement-count assertions |

## 4. Rabbit holes (resolved)

- **Atomicity of mark + retry row** → marks first (conditional, per row), then INSERT only the landed
  rows' retries, one commit ([`research/01`](research/01-retry-mark-and-cancel.md)). Probed: today
  the two commits leave an orphaned, unscheduled retry row on a stop or a contended mark, and the next
  round adds a second retry row for the same failed row.
- **In-server contender after #82 R19 / #103 R1** → **yes, narrowly**. Every in-server writer is
  conditional on `completion_time IS NULL`, so one that starts after the failure's result commits
  never locks the row (probe control: no wait). But one that started before it waited on the
  Receiver's lock, re-checked to 0 rows, and **kept the row lock until its own transaction ended**,
  on PostgreSQL 18 and on a TimescaleDB 2.32 hypertable (`research/probes/pg_probes.py`): the mark
  blocked (`LockNotAvailable` under a 500 ms `lock_timeout`) until that writer committed. A late
  confirmation racing its own failed result is the main case; an eviction, staged or hand-back revert
  or a duplicate result can do the same. Whether 2.29–2.31 then miss after a lock-only holder is
  unverifiable locally and not needed: the mark re-runs on any 0-row match. Cross-process: `hs
  update`, Python API callers ([`research/01`](research/01-retry-mark-and-cancel.md)).
- **Conflict-safe get-or-create on every database** → `ON CONFLICT (id) DO NOTHING` + re-read,
  probed on SQLite fresh and 2.8.3-upgraded, PostgreSQL 18 and TimescaleDB 2.32
  ([`research/02`](research/02-source-row.md)).
- **Reproducing the real miss** → only CI's TimescaleDB 2.31 jobs; locally the red is fault
  injection, and P1 is a tests-only commit the foreman pushes to a draft PR (TECH P2).

## 5. Risks & open questions

- **Decision (foreman): plain PostgreSQL jobs at the tests-first commit.** The GOAL's Evidence plan
  says both "the two-session tests fail on every job" and "`timescale-floor` and the plain PostgreSQL
  jobs green throughout". The PostgreSQL two-session test must fail before the fix (R5), and it runs
  on every PostgreSQL job, so `pg13`, `pg18` and `timescale-floor` are red at P1, by that test alone.
  This plan reads "green throughout" as "the real-race tests never fail there"; `ci_check.py red`
  checks exactly that per test.
- **Decision (foreman): API return values.** `cancel_all` returns `List[str]` (the ids cancelled)
  and `cancel` a `bool`, where both returned `None`. Additive, no in-tree caller depends on `None`.
- **Risk: CI 2.31 may not show the mark/cancel race red.** The same holder pattern was red for the
  dispatch stamp and Confirm on 2.31; if P2 shows them green on the base, P2 stops and the foreman
  decides (the fault-injection red still stands).
- **Risk: the injector's id matching.** SQLite binds the `UUID` column as text; the driver-level
  injector finds the task id among a statement's parameters by its string form. P1 pins it by
  showing each listed test red on the base for the defect's own error (`red_check.sh`).
- **Observation, out of scope:** `Task.next(eager=True)` returns a just-committed retry row twice
  (research/01, aside); unchanged here. Candidate seed for the foreman.
- **Optional (foreman):** one clause in AGENTS.md § Data layer specifics recording the probe result
  (a conditional write that waited on another writer and re-checked to 0 keeps the row lock until its
  transaction ends). Not required by R6; left out unless asked.

## 6. Verification strategy

- **P1 (tests only):** `src/` unchanged from `92af9c1`; every [`red.txt`](red.txt) line red on the base
  for the defect (`red_check.sh`, SQLite and the local PostgreSQL); the whole suite otherwise green
  (`drive/rest_green.sh`), and `tests/test_contended_write.py` otherwise green on local PostgreSQL 18
  and TimescaleDB 2.32, real-race additions included.
- **P2 (CI red):** the draft PR's run at the P1 commit, recorded under `evidence/ci-red/`, judged by
  `drive/ci_check.py red`: the `tests (3.x)` jobs fail exactly the SQLite red lines; every PostgreSQL
  job fails exactly the `pg:` lines (plus the upgraded-template case); `timescale` and
  `timescale-oss` also fail the two real-race tests; nothing else fails.
- **P3/P4:** the listed tests green; the contended modules green on SQLite, local PostgreSQL 18 and
  TimescaleDB 2.32; `red_check.sh` still red on the base (the tests still prove the defect); the
  drives: `drive/retry_drive.sh` (every failed row exactly one retry row; `hsx -r 1`, eager and not)
  on SQLite and the local TimescaleDB; `drive/concurrent_submit.sh` (six concurrent first `hs submit
  -f -`, all exit 0 with every task) on fresh SQLite, 2.8.3-upgraded SQLite and fresh PostgreSQL, 20
  rounds each (before: 3/20, 2/10, 7/10 failing rounds).
- **P5:** AGENTS.md/invariants greps; full suite; both contended modules on both local servers.
- **P6 (CI green):** the draft PR's run at the last code commit, every job green (`ci_check.py green`).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*

## Plan-gate rulings (2026-11-14)

Foreman, gate judge under the maintainer's mandate. Approved as planned.

1. **Plain PostgreSQL jobs at the tests-first commit.** "Green throughout" reads as "the real-race
   tests never fail there". The two-session source test is expected red on every PostgreSQL job
   before the fix, as R5 requires. `ci_check.py` checks it test by test.
2. **New return values** (`cancel_all` returns the ids cancelled, `cancel` a bool) are approved.
3. **Only the P1 commit is pushed for P2.** Nothing past it is pushed until P2's CI run is recorded.
   P3 may be built locally meanwhile.
4. **If CI on TimescaleDB 2.31 does not show the mark or cancel race red,** P2 stops for the
   foreman's call. The fault-injection red stands either way.
5. **The optional AGENTS.md lock-retention clause** is left out.
6. **Eager `Task.next` returning a retry row twice** is already seeded
   (`issues/eager-retry-double-bundle.md`). Out of scope.
