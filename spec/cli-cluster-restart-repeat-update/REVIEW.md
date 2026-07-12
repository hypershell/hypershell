# REVIEW — Safe re-submission: `--restart` / `--repeat` / `--update` source gating

> Adversarial QA by `hs-review`, run via a fresh blind correctness subagent. The correctness pass
> grades the branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does
> not see `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding
> cites an **executed** command, not an assertion.

- **Reviewed commit:** 93476f1  ·  **Base:** develop  ·  **Date:** 2026-07-11
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 (escalate to human on non-convergence)

## Verification run

Commands actually executed by the blind reviewer (spot-verified by the orchestrator against source):

- `uv run pytest -q` (full suite) → **352 passed** (~239s, exit 0).
- Feature files (`test_source` / `test_restart` / `test_submit` / `test_submit_json` / `test_groups`)
  → 98 passed; one **flaky** timing failure in `test_groups::test_group_failed_task_with_retries`
  cleared on re-run + in isolation + in full-file run. That test is **not modified** by the branch
  (the only test_groups change is distinct batch filenames, `batch1.in`/`batch2.in`, for R6) →
  timing flake, not a regression.
- `uv run hs initdb` + schema dump → `source(id,path,fingerprint,task_count,created)` table +
  `task.source`/`task.fingerprint` columns + the two new indices present.
- R2 fingerprint properties (11 Python assertions) → order-independent over tags; excludes
  template/uuid/attempt/timing/exit_status/`part`/resource knobs; `args` is pre-template. All pass.
- `hs submit` matrix on a fresh DB → R5 refuse (exit 2), R6 refuse-suggest-`--update` (exit 2), R8
  repeat-all, R9 update-novel-only, R10 reject (exit 2). All correct.
- Real local `hsx -N1` cluster → plain new file ran; R11 refuse (exit 2); **R12** idempotent restart
  ("All previous tasks completed (3) - stopping", exit 0, no hang); R12 changed-content refuse
  (exit 2); **R14** `--update --restart` ran only the novel task; bare `--restart` DB-resume (exit 0);
  **R15** `--repeat` re-ran all. All correct.
- in_memory guard → `hsx FILE --no-db` twice → both exit 0, **0 source rows, 0 task rows** persisted.
- `--from-json` gating → R5/R8/R9 correct; `--from-json -` (stdin) twice both exit 0;
  `hsx --from-json … --restart` exit 0 (removed incompat works).
- Retry propagation → 3-row `false` chain carries identical fingerprint + identical non-null source
  across the chain; links intact (spot-verified at `data/model.py:625-626`).
- **R17 query plans on a 300k-row table** → `Source.matching` uses `index_source_lookup`;
  `count_for_source` and `fingerprints_for_sources` → **`SCAN task`** (linear). Isolation proved the
  cause: adding the reserved-exclusion predicate, or a non-partial `(source,fingerprint)` index,
  flips the plan to `SEARCH … USING COVERING INDEX`.
- `bash -n` / `zsh -n` completions clean; new flags present. `sphinx-build` → only 2 pre-existing
  toctree warnings.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Source` entity, `data/model.py` | schema dump: source table + rows on submit | ✅ |
