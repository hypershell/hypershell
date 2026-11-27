# REVIEW — Result writeback at or below 2.8.3's CPU cost

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `43190577d354c933a7fe86733e1031a340b57b8a`  ·  **Base:** `develop` at
  `bc6547a` (the remote-tracking ref; the local `develop` was stale)  ·  **Date:** 2026-11-04
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

**How the pass ran.** The foreman spawned a fresh reviewer for this cycle and briefed it with
GOAL.md, the spec-excluded diff, `AGENTS.md`, `invariants.md`, `review-rubric.md` and the evidence
file (`EVIDENCE.md`, `cpu_bench.py`, `check_evidence.py`). That reviewer ran the correctness pass
itself rather than delegating it again, and it never read `PLAN.md`, `TECH.md` (beyond its
`slug`/`kind`/`status`/`base`/`review` keys), `research/` or `META.md`.

**Contract drift.** `GOAL.md` has one post-shaping commit, `168e294`. It resolves R6's
`[NEEDS CLARIFICATION]` marker with the text the shaper proposed, word for word, and records the
foreman's sign-off (2026-11-04). This review grades R6 against that resolved text.

## Verification run

All runs used the build agent's frozen offline environments (CPython 3.11.18, 3.12.16, 3.13.18 and
3.14.10, all with SQLAlchemy 2.0.50) on one Apple Silicon host. Every run had its own process group
and a kill timer, and all scratch files stayed outside the repository.

- **R1 bench, rerun:** `uv run --frozen python spec/writeback-prebuilt-update/cpu_bench.py` on 3.14,
  with N = 5,000, six recorded rounds, rotated order and exit 0. Every build ran 1.0 UPDATE and 1.0
  statement per result, and every child checked 5,000 completed rows and 50 failed rows.

  | build | commit | mean ms | median ms | min | max |
  |---|---|---|---|---|---|
  | 2.8 | `b819de6` | 0.4491 | 0.4575 | 0.4100 | 0.4877 |
  | develop | `bc6547a` | 0.5573 | 0.5786 | 0.4802 | 0.6283 |
  | fix | `4319057` | 0.3480 | 0.3536 | 0.3144 | 0.3915 |

  The fix used 22.5% less CPU than 2.8 and 37.6% less than `develop`, and it beat 2.8 in every one
  of the six rounds. The absolute numbers are higher than the recorded run's (2.8 0.3669, fix
  0.2981) because the host was busier, but the ranking and the margin hold.
- **R4 guard against `bc6547a`:** `git archive bc6547a src` with the branch's `tests/` beside it,
  then `PYTHONPATH=<that src> python -m pytest -v tests/test_writeback_statement.py
  tests/test_writeback.py`. An extra probe test confirmed that `hypershell` was imported from the
  `bc6547a` tree, which has no prebuilt statements. **Result: 1 failed, 27 passed.**
  `test_writeback_reuses_its_statements` fails at its final assertion, `AssertionError: 12 UPDATE
  objects over 12 executions`. Its preconditions held first: 10 applied, 1 duplicate and 1 stale,
  with the expected log lines. So it fails because statements are built per result, and for no
  other reason. On the fix the same test passes, including 10 repeated runs on 3.13.
- **Full suite:** `python -m pytest -q -n 6`. On 3.14: 645 passed, 74 skipped. On 3.11: 645
  passed, 74 skipped. The skips are the PostgreSQL and TimescaleDB tests, since no server was
  available. Neither listed flake occurred.
- **Targeted, 3.12 and 3.13:** `pytest tests/test_writeback.py tests/test_writeback_statement.py
  tests/test_writeback_postgres.py tests/test_server.py` gave 52 passed and 24 skipped on each
  version.
- **Differential, `bc6547a` against the fix:** one deterministic scenario run through
  `Receiver.update_tasks` (with `redirect_failures`), importing `hypershell` from each tree in turn.
  Each seed has 200 rows in mixed states: in flight (with or without a confirmed client), already
  completed, cancelled, reverted to unscheduled, rescheduled, or deleted. They receive 120 bundles
  of 1–6 results. The results carry an exact, `None`, +1 µs or re-offset `schedule_time`, and
  exit statuses that include `-1`, `-9`, `-1001` and `255`. A quarter of the bundles hold the
  same task twice, and the table has an extra column that the model does not know.

  Across 8 seeds, each run in DB mode and in `in_memory` mode, on 3.14 and again on 3.11, all 32
  comparisons were **byte-identical** between the two trees: every `hypershell` log record down to
  TRACE, the failures output, and a raw `SELECT *` dump of the final table. Each DB-mode seed
  exercised all three outcomes, with 35–54 applied, 41–51 duplicate and 201–244 stale.
- **Dialect compilation:** the fix's cached UPDATE and SELECT, against `bc6547a`'s per-result ORM
  statements, compiled for SQLite, `postgresql+psycopg` and `postgresql+psycopg2` in one process
  (one cached object, three dialects). On every dialect the SQL was **identical apart from bind
  names**. That includes the `::UUID`, `::TIMESTAMP WITH TIME ZONE`, `::SMALLINT`, `::INTEGER`
  and `::VARCHAR` casts and the 13-column SET list. The bind-processed parameter values also
  matched in order, for tz-aware datetimes at three offsets, UUID strings and `None` paths.
  Passing the `r_*` execution keys did not add anything to the SET list.
