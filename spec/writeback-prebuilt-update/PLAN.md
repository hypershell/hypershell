# PLAN — Result writeback at or below 2.8.3's CPU cost

> **Status:** Draft for review · **Last updated:** 2026-11-04
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`Task.__apply_result` builds a new ORM UPDATE for every returned result, and SQLAlchemy coerces its 13
values and generates its cache key on every execute. The fix builds the conditional UPDATE, and the
classifying SELECT, once per process against `Task.__table__`, with bind parameters typed from the
table's own columns, and binds only each result's values. The SQL, the fence, the classification and
the single commit per bundle stay as they are. A deterministic test pins statement reuse, a
reference-equivalence test pins the stored values on every CI database, an in-process benchmark
script in `spec/` records the CPU per result, and the foreman's cluster runs close the cycle. The
whole source change is in `src/hypershell/data/model.py`.

## 2. Design

### 2.1 The prebuilt statements (`src/hypershell/data/model.py`)

- Add `bindparam` to the `sqlalchemy` import.
- Build, once per process and lazily or at import (builder's choice: a `functools.cache`d private
  `Task` classmethod or a module-level pair after the class), two Core statements against
  `t = Task.__table__`:
  - **update:** `update(t).where(t.c.id == bindparam('r_id', type_=t.c.id.type),
    t.c.schedule_time == bindparam('r_schedule_time', type_=t.c.schedule_time.type),
    t.c.completion_time.is_(None)).values({name: bindparam(f'r_{name}', type_=t.c[name].type) for
    name in Task.result_columns})`;
  - **select:** `select(t.c.id).where(t.c.id == …, t.c.schedule_time == …,
    t.c.completion_time.isnot(None))`, with the same two typed binds.
- **Every type comes from `t.c[name].type`**, never named by hand, so the dialect variants (`UUID`,
  `SMALL_INTEGER`) and `DateTime(timezone=True)` resolve exactly as the column-typed binds do on
  `bc6547a` ([02 §1](research/02-types-and-dialects.md)).
- **Every bind name is prefixed** (`r_…`): a bind named like a SET column is reserved by SQLAlchemy.
- Precompute the `(column, bind key)` pairs once, so a result costs one dict build.
- `Task.__apply_result` keeps its shape: the `schedule_time is None` → STALE early return, then
  `Session.execute(update_stmt, params).rowcount` with **one parameter dict** (a list would be an
  executemany with no per-row rowcount), `== 1` → APPLIED, else
  `Session.execute(select_stmt, {r_id, r_schedule_time}).first()` → DUPLICATE or STALE.
- `Task.apply_results` is unchanged: one commit per list, rollback and re-raise on error, one trace
  line per outcome. Its docstring stays true. A one-line declarative comment on the statements says
  why they are built once (each result binds only its values).

Nothing else in `src/` changes: not `server.py` (`Receiver.update_tasks` already calls
`Task.apply_results`), not `Entity.update_all`, the wire codec, the schema or `data/upgrade.py`.

### 2.2 Tests

- **New `tests/test_writeback_statement.py`** (all `@mark.unit`), with its own copy of the
  `task_db` fixture and the dispatch/finish/receive helpers from `tests/test_writeback.py` (copied,
  not moved, so that file stays byte-identical):
  - `test_writeback_reuses_its_statements` (**R4**): wrap `Session.execute` on the scoped session
    (pytest `monkeypatch`), keep every statement object it receives, and drive at least 8 results
    through `Receiver.update_tasks` across several bundles, with APPLIED, DUPLICATE and STALE
    outcomes. Assert that the UPDATE statements are one object and the SELECT statements are at most
    one object. Uses only symbols that exist on `bc6547a`, so it fails there by assertion.
    Committed with `xfail(strict=True)` in P1; the marker goes in P2.
  - `test_applied_values_match_reference` (**R3**, SQLite), parametrized over cases: offsets east
    and west of UTC, microseconds 0 and 999999, `None` `outpath`/`errpath`/`csvpath`, a non-zero
    exit. Two tasks get identical result values; one goes through `Task.apply_results`, its twin
    through a reference statement written in the test as `bc6547a` builds it (`update(Task).where(…)
    .values({…}).execution_options(synchronize_session=False)`). Assert the two rows' raw stored
    values (a `text()` select, no type processing) and their ORM-read result columns are equal, and
    the other columns are untouched. Capture the UPDATE's SQL with `before_cursor_execute` and
    assert its SET list is exactly `Task.result_columns` and its WHERE has the three predicates.