| R2 | `Task.compute_fingerprint`, `data/model.py` | 11-property Python test | ✅ |
| R3 | reserved `<direct>`/`<stdin>`, `submit.py` | double-submit of both succeeds; reserved rows present | ✅ |
| R4 | `source_fingerprint_and_count`, `submit.py` | upfront md5+count; stdin streamed/exempt | ✅ |
| R5 | `apply_source_gate` (no-flag+match), `submit.py:240` | 2nd bare submit refuses, exit 2, count unchanged | ✅ |
| R6 | `apply_source_gate` (no-flag+differs), `submit.py:244` | changed content refuses "pass --update", exit 2 | ✅ |
| R7 | `_warn_if_incomplete`, `submit.py:175` | mechanism works, **but false-positives after dedup** | ⚠️ **F2** |
| R8 | `apply_source_gate` (repeat), `submit.py:217` | `--repeat` doubles task count | ✅ |
| R9 | `apply_source_gate` (update), `submit.py:221` | `--update` novel-only, same-path lineage | ✅ |
| R10 | `SubmitApp.check_arguments` | `--update --repeat` exit 2 | ✅ |
| R11 | `apply_source_gate` via `hsx` | `hsx FILE` seen → refuse exit 2 | ✅ |
| R12 | `apply_source_gate` (restart), `submit.py:226` | idempotent restart; changed→refuse exit 2 | ✅ |
| R13 | `ClusterApp.check_arguments` | `hsx --update` (no restart) exit 2 | ✅ |
| R14 | `apply_source_gate` (update+restart) | only novel task ran | ✅ |
| R15 | `apply_source_gate` (repeat) | all re-run, new source | ✅ |
| R16 | `ClusterApp.check_arguments` | `hsx --update --repeat` exit 2 | ✅ |
| R17 | `index_source_lookup` + `index_tasks_source` | lookup indexed ✓; **count-check + de-dup full-scan** | ❌ **F1** |
| R18 | log lines in `apply_source_gate` | found/present/submitting/refusal logs observed | ✅ |

**Unmapped changes (possible scope creep):**
- TimescaleDB provider aliases + `uuid7` gate (`data/core.py`) — maps loosely to R17's TimescaleDB
  target; **self-admittedly incomplete** ("hypertable management … not yet implemented"). Additive,
  guarded, no correctness defect. Report, not a blocker.
- `format_source(relative=…)` (`core/pretty_print.py`) — the `relative=True` branch is never called
  (future use). Harmless; presentation maps to R18.
- Regenerated man-page content for pre-existing `hs list` flags is legitimate §12 same-commit
  doc catch-up, not new scope.
- `server.py` scheduler-race fix (submitter ref + `submission_complete()` guard) — a deliberate,
  disclosed amendment; reproduced as **correct** (schedules on plain runs, halts without hang on
  idempotent restart, resumes on bare `--restart`, no early-exit; sentinel ordering preserved).

## Findings

### [HIGH / CONFIRMED] R17 unmet — count-check and de-dup full-scan the task table
- **Where:** `src/hypershell/data/model.py:827` (`count_for_source`), `:840`
  (`fingerprints_for_sources`), index `:850-854` (`index_tasks_source`).
- **Failure scenario:** `index_tasks_source` is a **partial** index carrying
  `WHERE source NOT IN (<direct>,<stdin>)`, but both queries filter plain `source == ?` /
  `source.in_(?)` without that predicate. Neither SQLite nor PostgreSQL can prove a bound param is
  non-reserved, so the partial-index predicate is not implied and the planner falls back to a full
  `SCAN task` — linear in **total** row count. Every `--update`/`--restart` de-dup
  (`fingerprints_for_sources`) and every seen-path submit's completeness check
  (`_warn_if_incomplete` → `count_for_source`) therefore scans the whole table. This is exactly the
  cost R17 forbids ("billions–trillions of rows … not materially slower than today"), and the
  `fingerprints_for_sources` docstring (`:833-835`) falsely claims the scan is "bounded,
  index-backed."