- **Cache across engines and a fork:** the cached pair served two SQLite engines in turn. A forked
  child then disposed of the inherited pools, used its own engine, and still received the same
  cached objects. Control then returned to the first engine. Every step gave `[APPLIED ×3,
  DUPLICATE]` (a task listed twice in one bundle) and the expected number of completed rows, and
  the child exited 0.
- **CLI drive** in a throwaway site, via `.agents/factory/bin/temp_site.sh`, on 3.13, for the fix
  and then for `bc6547a`:
  - `seq 100 | uv run hsx -t 'echo {}' -N4 -b1` exited 0 and printed 100 unique lines, with
    `count=100 remaining=0 failed=0`.
  - `seq 50 | uv run hsx -t 'test $(( {} % 10 )) -ne 0' -N4 -b2 -r1 -f failures.txt` exited 0. It
    wrote 10 lines to the failures file, left 155 rows with 10 failed, and logged no stale or
    duplicate warnings.
  - `--no-db --no-confirm` printed 20 lines.

  Both builds gave identical results.
- **Evidence file:** `python spec/writeback-prebuilt-update/check_evidence.py r1 ci r6` passed all
  three checks. Recomputing R6 independently gives means of 114.2 (2.8.3), 117.1 (`develop`) and
  117.2 (fix) tasks/s. `git diff 37bfc72 HEAD -- src tests` and `git diff 7439999 HEAD -- src
  tests` are both empty, so the measured builds and the CI head carry the same code as the reviewed
  commit. `2.8.3^{commit}` is `4d5771b8…`, as recorded.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `src/hypershell/data/model.py` `Task.__result_statements` / `Task.__apply_result` (`37bfc72`) | EVIDENCE R1 (0.2981 vs 2.8 0.3669 ms); rerun above (0.3480 vs 0.4491 ms, fix below 2.8 in every round); `check_evidence.py r1` | ✅ |
| R2 | same | The differential produced byte-identical outcomes, log lines, failures output and rows, covering no `schedule_time`, cancelled rows, a task twice in one bundle and deleted rows. `tests/test_writeback.py` is unchanged and passes on 3.11–3.14. | ✅ |
| R3 | same; `tests/test_writeback_statement.py`, `tests/test_writeback_postgres.py::test_applied_values_match_reference` | Dialect compilation gives the same SQL and processed binds for SQLite and both PostgreSQL drivers. Raw-value pins pass on SQLite. CI run `41840120711` passed the pg13, pg18 and three TimescaleDB jobs, and those jobs run `test_writeback_postgres.py` plus `test_timescale.py::test_run_compress_list_vacuum`, which completes tasks on a hypertable through the real cluster. The write remains one conditional UPDATE. | ✅ |
| R4 | `tests/test_writeback_statement.py::test_writeback_reuses_its_statements` (`@mark.unit`, default suite) | Fails on `bc6547a` at the statement-identity assertion with its preconditions met; passes on the fix, 10 of 10 repeats; no clock or CPU measurement | ✅ |
| R5 | The diff touches no serializer, schema column, `Entity.update_all` or `server.py` | `in_memory` differential identical; `test_in_memory_receiver_writes_nothing`, `test_no_confirm_result_applies` and `test_cluster_no_confirm_with_db` pass; CLI `--no-db --no-confirm` drive identical | ✅ |
| R6 | Foreman's cluster hand-back in `EVIDENCE.md` | The configuration matches GOAL: 256 single-executor clients, bundle size 1, TLS, SQLite, the server bound to 16 CPUs. There are 3 runs per build in 3 complete interleaved triples. Fix mean 117.2 ≥ 2.8.3 mean 114.2 tasks/s. `check_evidence.py r6` passes. | ✅ |

**Unmapped changes:** none. The `ROADMAP.md` entry and `issues/server-per-task-statement-overhead.md`
are the follow-up seed that GOAL's Non-goals name. Graded as prose:
- its figures match those GOAL records: 5.02 statements and 3.01 commits per task, 129.0 tasks/s,
  −4.3% and −8.0%, and about +6% CPU;
- it describes the old per-result statement accurately, in the past tense;
- it presents the scheduler and confirm paths only as a hypothesis;
- `status: unshaped` matches reality, and the ROADMAP entry's **Seed:** link resolves.

**Notes on R6, not findings.**
- The builds ran in a fixed order each round (2.8.3, then develop, then the fix) rather than the
  rotated order the hand-back requested. GOAL asks only that the builds be interleaved, and they
  were.
- As the foreman's note says, the server was not CPU-bound on that cluster: `develop` did not
  reproduce its loss there. R6 therefore shows that the fix does not regress throughput; it does
  not measure the size of the recovery. R1 is the evidence that discriminates.

## Findings

None. Several candidates were tried and dissolved under execution:
- a different SQL text, cast or bind processing on PostgreSQL or TimescaleDB;
- execution keys widening the SET list;
- the cached pair going stale across engines, dialects or a fork;
- the dropped ORM autoflush. Results are transient and never in the session, and the
  differential's identical outputs bear this out;
- a different `rowcount` meaning. The UPDATE and SELECT text are unchanged on every dialect, so
  each dialect keeps the meaning it had on `bc6547a`;
- a schema with extra columns;
- the `in_memory` and `no_confirm` paths.

## Human-gate triggers

- Not triggered. No finding is CONFIRMED. The diff touches `data/model.py`, a file in the
  high-blast-radius core, but this gate applies only to CONFIRMED findings.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run.
