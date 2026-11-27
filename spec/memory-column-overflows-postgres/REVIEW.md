# REVIEW — The memory column overflows on PostgreSQL

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 397c402d  ·  **Base:** develop (7c85c83c)  ·  **Date:** 2026-11-23
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Contract drift check:** `GOAL.md` changed after shaping in f78ddbea (GOAL gate) and de9f5861
  (plan gate, Q1 amended for TimescaleDB 2.22/2.23). Both are the maintainer's recorded rulings, and
  the review graded the amended contract.

## Verification run

Fresh reviewer, xhigh effort, local PostgreSQL 18 with TimescaleDB 2.32.0 Community available for
scratch databases (`HS_TEST_TIMESCALE` empty). Old-shape databases were built from real 2.8.3 and
2.9.0a9 installs in throwaway venvs, because the Docker templates are not available here.

- `tests/test_memory_column.py` → 17 passed.
- The non-legacy memory tests in `test_upgrade_postgres.py` → passed. The template-gated tests
  (`hs283`/`hs290a1`) were not run here; CI only.
- Related default suites → 504 passed. `test_writeback_postgres` and `test_zone_postgres` → 31
  passed. `test_upgrade_postgres`, `test_server_initdb` and `test_data/test_upgrade` → 77 passed,
  with failures only in the template-gated tests.
- CLI drives in throwaway sites:
  - `hs submit -m 4GB`, an inline `memory:4GB` tag, `hs list memory`, `hs info ID -x memory`,
    `hsx -M 8GB -r 2`.
  - Retries of a failing 5 GB task, on SQLite, fresh postgres, fresh timescale, and widened 2.8.3
    and 2.9.0a9 databases.
- Interruption, concurrency and lock tests:
  - SIGKILL and SIGINT mid-rewrite on 3M rows.
  - 5 concurrent first uses on 3M rows, and 3 on a 1M-row hypertable.
  - A held lock past the ceiling.
  - A dependent view.
- TimescaleDB 2.32:
  - direct widening with the columnstore on;
  - the compressed-chunk refusal for `hs list`, `hs submit` and `hs initdb`;
  - a schema-qualified database;
  - the forced 2.22/2.23 gate;
  - the Apache-style path;
  - a postgres-provider database whose `task` is a hypertable;
  - the race path through `_wrap`.
- The documented manual steps (compressed chunk; 2.22/2.23) followed literally on 2.32 → they work.
  They are unverified on 2.22/2.23 themselves.
- Docs build under `-W` → passes.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `data/model.py` (`BIG_INTEGER`), P2 | `hs initdb` gives `bigint` on postgres and timescale; the SQLite DDL is byte-identical to 2.9.0a9 | ✅ |
| R2 | P2 | 4 GB via `-m` and inline tag stored, listed, shown, run; retries carry 5368709120 | ✅ |
| R3 | `data/upgrade.py` (`WIDENED`, `_widen`), P2 | Real 2.8.3 and 2.9.0a9 databases widened on first use; row digests unchanged; 3M-row count and sum kept | ✅ |
| R4 | `Step.notice`, P2 | Notice before the rewrite (SIGINT after the notice cut a live rewrite); "Widened…" at INFO after commit | ✅ (see F1) |
| R5 | `_is_narrow_integer`, P2 | Already-wide database returns `[]` in 0.05 s while another session holds the advisory lock and an EXCLUSIVE table lock; no ALTER, advisory or TimescaleDB statements | ✅ |
| R6 | P2 | SIGKILL/SIGINT/view/ceiling cases leave the schema unchanged; concurrent first uses widen once | ✅ |
| R7 | `_columnstore`, refusals, `_wrap`, P3 | Every TimescaleDB case above; no columnstore statement issued | ✅ |
| R8 | `database.rst`, `database_alt.rst`, AGENTS.md, docstring | Two `versionchanged:: 2.9.0` markers; docs build `-W`; manual steps followed | ✅ |
| R9 | `tests/test_memory_column.py`, `test_upgrade_postgres.py`, `test_timescale.py` | Run as above; template and TimescaleDB integration cases are CI-only | ✅ |

Unmapped changes: none material. The seed and ROADMAP status moved to `adopted:…` (lifecycle).
`BIG_INTEGER` and `WIDENED` joined `__all__` in support of R1 and R3.

## Findings

### [LOW/CONFIRMED] F1 — The rewrite notice is logged on every lock-wait attempt, even when no rewrite happens
- **Where:** `src/hypershell/data/upgrade.py:466-467` (`if step.notice: log.info(step.notice)` in
  `_apply_transactional`).
- **Failure scenario:**
  - The notice is logged before the ALTER asks for its lock.
  - Each lock-wait slice rolls back and runs again, so while another session holds a lock on `task`,
    the log alternates "Rewriting table task…" and "Waiting for…".
  - The run can then give up with nothing rewritten: about 30 false "Rewriting" lines at the
    defaults.
  - Schema and data are unaffected.
- **Evidence:** a transaction holding `SELECT 1 FROM task`, then `hs list --count` with
  `LOCK_WAIT_SLICE=1`, `LOCK_WAIT_CEILING=4` → three "Rewriting" lines interleaved with "Waiting",
  then `SchemaUpgradeError … gave up after 4 s`, rc=5, column still `integer`.
- **Touches:** R4 (log accuracy). R4's ordering holds; the repeated line misleads.

## Absurd-input notes (not findings)

- `hs submit -m 8388608T` (just over 2^63−1 bytes) exits 5 with an overflow traceback. Values up to
  2^63−1 are stored. Range checking belongs to `issues/bounded-inputs.md`.
- `-m 8EB` / `-m 9000PB` are refused by `parse_bytes` (exit 2).

## Human-gate triggers

- None. F1 is in `data/upgrade.py`, which is outside the high-blast-radius list. It is a log line
  and touches no DB-lifecycle invariant: the schema and rows are unchanged in every case. The foreman
  gates under the maintainer's mandate.

## Review cycle 2 — approved (2026-11-23)

- **Reviewed commit:** 13df84a7 · **Mode:** a fresh blind pass over the full diff, excluding `spec/`,
  not scoped to the remediation · xhigh effort.
- **Verification:**
  - The reviewer built the `hs283` and `hs290a1` templates itself from the real 2.8.3 and 2.9.0a1
    releases, without Docker, so the template tests ran locally: `test_upgrade_postgres.py` 38
    passed.
  - `test_timescale.py` with `HS_TEST_TIMESCALE=community`: 98 passed, 1 skipped.
  - `test_memory_column.py`: 17 passed. `test_data/` and `test_server_initdb.py`: 92 passed.
  - The full default suite: 4121 passed.
- **F1 (cycle 1) is fixed.** Through the CLI, with the real 30 s slice and another session holding
  `task` for 45 s, the notice is logged once at 0.4 s, before the wait and the rewrite, and
  "Widened" follows at 45.0 s. Both regression tests pass.
- **All of R1–R9 were re-verified**, including:
  - real 2.9.0a9 postgres and timescale databases, with rows byte-identical after widening;
  - interruption and concurrency on 2M rows;
  - the compressed-chunk refusal, including a chunk compressed between plan and statement;
  - the documented recovery SQL.
- **Findings:** none.
- **Absurd input (not a finding):** `-m 8388608TB` (2^63 bytes) gives a DataError, exit 5, with
  nothing stored and no hang. That belongs to `issues/bounded-inputs.md`.
- **Human gate:** not triggered.