- **Append to `tests/test_writeback_postgres.py`** (existing lines untouched): the same
  reference-equivalence test under that module's `pg_tasks` fixture (both session time zones), raw
  values read through psycopg's own loaders. It runs in all five CI `postgres` entries.
- `tests/test_writeback.py` is **not edited** (R2).

### 2.3 Evidence in `spec/writeback-prebuilt-update/`

- [`cpu_bench.py`](cpu_bench.py) (written at plan time, run on the base): **R1's method**. It extracts
  `src/` for `b819de6` (2.8 stand-in), `bc6547a` and the fix with `git archive`. Each measurement is
  a fresh child, in its own session under a kill timer, that imports `hypershell` from one tree
  (`python -I`, tree first on `sys.path`, refusing any other import). It fills a throwaway SQLite
  site with 5,000 tasks, dispatches and finishes them through the bundle codec, and times
  `Receiver.update_tasks` on one-result bundles with `time.process_time`. One warm-up round is
  dropped, then six rotating rounds are recorded. The bar is fix mean ≤ 2.8 mean, and the exit status
  reports it. Offline, no install, one interpreter ([03 §1](research/03-measurement.md)).
- [`check_evidence.py`](check_evidence.py) (written at plan time): checks the JSON blocks in
  `EVIDENCE.md`: `r1` (the bench summary meets the bar at the final fix), `ci` (the PR's run: four
  Python entries and five PostgreSQL entries, all successful, at the final fix), and `r6` (the
  foreman's cluster runs).
- `EVIDENCE.md` (written by the build): the R1 method, table and `r1` block (P3); the R6 hand-back
  request (P4); the `ci` and `r6` blocks once the foreman delivers (P5).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | Prebuilt statements (§2.1); `cpu_bench.py` run interleaved, recorded in `EVIDENCE.md`, checked by `check_evidence.py r1` (P3) |
| R2 | `__apply_result`'s branches unchanged; `tests/test_writeback.py` unchanged and green; guard test drives mixed outcomes through `Receiver.update_tasks` (P1, P2) |
| R3 | Types from `Task.__table__.c`; same SQL text; reference-equivalence test on SQLite (local) and on pg13/pg18/timescale/timescale-oss/timescale-floor (CI, `ci` block, P5) |
| R4 | `test_writeback_reuses_its_statements`, red on `bc6547a` (P1), green on the fix (P2) |
| R5 | `src/` diff limited to `data/model.py`; no `mapped_column`/schema/wire change; `Entity.update_all` untouched; `in_memory`/`no_confirm` tests in `tests/test_writeback.py` unchanged (P2, P4) |
| R6 | Hand-back section in `EVIDENCE.md` (P4); foreman's `r6` block checked by `check_evidence.py r6` (P5) |

## 3. Invariant gate (AGENTS.md constitution check)

Gate #1 (before research) and gate #2 (against this design), per
[`.agents/factory/invariants.md`](../../.agents/factory/invariants.md):

- **§1 Task lifecycle.** The write still requires `completion_time IS NULL` and the dispatched
  `schedule_time`; the DUPLICATE/STALE read still requires `completion_time IS NOT NULL`. The
  predicates are the same expressions over the same columns. Logic stays in a `Task` classmethod.
- **§3 Retry model.** A result writes only `Task.result_columns`, only onto its current assignment;
  a stale or duplicate result writes nothing. Unchanged; the reference-equivalence test and the
  existing writeback tests pin it.
- **§4 Server modes.** `in_memory` never reaches `apply_results` (`Receiver.update_tasks` guard,
  untouched); `no_confirm` has no bearing on the write. Existing tests cover both.
- **§5/§6 Concurrency and ordering.** No FSM, thread or queue change. The shared statement is
  immutable, and each thread's scoped session executes it in its own transaction.
- **§12 Conventions.** No CLI change (no `docs/_include` or `share/` edits). No dependency floor
  moves. New tests are tagged `unit` (the PostgreSQL module is `integration` by its `pytestmark`).
  No R-IDs or phase ids in source or tests.

§2 (`exit_status` ranges), §7–§11 are untouched.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- *Is the cost compilation, so that a cache tweak suffices?* → No: `bc6547a` already hits the
  compiled cache after the first result; the repeated work is statement construction, value
  coercion and cache-key generation ([01 §1](research/01-statement-reuse.md)).
- *Can compiled-cache misses be the R4 observable?* → No, they are flat on `bc6547a` too. Statement
  identity flips from N to 1 ([01 §3](research/01-statement-reuse.md)).
- *Do typed binds keep the datetime and UUID processing?* → Types are taken from the columns, the
  same type objects the ORM path binds with. On SQLite the stored values and SQL text are identical
  (probe). PostgreSQL and TimescaleDB are proven in CI ([02](research/02-types-and-dialects.md)).
- *Can R1 run offline from extracted trees with one Python?* → Yes. The three builds share one lock,
  and `cpu_bench.py` ran on the base in about 90 s and reported the bar missed
  ([03 §1](research/03-measurement.md)).

## 5. Risks & open questions

For the foreman to rule on at the plan gate:

1. **R6 "spread".** The GOAL does not define it. `check_evidence.py r6` uses the wider of the two
   builds' (2.8.3, fix) max-minus-min ranges, and a shortfall within it passes only with a recorded
   `ruling`.
2. **No hypertable-specific writeback-equivalence test.** On the TimescaleDB entries, the equivalence
   and fence tests run on a plain table. Hypertable writeback is covered end to end by
   `test_timescale.py::test_run_compress_list_vacuum`. Bind processing is per dialect and the column
   types are identical, so the plan adds nothing further.
3. **The classifying SELECT is prebuilt too.** That is optional in the GOAL. It can be trimmed to
   UPDATE-only, and then the guard checks only UPDATE identity.
4. **The R4 guard names the mechanism.** A later legitimate redesign (an executemany batch, a
   different statement cache) has to update the guard with it. The GOAL accepts a mechanism-shaped
   guard.
5. **R1 runs on the build machine (macOS, the environment's Python).** It satisfies the GOAL's "one
   machine, one Python". The Linux cluster behaviour is R6's job. `b819de6` stands in for 2.8.3, as
   the GOAL allows.
6. **CI evidence needs the foreman.** The PostgreSQL and TimescaleDB green comes only from the PR's
   CI run. A red entry from the known Python 3.13 restart-update flake on `develop` would fail
   `check_evidence.py ci`, and needs a rerun or a ruling.
7. **Environment.** The worktree has no `.venv`. The `verify:` commands use `uv run --frozen`, which
   needs a synced environment (offline from the local cache). `cpu_bench.py` only needs an
   interpreter whose environment holds the lock.

## 6. Verification strategy

- **P1 (red first):** with `src/` and `tests/test_writeback.py` byte-identical to `bc6547a`, the
  guard reports XFAIL (strict) and the reference-equivalence tests pass. The PostgreSQL module
  collects and skips locally.
- **P2:** the `src/` diff is `data/model.py` only, with no column, table or CLI change, and no xfail
  left. The writeback, statement and server unit tests pass. A CLI drive in a throwaway site
  (`.agents/factory/bin/temp_site.sh`) runs 100 tasks with one failure through `hsx -b1 -f`, and checks
  99 succeeded, 1 failed, the failures file, no unattributed rows, and no stale/duplicate warnings.
- **P3:** `cpu_bench.py` exits 0 (bar met), and `check_evidence.py r1` passes on the recorded block.
- **P4:** the full suite passes (one recorded rerun allowed for the known localhost flakes). No
  code changes after the final fix commit. `EVIDENCE.md` names the three cluster builds by commit.
- **P5:** `check_evidence.py r1 ci r6` passes on the delivered evidence. No code changes after the
  final fix commit.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