- **Evidence:** 300k-row table, `EXPLAIN QUERY PLAN`:
  `SELECT count(*) … WHERE source=?` → `SCAN task`; `SELECT DISTINCT fingerprint … WHERE source IN (?)`
  → `SCAN task`; **the same query + `AND source NOT IN (reserved)`** → `SEARCH … USING COVERING INDEX
  index_tasks_source`; a non-partial `(source,fingerprint)` index → `SEARCH … USING COVERING INDEX`.
  (Verified on SQLite; the partial-index implication rule is identical on PostgreSQL/TimescaleDB,
  R17's explicit target.)
- **Touches:** R17. Lands in **`data/model.py`** (high-blast-radius core) → human gate.
- **Fix direction (for `hs-build`):** either add `Task.source.not_in([DIRECT_SOURCE_ID,
  STDIN_SOURCE_ID])` to both queries so the partial predicate is implied, or make
  `index_tasks_source` a full (non-partial) index. Re-verify with `EXPLAIN QUERY PLAN` + the
  reserved-id path.

### [MEDIUM / CONFIRMED] R7 false-positive — a correctly de-duplicated `--update`/`--restart` is later reported "incomplete"
- **Where:** `src/hypershell/submit.py:169` (`_new_source_id` records the **full** count), `:225`/`:231`
  (dedup returns `skip`), `:180` (`_warn_if_incomplete`).
- **Failure scenario:** `apply_source_gate` records `task_count = count` (the full file count) on the
  new `--update`/`--restart` source, but the `Loader` stamps that new source id onto **only the novel
  tasks** — skipped identities keep their prior source. So `count_for_source(new) < task_count` by
  construction, and the next re-submission's `_warn_if_incomplete` emits a spurious "appears
  incomplete" warning that misrepresents an intentional, correct dedup as a failed/partial ingest —
  which could prompt a user to `--repeat` and double-submit.
- **Evidence:** reproduced live — submit 3-line file; edit to 4 lines; `--update` (adds 1 novel,
  records `task_count=4`); a second `--update` →
  `WARNING [submit] Prior submission of …/tasks.in appears incomplete: 1 of 4 tasks present`.
- **Secondary imprecision (same code, not separately reported):** retry rows inherit the parent's
  `source`, so `count_for_source` also *over*-counts for retried sources — R7's "completeness"
  arithmetic is inexact in both directions.
- **Touches:** R7. Root cause in **`submit.py`** (not high-blast-radius core), but interacts with
  `data/model.py` counting.
- **Fix direction (for `hs-build`):** record the **stamped** (post-dedup) count on the new source, or
  scope the completeness check to the lineage's expected-vs-landed rather than a single source row.

*No other candidate survived refutation.* The `server.py` scheduler-race change, retry
fingerprint/source propagation, all gate exit codes, the in_memory guard, and the JSON path were each
reproduced as correct. No AGENTS.md invariant (§1 lifecycle predicates, §2 exit_status, §3 retry
chain, §4 in_memory, §6 sentinel ordering) was found violated.

## Human-gate triggers

**TRIGGERED.** F1 (CONFIRMED) lands in **`data/model.py`**, a high-blast-radius core file. Per the
review rubric, a human must sign off before any remediation (`hs-build`) or `hs-publish` proceeds —
regardless of the auto-loop. F2's root cause is in `submit.py` (not core) but is part of the same
remediation.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

Not run (plain `/hs-review` invocation). Available via `/hs-review completeness` if desired — but the
requirement→evidence matrix above already shows all 18 R-IDs implemented, with R17 partial (F1) and
R7 partial (F2).

## Review cycle 2 — remediation + human sign-off (2026-07-11)

Both CONFIRMED cycle-1 findings were remediated on this branch (each its own commit):

- **F1 (R17)** — `f876000`: `index_tasks_source` made **full covering, non-partial**. A partial
  `WHERE source NOT IN (reserved)` predicate can't be honored for parameter-bound `source` lookups, so
  `count_for_source`/`fingerprints_for_sources` were full-scanning; a full index is used with plain
  bound params on every engine. Verified via `EXPLAIN QUERY PLAN` at 300k rows (`SCAN task` →
  `SEARCH … USING COVERING INDEX`) + a structural and a query-plan regression test.
- **F2 (R7)** — `698580a`: `_warn_if_incomplete` now measures completeness across the whole same-path
  **lineage**, not a single source, so a de-duplicated `--update`/`--restart` is no longer misreported
  as incomplete. Verified via CLI (false "1 of 4" gone; genuine "2 of 3" still warns) + an integration
  regression test.

Full suite **354 passed** (was 352; net +2 regression guards). The mandatory coupled-core human-gate
(F1 touched `data/model.py`) is satisfied by the **maintainer's explicit sign-off** to publish, with
the two judgment calls flagged and acknowledged: the F1 partial→full index **design change**, and the
F2 refinement of R7's per-source wording to **lineage-scoped** (serves R7's intent under the dedup that
R9/R12/R14 mandate; not a `GOAL` R-ID change). Scale validation (10M+ rows on Anvil/HPC, and the
PostgreSQL/TimescaleDB query plans that SQLite-only plan checks can't cover) is being performed by the
maintainer out-of-band.

**Verdict: approved (human sign-off).**
